from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDay
from django.db.models import Q
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit_logs.models import AuditLogModel
from ai.models import DraftGenerationTask
from calendar_events.models import CalendarEvent
from contracts.models import Contract, ContractTemplate, ESignatureContract, FirmaSignatureContract, TemplateFile
from reviews.models import ReviewContract


class DashboardInsightsView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _safe_count(queryset) -> int:
        try:
            return int(queryset.count())
        except (OperationalError, ProgrammingError):
            return 0

    @staticmethod
    def _safe_rows(queryset) -> list[dict]:
        try:
            return list(queryset)
        except (OperationalError, ProgrammingError):
            return []

    def get(self, request):
        user = request.user
        tenant_id = getattr(user, 'tenant_id', None)
        user_id = getattr(user, 'user_id', None)

        if not tenant_id or not user_id:
            return Response({'success': False, 'error': 'Invalid user context'}, status=400)

        now = timezone.now()
        since_30d = now - timedelta(days=30)
        since_14d = now - timedelta(days=14)
        since_180d = now - timedelta(days=180)

        # Derive usage counts from first-class data so the dashboard remains
        # informative even when AuditLog coverage is partial.
        review_count_30d = self._safe_count(
            ReviewContract.objects.filter(
                tenant_id=tenant_id,
                created_by=user_id,
                created_at__gte=since_30d,
            )
        )

        def _count_r2_objects_since(*, prefix: str, since_dt, max_keys: int = 2000) -> int:
            try:
                from authentication.r2_service import R2StorageService

                r2 = R2StorageService()
                objects = r2.list_objects(prefix=prefix, max_keys=max_keys)
                n = 0
                for obj in objects:
                    lm = obj.get('last_modified')
                    if not lm:
                        continue
                    try:
                        dt = datetime.fromisoformat(str(lm))
                    except Exception:
                        continue
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt, timezone=timezone.utc)
                    if dt >= since_dt:
                        n += 1
                return int(n)
            except Exception:
                return 0

        repository_upload_count_30d = 0
        private_upload_count_30d = 0
        contracts_r2_upload_count_30d = 0

        # Repository (DB-backed) uploads
        try:
            from repository.models import Document

            repository_upload_count_30d = self._safe_count(
                Document.objects.filter(
                    tenant_id=tenant_id,
                    uploaded_by_id=user_id,
                    uploaded_at__gte=since_30d,
                )
            )
        except Exception:
            repository_upload_count_30d = 0

        # Private uploads (R2-only, per-user prefix).
        private_prefix = f"{str(tenant_id)}/private_uploads/{str(user_id)}/"
        private_upload_count_30d = _count_r2_objects_since(prefix=private_prefix, since_dt=since_30d)

        # Generic contract uploads (R2-only, tenant-wide prefix) used by legacy upload endpoints.
        contracts_prefix = f"{str(tenant_id)}/contracts/"
        contracts_r2_upload_count_30d = _count_r2_objects_since(prefix=contracts_prefix, since_dt=since_30d)

        upload_count_30d = int(repository_upload_count_30d) + int(private_upload_count_30d) + int(contracts_r2_upload_count_30d)

        # Templates: include both DB-backed template files and contract templates.
        template_files_count = self._safe_count(
            TemplateFile.objects.filter(
                Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True),
                status='active',
            )
        )

        contract_templates_count = self._safe_count(
            ContractTemplate.objects.filter(
                tenant_id=tenant_id,
                status='published',
            )
        )

        templates_count = int(template_files_count) + int(contract_templates_count)

        # -------------------- Feature usage (Audit logs) --------------------
        feature_rows = self._safe_rows(
            AuditLogModel.objects.filter(tenant_id=tenant_id, user_id=user_id, created_at__gte=since_30d)
            .values('entity_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        usage_map: dict[str, int] = {}
        for r in feature_rows:
            key = (r.get('entity_type') or 'unknown')
            usage_map[key] = int(r.get('count') or 0)

        # Ensure canonical keys used by the frontend exist.
        usage_map['review'] = max(int(usage_map.get('review', 0) or 0), int(review_count_30d))
        usage_map['upload'] = max(int(usage_map.get('upload', 0) or 0), int(upload_count_30d))

        feature_usage = [
            {'key': 'review', 'count': int(usage_map.get('review', 0) or 0)},
            {'key': 'upload', 'count': int(usage_map.get('upload', 0) or 0)},
        ]

        # Add remaining audit-derived keys (bounded).
        for key, count in sorted(
            ((k, v) for k, v in usage_map.items() if k not in {'review', 'upload'}),
            key=lambda kv: kv[1],
            reverse=True,
        ):
            feature_usage.append({'key': key, 'count': int(count)})
            if len(feature_usage) >= 12:
                break

        # -------------------- Activity trend (last 14 days) --------------------
        day_rows = self._safe_rows(
            AuditLogModel.objects.filter(tenant_id=tenant_id, user_id=user_id, created_at__gte=since_14d)
            .annotate(day=TruncDay('created_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )
        day_map = {r['day'].date().isoformat(): int(r['count']) for r in day_rows if r.get('day')}
        activity_last_14_days = []
        for i in range(13, -1, -1):
            d = (now - timedelta(days=i)).date().isoformat()
            activity_last_14_days.append({'date': d, 'count': int(day_map.get(d, 0))})

        # -------------------- Contracts by type --------------------
        contract_rows = self._safe_rows(
            Contract.objects.filter(tenant_id=tenant_id, created_by=user_id, created_at__gte=since_180d)
            .values('contract_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        contract_types = []
        for r in contract_rows:
            raw = (r.get('contract_type') or '').strip()
            contract_types.append({'type': raw or 'Unspecified', 'count': int(r.get('count') or 0)})

        # -------------------- AI usage (Contract generation vs Review) --------------------
        # IMPORTANT:
        # The dashboard KPI "AI Generations Used" should represent contract generation usage
        # (Template/AI Builder) backed by DraftGenerationTask. Review uses AI too, but it's
        # reported separately so it doesn't inflate contract-generation metrics.

        contract_ai_rows = self._safe_rows(
            DraftGenerationTask.objects.filter(tenant_id=tenant_id, user_id=user_id, created_at__gte=since_180d)
            .values('status')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        contract_ai_status_map: dict[str, int] = {}
        for r in contract_ai_rows:
            s = (r.get('status') or 'unknown')
            contract_ai_status_map[s] = contract_ai_status_map.get(s, 0) + int(r.get('count') or 0)

        ai_by_status = [
            {'status': s, 'count': int(c)}
            for s, c in sorted(contract_ai_status_map.items(), key=lambda kv: kv[1], reverse=True)
        ]

        review_ai_rows = self._safe_rows(
            ReviewContract.objects.filter(tenant_id=tenant_id, created_by=user_id, created_at__gte=since_180d)
            .values('status')
            .annotate(count=Count('id'))
        )
        review_status_map = {
            'uploaded': 'pending',
            'processing': 'processing',
            'ready': 'completed',
            'failed': 'failed',
        }
        review_ai_status_map: dict[str, int] = {}
        for r in review_ai_rows:
            raw = (r.get('status') or 'unknown')
            mapped = review_status_map.get(raw, 'unknown')
            review_ai_status_map[mapped] = review_ai_status_map.get(mapped, 0) + int(r.get('count') or 0)

        review_ai_by_status = [
            {'status': s, 'count': int(c)}
            for s, c in sorted(review_ai_status_map.items(), key=lambda kv: kv[1], reverse=True)
        ]

        # -------------------- Review stats --------------------
        review_rows = self._safe_rows(
            ReviewContract.objects.filter(tenant_id=tenant_id, created_by=user_id, created_at__gte=since_180d)
            .values('status')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        reviews_by_status = [
            {'status': (r.get('status') or 'unknown'), 'count': int(r.get('count') or 0)} for r in review_rows
        ]

        # -------------------- Calendar stats --------------------
        upcoming_end_30d = now + timedelta(days=30)
        upcoming_end_365d = now + timedelta(days=365)

        # Overlap filter: event intersects [now, end)
        upcoming_30d = self._safe_count(
            CalendarEvent.objects.filter(
                tenant_id=tenant_id,
                created_by=user_id,
                start_datetime__lt=upcoming_end_30d,
                end_datetime__gte=now,
            )
        )

        upcoming_365d = self._safe_count(
            CalendarEvent.objects.filter(
                tenant_id=tenant_id,
                created_by=user_id,
                start_datetime__lt=upcoming_end_365d,
                end_datetime__gte=now,
            )
        )

        calendar_rows = self._safe_rows(
            CalendarEvent.objects.filter(tenant_id=tenant_id, created_by=user_id, start_datetime__gte=since_180d)
            .values('category')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        calendar_by_category = [
            {'category': (r.get('category') or 'unknown'), 'count': int(r.get('count') or 0)}
            for r in calendar_rows
        ]

        # -------------------- E-sign usage (Firma vs SignNow) --------------------
        # Note: these models do not store tenant_id directly; we filter via the linked contract.
        firma_qs = FirmaSignatureContract.objects.filter(
            contract__tenant_id=tenant_id,
            contract__created_by=user_id,
            created_at__gte=since_180d,
        )
        signnow_qs = ESignatureContract.objects.filter(
            contract__tenant_id=tenant_id,
            contract__created_by=user_id,
            created_at__gte=since_180d,
        )

        firma_total = self._safe_count(firma_qs)
        signnow_total = self._safe_count(signnow_qs)

        firma_status_rows = self._safe_rows(firma_qs.values('status').annotate(count=Count('id')).order_by('-count'))
        signnow_status_rows = self._safe_rows(signnow_qs.values('status').annotate(count=Count('id')).order_by('-count'))

        firma_by_status = [
            {'status': (r.get('status') or 'unknown'), 'count': int(r.get('count') or 0)}
            for r in firma_status_rows
        ]
        signnow_by_status = [
            {'status': (r.get('status') or 'unknown'), 'count': int(r.get('count') or 0)}
            for r in signnow_status_rows
        ]

        return Response(
            {
                'success': True,
                'window_days': 180,
                'review_count_30d': int(review_count_30d),
                'upload_count_30d': int(upload_count_30d),
                'repository_upload_count_30d': int(repository_upload_count_30d),
                'private_upload_count_30d': int(private_upload_count_30d),
                'contracts_r2_upload_count_30d': int(contracts_r2_upload_count_30d),
                'templates_count': int(templates_count),
                'template_files_count': int(template_files_count),
                'contract_templates_count': int(contract_templates_count),
                'feature_usage_30d': feature_usage,
                'activity_last_14_days': activity_last_14_days,
                'contract_types_180d': contract_types,
                'ai_tasks_by_status_180d': ai_by_status,
                'review_ai_tasks_by_status_180d': review_ai_by_status,
                'reviews_by_status_180d': reviews_by_status,
                'calendar_by_category_180d': calendar_by_category,
                'calendar_upcoming_30d': int(upcoming_30d),
                'calendar_upcoming_365d': int(upcoming_365d),
                'esign_by_provider_180d': [
                    {'provider': 'firma', 'count': int(firma_total)},
                    {'provider': 'signnow', 'count': int(signnow_total)},
                ],
                'firma_by_status_180d': firma_by_status,
                'signnow_by_status_180d': signnow_by_status,
            }
        )
