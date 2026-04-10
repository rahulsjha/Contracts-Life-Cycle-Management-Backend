from __future__ import annotations

import json
import logging
import math
import os
import re
import importlib
from typing import Optional, Tuple

import requests
from django.conf import settings

from .clause_library_data import CLAUSE_LIBRARY
from .models import ClauseLibraryItem

logger = logging.getLogger(__name__)


_VOYAGE_DISABLED_DUE_TO_AUTH = False


MAX_EXTRACT_CHARS = 120_000


OCR_MIN_TEXT_CHARS = 800
OCR_MAX_PAGES = 5


def detect_contract_type(text: str, filename: str = '') -> str:
    """
    Detect contract type from text content and filename.
    Returns one of: nda, service_agreement, employment, purchase, license, lease, partnership, loan, vendor, unknown
    """
    text_lower = (text or '').lower()
    filename_lower = (filename or '').lower()
    
    # Keywords for each contract type
    keywords = {
        'nda': ['confidential', 'non-disclosure', 'nda', 'confidentiality agreement', 'proprietary'],
        'employment': ['employment agreement', 'offer letter', 'employee', 'employer', 'compensation', 'benefits', 'termination'],
        'service_agreement': ['service agreement', 'services to be provided', 'service provider', 'deliverables'],
        'purchase': ['purchase agreement', 'purchase order', 'seller', 'buyer', 'goods', 'price', 'payment terms'],
        'license': ['license agreement', 'licensed', 'licensee', 'licensor', 'intellectual property', 'trademark'],
        'lease': ['lease agreement', 'tenant', 'landlord', 'rent', 'lease term', 'leasee'],
        'partnership': ['partnership agreement', 'partner', 'joint venture', 'partnership'],
        'loan': ['loan agreement', 'lender', 'borrower', 'principal', 'interest rate', 'repayment'],
        'vendor': ['vendor agreement', 'supplier', 'supply agreement', 'vendor contract'],
    }
    
    # Score each type
    scores = {}
    for contract_type, type_keywords in keywords.items():
        score = 0
        for keyword in type_keywords:
            score += text_lower.count(keyword)
            score += filename_lower.count(keyword)
        scores[contract_type] = score
    
    # Return type with highest score, default to unknown
    if max(scores.values()) > 0:
        return max(scores, key=scores.get)
    return 'unknown'


def _safe_json_from_text(text: str) -> Optional[dict]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to find a JSON object in the text.
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def extract_text_from_bytes(file_bytes: bytes, filename: str) -> str:
    name = (filename or '').lower()

    try:
        if name.endswith('.pdf'):
            import io

            def _read_with(reader_cls):
                reader = reader_cls(io.BytesIO(file_bytes))
                parts: list[str] = []
                for page in getattr(reader, 'pages', []) or []:
                    try:
                        parts.append(page.extract_text() or '')
                    except Exception:
                        parts.append('')
                return "\n".join(parts)

            text = ''
            # Prefer pypdf (installed) but fall back to PyPDF2.
            try:
                from pypdf import PdfReader as PyPdfReader

                text = _read_with(PyPdfReader)
            except Exception:
                text = ''

            if not (text or '').strip():
                try:
                    from PyPDF2 import PdfReader as PyPDF2Reader

                    text = _read_with(PyPDF2Reader)
                except Exception:
                    text = text or ''

            return (text or '')[:MAX_EXTRACT_CHARS]

        if name.endswith('.docx'):
            import io
            import docx

            doc = docx.Document(io.BytesIO(file_bytes))
            text = "\n".join([p.text for p in doc.paragraphs if p.text])
            return text[:MAX_EXTRACT_CHARS]

        # txt or fallback: treat as utf-8
        return file_bytes.decode('utf-8', errors='ignore')[:MAX_EXTRACT_CHARS]

    except Exception as e:
        logger.exception('Text extraction failed: %s', e)
        return file_bytes.decode('utf-8', errors='ignore')[:MAX_EXTRACT_CHARS]


def _looks_like_scanned_pdf(text: str) -> bool:
    t = (text or '').strip()
    if len(t) >= OCR_MIN_TEXT_CHARS:
        return False
    # If it extracted mostly whitespace or a few repeated characters, assume scan.
    letters = sum(1 for ch in t if ch.isalpha())
    return letters < 100


def _tesseract_available() -> bool:
    # Best-effort: only used if pytesseract + tesseract binary are present.
    try:
        importlib.import_module('pytesseract')
    except Exception:
        return False
    return bool(os.popen('command -v tesseract').read().strip())


