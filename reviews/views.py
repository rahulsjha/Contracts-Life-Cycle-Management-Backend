from __future__ import annotations

import io
import os
import uuid
import mimetypes

from django.http import HttpResponse, FileResponse
from django.utils import timezone
from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from authentication.r2_service import R2StorageService

from .models import ReviewContract
from .serializers import (
    ReviewContractCreateSerializer,
    ReviewContractDetailSerializer,
    ReviewContractListSerializer,
    ReviewAcceptanceSerializer,
    ReviewRejectionSerializer,
)
from .services import (
    attach_clause_matches,
    compute_risk_score,
    extract_text_with_ocr_fallback,
    generate_voyage_embedding,
    gemini_extract_and_review,
    normalize_analysis_shape,
    naive_fallback_extract,
    detect_contract_type,
)


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}


class ReviewContractViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def get_queryset(self):
        tenant_id = getattr(self.request.user, 'tenant_id', None)
        user_id = getattr(self.request.user, 'user_id', None)
        qs = ReviewContract.objects.none()
        if tenant_id and user_id:
            qs = ReviewContract.objects.filter(tenant_id=tenant_id, created_by=user_id)

        q = (self.request.query_params.get('q') or '').strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(Q(title__icontains=q) | Q(original_filename__icontains=q))
        return qs

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ReviewContractDetailSerializer
        elif self.action == 'list':
            return ReviewContractListSerializer
        return ReviewContractListSerializer

    def create(self, request, *args, **kwargs):
        user = request.user
        tenant_id = getattr(user, 'tenant_id', None)
        user_id = getattr(user, 'user_id', None)
        if not tenant_id or not user_id:
            return Response({'success': False, 'error': 'Missing tenant/user id'}, status=status.HTTP_400_BAD_REQUEST)

        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'success': False, 'error': 'No file provided. Use form field name "file".'}, status=status.HTTP_400_BAD_REQUEST)

        if getattr(file_obj, 'size', 0) > MAX_UPLOAD_BYTES:
            return Response({'success': False, 'error': 'File too large. Max size is 25MB.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        filename = getattr(file_obj, 'name', '') or 'upload'
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext not in ALLOWED_EXTENSIONS:
            return Response({'success': False, 'error': 'Only .pdf, .docx and .txt files are supported.'}, status=status.HTTP_400_BAD_REQUEST)

        meta_ser = ReviewContractCreateSerializer(data=request.data)
        meta_ser.is_valid(raise_exception=True)
        title = (meta_ser.validated_data.get('title') or '').strip()
        analyze = bool(meta_ser.validated_data.get('analyze', True))

        # Read bytes once (upload + extraction)
        file_bytes = file_obj.read()

        r2 = R2StorageService()
        # Store under dedicated prefix
        bio = io.BytesIO(file_bytes)
        bio.name = filename
        info = r2.upload_review_contract_file(
            file_obj=bio,
            tenant_id=str(tenant_id),
            user_id=str(user_id),
            filename=filename,
        )

        rc = ReviewContract.objects.create(
            tenant_id=tenant_id,
            created_by=user_id,
            title=title or os.path.splitext(filename)[0],
            original_filename=filename,
            file_type=ext,
            size_bytes=int(getattr(file_obj, 'size', len(file_bytes)) or len(file_bytes)),
            r2_key=str(info.get('key')),
            status='processing' if analyze else 'uploaded',
        )

        if analyze:
            self._analyze_review_contract(rc, file_bytes)

        return Response({'success': True, 'review_contract': ReviewContractDetailSerializer(rc).data}, status=status.HTTP_201_CREATED)

    def _analyze_review_contract(self, rc: ReviewContract, file_bytes: bytes):
        try:
            rc.status = 'processing'
            rc.error_message = ''
            rc.save(update_fields=['status', 'error_message', 'updated_at'])

            text = extract_text_with_ocr_fallback(file_bytes, rc.original_filename)
            if not (text or '').strip():
                raise ValueError(
                    'No text could be extracted from this document. '
                    'If this is a scanned PDF, OCR is required (install tesseract) or configure GEMINI_API_KEY for OCR.'
                )
            rc.extracted_text = text

            # Detect contract type
            contract_type = detect_contract_type(text, rc.original_filename)
            rc.contract_type = contract_type

            embedding = generate_voyage_embedding(text)
            if embedding is not None:
                rc.embedding = embedding

            analysis, review_text = gemini_extract_and_review(text, filename=rc.original_filename)
            if not analysis:
                analysis = naive_fallback_extract(text)

                # Surface a helpful warning when we are in basic-mode extraction.
                if not (getattr(settings, 'GEMINI_API_KEY', '') or '').strip():
                    rc.error_message = (
                        'AI extraction is not configured (GEMINI_API_KEY is missing). '
                        'Showing basic extraction only; results may be limited.'
                    )

            analysis = normalize_analysis_shape(analysis or {})

            # Attach lightweight debug metadata for the frontend/report.
            analysis.setdefault('_meta', {})
            if isinstance(analysis.get('_meta'), dict):
                analysis['_meta'].update({
                    'text_chars': len(text or ''),
                    'gemini_configured': bool((getattr(settings, 'GEMINI_API_KEY', '') or '').strip()),
                    'voyage_configured': bool((getattr(settings, 'VOYAGE_API_KEY', '') or '').strip()),
                })

            tenant_id = str(getattr(rc, 'tenant_id', '') or '')
            if tenant_id:
                analysis = attach_clause_matches(tenant_id, analysis)

            analysis['analysis_summary'] = compute_risk_score(analysis)

            rc.analysis = analysis or {}
            rc.review_text = review_text or ''
            rc.review_status = 'pending_review'  # Always start as pending review

            rc.status = 'ready'
            rc.save(update_fields=['status', 'error_message', 'extracted_text', 'embedding', 'analysis', 'review_text', 'contract_type', 'review_status', 'updated_at'])

        except Exception as e:
            rc.status = 'failed'
            rc.error_message = str(e)
            rc.save(update_fields=['status', 'error_message', 'updated_at'])

    @action(detail=True, methods=['post'], url_path='analyze')
    def analyze(self, request, pk=None):
        rc = self.get_object()
        try:
            r2 = R2StorageService()
            file_bytes = r2.get_file_bytes(rc.r2_key)
            self._analyze_review_contract(rc, file_bytes)
            return Response({'success': True, 'review_contract': ReviewContractDetailSerializer(rc).data}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({'success': False, 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'], url_path='url')
    def presigned_url(self, request, pk=None):
        rc = self.get_object()
        r2 = R2StorageService()
        url = r2.generate_presigned_url(rc.r2_key, expiration=3600)
        return Response({'success': True, 'url': url, 'expires_in': 3600}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        """Download the original uploaded contract file."""
        rc = self.get_object()
        r2 = R2StorageService()

        file_bytes = r2.get_file_bytes(rc.r2_key)
        if not file_bytes:
            return Response({'success': False, 'error': 'File not found.'}, status=status.HTTP_404_NOT_FOUND)

        filename = (rc.original_filename or rc.title or 'contract').strip() or 'contract'
        content_type, _ = mimetypes.guess_type(filename)
        content_type = content_type or 'application/octet-stream'

        bio = io.BytesIO(file_bytes)
        return FileResponse(bio, as_attachment=True, filename=filename, content_type=content_type)

    def _build_report_text(self, rc: ReviewContract) -> str:
        a = rc.analysis or {}
        parties = a.get('parties') or []
        summary = a.get('summary') or ''
        insights = a.get('insights') or []
        obligations = a.get('obligations') or []
        suggestions = a.get('suggestions') or []

        lines = []
        lines.append('LAWFLOW')
        lines.append('Contract Review Report')
        lines.append('')
        lines.append(f"Document: {rc.title or rc.original_filename}")
        lines.append(f"Generated: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append('')

        if parties:
            lines.append('Parties')
            for p in parties:
                if isinstance(p, dict):
                    n = p.get('name')
                    r = p.get('role')
                    if n:
                        lines.append(f"- {n}{(' (' + str(r) + ')') if r else ''}")
            lines.append('')

        if summary:
            lines.append('Summary')
            lines.append(str(summary).strip())
            lines.append('')

        if obligations:
            lines.append('Obligations')
            for o in obligations:
                if isinstance(o, dict):
                    lines.append(f"- {o.get('party','')}: {o.get('obligation','')}")
            lines.append('')

        if insights:
            lines.append('Insights')
            for i in insights:
                if isinstance(i, dict):
                    lines.append(f"- {i.get('text','')}")
                else:
                    lines.append(f"- {i}")
            lines.append('')

        if suggestions:
            lines.append('Suggestions')
            for s in suggestions:
                if isinstance(s, dict):
                    title = s.get('title') or 'Suggestion'
                    text = s.get('text') or ''
                    lines.append(f"- {title}: {text}")
            lines.append('')

        if rc.review_text:
            lines.append('Reviewer Notes')
            lines.append(rc.review_text.strip())
            lines.append('')

        return "\n".join(lines).strip() + "\n"

    @action(detail=True, methods=['get'], url_path='report-txt')
    def report_txt(self, request, pk=None):
        rc = self.get_object()
        text = self._build_report_text(rc)
        filename = f"{(rc.title or 'lawflow_report').strip().replace(' ', '_')}_review.txt"
        resp = HttpResponse(text, content_type='text/plain; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    @action(detail=True, methods=['get'], url_path='report-pdf')
    def report_pdf(self, request, pk=None):
        rc = self.get_object()
        text = self._build_report_text(rc)

        from io import BytesIO
        import textwrap
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=LETTER)
        width, height = LETTER

        left = 0.75 * inch
        top = height - 0.75 * inch
        bottom = 0.75 * inch

        text_obj = c.beginText(left, top)
        text_obj.setFont('Helvetica', 11)

        max_chars = 110
        for line in (text or '').splitlines():
            wrapped = textwrap.wrap(line, width=max_chars) or ['']
            for wl in wrapped:
                if text_obj.getY() <= bottom:
                    c.drawText(text_obj)
                    c.showPage()
                    text_obj = c.beginText(left, top)
                    text_obj.setFont('Helvetica', 11)
                text_obj.textLine(wl)

        c.drawText(text_obj)
        c.save()
        buffer.seek(0)

        filename = f"{(rc.title or 'lawflow_report').strip().replace(' ', '_')}_review.pdf"
        return FileResponse(buffer, as_attachment=True, filename=filename, content_type='application/pdf')

    @action(detail=True, methods=['post'], url_path='accept')
    def accept_review(self, request, pk=None):
        """Accept the reviewed contract"""
        rc = self.get_object()
        user = request.user
        
        serializer = ReviewAcceptanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        notes = serializer.validated_data.get('notes', '').strip()
        
        rc.review_status = 'accepted'
        rc.review_notes = notes
        rc.reviewer_id = getattr(user, 'user_id', None)
        rc.reviewed_at = timezone.now()
        rc.save(update_fields=['review_status', 'review_notes', 'reviewer_id', 'reviewed_at', 'updated_at'])
        
        return Response({
            'success': True,
            'message': 'Contract accepted successfully',
            'review_contract': ReviewContractDetailSerializer(rc).data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reject')
    def reject_review(self, request, pk=None):
        """Reject the reviewed contract with reasons"""
        rc = self.get_object()
        user = request.user
        
        serializer = ReviewRejectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        notes = serializer.validated_data.get('notes', '').strip()
        suggested_changes = serializer.validated_data.get('suggested_changes', [])
        
        # Store rejection reason and suggested changes in review_notes
        rejection_text = f"REJECTED - Reason: {notes}"
        if suggested_changes:
            rejection_text += f"\n\nSuggested Changes:\n" + "\n".join(f"- {change}" for change in suggested_changes)
        
        rc.review_status = 'rejected'
        rc.review_notes = rejection_text
        rc.reviewer_id = getattr(user, 'user_id', None)
        rc.reviewed_at = timezone.now()
        rc.save(update_fields=['review_status', 'review_notes', 'reviewer_id', 'reviewed_at', 'updated_at'])
        
        return Response({
            'success': True,
            'message': 'Contract rejected successfully',
            'review_contract': ReviewContractDetailSerializer(rc).data
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='flag-issues')
    def flag_issues(self, request, pk=None):
        """Flag issues in the contract for review"""
        rc = self.get_object()
        user = request.user
        
        issues = request.data.get('issues', [])
        if not issues:
            return Response({
                'success': False,
                'error': 'Please provide at least one issue'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        notes = f"Issues Flagged: {len(issues)} issue(s) identified\n"
        notes += "\n".join(f"- {issue}" for issue in issues)
        
        rc.review_status = 'flags_raised'
        rc.review_notes = notes
        rc.reviewer_id = getattr(user, 'user_id', None)
        rc.reviewed_at = timezone.now()
        rc.save(update_fields=['review_status', 'review_notes', 'reviewer_id', 'reviewed_at', 'updated_at'])
        
        return Response({
            'success': True,
            'message': f'Flagged {len(issues)} issue(s)',
            'review_contract': ReviewContractDetailSerializer(rc).data
        }, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        rc = self.get_object()
        try:
            r2 = R2StorageService()
            r2.delete_file(rc.r2_key)
        except Exception:
            pass
        return super().destroy(request, *args, **kwargs)
