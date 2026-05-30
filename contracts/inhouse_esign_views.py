"""Inhouse e-sign endpoints (no third-party provider).

This module implements invite + magic-link signing + audit logging.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

_HAS_PDF_LIBS = True
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.lib.utils import ImageReader

    from PIL import Image
    from pypdf import PdfReader, PdfWriter
except Exception:  # pragma: no cover - allow running without native imaging libs
    # If reportlab/Pillow/pypdf aren't available (local dev), set a flag and
    # provide minimal fallbacks. Actual e-sign functionality will be disabled
    # until the dependencies are installed.
    _HAS_PDF_LIBS = False
    canvas = None
    LETTER = None
    inch = None
    ImageReader = None
    Image = None
    PdfReader = None
    PdfWriter = None

from authentication.r2_service import R2StorageService

from notifications.email_service import EmailService

from .models import Contract, TemplateFile
from .models import InhouseSignatureContract, InhouseSigner, InhouseSigningAuditLog


def _tenant_id_or_response(request) -> uuid.UUID | Response:
    """Get tenant UUID for the authenticated request.

    The API uses stateless JWT auth (`JWTClaimsUser`) so `request.user.tenant_id`
    is a *claim* (often a string). If the claim is missing/invalid (e.g. "None"),
    UUIDField filters will raise and the request becomes a 500.

    Return a Response (403/400) instead of raising.
    """

    raw = getattr(request.user, 'tenant_id', None)
    if raw is None:
        return Response({'error': 'User has no tenant_id assigned'}, status=status.HTTP_403_FORBIDDEN)

    if isinstance(raw, uuid.UUID):
        return raw

    raw_str = str(raw).strip()
    if not raw_str or raw_str.lower() == 'none':
        return Response({'error': 'User has no tenant_id assigned'}, status=status.HTTP_403_FORBIDDEN)

    try:
        return uuid.UUID(raw_str)
    except Exception:
        return Response({'error': 'Invalid tenant_id in token'}, status=status.HTTP_400_BAD_REQUEST)


def _clamp_number(val: Any, min_v: float, max_v: float) -> float:
    try:
        n = float(val)
    except Exception:
        n = float(min_v)
    if n < min_v:
        return float(min_v)
    if n > max_v:
        return float(max_v)
    return float(n)


def _placement_from_payload(payload: Any, *, recipient_index: int) -> tuple[Placement, dict] | None:
    """Parse a placement dict from the client or signer record.

    Expected payload shape:
      { recipient_index?, page_number, position: {x,y,width,height} }
    All values are percent-based (0..100) except page_number.
    """

    if not isinstance(payload, dict):
        return None

    pos = payload.get('position')
    if not isinstance(pos, dict):
        return None

    page_number = int(payload.get('page_number') or 1)
    page_number = max(1, page_number)

    x = _clamp_number(pos.get('x'), 0, 100)
    y = _clamp_number(pos.get('y'), 0, 100)
    width = _clamp_number(pos.get('width'), 1, 100)
    height = _clamp_number(pos.get('height'), 1, 100)

    # Ensure the box stays within the page
    width = _clamp_number(width, 1, max(1.0, 100.0 - x))
    height = _clamp_number(height, 1, max(1.0, 100.0 - y))

    placement = Placement(page_number=page_number, x_pct=x, y_pct=y, w_pct=width, h_pct=height)
    normalized = {
        'recipient_index': int(recipient_index),
        'page_number': int(page_number),
        'position': {'x': x, 'y': y, 'width': width, 'height': height},
    }
    return placement, normalized


def _client_ip(request) -> str | None:
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        parts = [p.strip() for p in str(xff).split(',') if p.strip()]
        if parts:
            return parts[0]
    ip = request.META.get('REMOTE_ADDR')
    return str(ip).strip() if ip else None


def _user_agent(request) -> str | None:
    ua = request.META.get('HTTP_USER_AGENT')
    return str(ua).strip() if ua else None


def _device_id(request) -> str | None:
    # Frontend can send a stable device id header (stored in localStorage).
    val = request.META.get('HTTP_X_DEVICE_ID')
    return str(val).strip() if val else None


def _log_event(
    *,
    signing_contract: InhouseSignatureContract,
    event: str,
    message: str,
    request,
    signer: InhouseSigner | None = None,
    extra: dict | None = None,
):
    InhouseSigningAuditLog.objects.create(
        inhouse_signature_contract=signing_contract,
        signer=signer,
        event=event,
        message=message,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
        device_id=_device_id(request),
        extra=extra or {},
    )


def _strip_html(html: str) -> str:
    if not html:
        return ''
    text = re.sub(r'(?i)<\s*br\s*/?>', '\n', html)
    text = re.sub(r'(?i)</\s*(p|div|h\d|li)\s*>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _get_editor_snapshot_from_r2(r2_key: str) -> dict | None:
    try:
        if not r2_key:
            return None
        r2 = R2StorageService()
        raw = r2.get_file_bytes(r2_key)
        if not raw:
            return None
        obj = json.loads(raw.decode('utf-8', errors='replace'))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _contract_export_text(contract: Contract) -> str:
    md = contract.metadata or {}

    try:
        r2_key = md.get('editor_r2_key')
        if isinstance(r2_key, str) and r2_key.strip():
            snap = _get_editor_snapshot_from_r2(r2_key.strip())
            if isinstance(snap, dict):
                txt = snap.get('rendered_text')
                if isinstance(txt, str) and txt.strip():
                    return txt
                html = snap.get('rendered_html')
                if isinstance(html, str) and html.strip():
                    return _strip_html(html)
    except Exception:
        pass

    txt = md.get('rendered_text')
    if isinstance(txt, str) and txt.strip():
        return txt
    html = md.get('rendered_html')
    if isinstance(html, str) and html.strip():
        return _strip_html(html)
    return ''


def _generate_contract_pdf_bytes(contract: Contract) -> bytes:
    text = _contract_export_text(contract)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=LETTER)
    width, height = LETTER
    left = 0.75 * inch
    top = height - 0.75 * inch
    bottom = 0.75 * inch

    text_obj = c.beginText(left, top)
    text_obj.setFont('Times-Roman', 11)

    import textwrap

    max_chars = 110
    for line in (text or '').splitlines():
        wrapped_lines = (
            textwrap.wrap(
                line,
                width=max_chars,
                replace_whitespace=False,
                drop_whitespace=False,
            )
            or ['']
        )
        for wl in wrapped_lines:
            if text_obj.getY() <= bottom:
                c.drawText(text_obj)
                c.showPage()
                text_obj = c.beginText(left, top)
                text_obj.setFont('Times-Roman', 11)
            text_obj.textLine(wl)

    c.drawText(text_obj)
    c.save()
    buffer.seek(0)
    return buffer.read()


@dataclass
class Placement:
    page_number: int
    x_pct: float
    y_pct: float
    w_pct: float
    h_pct: float


def _resolve_signature_placement(contract: Contract, recipient_index: int, *, pdf_bytes: bytes | None = None) -> Placement:
    md = contract.metadata or {}
    template_filename = str(md.get('template_filename') or md.get('template') or '').strip()

    if template_filename:
        tf = TemplateFile.objects.filter(filename=template_filename).first()
        cfg = (tf.signature_fields_config or {}) if tf else {}
        fields = cfg.get('fields') if isinstance(cfg, dict) else None
        if isinstance(fields, list):
            for f in fields:
                if not isinstance(f, dict):
                    continue
                if str(f.get('type') or '').lower() != 'signature':
                    continue
                if int(f.get('recipient_index') or 0) != int(recipient_index):
                    continue
                pos = f.get('position') or {}
                if isinstance(pos, dict):
                    return Placement(
                        page_number=max(1, int(f.get('page_number') or 1)),
                        x_pct=float(pos.get('x') or 10),
                        y_pct=float(pos.get('y') or 80),
                        w_pct=float(pos.get('width') or 30),
                        h_pct=float(pos.get('height') or 8),
                    )

    # Default placement:
    # - Prefer the last page (most contracts place signature blocks at the end)
    # - Place in upper/mid area so it's visible (y_pct measured from top)
    page_number = 1
    try:
        if pdf_bytes:
            reader = PdfReader(BytesIO(pdf_bytes))
            if reader.pages:
                page_number = max(1, len(reader.pages))
    except Exception:
        page_number = 1

    y = 30 + int(recipient_index) * 12
    if y > 85:
        y = 85
    return Placement(page_number=page_number, x_pct=12, y_pct=float(y), w_pct=35, h_pct=10)


def _parse_data_url_png(data_url: str) -> bytes:
    raw = str(data_url or '').strip()
    if not raw.startswith('data:'):
        raise ValueError('Expected a data URL')
    header, _, b64 = raw.partition(',')
    if 'base64' not in header.lower():
        raise ValueError('Expected base64 data URL')
    payload = base64.b64decode(b64)
    # Normalize to PNG bytes
    img = Image.open(BytesIO(payload))
    out = BytesIO()
    img.save(out, format='PNG')
    return out.getvalue()


def _stamp_signature_on_pdf(base_pdf: bytes, *, signature_png: bytes, placement: Placement) -> bytes:
    reader = PdfReader(BytesIO(base_pdf))
    writer = PdfWriter()

    page_index = max(0, int(placement.page_number) - 1)
    if page_index >= len(reader.pages):
        page_index = 0

    for idx, page in enumerate(reader.pages):
        if idx == page_index:
            page_w = float(page.mediabox.width)
            page_h = float(page.mediabox.height)

            box_x = page_w * (float(placement.x_pct) / 100.0)
            box_w = page_w * (float(placement.w_pct) / 100.0)

            box_h = page_h * (float(placement.h_pct) / 100.0)
            # y_pct is measured from top in the UI
            box_y_from_top = page_h * (float(placement.y_pct) / 100.0)
            box_y = page_h - box_y_from_top - box_h

            img = Image.open(BytesIO(signature_png))
            iw, ih = img.size
            if iw <= 0 or ih <= 0:
                raise ValueError('Invalid signature image')

            scale = min(box_w / float(iw), box_h / float(ih))
            draw_w = float(iw) * scale
            draw_h = float(ih) * scale
            draw_x = box_x + (box_w - draw_w) / 2.0
            draw_y = box_y + (box_h - draw_h) / 2.0

            overlay_buf = BytesIO()
            c = canvas.Canvas(overlay_buf, pagesize=(page_w, page_h))
            c.drawImage(ImageReader(img), draw_x, draw_y, width=draw_w, height=draw_h, mask='auto')
            c.save()
            overlay_buf.seek(0)

            overlay_pdf = PdfReader(overlay_buf)
            page.merge_page(overlay_pdf.pages[0])

        writer.add_page(page)

    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _frontend_base_url(request) -> str:
    # Prefer explicit config; otherwise use the request Origin (frontend),
    # and finally fall back to localhost dev.
    base = getattr(settings, 'FRONTEND_BASE_URL', None)
    if isinstance(base, str) and base.strip():
        return base.strip().rstrip('/')

    env_frontend = (os.getenv('FRONTEND_BASE_URL') or '').strip()
    if env_frontend:
        return env_frontend.rstrip('/')

    origin = (request.META.get('HTTP_ORIGIN') or '').strip()
    if origin:
        return origin.rstrip('/')

    env_app = (os.getenv('APP_URL') or '').strip()
    if env_app:
        # Common local-dev misconfig: APP_URL points at backend (8000).
        # If no explicit FRONTEND_BASE_URL or request Origin is present, prefer frontend (3000).
        try:
            parsed = urlparse(env_app)
            host = (parsed.hostname or '').lower()
            port = parsed.port
            if host in {'localhost', '127.0.0.1'} and port == 8000:
                return 'http://localhost:3000'
        except Exception:
            pass
        return env_app.rstrip('/')

    return 'http://localhost:3000'


def _owner_display_name(user) -> str:
    try:
        first = str(getattr(user, 'first_name', '') or '').strip()
        last = str(getattr(user, 'last_name', '') or '').strip()
        full = f"{first} {last}".strip()
        return full or str(getattr(user, 'email', '') or '').strip() or 'Owner'
    except Exception:
        return 'Owner'


def _certificate_logo_path() -> str | None:
    # Optional. If missing, certificate is generated without a logo.
    for key in (
        'INHOUSE_CERTIFICATE_LOGO_PATH',
        'CERTIFICATE_LOGO_PATH',
        'CERTIFICATE_LOGO_FILE',
    ):
        val = getattr(settings, key, None) or os.getenv(key)
        if isinstance(val, str) and val.strip() and os.path.exists(val.strip()):
            return val.strip()
    return None


def _generate_certificate_pdf_bytes(
    *,
    signing_contract: InhouseSignatureContract,
    executed_pdf_bytes: bytes,
) -> bytes:
    """Generate a structured, professional completion certificate PDF."""

    contract = signing_contract.contract
    title = (getattr(contract, 'title', '') or 'Contract').strip() or 'Contract'
    completed_at = (signing_contract.completed_at or timezone.now()).replace(microsecond=0)

    exec_sha = hashlib.sha256(executed_pdf_bytes or b'').hexdigest()
    cert_id = str(signing_contract.id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=LETTER)
    page_w, page_h = LETTER

    margin_x = 0.75 * inch
    left = margin_x
    right = page_w - margin_x
    top = page_h - 0.7 * inch
    bottom = 0.7 * inch

    def new_page() -> float:
        c.showPage()
        return top

    def ensure_space(y: float, needed: float) -> float:
        if y - needed <= bottom:
            return new_page()
        return y

    # --- Header ---
    header_h = 1.05 * inch
    c.setFillColorRGB(0.07, 0.09, 0.15)
    c.rect(0, page_h - header_h, page_w, header_h, fill=1, stroke=0)

    # Title
    c.setFillColorRGB(1, 1, 1)
    c.setFont('Helvetica-Bold', 19)
    c.drawString(left, page_h - 0.58 * inch, 'Certificate of Completion')
    c.setFont('Helvetica', 10)
    c.drawString(left, page_h - 0.86 * inch, 'In-house Electronic Signing (Audit Trail Included)')

    # Logo (optional)
    logo_path = _certificate_logo_path()
    if logo_path:
        try:
            img = Image.open(logo_path)
            iw, ih = img.size
            if iw > 0 and ih > 0:
                max_w = 1.45 * inch
                max_h = 0.55 * inch
                scale = min(max_w / float(iw), max_h / float(ih))
                draw_w = float(iw) * scale
                draw_h = float(ih) * scale
                c.drawImage(
                    ImageReader(img),
                    right - draw_w,
                    page_h - 0.90 * inch,
                    width=draw_w,
                    height=draw_h,
                    mask='auto',
                )
        except Exception:
            pass
    else:
        # Vector mark fallback, so the certificate doesn't look "blank".
        c.setFillColorRGB(1, 1, 1)
        c.circle(right - 22, page_h - 0.68 * inch, 16, stroke=0, fill=1)
        c.setFillColorRGB(0.07, 0.09, 0.15)
        c.setFont('Helvetica-Bold', 9)
        c.drawCentredString(right - 22, page_h - 0.71 * inch, 'CLM')

    # --- Summary card ---
    y = page_h - header_h - 0.35 * inch
    y = ensure_space(y, 120)

    card_h = 92
    c.setFillColorRGB(0.97, 0.98, 1.0)
    c.setStrokeColorRGB(0.85, 0.89, 0.97)
    c.roundRect(left, y - card_h, right - left, card_h, 10, stroke=1, fill=1)

    c.setFillColorRGB(0.10, 0.15, 0.35)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(left + 14, y - 22, title[:120])

    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.setFont('Helvetica', 9)
    c.drawString(left + 14, y - 40, f"Status: {signing_contract.status}")
    c.drawString(left + 14, y - 54, f"Completed (UTC): {completed_at.isoformat()}")
    c.drawString(left + 14, y - 68, f"Certificate ID: {cert_id}")

    # Right column
    c.drawString(left + 320, y - 40, f"Signing Order: {signing_contract.signing_order}")
    c.drawString(left + 320, y - 54, f"Contract ID: {str(contract.id)}")
    c.drawString(left + 320, y - 68, f"Executed PDF SHA-256: {exec_sha[:32]}…")

    y = y - card_h - 18

    # --- Signers table ---
    import textwrap

    signers = list(signing_contract.signers.all().order_by('signing_order', 'recipient_index', 'email'))
    y = ensure_space(y, 90)
    c.setFillColorRGB(0, 0, 0)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(left, y, 'Signers')
    y -= 12
    c.setFont('Helvetica', 9)
    c.setFillColorRGB(0.38, 0.38, 0.40)
    c.drawString(left, y, f"Total signers: {len(signers)}")
    y -= 16

    table_w = right - left
    col_num = 28
    col_name = 140
    col_email = 190
    col_status = 70
    col_signed = table_w - (col_num + col_name + col_email + col_status)

    def draw_table_header(y_pos: float) -> float:
        c.setFillColorRGB(0.94, 0.95, 0.97)
        c.setStrokeColorRGB(0.85, 0.85, 0.88)
        c.rect(left, y_pos - 16, table_w, 16, stroke=1, fill=1)
        c.setFillColorRGB(0.12, 0.12, 0.14)
        c.setFont('Helvetica-Bold', 8)
        c.drawString(left + 6, y_pos - 12, '#')
        c.drawString(left + col_num + 6, y_pos - 12, 'Name')
        c.drawString(left + col_num + col_name + 6, y_pos - 12, 'Email')
        c.drawString(left + col_num + col_name + col_email + 6, y_pos - 12, 'Status')
        c.drawString(left + col_num + col_name + col_email + col_status + 6, y_pos - 12, 'Signed At (UTC)')
        return y_pos - 16

    y = draw_table_header(y)
    c.setFont('Helvetica', 8)
    c.setFillColorRGB(0.15, 0.15, 0.15)

    row_h = 14
    for i, s in enumerate(signers, start=1):
        y = ensure_space(y, row_h + 24)
        if y == top:
            # New page, repeat section title + header
            c.setFillColorRGB(0, 0, 0)
            c.setFont('Helvetica-Bold', 12)
            c.drawString(left, y, 'Signers (continued)')
            y -= 18
            y = draw_table_header(y)
            c.setFont('Helvetica', 8)
            c.setFillColorRGB(0.15, 0.15, 0.15)

        if i % 2 == 0:
            c.setFillColorRGB(0.985, 0.985, 0.99)
            c.rect(left, y - row_h, table_w, row_h, stroke=0, fill=1)
            c.setFillColorRGB(0.15, 0.15, 0.15)

        signed_at = s.signed_at.replace(microsecond=0).isoformat() if s.signed_at else '—'
        c.drawString(left + 6, y - 10, str(i))
        c.drawString(left + col_num + 6, y - 10, (s.name or '').strip()[:24])
        c.drawString(left + col_num + col_name + 6, y - 10, (s.email or '').strip()[:32])
        c.drawString(left + col_num + col_name + col_email + 6, y - 10, (s.status or '').strip())
        c.drawString(left + col_num + col_name + col_email + col_status + 6, y - 10, signed_at)
        y -= row_h

    y -= 18

    # --- Audit timeline ---
    y = ensure_space(y, 80)
    c.setFillColorRGB(0, 0, 0)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(left, y, 'Audit Timeline')
    y -= 16
    c.setFont('Helvetica', 9)
    c.setFillColorRGB(0.38, 0.38, 0.40)
    c.drawString(left, y, 'Chronological activity log captured during the signing flow.')
    y -= 14

    logs = list(signing_contract.audit_logs.all().order_by('created_at')[:250])

    # Timeline header
    y = ensure_space(y, 22)
    c.setFillColorRGB(0.94, 0.95, 0.97)
    c.setStrokeColorRGB(0.85, 0.85, 0.88)
    c.rect(left, y - 16, table_w, 16, stroke=1, fill=1)
    c.setFillColorRGB(0.12, 0.12, 0.14)
    c.setFont('Helvetica-Bold', 8)
    c.drawString(left + 6, y - 12, 'Timestamp (UTC)')
    c.drawString(left + 128, y - 12, 'Event')
    c.drawString(left + 240, y - 12, 'Actor')
    c.drawString(left + 360, y - 12, 'IP')
    c.drawString(left + 420, y - 12, 'Message')
    y -= 18

    c.setFont('Helvetica', 8)
    c.setFillColorRGB(0.15, 0.15, 0.15)

    for idx, log in enumerate(logs, start=1):
        ts = log.created_at.replace(microsecond=0).isoformat() if log.created_at else ''
        actor = (log.signer.email if log.signer_id and log.signer else 'system')
        ip = (log.ip_address or '').strip()
        msg = (log.message or '').strip()

        msg_lines = textwrap.wrap(msg, width=52) or ['']
        row_needed = 12 * len(msg_lines) + 4
        y = ensure_space(y, row_needed + 20)
        if y == top:
            # New page; repeat timeline header
            c.setFillColorRGB(0, 0, 0)
            c.setFont('Helvetica-Bold', 12)
            c.drawString(left, y, 'Audit Timeline (continued)')
            y -= 18
            c.setFillColorRGB(0.94, 0.95, 0.97)
            c.setStrokeColorRGB(0.85, 0.85, 0.88)
            c.rect(left, y - 16, table_w, 16, stroke=1, fill=1)
            c.setFillColorRGB(0.12, 0.12, 0.14)
            c.setFont('Helvetica-Bold', 8)
            c.drawString(left + 6, y - 12, 'Timestamp (UTC)')
            c.drawString(left + 128, y - 12, 'Event')
            c.drawString(left + 240, y - 12, 'Actor')
            c.drawString(left + 360, y - 12, 'IP')
            c.drawString(left + 420, y - 12, 'Message')
            y -= 18
            c.setFont('Helvetica', 8)
            c.setFillColorRGB(0.15, 0.15, 0.15)

        if idx % 2 == 0:
            c.setFillColorRGB(0.985, 0.985, 0.99)
            c.rect(left, y - row_needed + 4, table_w, row_needed, stroke=0, fill=1)
            c.setFillColorRGB(0.15, 0.15, 0.15)

        c.drawString(left + 6, y - 10, ts)
        c.drawString(left + 128, y - 10, (log.event or '')[:18])
        c.drawString(left + 240, y - 10, actor[:18])
        c.drawString(left + 360, y - 10, ip[:16])

        msg_y = y - 10
        for ml in msg_lines:
            c.drawString(left + 420, msg_y, ml)
            msg_y -= 12

        y = msg_y - 6

    # --- Verification + Footer ---
    y -= 10
    y = ensure_space(y, 60)
    c.setFillColorRGB(0.10, 0.10, 0.10)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(left, y, 'Verification')
    y -= 14
    c.setFont('Helvetica', 9)
    c.setFillColorRGB(0.20, 0.20, 0.20)
    verify_text = (
        'This certificate summarizes the signing activity and references the executed PDF hash shown above. '
        'The audit timeline records key actions including invites, views, and signature completion.'
    )
    for wl in textwrap.wrap(verify_text, width=105):
        y = ensure_space(y, 14)
        c.drawString(left, y, wl)
        y -= 12

    c.setFont('Helvetica', 8)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawString(left, 0.45 * inch, f"Generated: {timezone.now().replace(microsecond=0).isoformat()} UTC")
    c.drawRightString(right, 0.45 * inch, 'CLM • In-house e-sign')

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def inhouse_start(request):
    contract_id = request.data.get('contract_id')
    signers_data = request.data.get('signers', [])
    signing_order = str(request.data.get('signing_order') or 'sequential').strip() or 'sequential'
    expires_in_days = int(request.data.get('expires_in_days') or 30)

    if signing_order not in ('sequential', 'parallel'):
        return Response({'error': 'Invalid signing_order'}, status=status.HTTP_400_BAD_REQUEST)

    if not contract_id or not isinstance(signers_data, list) or len(signers_data) == 0:
        return Response({'error': 'contract_id and signers are required'}, status=status.HTTP_400_BAD_REQUEST)

    cleaned = []
    for s in signers_data:
        if not isinstance(s, dict):
            continue
        email = str(s.get('email') or '').strip()
        name = str(s.get('name') or '').strip()
        if not email or not name:
            continue
        cleaned.append({'email': email, 'name': name})

    if not cleaned:
        return Response({'error': 'At least one valid signer is required'}, status=status.HTTP_400_BAD_REQUEST)

    tenant_id = _tenant_id_or_response(request)
    if isinstance(tenant_id, Response):
        return tenant_id
    contract = get_object_or_404(Contract, id=contract_id, tenant_id=tenant_id)

    sc, _ = InhouseSignatureContract.objects.get_or_create(contract=contract)

    # Reset if already in a non-draft state.
    if sc.status in ('sent', 'in_progress', 'completed', 'declined', 'failed'):
        sc.status = 'draft'
        sc.executed_pdf = None
        sc.certificate_pdf = None
        sc.certificate_generated_at = None
        sc.signing_request_data = {}
        sc.sent_at = None
        sc.completed_at = None
        sc.last_activity_at = None
        sc.save(
            update_fields=[
                'status',
                'executed_pdf',
                'certificate_pdf',
                'certificate_generated_at',
                'signing_request_data',
                'sent_at',
                'completed_at',
                'last_activity_at',
                'updated_at',
            ]
        )
        sc.signers.all().delete()

    sc.status = 'sent'
    sc.signing_order = signing_order
    sc.sent_at = timezone.now()
    sc.expires_at = timezone.now() + timedelta(days=max(1, expires_in_days))
    sc.last_activity_at = timezone.now()
    sc.signing_request_data = {
        'signers': cleaned,
        'signing_order': signing_order,
        'expires_in_days': expires_in_days,
        'provider': 'inhouse',
        'owner_email': str(getattr(request.user, 'email', '') or '').strip() or None,
        'owner_name': _owner_display_name(request.user),
    }
    sc.save()
    invite_urls = []

    for idx, signer_info in enumerate(cleaned):
        signer = InhouseSigner.objects.create(
            inhouse_signature_contract=sc,
            email=signer_info['email'],
            name=signer_info['name'],
            recipient_index=idx,
            signing_order=(idx + 1) if signing_order == 'sequential' else 0,
            token_expires_at=sc.expires_at,
        )

        url = f"/sign/inhouse?token={signer.access_token}"
        signing_url = f"{_frontend_base_url(request)}{url}"
        invite_urls.append(
            {
                'email': signer.email,
                'name': signer.name,
                # Backward-compatible relative path
                'signing_url': url,
                # Full URL for copy/paste outside the frontend app
                'signing_url_full': signing_url,
            }
        )
        _log_event(
            signing_contract=sc,
            event='invite_sent',
            message=f"Invitation created for {signer.email}",
            request=request,
            signer=signer,
        )

        # Best-effort email delivery; do not fail request if SMTP fails.
        try:
            ok = EmailService().send_inhouse_signature_invite_email(
                recipient_email=signer.email,
                recipient_name=signer.name,
                contract_title=str(contract.title or 'Contract'),
                signing_url=signing_url,
                expires_at_iso=sc.expires_at.isoformat() if sc.expires_at else None,
                sender_name=_owner_display_name(request.user),
            )
            _log_event(
                signing_contract=sc,
                event='invite_email_sent' if ok else 'error',
                message=(
                    f"Invite email sent to {signer.email}" if ok else f"Failed to send invite email to {signer.email}"
                ),
                request=request,
                signer=signer,
            )
        except Exception as e:
            _log_event(
                signing_contract=sc,
                event='error',
                message=f"Invite email exception for {signer.email}: {str(e)}",
                request=request,
                signer=signer,
            )

    first_url = invite_urls[0]['signing_url'] if invite_urls else None

    return Response(
        {
            'success': True,
            'contract_id': str(contract.id),
            'status': sc.status,
            'signing_order': sc.signing_order,
            'expires_at': sc.expires_at.isoformat() if sc.expires_at else None,
            'signing_url': first_url,
            'invite_urls': invite_urls,
        },
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inhouse_status(request, contract_id: str):
    tenant_id = _tenant_id_or_response(request)
    if isinstance(tenant_id, Response):
        return tenant_id
    contract = get_object_or_404(Contract, id=contract_id, tenant_id=tenant_id)
    sc = get_object_or_404(InhouseSignatureContract, contract=contract)

    signers_response = []
    for signer in sc.signers.all().order_by('recipient_index', 'email'):
        signers_response.append(
            {
                'email': signer.email,
                'name': signer.name,
                'status': signer.status,
                'signed_at': signer.signed_at.isoformat() if signer.signed_at else None,
                'has_signed': signer.has_signed,
                'recipient_index': signer.recipient_index,
            }
        )

    all_signed = bool(signers_response) and all(s.get('has_signed') for s in signers_response)

    _log_event(
        signing_contract=sc,
        event='status_checked',
        message='Status checked',
        request=request,
        extra={'all_signed': all_signed},
    )

    return Response(
        {
            'success': True,
            'contract_id': str(contract.id),
            'status': sc.status,
            'signers': signers_response,
            'all_signed': all_signed,
            'expires_at': sc.expires_at.isoformat() if sc.expires_at else None,
            'last_checked': timezone.now().isoformat(),
        },
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inhouse_audit(request, contract_id: str):
    """Return audit logs for an in-house signing request (tenant scoped)."""

    tenant_id = _tenant_id_or_response(request)
    if isinstance(tenant_id, Response):
        return tenant_id
    contract = get_object_or_404(Contract, id=contract_id, tenant_id=tenant_id)
    sc = get_object_or_404(InhouseSignatureContract, contract=contract)

    try:
        limit = int(request.query_params.get('limit') or 200)
    except Exception:
        limit = 200
    limit = max(1, min(500, limit))

    logs_qs = (
        InhouseSigningAuditLog.objects.select_related('signer')
        .filter(inhouse_signature_contract=sc)
        .order_by('-created_at')[:limit]
    )

    logs = []
    for row in logs_qs:
        signer = getattr(row, 'signer', None)
        logs.append(
            {
                'id': str(row.id),
                'created_at': row.created_at.isoformat() if row.created_at else None,
                'event': row.event,
                'message': row.message,
                'ip_address': row.ip_address,
                'user_agent': row.user_agent,
                'extra': row.extra,
                'signer': (
                    {
                        'email': signer.email,
                        'name': signer.name,
                        'recipient_index': signer.recipient_index,
                    }
                    if signer
                    else None
                ),
            }
        )

    return Response(
        {
            'success': True,
            'contract_id': str(contract.id),
            'logs': logs,
        },
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inhouse_requests(request):
    """List in-house signing requests for the current tenant.

    Query params:
      - status: draft|sent|in_progress|completed|declined|failed|all
      - q: free-text search across contract title and signer name/email
      - limit: page size (default 50, max 200)
      - offset: pagination offset (default 0)
    """

    allowed_statuses = {'draft', 'sent', 'in_progress', 'completed', 'declined', 'failed'}

    status_filter = str(request.query_params.get('status') or '').strip().lower()
    q = str(request.query_params.get('q') or '').strip()

    try:
        limit = int(request.query_params.get('limit') or 50)
    except Exception:
        limit = 50
    try:
        offset = int(request.query_params.get('offset') or 0)
    except Exception:
        offset = 0

    limit = max(1, min(200, limit))
    offset = max(0, offset)

    if status_filter and status_filter != 'all' and status_filter not in allowed_statuses:
        return Response({'error': 'Invalid status filter'}, status=status.HTTP_400_BAD_REQUEST)

    tenant_id = _tenant_id_or_response(request)
    if isinstance(tenant_id, Response):
        return tenant_id

    qs = (
        InhouseSignatureContract.objects.select_related('contract')
        .prefetch_related('signers')
        .filter(contract__tenant_id=tenant_id)
    )

    if status_filter and status_filter != 'all':
        qs = qs.filter(status=status_filter)

    if q:
        qs = qs.filter(
            Q(contract__title__icontains=q)
            | Q(signers__email__icontains=q)
            | Q(signers__name__icontains=q)
        ).distinct()

    total = qs.count()
    qs = qs.order_by('-updated_at')

    results = []
    for sc in qs[offset : offset + limit]:
        contract = sc.contract
        signers = []
        for signer in sc.signers.all().order_by('recipient_index', 'email'):
            signers.append(
                {
                    'email': signer.email,
                    'name': signer.name,
                    'status': signer.status,
                    'signed_at': signer.signed_at.isoformat() if signer.signed_at else None,
                    'has_signed': signer.has_signed,
                    'recipient_index': signer.recipient_index,
                }
            )

        owner_email = None
        owner_name = None
        if isinstance(sc.signing_request_data, dict):
            owner_email = sc.signing_request_data.get('owner_email')
            owner_name = sc.signing_request_data.get('owner_name')

        results.append(
            {
                'id': str(sc.id),
                'provider': 'inhouse',
                'contract_id': str(contract.id),
                'contract_title': contract.title,
                'status': sc.status,
                'signing_order': sc.signing_order,
                'sent_at': sc.sent_at.isoformat() if sc.sent_at else None,
                'completed_at': sc.completed_at.isoformat() if sc.completed_at else None,
                'expires_at': sc.expires_at.isoformat() if sc.expires_at else None,
                'last_activity_at': sc.last_activity_at.isoformat() if sc.last_activity_at else None,
                'created_at': sc.created_at.isoformat() if sc.created_at else None,
                'updated_at': sc.updated_at.isoformat() if sc.updated_at else None,
                'owner_email': owner_email,
                'owner_name': owner_name,
                'signers': signers,
            }
        )

    return Response(
        {
            'success': True,
            'count': total,
            'results': results,
            'limit': limit,
            'offset': offset,
        },
        status=status.HTTP_200_OK,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inhouse_download_executed(request, contract_id: str):
    tenant_id = _tenant_id_or_response(request)
    if isinstance(tenant_id, Response):
        return tenant_id
    contract = get_object_or_404(Contract, id=contract_id, tenant_id=tenant_id)
    sc = get_object_or_404(InhouseSignatureContract, contract=contract)

    if sc.status != 'completed' or not sc.executed_pdf:
        return Response({'error': 'Contract not yet completed'}, status=status.HTTP_400_BAD_REQUEST)

    _log_event(
        signing_contract=sc,
        event='document_downloaded',
        message='Executed document downloaded',
        request=request,
    )

    filename = f"{(contract.title or 'contract').strip().replace(' ', '_')}_signed.pdf"
    return FileResponse(
        BytesIO(sc.executed_pdf),
        as_attachment=True,
        filename=filename,
        content_type='application/pdf',
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inhouse_download_certificate(request, contract_id: str):
    tenant_id = _tenant_id_or_response(request)
    if isinstance(tenant_id, Response):
        return tenant_id
    contract = get_object_or_404(Contract, id=contract_id, tenant_id=tenant_id)
    sc = get_object_or_404(InhouseSignatureContract, contract=contract)

    if sc.status != 'completed' or not sc.certificate_pdf:
        return Response({'error': 'Certificate not yet available'}, status=status.HTTP_400_BAD_REQUEST)

    _log_event(
        signing_contract=sc,
        event='document_downloaded',
        message='Certificate downloaded',
        request=request,
        extra={'type': 'certificate'},
    )

    filename = f"{(contract.title or 'contract').strip().replace(' ', '_')}_certificate.pdf"
    return FileResponse(
        BytesIO(sc.certificate_pdf),
        as_attachment=True,
        filename=filename,
        content_type='application/pdf',
    )


@api_view(['GET'])
@permission_classes([AllowAny])
def inhouse_session(request, token: UUID):
    signer = get_object_or_404(InhouseSigner, access_token=token)
    sc = signer.inhouse_signature_contract

    if signer.token_expires_at and signer.token_expires_at <= timezone.now():
        return Response({'error': 'Signing link expired'}, status=status.HTTP_410_GONE)

    if signer.status == 'invited':
        signer.status = 'viewed'
        signer.last_viewed_at = timezone.now()
        signer.save(update_fields=['status', 'last_viewed_at', 'updated_at'])

    if sc.status == 'sent':
        sc.status = 'in_progress'
        sc.last_activity_at = timezone.now()
        sc.save(update_fields=['status', 'last_activity_at', 'updated_at'])

    _log_event(
        signing_contract=sc,
        event='link_viewed',
        message=f"Signing link viewed by {signer.email}",
        request=request,
        signer=signer,
    )

    contract = sc.contract
    pdf_bytes = sc.executed_pdf or _generate_contract_pdf_bytes(contract)

    # Prefer signer-chosen placement if present; otherwise fall back to template/default.
    placement_from_signer = None
    if isinstance(signer.signature_placement, dict) and signer.signature_placement:
        try:
            placement_from_signer = _placement_from_payload(signer.signature_placement, recipient_index=signer.recipient_index)
        except Exception:
            placement_from_signer = None

    if placement_from_signer:
        placement, normalized_payload = placement_from_signer
    else:
        placement = _resolve_signature_placement(contract, signer.recipient_index, pdf_bytes=pdf_bytes)
        normalized_payload = {
            'recipient_index': signer.recipient_index,
            'page_number': placement.page_number,
            'position': {
                'x': placement.x_pct,
                'y': placement.y_pct,
                'width': placement.w_pct,
                'height': placement.h_pct,
            },
        }

    return Response(
        {
            'success': True,
            'contract_id': str(contract.id),
            'contract_title': contract.title,
            'status': sc.status,
            'signer': {
                'email': signer.email,
                'name': signer.name,
                'recipient_index': signer.recipient_index,
                'status': signer.status,
            },
            'pdf_url': f"/api/v1/inhouse/esign/pdf/{signer.access_token}/",
            'placement': normalized_payload,
        },
        status=status.HTTP_200_OK,
    )


@xframe_options_exempt
@api_view(['GET', 'OPTIONS'])
@permission_classes([AllowAny])
def inhouse_pdf(request, token: UUID):
    origin = (request.META.get('HTTP_ORIGIN') or '').strip()
    cors_origin = origin or '*'

    if request.method == 'OPTIONS':
        resp = Response(status=status.HTTP_200_OK)
        resp['Access-Control-Allow-Origin'] = cors_origin
        resp['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        # pdf.js uses Range requests, which trigger preflight.
        resp['Access-Control-Allow-Headers'] = 'Range'
        resp['Access-Control-Max-Age'] = '86400'
        return resp

    signer = get_object_or_404(InhouseSigner, access_token=token)
    sc = signer.inhouse_signature_contract

    if signer.token_expires_at and signer.token_expires_at <= timezone.now():
        return Response({'error': 'Signing link expired'}, status=status.HTTP_410_GONE)

    contract = sc.contract
    pdf_bytes = sc.executed_pdf or _generate_contract_pdf_bytes(contract)

    resp = FileResponse(
        BytesIO(pdf_bytes),
        as_attachment=False,
        filename=f"{(contract.title or 'contract').strip().replace(' ', '_')}.pdf",
        content_type='application/pdf',
    )

    # Avoid stale iframe renders (especially after signing) by disabling caching.
    resp['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp['Pragma'] = 'no-cache'

    # Allow the signer frontend (often on a different origin in dev/prod)
    # to embed the PDF in an iframe.
    resp.xframe_options_exempt = True

    # Allow cross-origin fetch (pdf.js) from the frontend.
    resp['Access-Control-Allow-Origin'] = cors_origin
    resp['Access-Control-Expose-Headers'] = 'Accept-Ranges, Content-Encoding, Content-Length, Content-Range'
    return resp


@api_view(['POST'])
@permission_classes([AllowAny])
def inhouse_sign(request, token: UUID):
    # Lock the signing contract row to prevent parallel signers overwriting executed_pdf.
    with transaction.atomic():
        signer = get_object_or_404(InhouseSigner.objects.select_for_update(), access_token=token)
        sc = get_object_or_404(
            InhouseSignatureContract.objects.select_for_update(),
            id=signer.inhouse_signature_contract_id,
        )

        if signer.token_expires_at and signer.token_expires_at <= timezone.now():
            return Response({'error': 'Signing link expired'}, status=status.HTTP_410_GONE)

        if signer.has_signed:
            return Response({'error': 'Already signed'}, status=status.HTTP_400_BAD_REQUEST)

        if sc.signing_order == 'sequential':
            next_signer = (
                sc.signers.filter(has_signed=False)
                .exclude(status='declined')
                .order_by('signing_order', 'recipient_index')
                .first()
            )
            if next_signer and next_signer.id != signer.id:
                _log_event(
                    signing_contract=sc,
                    event='error',
                    message='Signer attempted to sign out of order',
                    request=request,
                    signer=signer,
                    extra={'expected_signer_email': next_signer.email},
                )
                return Response({'error': 'Not your turn to sign'}, status=status.HTTP_403_FORBIDDEN)

        signature_data_url = request.data.get('signature_data_url')
        if not signature_data_url:
            return Response({'error': 'signature_data_url is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            png = _parse_data_url_png(signature_data_url)
        except Exception as e:
            return Response({'error': f'Invalid signature image: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

        contract = sc.contract

        base_pdf = sc.executed_pdf or _generate_contract_pdf_bytes(contract)

        # Placement precedence: request payload > saved signer placement > template/default.
        requested_payload = request.data.get('placement')
        placement = None
        normalized_payload = None

        if requested_payload is not None:
            parsed = _placement_from_payload(requested_payload, recipient_index=signer.recipient_index)
            if not parsed:
                return Response({'error': 'Invalid placement'}, status=status.HTTP_400_BAD_REQUEST)
            placement, normalized_payload = parsed
            signer.signature_placement = normalized_payload
            signer.save(update_fields=['signature_placement', 'updated_at'])
        elif isinstance(signer.signature_placement, dict) and signer.signature_placement:
            parsed = _placement_from_payload(signer.signature_placement, recipient_index=signer.recipient_index)
            if parsed:
                placement, normalized_payload = parsed

        if not placement:
            placement = _resolve_signature_placement(contract, signer.recipient_index, pdf_bytes=base_pdf)
            normalized_payload = {
                'recipient_index': signer.recipient_index,
                'page_number': placement.page_number,
                'position': {
                    'x': placement.x_pct,
                    'y': placement.y_pct,
                    'width': placement.w_pct,
                    'height': placement.h_pct,
                },
            }

        try:
            next_pdf = _stamp_signature_on_pdf(base_pdf, signature_png=png, placement=placement)
        except Exception as e:
            _log_event(
                signing_contract=sc,
                event='error',
                message=f"Failed to stamp signature: {str(e)}",
                request=request,
                signer=signer,
            )
            return Response({'error': 'Failed to apply signature'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        signer.signature_png = png
        signer.signature_mime = 'image/png'
        signer.status = 'signed'
        signer.has_signed = True
        signer.signed_at = timezone.now()
        signer.signed_ip_address = _client_ip(request)
        signer.signed_user_agent = _user_agent(request)
        signer.signed_device_id = _device_id(request)
        signer.save()

        sc.executed_pdf = next_pdf
        sc.last_activity_at = timezone.now()

        # Check completion
        remaining = sc.signers.filter(has_signed=False).count()
        just_completed = False
        if remaining == 0 and sc.status != 'completed':
            sc.status = 'completed'
            sc.completed_at = timezone.now()
            just_completed = True
            try:
                contract.status = 'executed'
                contract.save(update_fields=['status', 'updated_at'])
            except Exception:
                pass

        sc.save()

        _log_event(
            signing_contract=sc,
            event='signing_completed',
            message=f"Signer completed: {signer.email}",
            request=request,
            signer=signer,
            extra={'placement': normalized_payload},
        )

        # If fully completed, generate certificate and email executed + certificate to everyone.
        if just_completed:
            try:
                if not sc.certificate_pdf:
                    cert_bytes = _generate_certificate_pdf_bytes(signing_contract=sc, executed_pdf_bytes=sc.executed_pdf or b'')
                    sc.certificate_pdf = cert_bytes
                    sc.certificate_generated_at = timezone.now()
                    sc.save(update_fields=['certificate_pdf', 'certificate_generated_at', 'updated_at'])
                    _log_event(
                        signing_contract=sc,
                        event='certificate_generated',
                        message='Completion certificate generated',
                        request=request,
                    )
            except Exception as e:
                _log_event(
                    signing_contract=sc,
                    event='error',
                    message=f"Certificate generation failed: {str(e)}",
                    request=request,
                )

            # Send emails after the transaction commits to avoid holding locks during SMTP.
            try:
                owner_email = None
                owner_name = None
                if isinstance(sc.signing_request_data, dict):
                    owner_email = sc.signing_request_data.get('owner_email')
                    owner_name = sc.signing_request_data.get('owner_name')
                owner_email = str(owner_email or '').strip() or None
                owner_name = str(owner_name or '').strip() or None

                executed_filename = f"{(contract.title or 'contract').strip().replace(' ', '_')}_signed.pdf"
                cert_filename = f"{(contract.title or 'contract').strip().replace(' ', '_')}_certificate.pdf"

                attachments = [
                    {
                        'filename': executed_filename,
                        'content_type': 'application/pdf',
                        'content': sc.executed_pdf or b'',
                    },
                    {
                        'filename': cert_filename,
                        'content_type': 'application/pdf',
                        'content': sc.certificate_pdf or b'',
                    },
                ]

                recipients: list[tuple[str, str]] = []
                for s in sc.signers.all().order_by('recipient_index', 'email'):
                    recipients.append((s.email, s.name or s.email))
                if owner_email:
                    recipients.append((owner_email, owner_name or owner_email))

                contract_title = str(contract.title or 'Contract')
                completed_at_iso = sc.completed_at.isoformat() if sc.completed_at else None

                def _send_after_commit():
                    try:
                        svc = EmailService()
                        for email, name in recipients:
                            if not email:
                                continue
                            ok = svc.send_inhouse_signing_completed_email(
                                recipient_email=email,
                                recipient_name=name,
                                contract_title=contract_title,
                                completed_at_iso=completed_at_iso,
                                attachments=attachments,
                            )
                            _log_event(
                                signing_contract=sc,
                                event='completion_email_sent' if ok else 'error',
                                message=(
                                    f"Completion email sent to {email}"
                                    if ok
                                    else f"Failed to send completion email to {email}"
                                ),
                                request=request,
                                extra={'recipient_email': email},
                            )
                    except Exception as e:
                        _log_event(
                            signing_contract=sc,
                            event='error',
                            message=f"Completion email pipeline failed: {str(e)}",
                            request=request,
                        )

                transaction.on_commit(_send_after_commit)
            except Exception as e:
                _log_event(
                    signing_contract=sc,
                    event='error',
                    message=f"Completion email scheduling failed: {str(e)}",
                    request=request,
                )

        return Response(
            {
                'success': True,
                'contract_id': str(contract.id),
                'status': sc.status,
                'all_signed': remaining == 0,
            },
            status=status.HTTP_200_OK,
        )