def ocr_extract_pdf_text(file_bytes: bytes) -> str:
    """Best-effort OCR for scanned PDFs.

    Priority:
    1) Tesseract (if installed)
    2) Gemini multimodal (if enabled) + pdf2image (requires poppler)

    If neither is available, returns empty string.
    """

    # 1) Tesseract OCR
    if _tesseract_available():
        try:
            from pdf2image import convert_from_bytes
            pytesseract = importlib.import_module('pytesseract')

            images = convert_from_bytes(file_bytes, first_page=1, last_page=OCR_MAX_PAGES)
            parts: list[str] = []
            for img in images:
                parts.append(pytesseract.image_to_string(img) or '')
            return "\n".join(parts)[:MAX_EXTRACT_CHARS]
        except Exception as e:
            logger.warning('Tesseract OCR failed: %s', e)

    # 2) Gemini vision OCR (optional)
    api_key = (settings.GEMINI_API_KEY or '').strip()
    if api_key:
        try:
            from pdf2image import convert_from_bytes
            import google.generativeai as genai

            genai.configure(api_key=api_key)
            model_name = getattr(settings, 'GEMINI_OCR_MODEL', None) or 'gemini-2.0-flash'
            model = genai.GenerativeModel(model_name)

            images = convert_from_bytes(file_bytes, first_page=1, last_page=OCR_MAX_PAGES)
            prompt = (
                "You are an OCR engine. Extract the exact text from these pages. "
                "Return plain text only. Preserve paragraphs and headings when possible."
            )

            # google-generativeai accepts PIL images.
            resp = model.generate_content([prompt, *images])
            out_text = getattr(resp, 'text', None) or ''
            return out_text[:MAX_EXTRACT_CHARS]
        except Exception as e:
            logger.warning('Gemini OCR failed: %s', e)

    return ''


def extract_text_with_ocr_fallback(file_bytes: bytes, filename: str) -> str:
    text = extract_text_from_bytes(file_bytes, filename)
    if (filename or '').lower().endswith('.pdf') and _looks_like_scanned_pdf(text):
        ocr_text = ocr_extract_pdf_text(file_bytes)
        if ocr_text and len(ocr_text.strip()) > len((text or '').strip()):
            return ocr_text
    return text


def generate_voyage_embedding(text: str) -> Optional[list]:
    global _VOYAGE_DISABLED_DUE_TO_AUTH
    if _VOYAGE_DISABLED_DUE_TO_AUTH:
        return None

    api_key = (settings.VOYAGE_API_KEY or '').strip()
    if not api_key:
        return None

    try:
        payload = {
            'model': 'voyage-law-2',
            'input': [text[:8000] if text else ''],
            'input_type': 'document',
        }
        resp = requests.post(
            'https://api.voyageai.com/v1/embeddings',
            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
            json=payload,
            timeout=25,
        )
        if resp.status_code >= 400:
            if resp.status_code in (401, 403):
                _VOYAGE_DISABLED_DUE_TO_AUTH = True
                logger.warning('Voyage embedding disabled due to auth failure (%s). Fix VOYAGE_API_KEY to re-enable.', resp.status_code)
            else:
                logger.warning('Voyage embedding failed: %s %s', resp.status_code, resp.text[:500])
            return None

        data = resp.json() or {}
        emb = None
        if isinstance(data, dict):
            items = data.get('data')
            if isinstance(items, list) and items:
                emb = items[0].get('embedding') if isinstance(items[0], dict) else None
        if isinstance(emb, list):
            return emb
        return None
    except Exception as e:
        logger.exception('Voyage embedding exception: %s', e)
        return None


def cosine_similarity(a: list, b: list) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        try:
            va = float(a[i])
            vb = float(b[i])
        except Exception:
            continue
        dot += va * vb
        na += va * va
        nb += vb * vb
    denom = math.sqrt(na) * math.sqrt(nb)
    return float(dot / denom) if denom else 0.0


def similarity_to_percent(sim: float) -> int:
    # Embeddings typically yield cosine ~0..1. Clamp and convert.
    s = max(0.0, min(1.0, float(sim)))
    return int(round(s * 100))


EXTRACTION_SCHEMA_HINT = {
    'parties': [
        {'name': 'Party A', 'role': 'Client/Provider/Discloser/Recipient', 'address': 'optional'}
    ],
    'dates': [
        {'label': 'Effective', 'value': 'YYYY-MM-DD', 'type': 'effective_date'},
        {'label': 'Expires', 'value': 'YYYY-MM-DD', 'type': 'end_date'},
    ],
    'values': [
        {'label': 'Total Fee', 'amount': 120000, 'currency': 'USD'}
    ],
    'clauses': [
        {
            'category': 'Termination / Confidentiality / Liability / Payment / IP / Governing Law',
            'title': 'Clause title',
            'snippet': 'short snippet',
            'risk': 'low|medium|high',
            'confidence': 0.0,
        }
    ],
    'obligations': [
        {'party': 'Client', 'obligation': 'Pay within 30 days', 'due': 'optional'}
    ],
    'constraints': [
        {'type': 'term|notice|cap|jurisdiction|data', 'text': 'constraint text'}
    ],
    'insights': [
        {'type': 'risk|missing|note', 'text': 'insight text'}
    ],
    'suggestions': [
        {'title': 'Suggestion title', 'text': 'What to change and why'}
    ],
    'summary': 'short summary',
    'review_text': 'professional review text for a human',
}


def gemini_extract_and_review(text: str, *, filename: str = '') -> Tuple[dict, str, Optional[str]]:
    api_key = (settings.GEMINI_API_KEY or '').strip()
    if not api_key:
        return ({}, '', 'AI extraction is not configured (GEMINI_API_KEY is missing).')

    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model_name = (getattr(settings, 'GEMINI_REVIEW_MODEL', None) or '').strip() or 'gemini-2.5-flash-lite'
        model = genai.GenerativeModel(model_name)

        prompt = (
            "You are Lawflow, a contract review assistant.\n"
            "Extract structured contract data and provide professional review suggestions.\n\n"
            "Return STRICT JSON only (no markdown, no extra text) with this shape:\n"
            f"{json.dumps(EXTRACTION_SCHEMA_HINT, ensure_ascii=False)}\n\n"
            "Rules:\n"
            "- Keep names exactly as in the document when possible\n"
            "- Dates must be ISO YYYY-MM-DD when possible\n"
            "- Identify governing law / jurisdiction when present\n"
            "- Include clause categories and a short snippet\n"
            "- Provide practical suggestions and risks\n\n"
            f"Filename: {filename}\n\n"
            "Contract text:\n"
            f"{(text or '')[:25000]}"
        )

        # Prefer structured JSON responses when supported.
        try:
            resp = model.generate_content(
                prompt,
                generation_config={
                    'response_mime_type': 'application/json',
                    'temperature': 0.2,
                },
            )
        except TypeError:
            # Older SDK versions may not support generation_config.
            resp = model.generate_content(prompt)
        out_text = getattr(resp, 'text', None) or ''
        data = _safe_json_from_text(out_text)
        if not isinstance(data, dict) or not data:
            return ({}, '', 'AI extraction returned invalid JSON; showing basic extraction only.')
        review_text = ''
        if isinstance(data, dict):
            review_text = str(data.get('review_text') or '')
        return (data, review_text, None)

    except Exception as e:
        logger.exception('Gemini extract/review failed: %s', e)
        # Keep the user-visible error short and stable (no stack traces / secrets).
        return ({}, '', 'AI extraction failed (Gemini). Showing basic extraction only.')


def normalize_analysis_shape(analysis: dict) -> dict:
    if not isinstance(analysis, dict):
        return {}

    out = dict(analysis)

    # Normalize dates to list
    dates = out.get('dates')
    if isinstance(dates, dict):
        arr = []
        for k, v in dates.items():
            if not v:
                continue
            arr.append({'label': k.replace('_', ' ').title(), 'value': v, 'type': k})
        out['dates'] = arr
    elif not isinstance(dates, list):
        out['dates'] = []

    if not isinstance(out.get('parties'), list):
        out['parties'] = []
    if not isinstance(out.get('values'), list):
        out['values'] = []
    if not isinstance(out.get('clauses'), list):
        out['clauses'] = []
    if not isinstance(out.get('obligations'), list):
        out['obligations'] = []
    if not isinstance(out.get('insights'), list):
        out['insights'] = []
    if not isinstance(out.get('suggestions'), list):
        out['suggestions'] = []
    if not isinstance(out.get('constraints'), list):
        out['constraints'] = []

    # Promote jurisdiction if present in insights/clauses into extracted_data helper
    jurisdiction = out.get('jurisdiction')
    if not jurisdiction:
        # try infer from clauses or suggestions
        for c in out.get('clauses') or []:
            if isinstance(c, dict) and str(c.get('category') or '').lower() in {'governing law', 'jurisdiction'}:
                jurisdiction = c.get('snippet')
                break
    if jurisdiction:
        out['jurisdiction'] = jurisdiction

    return out


def compute_risk_score(analysis: dict) -> dict:
    clauses = analysis.get('clauses') or []
    suggestions = analysis.get('suggestions') or []
    insights = analysis.get('insights') or []

    high = 0
    medium = 0
    low = 0
    for c in clauses:
        if not isinstance(c, dict):
            continue
        r = str(c.get('risk') or '').lower()
        if r == 'high':
            high += 1
        elif r == 'medium':
            medium += 1
        elif r == 'low':
            low += 1

    # Heuristic score 0..100
    score = 40
    score += high * 18
    score += medium * 10
    score += low * 4
    score += min(20, len(suggestions) * 2)
    score += min(10, sum(1 for i in insights if isinstance(i, dict) and str(i.get('type') or '').lower() == 'missing'))
    score = int(max(0, min(100, score)))

    if score >= 80:
        level = 'HIGH'
    elif score >= 55:
        level = 'MEDIUM'
    else:
        level = 'LOW'

    return {
        'risk_score': score,
        'risk_level': level,
        'clauses_count': len(clauses),
        'obligations_count': len(analysis.get('obligations') or []),
        'constraints_count': len(analysis.get('constraints') or []),
    }


def ensure_clause_library_seeded(tenant_id: str, user_id: Optional[str] = None) -> int:
    """Create tenant clause library rows if missing. Returns count created."""
    if not tenant_id:
        return 0
    existing = ClauseLibraryItem.objects.filter(tenant_id=tenant_id).count()
    if existing > 0:
        return 0

    created = 0
    objs = []
    for entry in CLAUSE_LIBRARY:
        objs.append(
            ClauseLibraryItem(
                tenant_id=tenant_id,
                key=entry['key'],
                category=entry['category'],
                title=entry['title'],
                content=entry['content'],
                default_risk=entry.get('default_risk') or 'medium',
                embedding=[],
                created_by=user_id,
            )
        )
    ClauseLibraryItem.objects.bulk_create(objs, ignore_conflicts=True)
    created = len(objs)
    return created


def _get_or_create_embedding(item: ClauseLibraryItem) -> Optional[list]:
    emb = item.embedding if isinstance(item.embedding, list) else []
    if emb:
        return emb
    # Generate embedding and cache
    text = f"{item.category}: {item.title}\n\n{item.content}"[:2000]
    new_emb = generate_voyage_embedding(text)
    if new_emb is not None:
        item.embedding = new_emb
        item.save(update_fields=['embedding', 'updated_at'])
        return new_emb
    return None


def attach_clause_matches(tenant_id: str, analysis: dict) -> dict:
    """Attach match_percent + matched_library to each detected clause.

    Uses Voyage embeddings + cosine similarity against tenant clause library.
    """

    clauses = analysis.get('clauses') or []
    if not clauses:
        return analysis

    # Ensure a library exists.
    ensure_clause_library_seeded(tenant_id)

    # Preload library items grouped by category.
    lib_items = list(ClauseLibraryItem.objects.filter(tenant_id=tenant_id).only('id', 'category', 'title', 'content', 'embedding', 'default_risk'))
    by_cat: dict[str, list[ClauseLibraryItem]] = {}
    for it in lib_items:
        by_cat.setdefault((it.category or '').strip() or 'General', []).append(it)

    out_clauses = []
    for c in clauses:
        if not isinstance(c, dict):
            out_clauses.append(c)
            continue

        category = (c.get('category') or 'General').strip()
        snippet = str(c.get('snippet') or c.get('title') or '')
        detected_text = (snippet or '')[:800]
        det_emb = generate_voyage_embedding(detected_text) if detected_text else None

        best = None
        best_sim = 0.0
        pool = by_cat.get(category) or by_cat.get('General') or lib_items
        # To keep runtime bounded, sample first N (seeded list is ordered; good enough for MVP)
        pool = pool[:60]
        if det_emb is not None:
            for li in pool:
                li_emb = _get_or_create_embedding(li)
                if not li_emb:
                    continue
                sim = cosine_similarity(det_emb, li_emb)
                if sim > best_sim:
                    best_sim = sim
                    best = li

        match_percent = similarity_to_percent(best_sim)
        enriched = dict(c)
        enriched['match_percent'] = match_percent
        if best is not None:
            enriched['matched_library'] = {
                'title': best.title,
                'category': best.category,
                'default_risk': best.default_risk,
            }
        # If Gemini didn't set risk, use baseline.
        if not enriched.get('risk') and best is not None:
            enriched['risk'] = best.default_risk

        out_clauses.append(enriched)

    analysis['clauses'] = out_clauses
    return analysis


def naive_fallback_extract(text: str) -> dict:
    if not text:
        return {}

    parties = []
    # Very naive party detection
    m = re.search(r"between\s+(.*?)\s+and\s+(.*?)(?:\.|\n)", text, re.IGNORECASE)
    if m:
        parties = [{'name': m.group(1).strip(), 'role': 'Party A'}, {'name': m.group(2).strip(), 'role': 'Party B'}]

    dates = {}
    for label, rx in [
        ('effective_date', r"Effective\s+Date\s*[:\-]\s*([A-Za-z0-9,\-/ ]+)")
    ]:
        dm = re.search(rx, text, re.IGNORECASE)
        if dm:
            dates[label] = dm.group(1).strip()

    values = []
    money = re.findall(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text)
    if money:
        values.append({'label': 'Amount', 'amount': money[0], 'currency': 'USD'})

    return {
        'parties': parties,
        'dates': dates,
        'values': values,
        'clauses': [],
        'obligations': [],
        'insights': [],
        'suggestions': [],
        'summary': '',
    }
