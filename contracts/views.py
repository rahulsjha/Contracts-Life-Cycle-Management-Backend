"""
CLM Backend - Consolidated Views
All contract management endpoints in one file
"""

# ========== WEEK 1 & 2: BASIC CONTRACT CRUD ==========

"""
Consolidated Contract API Views
All contract-related views organized by functionality
"""
# ============================================================================
# IMPORTS
# ============================================================================
from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.decorators import action, api_view, permission_classes, parser_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.shortcuts import get_object_or_404
from django.db import transaction, connection
from django.db.models import BigIntegerField, Q
from django.db.models.expressions import RawSQL
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, Length
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.utils import timezone
from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from datetime import datetime, timedelta
import uuid
import hashlib
import json
import logging
import os
import re
import time

from .models import (
    Contract, WorkflowLog, ContractVersion, ContractTemplate, Clause,
    GenerationJob, BusinessRule, ContractClause, ESignatureContract,
    Signer, SigningAuditLog,
    ContractEditingSession, ContractEditingTemplate, ContractPreview,
    ContractEditingStep, ContractEdits, ContractFieldValidationRule
)
from .serializers import (
    ContractSerializer, ContractListSerializer, ContractDetailSerializer, ContractDecisionSerializer,
    WorkflowLogSerializer, ContractTemplateSerializer, ContractTemplateListSerializer, ClauseSerializer,
    ContractVersionSerializer, GenerationJobSerializer,
    ContractGenerateSerializer, ContractApproveSerializer,
    ContractEditingSessionSerializer, ContractEditingSessionDetailSerializer,
    ContractEditingTemplateSerializer, ContractPreviewSerializer,
    ContractEditingStepSerializer, ContractEditsSerializer, 
    ContractFieldValidationRuleSerializer,
    ESignatureContractSerializer, SignerSerializer, SigningAuditLogSerializer,
    FormFieldSubmissionSerializer, ClauseSelectionSerializer,
    ConstraintDefinitionSerializer, ContractPreviewRequestSerializer,
    ContractEditAfterPreviewSerializer, FinalizedContractSerializer
)
from .services import (
    ContractGenerator, RuleEngine,
    SignNowAPIService, SignNowAuthService
)
from .clause_seed import ensure_tenant_clause_library_seeded
from .constraint_library import CONSTRAINT_LIBRARY
from authentication.r2_service import R2StorageService
from notifications.email_service import EmailService
from notifications.models import ContractSummaryEmailLog
from notifications.tasks import send_contract_summary_followup

logger = logging.getLogger(__name__)


_signnow_api_service = None


def get_signnow_api_service() -> SignNowAPIService:
    global _signnow_api_service
    if _signnow_api_service is None:
        _signnow_api_service = SignNowAPIService()
    return _signnow_api_service


# ============================================================================
# SECTION 1: WEEK 1 & WEEK 2 BASIC CONTRACT VIEWS
# ============================================================================

class ContractListCreateView(APIView):
    """
    POST /api/v1/contracts/ - Create a new contract with file upload
    GET /api/v1/contracts/ - List all contracts for the tenant
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def post(self, request):
        if not request.user.tenant_id:
            return Response(
                {'error': 'Tenant ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        file = request.FILES.get('file')
        title = request.data.get('title')
        contract_status = request.data.get('status', 'draft')
        counterparty = request.data.get('counterparty', '')
        contract_type = request.data.get('contract_type', '')
        
        if not file:
            return Response(
                {'error': 'File is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not title:
            return Response(
                {'error': 'Title is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            r2_service = R2StorageService()
            r2_key = r2_service.upload_file(file, request.user.tenant_id, file.name)
            
            contract = Contract.objects.create(
                tenant_id=request.user.tenant_id,
                title=title,
                r2_key=r2_key,
                document_r2_key=r2_key,
                status=contract_status,
                created_by=request.user.user_id,
                counterparty=counterparty,
                contract_type=contract_type
            )
            
            WorkflowLog.objects.create(
                contract=contract,
                action='created',
                performed_by=request.user.user_id,
                comment='Contract created'
            )
            
            serializer = ContractSerializer(contract)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to create contract: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def get(self, request):
        if not request.user.tenant_id:
            return Response(
                {'error': 'Tenant ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        contracts = Contract.objects.filter(tenant_id=request.user.tenant_id)
        serializer = ContractSerializer(contracts, many=True)
        return Response(serializer.data)


class ContractDetailView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request, contract_id):
        if not request.user.tenant_id:
            return Response(
                {'error': 'Tenant ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        contract = get_object_or_404(
            Contract,
            id=contract_id,
            tenant_id=request.user.tenant_id
        )
        
        try:
            r2_service = R2StorageService()
            download_url = r2_service.generate_presigned_url(contract.r2_key)
            
            serializer = ContractDetailSerializer(contract)
            data = serializer.data
            data['download_url'] = download_url
            
            return Response(data)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to generate download URL: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ContractSubmitView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request, contract_id):
        if not request.user.tenant_id:
            return Response(
                {'error': 'Tenant ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        contract = get_object_or_404(
            Contract,
            id=contract_id,
            tenant_id=request.user.tenant_id
        )
        
        if contract.status != 'draft':
            return Response(
                {'error': f'Cannot submit contract with status "{contract.status}"'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            with transaction.atomic():
                contract.status = 'pending'
                contract.save()
                
                WorkflowLog.objects.create(
                    contract=contract,
                    action='submitted',
                    performed_by=request.user.user_id,
                    comment='Submitted for approval'
                )
            
            serializer = ContractSerializer(contract)
            return Response(serializer.data)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to submit contract: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ContractDecideView(APIView):
    permission_classes = [IsAuthenticated]
    
    def post(self, request, contract_id):
        if not request.user.tenant_id:
            return Response(
                {'error': 'Tenant ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        contract = get_object_or_404(
            Contract,
            id=contract_id,
            tenant_id=request.user.tenant_id
        )
        
        serializer = ContractDecisionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        decision = serializer.validated_data['decision']
        comment = serializer.validated_data.get('comment', '')
        
        if contract.status != 'pending':
            return Response(
                {'error': f'Cannot decide on contract with status "{contract.status}"'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            with transaction.atomic():
                if decision == 'approve':
                    contract.status = 'approved'
                    action = 'approved'
                else:
                    contract.status = 'rejected'
                    action = 'rejected'
                
                contract.save()
                
                WorkflowLog.objects.create(
                    contract=contract,
                    action=action,
                    performed_by=request.user.user_id,
                    comment=comment or f'Contract {action}'
                )
            
            serializer = ContractSerializer(contract)
            return Response(serializer.data)
            
        except Exception as e:
            return Response(
                {'error': f'Failed to process decision: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ContractDeleteView(APIView):
    permission_classes = [IsAuthenticated]
    
    def delete(self, request, contract_id):
        if not request.user.tenant_id:
            return Response(
                {'error': 'Tenant ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        contract = get_object_or_404(
            Contract,
            id=contract_id,
            tenant_id=request.user.tenant_id
        )
        
        try:
            with transaction.atomic():
                WorkflowLog.objects.create(
                    contract=contract,
                    action='deleted',
                    performed_by=request.user.user_id,
                    comment='Contract deleted'
                )
                
                r2_service = R2StorageService()
                r2_service.delete_file(contract.r2_key)
                
                contract.delete()
            
            return Response(
                {'message': 'Contract deleted successfully'},
                status=status.HTTP_204_NO_CONTENT
            )
            
        except Exception as e:
            return Response(
                {'error': f'Failed to delete contract: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ============================================================================
# SECTION 2: CONTRACT GENERATION VIEWSETS
# ============================================================================

class ContractTemplateViewSet(viewsets.ModelViewSet):
    """
    API endpoint for contract templates
    """
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if getattr(self, 'action', None) == 'list':
            return ContractTemplateListSerializer
        return ContractTemplateSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        qs = ContractTemplate.objects.filter(
            tenant_id=tenant_id,
            status='published'
        )

        if getattr(self, 'action', None) == 'list':
            return qs.defer('merge_fields', 'mandatory_clauses', 'business_rules').order_by('-updated_at')

        return qs.order_by('-updated_at')
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Set tenant_id and created_by when creating a template"""
        serializer.save(
            tenant_id=self.request.user.tenant_id,
            created_by=self.request.user.user_id
        )


class ClauseViewSet(viewsets.ModelViewSet):
    """
    API endpoint for clauses with alternative suggestions
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ClauseSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        contract_type = self.request.query_params.get('contract_type')

        # Ensure each tenant has a substantial clause library (30-50+)
        # before listing/filtering.
        ensure_tenant_clause_library_seeded(
            tenant_id=tenant_id,
            user_id=self.request.user.user_id,
            contract_type=contract_type,
            min_count=50,
        )

        queryset = Clause.objects.filter(
            tenant_id=tenant_id,
            status='published'
        )
        
        # Filter by contract type if provided
        if contract_type:
            queryset = queryset.filter(contract_type=contract_type)
        
        return queryset

    @action(detail=False, methods=['get'], url_path='constraints-library')
    def constraints_library(self, request):
        """GET /clauses/constraints-library/

        Returns a built-in set of constraint templates that the UI can present as
        pick-and-add items.
        """

        q = (request.query_params.get('q') or '').strip().lower()
        category = (request.query_params.get('category') or '').strip().lower()

        items = CONSTRAINT_LIBRARY
        if category:
            items = [x for x in items if str(x.get('category') or '').strip().lower() == category]
        if q:
            items = [
                x
                for x in items
                if q in str(x.get('label') or '').lower()
                or q in str(x.get('key') or '').lower()
                or q in str(x.get('category') or '').lower()
            ]

        return Response({'success': True, 'count': len(items), 'results': items}, status=status.HTTP_200_OK)
    
    def perform_create(self, serializer):
        """Set tenant_id and created_by when creating a clause"""
        obj = serializer.save(
            tenant_id=self.request.user.tenant_id,
            created_by=self.request.user.user_id
        )

        try:
            from search.services import SearchIndexingService

            SearchIndexingService.create_index(
                entity_type='clause',
                entity_id=str(obj.id),
                title=obj.name or obj.clause_id or 'Clause',
                content=obj.content or '',
                tenant_id=str(self.request.user.tenant_id),
                keywords=[x for x in [obj.contract_type, obj.status] if x],
            )
        except Exception:
            pass

    def perform_update(self, serializer):
        obj = serializer.save()

        try:
            from search.services import SearchIndexingService

            SearchIndexingService.create_index(
                entity_type='clause',
                entity_id=str(obj.id),
                title=obj.name or obj.clause_id or 'Clause',
                content=obj.content or '',
                tenant_id=str(self.request.user.tenant_id),
                keywords=[x for x in [obj.contract_type, obj.status] if x],
            )
        except Exception:
            pass
    
    @action(detail=True, methods=['post'], url_path='alternatives')
    def alternatives(self, request, pk=None):
        """
        POST /clauses/{id}/alternatives/
        Get alternative clause suggestions based on contract context
        
        Request:
        {
            "contract_type": "MSA",
            "contract_value": 5000000,
            "counterparty": "Acme Corp"
        }
        
        Response:
        {
            "alternatives": [
                {
                    "clause_id": "LIAB-002",
                    "name": "Strict Liability Clause",
                    "content": "...",
                    "rationale": "Higher value contracts require stricter liability",
                    "confidence": 0.92,
                    "source": "rule_based"
                }
            ]
        }
        """
        clause = self.get_object()
        tenant_id = self.request.user.tenant_id
        
        context = request.data
        contract_type = context.get('contract_type', 'Unknown')
        
        rule_engine = RuleEngine()
        suggestions = rule_engine.get_clause_suggestions(
            tenant_id, contract_type, context, clause.clause_id
        )
        
        suggestion_ids = [s['clause_id'] for s in suggestions]
        alt_clauses = Clause.objects.filter(
            tenant_id=tenant_id,
            clause_id__in=suggestion_ids,
            status='published'
        )
        
        clause_map = {c.clause_id: c for c in alt_clauses}
        
        result = []
        for suggestion in suggestions:
            alt_clause = clause_map.get(suggestion['clause_id'])
            if alt_clause:
                result.append({
                    **ClauseSerializer(alt_clause).data,
                    'rationale': suggestion['rationale'],
                    'confidence': suggestion['confidence'],
                    'source': suggestion['source']
                })
        
        return Response({'alternatives': result})
    
    @action(detail=False, methods=['post'], url_path='contract-suggestions')
    def contract_suggestions(self, request):
        """
        POST /clauses/contract-suggestions/
        Get clause suggestions for a contract
        
        Request:
        {
            "contract_id": "uuid"
        }
        """
        contract_id = request.data.get('contract_id')
        if not contract_id:
            return Response(
                {'error': 'contract_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            contract = Contract.objects.get(
                id=contract_id,
                tenant_id=request.user.tenant_id
            )
        except Exception:
            # Handle invalid UUID or missing contract
            return Response(
                {'error': 'Contract not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        rule_engine = RuleEngine()
        context = {
            'contract_type': contract.contract_type,
            'contract_value': float(contract.value or 0),
            'counterparty': contract.counterparty
        }
        
        # Get all published clauses for this contract type
        clauses = Clause.objects.filter(
            tenant_id=request.user.tenant_id,
            contract_type=contract.contract_type,
            status='published'
        )
        
        suggestions = []
        for clause in clauses:
            suggestions_for_clause = rule_engine.get_clause_suggestions(
                request.user.tenant_id,
                contract.contract_type,
                context,
                clause.clause_id
            )
            if suggestions_for_clause:
                suggestions.extend(suggestions_for_clause)
        
        return Response({'suggestions': suggestions})
    
    @action(detail=False, methods=['post'], url_path='bulk-suggestions')
    def bulk_suggestions(self, request):
        """
        POST /clauses/bulk-suggestions/
        Get clause suggestions for multiple contracts
        
        Request:
        {
            "contract_ids": ["uuid1", "uuid2"]
        }
        """
        contract_ids = request.data.get('contract_ids', [])
        
        if not contract_ids or not isinstance(contract_ids, list):
            return Response(
                {'suggestions': {}},
                status=status.HTTP_200_OK
            )
        
        # Filter out invalid UUIDs
        valid_ids = []
        for cid in contract_ids:
            try:
                uuid.UUID(str(cid))
                valid_ids.append(cid)
            except (ValueError, AttributeError):
                pass
        
        if not valid_ids:
            return Response(
                {'suggestions': {}},
                status=status.HTTP_200_OK
            )
        
        contracts = Contract.objects.filter(
            id__in=valid_ids,
            tenant_id=request.user.tenant_id
        )
        
        rule_engine = RuleEngine()
        suggestions = {}
        
        for contract in contracts:
            context = {
                'contract_type': contract.contract_type,
                'contract_value': float(contract.value or 0),
                'counterparty': contract.counterparty
            }
            
            # Get all published clauses for this contract type
            clauses = Clause.objects.filter(
                tenant_id=request.user.tenant_id,
                contract_type=contract.contract_type,
                status='published'
            )
            
            contract_suggestions = []
            for clause in clauses:
                suggestions_for_clause = rule_engine.get_clause_suggestions(
                    request.user.tenant_id,
                    contract.contract_type,
                    context,
                    clause.clause_id
                )
                if suggestions_for_clause:
                    contract_suggestions.extend(suggestions_for_clause)
            
            suggestions[str(contract.id)] = contract_suggestions
        
        return Response({'suggestions': suggestions})


class ContractViewSet(viewsets.ModelViewSet):
    """
    API endpoint for contracts with generation, approval, and version management
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def _is_admin_like(self) -> bool:
        user = getattr(self, 'request', None) and getattr(self.request, 'user', None)
        if not user:
            return False
        return bool(
            getattr(user, 'is_superuser', False)
            or getattr(user, 'is_staff', False)
            or getattr(user, 'is_superadmin', False)
            or getattr(user, 'is_admin', False)
        )

    def _user_id_str(self) -> str:
        user = getattr(self, 'request', None) and getattr(self.request, 'user', None)
        val = getattr(user, 'user_id', '') if user else ''
        return str(val or '').strip()
    
    def get_serializer_class(self):
        if self.action == 'list':
            return ContractListSerializer
        if self.action == 'retrieve':
            return ContractDetailSerializer
        elif self.action == 'generate':
            return ContractGenerateSerializer
        elif self.action == 'approve':
            return ContractApproveSerializer
        return ContractSerializer

    def _templates_dir(self) -> str:
        from django.conf import settings
        return os.path.join(settings.BASE_DIR, 'templates')

    def _sanitize_template_filename(self, name: str) -> str:
        base = os.path.basename((name or '').strip())
        base = base.replace('\\', '').replace('/', '')
        base = re.sub(r'[^A-Za-z0-9 _.-]+', '', base)
        base = re.sub(r'\s+', '_', base).strip('_')
        if not base.lower().endswith('.txt'):
            base = f"{base}.txt" if base else 'Template.txt'
        return base

    def _render_template_text(self, raw_text: str, values: dict) -> str:
        if not raw_text:
            return ''
        if not values:
            return raw_text

        rendered = raw_text
        for key, value in values.items():
            if key is None:
                continue
            placeholder = re.compile(r'\{\{\s*' + re.escape(str(key)) + r'\s*\}\}')
            rendered = placeholder.sub(str(value) if value is not None else '', rendered)
        return rendered

    def _infer_contract_type_from_filename(self, filename: str) -> str:
        name = (filename or '').lower()
        if 'nda' in name:
            return 'NDA'
        if 'employ' in name:
            return 'EMPLOYMENT'
        if 'agency' in name:
            return 'AGENCY_AGREEMENT'
        if 'property' in name:
            return 'PROPERTY_MANAGEMENT'
        if 'purchase' in name:
            return 'PURCHASE_AGREEMENT'
        if 'msa' in name or 'master' in name:
            return 'MSA'
        return 'SERVICE_AGREEMENT'
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        if not tenant_id:
            return Contract.objects.none()

        qs = Contract.objects.filter(tenant_id=tenant_id)

        # Scope contracts so users only see their own (plus any contracts
        # they are currently assigned to approve). Admin-like users can see
        # the whole tenant.
        if not self._is_admin_like():
            user_id = self._user_id_str()
            if not user_id:
                return Contract.objects.none()
            qs = qs.filter(Q(created_by=user_id) | Q(current_approvers__contains=[user_id]))

        # Contract list endpoints should be fast and small. Defer large/binary columns
        # so Postgres/Django don't pull them over the wire.
        if getattr(self, 'action', None) == 'list':
            return (
                qs.defer(
                    'signed_pdf',
                    'metadata',
                    'clauses',
                    'form_inputs',
                    'user_instructions',
                    'approval_chain',
                    'current_approvers',
                    'signed',
                    'description',
                    'document_r2_key',
                )
                .order_by('-updated_at')
            )

        # Even for detail actions, avoid pulling large/binary columns unless explicitly needed.
        return qs.defer('signed_pdf')

    def destroy(self, request, *args, **kwargs):
        instance: Contract = self.get_object()

        # Non-admins may only delete contracts they created.
        if not self._is_admin_like() and str(instance.created_by) != self._user_id_str():
            return Response({'detail': 'You do not have permission to delete this contract.'}, status=status.HTTP_403_FORBIDDEN)

        # Be careful with irreversible deletes of finalized documents.
        if not self._is_admin_like() and instance.status == 'executed':
            return Response({'detail': 'Executed contracts cannot be deleted.'}, status=status.HTTP_400_BAD_REQUEST)

        tenant_id = str(getattr(request.user, 'tenant_id', '') or '').strip()
        r2_keys: set[str] = set()

        try:
            if instance.document_r2_key:
                r2_keys.add(str(instance.document_r2_key))

            md = instance.metadata or {}
            if isinstance(md, dict):
                editor_r2_key = md.get('editor_r2_key')
                if isinstance(editor_r2_key, str) and editor_r2_key.strip():
                    r2_keys.add(editor_r2_key.strip())

            for key in ContractVersion.objects.filter(contract=instance).values_list('r2_key', flat=True):
                if key:
                    r2_keys.add(str(key))

            # Delete any contract-scoped artifacts (editor snapshots, signature field config, etc.)
            # stored under a deterministic prefix.
            if tenant_id:
                prefix = f"{tenant_id}/contracts/{instance.id}/"
                try:
                    r2 = R2StorageService()
                    for obj in r2.list_objects(prefix=prefix, max_keys=200):
                        k = obj.get('key')
                        if isinstance(k, str) and k.strip():
                            r2_keys.add(k.strip())
                except Exception:
                    pass
        except Exception:
            # Never block delete on cleanup bookkeeping.
            pass

        with transaction.atomic():
            instance.delete()

        # Best-effort storage cleanup (do not fail the API call).
        if r2_keys:
            try:
                r2 = R2StorageService()
                for key in r2_keys:
                    try:
                        r2.delete_file(key)
                    except Exception:
                        continue
            except Exception:
                pass

        return Response(status=status.HTTP_204_NO_CONTENT)

    def _editor_content_r2_key_for_contract_id(self, contract_id: uuid.UUID) -> str:
        tenant_id = str(self.request.user.tenant_id)
        return f"{tenant_id}/contracts/{contract_id}/editor/latest.json"

    def _get_editor_snapshot_from_r2(self, r2_key: str) -> dict | None:
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
    
    def perform_create(self, serializer):
        """Set tenant_id and created_by when creating a contract"""
        serializer.save(
            tenant_id=self.request.user.tenant_id,
            created_by=self.request.user.user_id
        )

    def _extract_contract_obligations(self, contract: Contract) -> list[dict]:
        md = contract.metadata or {}
        if not isinstance(md, dict):
            return []
        for key in ('obligations', 'extracted_obligations', 'obligations_extracted'):
            val = md.get(key)
            if isinstance(val, list):
                return [o for o in val if isinstance(o, dict)]
        return []

    def _parse_date(self, raw: object):
        if not raw:
            return None
        if hasattr(raw, 'year') and hasattr(raw, 'month') and hasattr(raw, 'day'):
            return raw
        s = str(raw).strip()
        if not s:
            return None
        try:
            return datetime.strptime(s[:10], '%Y-%m-%d').date()
        except Exception:
            return None

    def _term_brief(self, contract: Contract) -> dict:
        return {
            'status': contract.status,
            'contract_type': contract.contract_type,
            'value': str(contract.value) if contract.value is not None else '',
            'start_date': contract.start_date.isoformat() if contract.start_date else '',
            'end_date': contract.end_date.isoformat() if contract.end_date else '',
        }

    def _cooldown_blocked(self, *, tenant_id, contract_id, kind: str, recipient_email: str) -> tuple[bool, str | None]:
        last = (
            ContractSummaryEmailLog.objects.filter(
                tenant_id=tenant_id,
                contract_id=contract_id,
                kind=kind,
                recipient_email=recipient_email,
                status='sent',
            )
            .order_by('-sent_at')
            .first()
        )
        if not last:
            return (False, None)

        now = timezone.now()
        next_allowed = last.sent_at + timedelta(hours=12)
        if now < next_allowed:
            return (True, next_allowed.isoformat())
        return (False, None)

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        """Dashboard contract summary: overdue / due-this-week / upcoming / expiring."""
        today = timezone.localdate()
        due_week_end = today + timedelta(days=7)
        upcoming_end = today + timedelta(days=30)

        qs = self.get_queryset()

        overdue_contracts_by_id = {}
        expiring_contracts = []
        due_this_week = []
        upcoming = []

        for c in qs.only('id', 'title', 'counterparty', 'contract_type', 'status', 'value', 'start_date', 'end_date', 'metadata'):
            end_date = c.end_date
            days_to_end = (end_date - today).days if end_date else None

            obligations = []
            for o in self._extract_contract_obligations(c):
                due = self._parse_date(o.get('due_date') or o.get('due'))
                if due:
                    o = dict(o)
                    o['due_date'] = due.isoformat()
                    obligations.append(o)

            overdue_obs = [o for o in obligations if self._parse_date(o.get('due_date')) and self._parse_date(o.get('due_date')) < today]
            due_week_obs = [o for o in obligations if self._parse_date(o.get('due_date')) and today <= self._parse_date(o.get('due_date')) <= due_week_end]
            upcoming_obs = [o for o in obligations if self._parse_date(o.get('due_date')) and due_week_end < self._parse_date(o.get('due_date')) <= upcoming_end]

            base = {
                'id': str(c.id),
                'title': c.title,
                'counterparty': c.counterparty,
                'contract_type': c.contract_type,
                'status': c.status,
                'value': str(c.value) if c.value is not None else None,
                'start_date': c.start_date.isoformat() if c.start_date else None,
                'end_date': c.end_date.isoformat() if c.end_date else None,
            }

            if end_date and end_date < today:
                entry = overdue_contracts_by_id.setdefault(str(c.id), {**base, 'reasons': []})
                entry['reasons'].append('end_date_passed')
                entry['days_overdue'] = abs(days_to_end) if days_to_end is not None else None

            if overdue_obs:
                entry = overdue_contracts_by_id.setdefault(str(c.id), {**base, 'reasons': []})
                entry['reasons'].append('overdue_obligations')
                entry['overdue_obligations'] = overdue_obs

            if end_date and today <= end_date <= due_week_end:
                expiring_contracts.append({**base, 'days_to_expiry': days_to_end, 'term_brief': self._term_brief(c)})

            if due_week_obs:
                due_this_week.append({**base, 'obligations': due_week_obs})

            if upcoming_obs:
                upcoming.append({**base, 'obligations': upcoming_obs})

        return Response(
            {
                'today': today.isoformat(),
                'overdue': list(overdue_contracts_by_id.values()),
                'due_this_week': due_this_week,
                'upcoming': upcoming,
                'expiring': expiring_contracts,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], url_path='summary/send-email')
    def send_summary_email(self, request, pk=None):
        contract: Contract = self.get_object()
        from .serializers import ContractSummaryEmailRequestSerializer

        ser = ContractSummaryEmailRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        kind = ser.validated_data['kind']
        recipient_email = ser.validated_data['recipient_email']
        recipient_name = (ser.validated_data.get('recipient_name') or '').strip() or 'there'

        tenant_id = request.user.tenant_id
        blocked, next_allowed_at = self._cooldown_blocked(
            tenant_id=tenant_id,
            contract_id=contract.id,
            kind=kind,
            recipient_email=recipient_email,
        )
        if blocked:
            return Response(
                {
                    'success': False,
                    'error': 'Cooldown active. You can send again after 12 hours.',
                    'next_allowed_at': next_allowed_at,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        contract_url = None
        try:
            from django.conf import settings
            base = (getattr(settings, 'FRONTEND_BASE_URL', '') or os.getenv('FRONTEND_BASE_URL') or '').strip().rstrip('/')
            if base:
                contract_url = f"{base}/contracts/{contract.id}"
        except Exception:
            contract_url = None

        email = EmailService()
        ok = False
        subject = ''
        html = ''

        if kind == 'overdue':
            obligations = self._extract_contract_obligations(contract)
            ok = email.send_contract_overdue_email(
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                contract_title=contract.title,
                counterparty=contract.counterparty,
                obligations=obligations,
                contract_url=contract_url,
            )
            subject = f"⏰ Overdue items: {contract.title}"
            html = email._get_contract_overdue_template(
                recipient_name=recipient_name,
                contract_title=contract.title,
                counterparty=contract.counterparty,
                obligations=obligations,
                contract_url=contract_url,
            )
        else:
            term_brief = self._term_brief(contract)
            ok = email.send_contract_renewal_email(
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                contract_title=contract.title,
                counterparty=contract.counterparty,
                term_brief=term_brief,
                contract_url=contract_url,
            )
            subject = f"🔁 Renewal brief: {contract.title}"
            html = email._get_contract_renewal_template(
                recipient_name=recipient_name,
                contract_title=contract.title,
                counterparty=contract.counterparty,
                term_brief=term_brief,
                contract_url=contract_url,
            )

        log = ContractSummaryEmailLog.objects.create(
            tenant_id=tenant_id,
            contract_id=contract.id,
            kind=kind,
            recipient_email=recipient_email,
            subject=subject,
            body_html=html,
            status='sent' if ok else 'failed',
            followup_scheduled_at=(timezone.now() + timedelta(hours=12)) if ok else None,
        )

        if ok:
            try:
                send_contract_summary_followup.apply_async(args=[str(log.id)], countdown=12 * 60 * 60)
            except Exception:
                pass

        return Response(
            {
                'success': bool(ok),
                'kind': kind,
                'log_id': str(log.id),
                'followup_scheduled_at': log.followup_scheduled_at.isoformat() if log.followup_scheduled_at else None,
            },
            status=status.HTTP_200_OK if ok else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    def _strip_html(self, html: str) -> str:
        """Best-effort HTML -> plain text conversion for exports."""
        if not html:
            return ''
        # Keep line breaks for common tags.
        text = re.sub(r'(?i)<\s*br\s*/?>', '\n', html)
        text = re.sub(r'(?i)</\s*(p|div|h\d|li)\s*>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        # Unescape a few common entities without pulling in extra deps.
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        return re.sub(r'\n{3,}', '\n\n', text).strip()

    def _contract_export_text(self, contract: Contract) -> str:
        md = contract.metadata or {}

        # Prefer the latest editor snapshot in R2 (full content).
        try:
            r2_key = md.get('editor_r2_key')
            if isinstance(r2_key, str) and r2_key.strip():
                snap = self._get_editor_snapshot_from_r2(r2_key.strip())
                if isinstance(snap, dict):
                    txt = snap.get('rendered_text')
                    if isinstance(txt, str) and txt.strip():
                        return txt
                    html = snap.get('rendered_html')
                    if isinstance(html, str) and html.strip():
                        return self._strip_html(html)
        except Exception:
            pass

        # Fallback to DB metadata (may be truncated).
        txt = md.get('rendered_text')
        if isinstance(txt, str) and txt.strip():
            return txt
        html = md.get('rendered_html')
        if isinstance(html, str) and html.strip():
            return self._strip_html(html)
        return ''

    def _editor_snapshot_r2_key(self, contract: Contract) -> str:
        """Deterministic R2 key for the latest editor snapshot."""
        tenant_id = str(self.request.user.tenant_id)
        return f"{tenant_id}/contracts/{contract.id}/editor/latest.json"

    def _try_rehydrate_editor_content_from_r2(self, contract: Contract) -> bool:
        """If rendered content is missing in DB, try to rehydrate from R2 snapshot."""
        try:
            md = contract.metadata or {}
            has_text = isinstance(md.get('rendered_text'), str) and md.get('rendered_text').strip()
            has_html = isinstance(md.get('rendered_html'), str) and md.get('rendered_html').strip()
            if has_text or has_html:
                return False

            r2_key = md.get('editor_r2_key')
            if not isinstance(r2_key, str) or not r2_key.strip():
                return False

            r2 = R2StorageService()
            raw = r2.get_file_bytes(r2_key)
            if not raw:
                return False

            obj = json.loads(raw.decode('utf-8', errors='replace'))
            if not isinstance(obj, dict):
                return False

            rendered_text = obj.get('rendered_text')
            rendered_html = obj.get('rendered_html')
            changed = False
            if isinstance(rendered_text, str) and rendered_text.strip():
                md['rendered_text'] = rendered_text
                changed = True
            if isinstance(rendered_html, str) and rendered_html.strip():
                md['rendered_html'] = rendered_html
                changed = True
            if not changed:
                return False

            contract.metadata = md
            contract.save(update_fields=['metadata', 'updated_at'])
            return True
        except Exception:
            return False

    def retrieve(self, request, *args, **kwargs):
        # Avoid transferring huge JSON blobs over the wire: strip large editor fields
        # at the DB level and let the editor fetch full content from /content/.
        qs = (
            self.get_queryset()
            .defer('metadata')
            .annotate(
                _metadata_stripped=RawSQL(
                    "(metadata - 'rendered_html' - 'rendered_text' - 'raw_text')",
                    [],
                )
            )
        )
        instance = get_object_or_404(qs, id=kwargs.get('pk'))
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=True, methods=['get', 'patch'], url_path='content')
    def update_content(self, request, pk=None):
        """Persist editor changes into Contract.metadata (rendered_text/rendered_html)."""
        if request.method == 'GET':
            base_qs = self.get_queryset().filter(id=pk)
            row = (
                base_qs.annotate(
                    r2_key=KeyTextTransform('editor_r2_key', 'metadata'),
                    client_ms=Cast(KeyTextTransform('editor_client_updated_at_ms', 'metadata'), BigIntegerField()),
                    server_ms=Cast(KeyTextTransform('editor_server_updated_at_ms', 'metadata'), BigIntegerField()),
                )
                .values('id', 'title', 'r2_key', 'client_ms', 'server_ms')
                .first()
            )
            if not row:
                return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

            r2_key = (row.get('r2_key') or '').strip()
            snapshot = self._get_editor_snapshot_from_r2(r2_key) if r2_key else None
            if snapshot:
                return Response(
                    {
                        'contract_id': str(row['id']),
                        'r2_key': r2_key,
                        'client_updated_at_ms': snapshot.get('client_updated_at_ms') or row.get('client_ms'),
                        'server_updated_at_ms': snapshot.get('server_updated_at_ms') or row.get('server_ms'),
                        'rendered_text': snapshot.get('rendered_text') or '',
                        'rendered_html': snapshot.get('rendered_html') or '',
                    },
                    status=status.HTTP_200_OK,
                )

            # Fallback: older rows may still have full content in DB metadata.
            contract = base_qs.only('id', 'metadata', 'updated_at', 'title').first()
            if not contract:
                return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
            md = contract.metadata or {}
            rendered_text = md.get('rendered_text') or ''
            rendered_html = md.get('rendered_html') or ''

            # Best-effort: write a snapshot to R2 and compact metadata (remove large keys).
            try:
                key = self._editor_content_r2_key_for_contract_id(contract.id)
                payload = {
                    'schema': 'clm.editor_snapshot.v1',
                    'contract_id': str(contract.id),
                    'tenant_id': str(request.user.tenant_id),
                    'updated_at': timezone.now().isoformat(),
                    'client_updated_at_ms': md.get('editor_client_updated_at_ms'),
                    'server_updated_at_ms': md.get('editor_server_updated_at_ms'),
                    'rendered_text': rendered_text,
                    'rendered_html': rendered_html,
                }
                r2 = R2StorageService()
                r2.put_text(
                    key,
                    json.dumps(payload, ensure_ascii=False),
                    content_type='application/json; charset=utf-8',
                    metadata={
                        'tenant_id': str(request.user.tenant_id),
                        'contract_id': str(contract.id),
                        'purpose': 'editor_snapshot',
                    },
                )
                md['editor_r2_key'] = key
                md['editor_r2_synced_at'] = timezone.now().isoformat()
                md['editor_r2_sync_ok'] = True
                md.pop('rendered_html', None)
                # Keep only a bounded excerpt of text in DB.
                if isinstance(rendered_text, str):
                    md['rendered_text'] = rendered_text[:20000]
                    md['rendered_text_truncated'] = len(rendered_text) > 20000
                contract.metadata = md
                contract.save(update_fields=['metadata', 'updated_at'])
                r2_key = key
            except Exception:
                pass

            return Response(
                {
                    'contract_id': str(contract.id),
                    'r2_key': r2_key or None,
                    'client_updated_at_ms': md.get('editor_client_updated_at_ms'),
                    'server_updated_at_ms': md.get('editor_server_updated_at_ms'),
                    'rendered_text': rendered_text if isinstance(rendered_text, str) else '',
                    'rendered_html': rendered_html if isinstance(rendered_html, str) else '',
                },
                status=status.HTTP_200_OK,
            )

        # PATCH: persist editor changes.
        base_qs = self.get_queryset().filter(id=pk)
        row = (
            base_qs.annotate(
                _metadata_stripped=RawSQL(
                    "(metadata - 'rendered_html' - 'rendered_text' - 'raw_text')",
                    [],
                ),
                existing_client_ms=Cast(KeyTextTransform('editor_client_updated_at_ms', 'metadata'), BigIntegerField()),
                existing_r2_key=KeyTextTransform('editor_r2_key', 'metadata'),
                existing_text_len=Coalesce(Length(KeyTextTransform('rendered_text', 'metadata')), 0),
                existing_html_len=Coalesce(Length(KeyTextTransform('rendered_html', 'metadata')), 0),
            )
            .values(
                'id',
                'title',
                'contract_type',
                'status',
                '_metadata_stripped',
                'existing_client_ms',
                'existing_r2_key',
                'existing_text_len',
                'existing_html_len',
            )
            .first()
        )
        if not row:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        rendered_text = request.data.get('rendered_text', None)
        rendered_html = request.data.get('rendered_html', None)
        client_updated_at_ms = request.data.get('client_updated_at_ms', None)
        allow_clear = request.data.get('allow_clear', False)

        if rendered_text is not None and not isinstance(rendered_text, str):
            return Response({'error': 'rendered_text must be a string'}, status=status.HTTP_400_BAD_REQUEST)
        if rendered_html is not None and not isinstance(rendered_html, str):
            return Response({'error': 'rendered_html must be a string'}, status=status.HTTP_400_BAD_REQUEST)
        if client_updated_at_ms is not None and not isinstance(client_updated_at_ms, (int, float, str)):
            return Response({'error': 'client_updated_at_ms must be a number'}, status=status.HTTP_400_BAD_REQUEST)
        if rendered_text is None and rendered_html is None:
            return Response({'error': 'rendered_text or rendered_html is required'}, status=status.HTTP_400_BAD_REQUEST)

        if rendered_text is None and rendered_html is not None:
            rendered_text = self._strip_html(rendered_html)

        md = row.get('_metadata_stripped') or {}
        if not isinstance(md, dict):
            md = {}

        # Safety: refuse accidental empty overwrites (common if the editor briefly initializes empty
        # and an autosave fires). Allow explicit clearing via `allow_clear=true`.
        def _is_meaningfully_empty_html(value: str) -> bool:
            raw = (value or '').strip()
            if not raw:
                return True
            normalized = re.sub(r'\s+', '', raw).replace('&nbsp;', '').lower()
            if normalized in {
                '<p></p>',
                '<p><br></p>',
                '<p><br/></p>',
                '<p><br/></p><p><br/></p>',
                '<p></p><p></p>',
            }:
                return True
            # Strip tags and see if anything remains.
            text_only = re.sub(r'<[^>]*>', '', normalized)
            return len(text_only) == 0

        # Determine whether server already has content without pulling the full blobs.
        existing_text_len = int(row.get('existing_text_len') or 0)
        existing_html_len = int(row.get('existing_html_len') or 0)
        existing_r2_key = str(row.get('existing_r2_key') or '').strip()
        incoming_text = str(rendered_text or '').strip()
        incoming_html = str(rendered_html or '')
        incoming_is_empty = (not incoming_text) and _is_meaningfully_empty_html(incoming_html)
        existing_is_nonempty = bool(existing_r2_key) or existing_text_len > 0 or existing_html_len > 0

        if incoming_is_empty and existing_is_nonempty and not allow_clear:
            # Return current state without modifying.
            # Return a lightweight contract payload.
            contract_obj = (
                base_qs.defer('metadata')
                .annotate(
                    _metadata_stripped=RawSQL(
                        "(metadata - 'rendered_html' - 'rendered_text' - 'raw_text')",
                        [],
                    )
                )
                .first()
            )
            if not contract_obj:
                return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
            return Response(ContractDetailSerializer(contract_obj).data, status=status.HTTP_200_OK)

        # Guard against out-of-order autosave requests overwriting newer content.
        # Frontend sends a monotonic `client_updated_at_ms`; ignore writes older than what we have.
        incoming_client_ms: int | None = None
        if client_updated_at_ms is not None:
            try:
                incoming_client_ms = int(float(client_updated_at_ms))
            except Exception:
                return Response({'error': 'client_updated_at_ms must be a number'}, status=status.HTTP_400_BAD_REQUEST)
            if incoming_client_ms < 0:
                incoming_client_ms = None

        existing_client_ms = row.get('existing_client_ms')
        if isinstance(existing_client_ms, (int, float)):
            existing_client_ms = int(existing_client_ms)
        else:
            existing_client_ms = None

        if incoming_client_ms is not None and existing_client_ms is not None and incoming_client_ms < existing_client_ms:
            # Stale write; return current state without modifying.
            return Response(ContractDetailSerializer(contract).data, status=status.HTTP_200_OK)
        # Track content hash to make it easy to detect and debug overwrites.
        try:
            h = hashlib.sha256()
            h.update((rendered_text or '').encode('utf-8', errors='replace'))
            h.update(b"\n---\n")
            h.update((rendered_html or '').encode('utf-8', errors='replace'))
            md['editor_content_sha256'] = h.hexdigest()
        except Exception:
            pass

        server_ms = int(time.time() * 1000)
        if incoming_client_ms is not None:
            md['editor_client_updated_at_ms'] = incoming_client_ms
        md['editor_server_updated_at_ms'] = server_ms
        md['editor_updated_at_ms'] = max(server_ms, incoming_client_ms or 0)

        # Keep only bounded DB content; full content lives in R2.
        md.pop('rendered_html', None)
        md.pop('raw_text', None)
        md['editor_has_content'] = bool(incoming_text) or (not _is_meaningfully_empty_html(incoming_html))
        md['editor_text_len'] = len(rendered_text or '')
        md['editor_html_len'] = len(rendered_html or '')
        md['editor_preview_text'] = (incoming_text[:2000] if incoming_text else '')
        if rendered_text is not None:
            md['rendered_text'] = (rendered_text or '')[:20000]
            md['rendered_text_truncated'] = len(rendered_text or '') > 20000

        now = timezone.now()
        base_qs.update(
            metadata=md,
            last_edited_at=now,
            last_edited_by=request.user.user_id,
            updated_at=now,
        )

        # Best-effort: keep search index in sync for hybrid/semantic search.
        try:
            from search.services import SearchIndexingService

            content_for_index = str(rendered_text or '').strip()
            if content_for_index:
                SearchIndexingService.create_index(
                    entity_type='contract',
                    entity_id=str(row['id']),
                    title=(row.get('title') or 'Contract'),
                    content=content_for_index,
                    tenant_id=str(request.user.tenant_id),
                    keywords=[x for x in [row.get('contract_type'), row.get('status')] if x],
                )
        except Exception:
            # Do not fail the editor save if search indexing fails.
            pass

        # Sync the latest editor snapshot to Cloudflare R2 for durable retrieval.
        # This intentionally overwrites a deterministic key (latest.json) to avoid unbounded growth.
        # Version history is handled via explicit contract versioning endpoints.
        try:
            r2 = R2StorageService()
            key = self._editor_content_r2_key_for_contract_id(row['id'])
            payload = {
                'schema': 'clm.editor_snapshot.v1',
                'contract_id': str(row['id']),
                'tenant_id': str(request.user.tenant_id),
                'updated_at': timezone.now().isoformat(),
                'client_updated_at_ms': incoming_client_ms,
                'server_updated_at_ms': server_ms,
                'rendered_text': rendered_text,
                'rendered_html': rendered_html,
            }
            r2.put_text(
                key,
                json.dumps(payload, ensure_ascii=False),
                content_type='application/json; charset=utf-8',
                metadata={
                    'tenant_id': str(request.user.tenant_id),
                    'contract_id': str(row['id']),
                    'purpose': 'editor_snapshot',
                },
            )
            md['editor_r2_key'] = key
            md['editor_r2_synced_at'] = timezone.now().isoformat()
            md['editor_r2_sync_ok'] = True
            md.pop('editor_r2_sync_error', None)
            base_qs.update(metadata=md, updated_at=timezone.now())
        except Exception as e:
            # DB save succeeded; keep a lightweight marker to surface R2 sync issues.
            md['editor_r2_sync_ok'] = False
            md['editor_r2_sync_error'] = str(e)[:400]
            base_qs.update(metadata=md, updated_at=timezone.now())

        # Return a lightweight contract payload.
        contract_obj = (
            base_qs.defer('metadata')
            .annotate(
                _metadata_stripped=RawSQL(
                    "(metadata - 'rendered_html' - 'rendered_text' - 'raw_text')",
                    [],
                )
            )
            .first()
        )
        if not contract_obj:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        return Response(ContractDetailSerializer(contract_obj).data, status=status.HTTP_200_OK)

        return Response(ContractDetailSerializer(contract).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='ai/generate-stream')
    def ai_generate_stream(self, request, pk=None):
        """Stream AI-generated contract edits as Server-Sent Events (SSE).

        POST /api/v1/contracts/{id}/ai/generate-stream/

        Body:
          - prompt: string (required)
          - current_text: string (optional; defaults to stored rendered_text)

        Emits SSE events:
          - event: delta, data: {"delta": "..."}
          - event: done,  data: {"ok": true}
          - event: error, data: {"error": "..."}
        """

        contract = self.get_object()
        prompt = (request.data.get('prompt') or '').strip()
        if not prompt:
            return Response({'error': 'prompt is required'}, status=status.HTTP_400_BAD_REQUEST)

        md = contract.metadata or {}
        current_text = request.data.get('current_text')
        if current_text is None:
            # Prefer R2 snapshot (full content) if present.
            snap_text = None
            try:
                r2_key = md.get('editor_r2_key')
                if isinstance(r2_key, str) and r2_key.strip():
                    snap = self._get_editor_snapshot_from_r2(r2_key.strip())
                    if isinstance(snap, dict):
                        snap_text = snap.get('rendered_text') or self._strip_html(snap.get('rendered_html') or '')
            except Exception:
                snap_text = None

            current_text = snap_text or md.get('rendered_text') or self._strip_html(md.get('rendered_html') or '')
        if not isinstance(current_text, str):
            return Response({'error': 'current_text must be a string'}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve a few relevant clauses using Voyage Law-2 embeddings.
        def _pick_relevant_clauses() -> list[dict]:
            try:
                from repository.embeddings_service import VoyageEmbeddingsService
                import numpy as np

                svc = VoyageEmbeddingsService()
                query_emb = svc.embed_query(prompt) or svc.embed_text(prompt)
                if not query_emb:
                    return []

                qs = Clause.objects.filter(
                    tenant_id=request.user.tenant_id,
                    status='published',
                )
                if contract.contract_type:
                    qs = qs.filter(contract_type=contract.contract_type)
                # Keep it bounded for latency.
                items = list(qs.order_by('-updated_at')[:40])
                if not items:
                    return []

                texts = [f"{c.name}\n\n{c.content}"[:8000] for c in items]
                embs = svc.embed_batch(texts)

                q = np.array(query_emb, dtype=np.float32)
                qn = float(np.linalg.norm(q))
                if qn <= 0:
                    return []

                scored: list[tuple[float, Clause]] = []
                for clause, emb in zip(items, embs):
                    if not emb:
                        continue
                    v = np.array(emb, dtype=np.float32)
                    vn = float(np.linalg.norm(v))
                    if vn <= 0:
                        continue
                    sim = float(np.dot(q, v) / (qn * vn))
                    scored.append((sim, clause))

                scored.sort(key=lambda x: x[0], reverse=True)
                out = []
                for sim, clause in scored[:5]:
                    out.append(
                        {
                            'clause_id': clause.clause_id,
                            'name': clause.name,
                            'similarity': round(sim, 4),
                            'content': clause.content,
                        }
                    )
                return out
            except Exception:
                return []

        relevant = _pick_relevant_clauses()

        from django.conf import settings
        api_key = (getattr(settings, 'GEMINI_API_KEY', '') or '').strip()
        if not api_key:
            return Response({'error': 'GEMINI_API_KEY is not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        model_name = getattr(settings, 'GEMINI_CONTRACT_EDIT_MODEL', None) or 'gemini-2.5-pro'

        # Build a safe, bounded prompt.
        clauses_block = ''
        if relevant:
            chunks = []
            for c in relevant:
                chunks.append(
                    f"- [{c['clause_id']}] {c['name']} (sim={c['similarity']})\n{c['content']}"
                )
            clauses_block = "\n\n".join(chunks)[:12000]

        base_text = (current_text or '')[:20000]
        user_instruction = prompt[:4000]

        generation_prompt = f"""
You are a senior legal contract drafting assistant.

Task:
- Apply the user's instruction to the existing contract text.
- Keep the contract coherent and legally styled.
- Preserve headings/numbering where possible.
- If the user asks to insert a clause, add it in the most appropriate section.
- If the user asks to update a term (e.g., payment days), change it consistently everywhere.

Output rules:
- Return ONLY the full revised contract as plain text.
- Do not include markdown fences, commentary, or JSON.

User instruction:
{user_instruction}

Existing contract text:
---
{base_text}
---

Relevant clause library (optional):
---
{clauses_block or '(none)'}
---
""".strip()

        def event_stream():
            # Initial metadata event
            try:
                yield f"event: meta\ndata: {json.dumps({'model': model_name})}\n\n"
            except Exception:
                pass

            try:
                import google.generativeai as genai

                genai.configure(api_key=api_key)
                model = genai.GenerativeModel(model_name)

                for chunk in model.generate_content(generation_prompt, stream=True):
                    delta = getattr(chunk, 'text', None)
                    if not delta:
                        continue
                    yield f"event: delta\ndata: {json.dumps({'delta': delta})}\n\n"

                yield f"event: done\ndata: {json.dumps({'ok': True})}\n\n"
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

        resp = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
        resp['Cache-Control'] = 'no-cache'
        resp['X-Accel-Buffering'] = 'no'
        return resp

    @action(detail=False, methods=['post'], url_path='create-from-content')
    def create_from_content(self, request):
        """Create a draft contract from provided editor content.

        POST /api/v1/contracts/create-from-content/
        Body:
          - title: string (required)
          - contract_type: string (optional)
          - rendered_text: string (optional)
          - rendered_html: string (optional)
        """

        title = (request.data.get('title') or '').strip()
        if not title:
            return Response({'error': 'title is required'}, status=status.HTTP_400_BAD_REQUEST)

        rendered_text = request.data.get('rendered_text', None)
        rendered_html = request.data.get('rendered_html', None)
        if rendered_text is not None and not isinstance(rendered_text, str):
            return Response({'error': 'rendered_text must be a string'}, status=status.HTTP_400_BAD_REQUEST)
        if rendered_html is not None and not isinstance(rendered_html, str):
            return Response({'error': 'rendered_html must be a string'}, status=status.HTTP_400_BAD_REQUEST)
        if rendered_text is None and rendered_html is None:
            return Response({'error': 'rendered_text or rendered_html is required'}, status=status.HTTP_400_BAD_REQUEST)

        if rendered_text is None and rendered_html is not None:
            rendered_text = self._strip_html(rendered_html)

        contract_type = (request.data.get('contract_type') or '').strip() or None

        extra_md = request.data.get('metadata', None)
        if extra_md is not None and not isinstance(extra_md, dict):
            return Response({'error': 'metadata must be an object'}, status=status.HTTP_400_BAD_REQUEST)

        md = {
            'rendered_text': rendered_text or '',
            'rendered_html': rendered_html or '',
        }
        if isinstance(extra_md, dict):
            # Shallow-merge is intentional: keep metadata JSON simple and stable.
            # Do not allow overriding rendered_text/rendered_html.
            for k, v in extra_md.items():
                if k in ('rendered_text', 'rendered_html'):
                    continue
                md[k] = v

        contract = Contract.objects.create(
            tenant_id=request.user.tenant_id,
            title=title,
            status='draft',
            created_by=request.user.user_id,
            contract_type=contract_type,
            metadata=md,
            last_edited_at=timezone.now(),
            last_edited_by=request.user.user_id,
        )

        return Response(ContractDetailSerializer(contract).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='download-txt')
    def download_txt(self, request, pk=None):
        contract = self.get_object()
        text = self._contract_export_text(contract)
        filename = f"{(contract.title or 'contract').strip().replace(' ', '_')}.txt"

        resp = HttpResponse(text, content_type='text/plain; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    @action(detail=True, methods=['get'], url_path='download-pdf')
    def download_pdf(self, request, pk=None):
        contract = self.get_object()
        text = self._contract_export_text(contract)

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
        text_obj.setFont('Times-Roman', 11)

        max_chars = 110
        for line in (text or '').splitlines():
            wrapped_lines = textwrap.wrap(
                line,
                width=max_chars,
                replace_whitespace=False,
                drop_whitespace=False,
            ) or ['']
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

        filename = f"{(contract.title or 'contract').strip().replace(' ', '_')}.pdf"
        return FileResponse(
            buffer,
            as_attachment=True,
            filename=filename,
            content_type='application/pdf',
        )

    # ---------------------------------------------------------------------
    # Filesystem-backed template support (no DB templates)
    # ---------------------------------------------------------------------
    def _templates_dir(self) -> str:
        from django.conf import settings

        return os.path.join(settings.BASE_DIR, 'templates')

    def _sanitize_template_filename(self, name: str) -> str:
        base = os.path.basename(str(name or '')).strip()
        base = base.replace('\\', '').replace('/', '')
        base = re.sub(r'[^A-Za-z0-9 _.-]+', '', base)
        base = re.sub(r'\s+', '_', base).strip('_')
        if not base.lower().endswith('.txt'):
            base = f'{base}.txt' if base else 'Template.txt'
        return base

    def _infer_contract_type_from_filename(self, filename: str) -> str:
        name = (filename or '').lower()
        if 'nda' in name:
            return 'NDA'
        if 'msa' in name or 'master' in name:
            return 'MSA'
        if 'sow' in name or 'statement_of_work' in name or 'statement-of-work' in name:
            return 'SOW'
        if 'contractor' in name:
            return 'CONTRACTOR_AGREEMENT'
        if 'employ' in name:
            return 'EMPLOYMENT'
        if 'agency' in name:
            return 'AGENCY_AGREEMENT'
        if 'property' in name:
            return 'PROPERTY_MANAGEMENT'
        if 'purchase' in name:
            return 'PURCHASE_AGREEMENT'
        return 'SERVICE_AGREEMENT'

    def _render_template_text(self, raw_text: str, values: dict) -> str:
        rendered = raw_text or ''
        for key, value in (values or {}).items():
            placeholder = re.compile(r'\{\{\s*' + re.escape(str(key)) + r'\s*\}\}')
            rendered = placeholder.sub(str(value) if value is not None else '', rendered)
        return rendered

    def _assemble_additions_block(self, tenant_id, contract_type: str, selected_clause_ids, custom_clauses, constraints) -> str:
        selected_clause_ids = selected_clause_ids or []
        custom_clauses = custom_clauses or []
        constraints = constraints or []

        blocks = []

        if selected_clause_ids:
            clause_qs = Clause.objects.filter(
                tenant_id=tenant_id,
                status='published',
                clause_id__in=selected_clause_ids,
            )
            if contract_type:
                clause_qs = clause_qs.filter(contract_type=contract_type)

            clause_map = {c.clause_id: c for c in clause_qs}
            for cid in selected_clause_ids:
                clause = clause_map.get(cid)
                if not clause:
                    continue
                name = (clause.name or cid).strip()
                content = (clause.content or '').strip()
                if content:
                    blocks.append(f'{name}\n{content}')

        constraint_lines = []
        for c in constraints:
            if not isinstance(c, dict):
                continue
            n = (c.get('name') or '').strip()
            v = (c.get('value') or '').strip()
            if n and v:
                constraint_lines.append(f'- {n}: {v}')
        if constraint_lines:
            blocks.append('Constraints\n' + '\n'.join(constraint_lines))

        for cc in custom_clauses:
            if not isinstance(cc, dict):
                continue
            title = (cc.get('title') or '').strip() or 'Custom Clause'
            content = (cc.get('content') or '').strip()
            if content:
                blocks.append(f'{title}\n{content}')

        if not blocks:
            return ''

        return 'ADDITIONAL CLAUSES & CONSTRAINTS\n\n' + ('\n\n'.join(blocks))

    def _apply_additions(self, rendered_text: str, additions_block: str) -> str:
        if not additions_block:
            return rendered_text

        out = rendered_text or ''
        for slot in ('clauses_section', 'clauses', 'constraints_section', 'constraints'):
            token_rx = re.compile(r'\{\{\s*' + re.escape(slot) + r'\s*\}\}')
            if token_rx.search(out):
                return token_rx.sub(additions_block, out)

        return out + '\n\n---\n\n' + additions_block + '\n'

    # ---------------------------------------------------------------------
    # Filesystem-backed template support (no DB templates)
    # ---------------------------------------------------------------------
    def _templates_dir(self) -> str:
        from django.conf import settings

        return os.path.join(settings.BASE_DIR, 'templates')

    def _sanitize_template_filename(self, name: str) -> str:
        base = os.path.basename(str(name or '')).strip()
        base = base.replace('\\', '').replace('/', '')
        base = re.sub(r'[^A-Za-z0-9 _.-]+', '', base)
        base = re.sub(r'\s+', '_', base).strip('_')
        if not base.lower().endswith('.txt'):
            base = f'{base}.txt' if base else 'Template.txt'
        return base

    def _infer_contract_type_from_filename(self, filename: str) -> str:
        name = (filename or '').lower()
        if 'nda' in name:
            return 'NDA'
        if 'msa' in name or 'master' in name:
            return 'MSA'
        if 'sow' in name or 'statement_of_work' in name or 'statement-of-work' in name:
            return 'SOW'
        if 'contractor' in name:
            return 'CONTRACTOR_AGREEMENT'
        if 'employ' in name:
            return 'EMPLOYMENT'
        if 'agency' in name:
            return 'AGENCY_AGREEMENT'
        if 'property' in name:
            return 'PROPERTY_MANAGEMENT'
        if 'purchase' in name:
            return 'PURCHASE_AGREEMENT'
        return 'SERVICE_AGREEMENT'

    def _render_template_text(self, raw_text: str, values: dict) -> str:
        rendered = raw_text or ''
        for key, value in (values or {}).items():
            placeholder = re.compile(r'\{\{\s*' + re.escape(str(key)) + r'\s*\}\}')
            rendered = placeholder.sub(str(value) if value is not None else '', rendered)
        return rendered

    def _assemble_additions_block(self, tenant_id, contract_type: str, selected_clause_ids, custom_clauses, constraints) -> str:
        selected_clause_ids = selected_clause_ids or []
        custom_clauses = custom_clauses or []
        constraints = constraints or []

        blocks = []

        # Clause library selections
        if selected_clause_ids:
            clause_qs = Clause.objects.filter(
                tenant_id=tenant_id,
                status='published',
                clause_id__in=selected_clause_ids,
            )
            if contract_type:
                clause_qs = clause_qs.filter(contract_type=contract_type)

            clause_map = {c.clause_id: c for c in clause_qs}
            for cid in selected_clause_ids:
                clause = clause_map.get(cid)
                if not clause:
                    continue
                name = (clause.name or cid).strip()
                content = (clause.content or '').strip()
                if content:
                    blocks.append(f'{name}\n{content}')

        # Constraints
        constraint_lines = []
        for c in constraints:
            if not isinstance(c, dict):
                continue
            n = (c.get('name') or '').strip()
            v = (c.get('value') or '').strip()
            if n and v:
                constraint_lines.append(f'- {n}: {v}')
        if constraint_lines:
            blocks.append('Constraints\n' + '\n'.join(constraint_lines))

        # Custom clauses
        for cc in custom_clauses:
            if not isinstance(cc, dict):
                continue
            title = (cc.get('title') or '').strip() or 'Custom Clause'
            content = (cc.get('content') or '').strip()
            if content:
                blocks.append(f'{title}\n{content}')

        if not blocks:
            return ''

        return 'ADDITIONAL CLAUSES & CONSTRAINTS\n\n' + ('\n\n'.join(blocks))

    def _apply_additions(self, rendered_text: str, additions_block: str) -> str:
        if not additions_block:
            return rendered_text

        out = rendered_text or ''
        # Prefer inserting into an explicit placeholder if present
        for slot in ('clauses_section', 'clauses', 'constraints_section', 'constraints'):
            token_rx = re.compile(r'\{\{\s*' + re.escape(slot) + r'\s*\}\}')
            if token_rx.search(out):
                return token_rx.sub(additions_block, out)

        # Otherwise append at the end
        sep = '\n\n' if out.endswith('\n') else '\n\n'
        return out + sep + '---\n\n' + additions_block + '\n'

    def create(self, request, *args, **kwargs):
        """Create contract.

        Supports multipart form uploads with a `file` field. If a file is provided,
        it is uploaded to Cloudflare R2 and stored as the initial ContractVersion.
        """
        uploaded_file = request.FILES.get('file')

        allowed_fields = [
            'title',
            'contract_type',
            'status',
            'value',
            'counterparty',
            'start_date',
            'end_date',
        ]
        payload = {k: request.data.get(k) for k in allowed_fields if k in request.data}

        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            contract = serializer.save(
                tenant_id=request.user.tenant_id,
                created_by=request.user.user_id,
            )

            if uploaded_file:
                file_bytes = uploaded_file.read()
                try:
                    uploaded_file.seek(0)
                except Exception:
                    pass

                file_hash = hashlib.sha256(file_bytes).hexdigest()
                file_size = getattr(uploaded_file, 'size', None)

                r2_service = R2StorageService()
                r2_key = r2_service.upload_file(uploaded_file, request.user.tenant_id, uploaded_file.name)

                template_id = contract.template_id or uuid.uuid4()
                template_version = getattr(contract.template, 'version', None) or 1

                ContractVersion.objects.create(
                    contract=contract,
                    version_number=1,
                    r2_key=r2_key,
                    template_id=template_id,
                    template_version=template_version,
                    change_summary='Initial document upload',
                    created_by=request.user.user_id,
                    file_size=file_size,
                    file_hash=file_hash,
                )

                contract.current_version = 1
                contract.save(update_fields=['current_version'])

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['get'], url_path='download-url')
    def download_url(self, request, pk=None):
        """GET /contracts/{id}/download-url/

        Returns a presigned URL for the latest uploaded/generated document.
        """
        contract = self.get_object()
        try:
            latest_version = contract.versions.latest('version_number')
        except ContractVersion.DoesNotExist:
            return Response(
                {'error': 'No document available for this contract'},
                status=status.HTTP_404_NOT_FOUND
            )

        r2_service = R2StorageService()
        url = r2_service.generate_presigned_url(latest_version.r2_key)
        return Response({
            'contract_id': str(contract.id),
            'version_number': latest_version.version_number,
            'r2_key': latest_version.r2_key,
            'download_url': url,
        })
    
    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        """GET /contracts/{id}/history/
        
        Get contract change history from audit logs
        """
        from audit_logs.models import AuditLogModel
        
        contract = self.get_object()
        history = AuditLogModel.objects.filter(
            entity_id=str(contract.id),
            entity_type='contract'
        ).order_by('-created_at')[:50]
        
        result = []
        for log in history:
            result.append({
                'id': str(log.id),
                'entity_type': log.entity_type,
                'entity_id': log.entity_id,
                'action': log.action,
                'performed_by': str(log.performed_by),
                'performer_email': getattr(log, 'performer_email', 'Unknown'),
                'changes': log.changes or {},
                'created_at': log.created_at.isoformat()
            })
        
        return Response({'history': result})
    
    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """
        GET /contracts/statistics/
        Get contract statistics for dashboard
        
        Response:
        {
            "total": 127,
            "draft": 45,
            "pending": 30,
            "approved": 40,
            "rejected": 5,
            "executed": 7,
            "monthly_trends": [
                {"month": "Jan", "approved": 12, "rejected": 2},
                ...
            ]
        }
        """
        from django.db.models import Count, Q
        from datetime import timedelta
        from django.utils import timezone
        
        tenant_id = request.user.tenant_id
        queryset = Contract.objects.filter(tenant_id=tenant_id)
        
        # Get status counts
        stats = queryset.aggregate(
            total=Count('id'),
            draft=Count('id', filter=Q(status='draft')),
            pending=Count('id', filter=Q(status='pending')),
            approved=Count('id', filter=Q(status='approved')),
            rejected=Count('id', filter=Q(status='rejected')),
            executed=Count('id', filter=Q(status='executed'))
        )
        
        # Get monthly trends for last 6 months
        monthly_trends = []
        for i in range(5, -1, -1):
            month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30 * i)
            month_end = (month_start + timedelta(days=32)).replace(day=1)
            
            month_contracts = queryset.filter(
                created_at__gte=month_start,
                created_at__lt=month_end
            )
            
            approved_count = month_contracts.filter(status='approved').count()
            rejected_count = month_contracts.filter(status='rejected').count()
            
            monthly_trends.append({
                'month': month_start.strftime('%b'),
                'approved': approved_count,
                'rejected': rejected_count
            })
        
        return Response({
            **stats,
            'monthly_trends': monthly_trends
        })
    
    @action(detail=False, methods=['get'], url_path='recent')
    def recent(self, request):
        """
        GET /contracts/recent/?limit=10
        Get recent contracts
        """
        tenant_id = request.user.tenant_id
        limit = int(request.query_params.get('limit', 10))
        
        contracts = Contract.objects.filter(
            tenant_id=tenant_id
        ).order_by('-updated_at')[:limit]
        
        # Get user names from authentication app
        from authentication.models import User
        user_ids = list(set([str(c.created_by) for c in contracts]))
        users = User.objects.filter(user_id__in=user_ids)
        user_map = {str(u.user_id): f"{u.first_name} {u.last_name}".strip() or u.email for u in users}
        
        result = []
        for contract in contracts:
            data = ContractSerializer(contract).data
            data['created_by_name'] = user_map.get(str(contract.created_by), 'Unknown')
            result.append(data)
        
        return Response(result)
    
    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        """
        POST /contracts/generate/
        Generate a new contract from template with clause assembly
        
        Request:
        {
            "template_id": "uuid",
            "structured_inputs": {
                "counterparty": "Acme Corp",
                "value": 5000000,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31"
            },
            "user_instructions": "Make termination stricter",
            "title": "MSA with Acme Corp",
            "selected_clauses": ["CONF-001", "TERM-001"]  # Optional
        }
        
        Response:
        {
            "contract": {...},
            "version": {...},
            "mandatory_clauses": [
                {
                    "clause_id": "CONF-001",
                    "message": "Confidentiality required for all contracts",
                    "rule_name": "Mandatory Confidentiality"
                }
            ],
            "clause_suggestions": {
                "CONF-001": [
                    {
                        "clause_id": "CONF-002",
                        "rationale": "Higher value contracts",
                        "confidence": 0.89
                    }
                ]
            },
            "validation_errors": []
        }
        """
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        
        template_id = request.data.get('template_id')
        structured_inputs = request.data.get('structured_inputs', {})
        user_instructions = request.data.get('user_instructions')
        title = request.data.get('title')
        selected_clauses = request.data.get('selected_clauses')
        
        if not template_id:
            return Response(
                {'error': 'template_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            generator = ContractGenerator(user_id, tenant_id)
            
            # Create contract
            contract = generator.generate_from_template(
                template_id=uuid.UUID(template_id),
                structured_inputs=structured_inputs,
                user_instructions=user_instructions,
                title=title
            )
            
            # Create first version with clause assembly
            version = generator.create_version(
                contract=contract,
                selected_clauses=selected_clauses,
                change_summary='Initial draft'
            )
            
            # Get context for suggestions and validation
            context = {
                'contract_type': contract.contract_type,
                'contract_value': float(contract.value) if contract.value else 0,
                **structured_inputs
            }
            
            rule_engine = RuleEngine()
            mandatory_clauses = rule_engine.get_mandatory_clauses(
                tenant_id, contract.contract_type, context
            )
            
            # Get all clause suggestions
            clause_suggestions = {}
            for clause_id in (selected_clauses or []):
                suggestions = rule_engine.get_clause_suggestions(
                    tenant_id, contract.contract_type, context, clause_id
                )
                if suggestions:
                    clause_suggestions[clause_id] = suggestions
            
            return Response({
                'contract': ContractDetailSerializer(contract).data,
                'version': ContractVersionSerializer(version).data,
                'mandatory_clauses': mandatory_clauses,
                'clause_suggestions': clause_suggestions,
                'validation_errors': []
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['post'], url_path='preview-from-file')
    def preview_from_file(self, request):
        tenant_id = request.user.tenant_id
        filename = request.data.get('filename')
        if not filename:
            return Response({'error': 'filename is required'}, status=status.HTTP_400_BAD_REQUEST)

        structured_inputs = request.data.get('structured_inputs') or {}
        if not isinstance(structured_inputs, dict):
            return Response({'error': 'structured_inputs must be an object'}, status=status.HTTP_400_BAD_REQUEST)

        selected_clauses = request.data.get('selected_clauses') or []
        if selected_clauses is not None and not isinstance(selected_clauses, list):
            return Response({'error': 'selected_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        custom_clauses = request.data.get('custom_clauses') or []
        if custom_clauses is not None and not isinstance(custom_clauses, list):
            return Response({'error': 'custom_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        constraints = request.data.get('constraints') or []
        if constraints is not None and not isinstance(constraints, list):
            return Response({'error': 'constraints must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        safe = self._sanitize_template_filename(filename)
        try:
            from django.db.models import Q
            from contracts.models import TemplateFile
            from contracts.utils.template_files_db import get_or_import_template_from_filesystem

            tmpl = TemplateFile.objects.filter(filename=safe).filter(
                Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True)
            ).first()
        except Exception:
            tmpl = None

        if not tmpl:
            try:
                tmpl = get_or_import_template_from_filesystem(filename=safe, tenant_id=tenant_id)
            except Exception:
                tmpl = None

        if not tmpl:
            return Response({'error': 'Template not found', 'filename': safe}, status=status.HTTP_404_NOT_FOUND)

        raw_text = tmpl.content or ''

        rendered = self._render_template_text(raw_text, structured_inputs)
        contract_type = (tmpl.contract_type or self._infer_contract_type_from_filename(safe))
        additions = self._assemble_additions_block(tenant_id, contract_type, selected_clauses, custom_clauses, constraints)
        rendered = self._apply_additions(rendered, additions)

        return Response(
            {
                'success': True,
                'filename': safe,
                'contract_type': contract_type,
                'raw_text': raw_text,
                'rendered_text': rendered,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'], url_path='generate-from-file')
    def generate_from_file(self, request):
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id

        filename = request.data.get('filename')
        if not filename:
            return Response({'error': 'filename is required'}, status=status.HTTP_400_BAD_REQUEST)

        structured_inputs = request.data.get('structured_inputs') or {}
        if not isinstance(structured_inputs, dict):
            return Response({'error': 'structured_inputs must be an object'}, status=status.HTTP_400_BAD_REQUEST)

        user_instructions = request.data.get('user_instructions')
        title = request.data.get('title')

        selected_clauses = request.data.get('selected_clauses') or []
        if selected_clauses is not None and not isinstance(selected_clauses, list):
            return Response({'error': 'selected_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        custom_clauses = request.data.get('custom_clauses') or []
        if custom_clauses is not None and not isinstance(custom_clauses, list):
            return Response({'error': 'custom_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        constraints = request.data.get('constraints') or []
        if constraints is not None and not isinstance(constraints, list):
            return Response({'error': 'constraints must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        safe = self._sanitize_template_filename(filename)
        try:
            from django.db.models import Q
            from contracts.models import TemplateFile
            from contracts.utils.template_files_db import get_or_import_template_from_filesystem

            tmpl = TemplateFile.objects.filter(filename=safe).filter(
                Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True)
            ).first()
        except Exception:
            tmpl = None

        if not tmpl:
            try:
                tmpl = get_or_import_template_from_filesystem(filename=safe, tenant_id=tenant_id)
            except Exception:
                tmpl = None

        if not tmpl:
            return Response({'error': 'Template not found', 'filename': safe}, status=status.HTTP_404_NOT_FOUND)

        raw_text = tmpl.content or ''

        rendered = self._render_template_text(raw_text, structured_inputs)
        inferred_type = (tmpl.contract_type or self._infer_contract_type_from_filename(safe))
        additions = self._assemble_additions_block(tenant_id, inferred_type, selected_clauses, custom_clauses, constraints)
        rendered = self._apply_additions(rendered, additions)

        counterparty = (
            structured_inputs.get('counterparty')
            or structured_inputs.get('counterparty_name')
            or structured_inputs.get('receiving_party_name')
            or structured_inputs.get('client_name')
            or structured_inputs.get('provider_name')
            or structured_inputs.get('contractor_name')
        )

        clauses_payload = []
        for cid in selected_clauses or []:
            if isinstance(cid, str) and cid.strip():
                clauses_payload.append({'kind': 'library', 'clause_id': cid.strip()})
        for c in constraints or []:
            if isinstance(c, dict) and (c.get('name') or '').strip() and (c.get('value') or '').strip():
                clauses_payload.append({'kind': 'constraint', 'name': (c.get('name') or '').strip(), 'value': (c.get('value') or '').strip()})
        for c in custom_clauses or []:
            if isinstance(c, dict) and (c.get('content') or '').strip():
                clauses_payload.append({'kind': 'custom', 'title': (c.get('title') or 'Custom Clause').strip(), 'content': (c.get('content') or '').strip()})

        with transaction.atomic():
            contract = Contract.objects.create(
                tenant_id=tenant_id,
                created_by=user_id,
                title=(title or os.path.splitext(safe)[0]),
                status='draft',
                contract_type=inferred_type,
                counterparty=counterparty,
                form_inputs=structured_inputs,
                user_instructions=user_instructions,
                clauses=clauses_payload,
                metadata={
                    'template_filename': safe,
                    'template_source': 'template_files_db',
                    'raw_text': raw_text,
                    'rendered_text': rendered,
                },
            )

            WorkflowLog.objects.create(
                contract=contract,
                action='created',
                performed_by=user_id,
                comment=f'Created from template file {safe}',
            )

        return Response(
            {
                'contract': ContractDetailSerializer(contract).data,
                'rendered_text': rendered,
                'raw_text': raw_text,
            },
            status=status.HTTP_201_CREATED,
        )
    
    @action(detail=True, methods=['get'], url_path='versions')
    def versions(self, request, pk=None):
        """
        GET /contracts/{id}/versions/
        Get all versions of a contract with clause provenance
        """
        contract = self.get_object()
        versions = contract.versions.all()
        serializer = ContractVersionSerializer(versions, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='create-version')
    def create_version(self, request, pk=None):
        """
        POST /contracts/{id}/create-version/
        Create a new version with different clauses
        
        Request:
        {
            "selected_clauses": ["CONF-001", "TERM-001", "LIAB-002"],
            "change_summary": "Updated liability clause to stricter version"
        }
        """
        contract = self.get_object()
        
        selected_clauses = request.data.get('selected_clauses', [])
        change_summary = request.data.get('change_summary', '')
        
        if not selected_clauses:
            return Response(
                {'error': 'selected_clauses is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            tenant_id = request.user.tenant_id
            user_id = request.user.user_id
            
            # Get latest version number
            latest_version = contract.versions.order_by('-version_number').first()
            version_number = (latest_version.version_number + 1) if latest_version else 1
            
            # Create version without requiring generator
            version = ContractVersion.objects.create(
                contract=contract,
                version_number=version_number,
                template_id=contract.template_id or uuid.uuid4(),
                template_version=1,
                change_summary=change_summary or f'Version {version_number}',
                created_by=user_id,
                file_size=0,
                file_hash='',
                r2_key=f'contracts/{contract.id}/v{version_number}.docx'
            )
            
            contract.current_version = version_number
            contract.save(update_fields=['current_version'])
            
            return Response(
                ContractVersionSerializer(version).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['get'], url_path='versions/(?P<version_number>[0-9]+)/clauses')
    def version_clauses(self, request, pk=None, version_number=None):
        """
        GET /contracts/{id}/versions/{version_number}/clauses/
        Get all clauses with full provenance for a specific version
        
        Response:
        {
            "clauses": [
                {
                    "clause_id": "CONF-001",
                    "clause_version": 3,
                    "name": "Confidentiality Clause",
                    "content": "...",
                    "is_mandatory": true,
                    "position": 1,
                    "alternatives_suggested": [...],
                    "provenance": {
                        "source": "template",
                        "template_id": "uuid",
                        "template_version": 5,
                        "added_at": "2026-01-01T12:00:00Z"
                    }
                }
            ]
        }
        """
        contract = self.get_object()
        version = get_object_or_404(
            ContractVersion,
            contract=contract,
            version_number=version_number
        )
        
        clauses = version.clauses.all()
        
        data = []
        for cc in clauses:
            data.append({
                'clause_id': cc.clause_id,
                'clause_version': cc.clause_version,
                'name': cc.clause_name,
                'content': cc.clause_content,
                'is_mandatory': cc.is_mandatory,
                'position': cc.position,
                'alternatives_suggested': cc.alternatives_suggested,
                'provenance': {
                    'source': 'template',
                    'template_id': str(version.template_id),
                    'template_version': version.template_version,
                    'added_at': version.created_at.isoformat(),
                    'added_by': str(version.created_by)
                }
            })
        
        return Response({'clauses': data})
    
    @action(detail=False, methods=['post'], url_path='validate-clauses')
    def validate_clauses(self, request):
        """
        POST /contracts/validate-clauses/
        Validate a set of clauses against business rules
        
        Request:
        {
            "contract_type": "MSA",
            "contract_value": 5000000,
            "selected_clauses": ["CONF-001", "TERM-001"]
        }
        
        Response:
        {
            "is_valid": false,
            "errors": ["Mandatory clause missing: LIAB-001"],
            "warnings": [],
            "mandatory_clauses": [...]
        }
        """
        tenant_id = request.user.tenant_id
        
        contract_type = request.data.get('contract_type', '')
        context = request.data
        selected_clauses = request.data.get('selected_clauses', [])
        
        rule_engine = RuleEngine()
        
        is_valid, errors = rule_engine.validate_contract(
            tenant_id, contract_type, context, selected_clauses
        )
        
        mandatory = rule_engine.get_mandatory_clauses(
            tenant_id, contract_type, context
        )
        
        return Response({
            'is_valid': is_valid,
            'errors': errors,
            'warnings': [],
            'mandatory_clauses': mandatory
        })
    
    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """
        POST /contracts/{id}/approve
        Approve a contract for download/send/sign
        """
        contract = self.get_object()
        serializer = ContractApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        if not serializer.validated_data['reviewed']:
            return Response(
                {'error': 'You must review the contract before approving'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if contract.is_approved:
            return Response(
                {'error': 'Contract is already approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Approve the contract
        contract.is_approved = True
        contract.approved_by = request.user.user_id
        contract.approved_at = timezone.now()
        contract.save(update_fields=['is_approved', 'approved_by', 'approved_at'])
        
        # Create audit log entry with correct fields
        from audit_logs.models import AuditLogModel
        AuditLogModel.objects.create(
            tenant_id=request.user.tenant_id,
            user_id=request.user.user_id,
            entity_type='contract',
            entity_id=contract.id,
            action='update',
            changes={'is_approved': True, 'comments': serializer.validated_data.get('comments', '')}
        )
        
        return Response(
            ContractDetailSerializer(contract).data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        """
        GET /contracts/{id}/download
        Download the latest version (requires approval)
        """
        contract = self.get_object()
        
        if not contract.is_approved:
            return Response(
                {'error': 'Contract must be approved before download'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        latest_version = contract.versions.first()
        if not latest_version:
            return Response(
                {'error': 'No version available'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # In production: Generate presigned R2 URL and redirect
        download_url = f"https://r2.example.com/{latest_version.r2_key}"
        
        return Response({
            'download_url': download_url,
            'filename': f"{contract.title}_v{latest_version.version_number}.docx",
            'version': latest_version.version_number
        })
    
    @action(detail=True, methods=['get'], url_path='versions/(?P<version_number>[0-9]+)')
    def version_detail(self, request, pk=None, version_number=None):
        """
        GET /contracts/{id}/versions/{version_number}
        Get specific version details
        """
        contract = self.get_object()
        version = get_object_or_404(
            ContractVersion,
            contract=contract,
            version_number=version_number
        )
        serializer = ContractVersionSerializer(version)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='new-version')
    def new_version(self, request, pk=None):
        """
        POST /contracts/{id}/new-version
        Create a new version with changes
        """
        contract = self.get_object()
        
        changes = request.data.get('changes', {})
        change_summary = request.data.get('change_summary', 'Updated contract')
        
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        
        service = ContractGenerationService(tenant_id, user_id)
        
        try:
            new_version = service.create_new_version(
                contract_id=str(contract.id),
                changes=changes,
                change_summary=change_summary
            )
            
            return Response(
                ContractVersionSerializer(new_version).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'error': 'Failed to create new version', 'details': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='clone')
    def clone(self, request, pk=None):
        """
        POST /contracts/{id}/clone/
        Clone a contract to create a new copy
        
        Request:
        {
            "title": "New Contract Title"
        }
        """
        contract = self.get_object()
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        
        new_title = request.data.get('title', f"{contract.title} (Copy)")
        
        try:
            cloned_contract = Contract.objects.create(
                tenant_id=tenant_id,
                title=new_title,
                contract_type=contract.contract_type,
                status='draft',
                value=contract.value,
                counterparty=contract.counterparty,
                start_date=contract.start_date,
                end_date=contract.end_date,
                created_by=user_id,
                template_id=contract.template_id
            )
            
            # Clone latest version if exists
            latest_version = contract.versions.order_by('-version_number').first()
            if latest_version:
                ContractVersion.objects.create(
                    contract=cloned_contract,
                    version_number=1,
                    r2_key=latest_version.r2_key,
                    template_id=latest_version.template_id,
                    template_version=latest_version.template_version,
                    change_summary=f'Cloned from {contract.title}',
                    created_by=user_id,
                    file_size=latest_version.file_size,
                    file_hash=latest_version.file_hash
                )
                cloned_contract.current_version = 1
                cloned_contract.save(update_fields=['current_version'])
            
            return Response(
                ContractSerializer(cloned_contract).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class GenerationJobViewSet(viewsets.ModelViewSet):
    """
    API endpoint for tracking async generation jobs
    """
    permission_classes = [IsAuthenticated]
    serializer_class = GenerationJobSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        # Filter by user's contracts
        user_id = self.request.user.user_id
        return GenerationJob.objects.filter(
            contract__tenant_id=tenant_id,
            contract__created_by=user_id
        )


# ============================================================================
# SECTION 3: MANUAL EDITING VIEWS
# ============================================================================

class ContractEditingTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for managing contract editing templates
    Users can browse available templates for manual editing
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContractEditingTemplateSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        return ContractEditingTemplate.objects.filter(
            tenant_id=tenant_id,
            is_active=True
        ).order_by('-created_at')
    
    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """
        GET /manual-templates/by-category/?category=nda
        Get templates filtered by category
        """
        category = request.query_params.get('category')
        tenant_id = request.user.tenant_id
        
        if not category:
            return Response(
                {'error': 'category parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        templates = ContractEditingTemplate.objects.filter(
            tenant_id=tenant_id,
            category=category,
            is_active=True
        )
        
        serializer = self.get_serializer(templates, many=True)
        return Response({
            'category': category,
            'count': len(templates),
            'templates': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def by_type(self, request):
        """
        GET /manual-templates/by-type/?contract_type=nda
        Get templates filtered by contract type
        """
        contract_type = request.query_params.get('contract_type')
        tenant_id = request.user.tenant_id
        
        if not contract_type:
            return Response(
                {'error': 'contract_type parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        templates = ContractEditingTemplate.objects.filter(
            tenant_id=tenant_id,
            contract_type=contract_type.upper(),
            is_active=True
        )
        
        serializer = self.get_serializer(templates, many=True)
        return Response({
            'contract_type': contract_type,
            'count': len(templates),
            'templates': serializer.data
        })


class ContractEditingSessionViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing contract editing sessions
    Complete workflow: select template -> fill form -> select clauses -> preview -> finalize
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContractEditingSessionSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        user_id = self.request.user.user_id
        return ContractEditingSession.objects.filter(
            tenant_id=tenant_id,
            user_id=user_id
        ).order_by('-updated_at')
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        POST /manual-sessions/
        Create a new contract editing session
        
        Request body:
        {
            "template_id": "uuid",
            "initial_form_data": {...}
        }
        """
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        template_id = request.data.get('template_id')
        initial_form_data = request.data.get('initial_form_data', {})
        
        # Validate template exists and is accessible
        try:
            template = ContractEditingTemplate.objects.get(
                id=template_id,
                tenant_id=tenant_id,
                is_active=True
            )
        except ContractEditingTemplate.DoesNotExist:
            return Response(
                {'error': 'Template not found or not accessible'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create session
        session = ContractEditingSession.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            template_id=template_id,
            status='draft',
            form_data=initial_form_data
        )
        
        # Log first step
        ContractEditingStep.objects.create(
            session=session,
            step_type='template_selection',
            step_data={
                'template_id': str(template_id),
                'template_name': template.name,
                'contract_type': template.contract_type
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'])
    def detail(self, request, pk=None):
        """
        GET /manual-sessions/{id}/detail/
        Get detailed session information with all steps and edits
        """
        session = self.get_object()
        serializer = ContractEditingSessionDetailSerializer(session)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def fill_form(self, request, pk=None):
        """
        POST /manual-sessions/{id}/fill-form/
        Fill form fields for the contract
        
        Request body:
        {
            "form_data": {
                "party_a_name": "ACME Corp",
                "party_b_name": "Beta LLC",
                "contract_value": 50000,
                "effective_date": "2026-01-20"
            }
        }
        """
        session = self.get_object()
        form_data = request.data.get('form_data', {})
        
        if not form_data:
            return Response(
                {'error': 'form_data is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get template for validation
        template = ContractEditingTemplate.objects.get(id=session.template_id)
        
        # Validate all required fields are present
        validation_errors = {}
        for field_name, field_config in template.form_fields.items():
            if field_config.get('required') and field_name not in form_data:
                validation_errors[field_name] = 'This field is required'
        
        if validation_errors:
            return Response(
                {'errors': validation_errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update session
        session.form_data = form_data
        session.status = 'in_progress'
        session.last_saved_at = timezone.now()
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='form_fill',
            step_data=form_data
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Form data saved successfully',
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def select_clauses(self, request, pk=None):
        """
        POST /manual-sessions/{id}/select-clauses/
        Select clauses for the contract
        
        Request body:
        {
            "clause_ids": ["CONF-001", "TERM-001", "LIAB-001"],
            "custom_clause_content": {
                "CUSTOM-001": "Custom clause text here..."
            }
        }
        """
        session = self.get_object()
        clause_ids = request.data.get('clause_ids', [])
        custom_clauses = request.data.get('custom_clause_content', {})
        
        if not clause_ids:
            return Response(
                {'error': 'At least one clause must be selected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate all clause IDs exist
        invalid_clauses = []
        valid_clauses = []
        for clause_id in clause_ids:
            try:
                Clause.objects.get(
                    clause_id=clause_id,
                    tenant_id=request.user.tenant_id,
                    status='published'
                )
                valid_clauses.append(clause_id)
            except Clause.DoesNotExist:
                invalid_clauses.append(clause_id)
        
        if invalid_clauses:
            return Response(
                {
                    'error': 'Some clauses not found',
                    'invalid_clauses': invalid_clauses,
                    'valid_clauses': valid_clauses
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update session
        session.selected_clause_ids = clause_ids
        session.custom_clauses = custom_clauses
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='clause_selection',
            step_data={
                'clause_ids': clause_ids,
                'custom_clauses': bool(custom_clauses)
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Clauses selected successfully',
                'selected_count': len(valid_clauses),
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def define_constraints(self, request, pk=None):
        """
        POST /manual-sessions/{id}/define-constraints/
        Define constraints/versions for the contract
        
        Request body:
        {
            "constraints": {
                "payment_terms": "Net 30",
                "jurisdiction": "California",
                "confidentiality_period": "5 years"
            }
        }
        """
        session = self.get_object()
        constraints = request.data.get('constraints', {})
        
        if not constraints:
            return Response(
                {'error': 'At least one constraint must be defined'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update session
        session.constraints_config = constraints
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='constraint_definition',
            step_data=constraints
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Constraints defined successfully',
                'constraints_count': len(constraints),
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def generate_preview(self, request, pk=None):
        """
        POST /manual-sessions/{id}/generate-preview/
        Generate HTML and text preview of the contract
        
        Request body:
        {
            "form_data": {...},
            "selected_clause_ids": [...],
            "constraints_config": {...}
        }
        """
        session = self.get_object()
        
        form_data = request.data.get('form_data', session.form_data)
        clause_ids = request.data.get('selected_clause_ids', session.selected_clause_ids)
        constraints = request.data.get('constraints_config', session.constraints_config)
        
        if not form_data or not clause_ids:
            return Response(
                {
                    'error': 'Form data and clause IDs are required',
                    'current_form_data': bool(session.form_data),
                    'current_clause_ids': bool(session.selected_clause_ids)
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get template
        template = ContractEditingTemplate.objects.get(id=session.template_id)
        
        # Build contract content
        contract_html = self._build_contract_html(
            template, form_data, clause_ids, constraints, request.user.tenant_id
        )
        contract_text = self._build_contract_text(
            template, form_data, clause_ids, constraints, request.user.tenant_id
        )
        
        # Save preview
        preview, created = ContractPreview.objects.update_or_create(
            session=session,
            defaults={
                'preview_html': contract_html,
                'preview_text': contract_text,
                'form_data_snapshot': form_data,
                'clauses_snapshot': clause_ids,
                'constraints_snapshot': constraints
            }
        )
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='preview_generated',
            step_data={
                'preview_id': str(preview.id),
                'form_fields_count': len(form_data),
                'clauses_count': len(clause_ids)
            }
        )
        
        serializer = ContractPreviewSerializer(preview)
        return Response(
            {
                'message': 'Preview generated successfully',
                'preview': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def edit_after_preview(self, request, pk=None):
        """
        POST /manual-sessions/{id}/edit-after-preview/
        Make edits after reviewing the preview
        
        Request body:
        {
            "edit_type": "form_field",
            "field_name": "party_a_name",
            "old_value": "ACME Corp",
            "new_value": "ACME Corporation",
            "edit_reason": "Corrected company name spelling"
        }
        """
        session = self.get_object()
        
        edit_type = request.data.get('edit_type')
        field_name = request.data.get('field_name')
        old_value = request.data.get('old_value')
        new_value = request.data.get('new_value')
        edit_reason = request.data.get('edit_reason', '')
        
        if not edit_type:
            return Response(
                {'error': 'edit_type is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Apply edit based on type
        if edit_type == 'form_field':
            if field_name not in session.form_data:
                return Response(
                    {'error': f'Field {field_name} not found in form data'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            session.form_data[field_name] = new_value
        
        elif edit_type == 'clause_added':
            clause_id = request.data.get('clause_id')
            if clause_id not in session.selected_clause_ids:
                session.selected_clause_ids.append(clause_id)
        
        elif edit_type == 'clause_removed':
            clause_id = request.data.get('clause_id')
            if clause_id in session.selected_clause_ids:
                session.selected_clause_ids.remove(clause_id)
        
        elif edit_type == 'clause_content_edited':
            clause_id = request.data.get('clause_id')
            custom_content = request.data.get('custom_content')
            session.custom_clauses[clause_id] = custom_content
        
        session.save()
        
        # Log edit
        ContractEdits.objects.create(
            session=session,
            edit_type=edit_type,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            edit_reason=edit_reason
        )
        
        # Log as step
        ContractEditingStep.objects.create(
            session=session,
            step_type='field_edited',
            step_data={
                'edit_type': edit_type,
                'field_name': field_name,
                'edit_reason': edit_reason
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Edit applied successfully',
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def finalize_and_create(self, request, pk=None):
        """
        POST /manual-sessions/{id}/finalize-and-create/
        Finalize editing and create actual contract
        
        Request body:
        {
            "contract_title": "NDA with ACME Corp",
            "contract_description": "Non-disclosure agreement",
            "contract_value": 50000,
            "effective_date": "2026-01-20",
            "expiration_date": "2027-01-20",
            "additional_metadata": {...}
        }
        """
        session = self.get_object()
        
        # Validate session has required data
        if not session.form_data:
            return Response(
                {'error': 'Form data is required. Please fill the form first.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not session.selected_clause_ids:
            return Response(
                {'error': 'At least one clause must be selected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get template to use default clauses if needed
        template = ContractEditingTemplate.objects.get(id=session.template_id)
        
        # Use selected clauses or fall back to template defaults
        final_clause_ids = session.selected_clause_ids or template.mandatory_clauses
        final_constraints = session.constraints_config or {}
        
        # Create contract
        contract_data = {
            'title': request.data.get('contract_title', 'Untitled Contract'),
            'contract_type': template.contract_type,
            'status': 'draft',
            'generation_mode': 'manual',
            'form_inputs': session.form_data,
            'metadata': {
                'editing_session_id': str(session.id),
                'constraints': final_constraints,
                'custom_clauses': session.custom_clauses
            }
        }
        
        try:
            contract = Contract.objects.create(
                tenant_id=request.user.tenant_id,
                created_by=request.user.user_id,
                **contract_data
            )
            
            # Create contract version with selected clauses
            version = ContractVersion.objects.create(
                contract=contract,
                version_number=1,
                template_id=session.template_id,
                template_version=template.version,
                change_summary='Created from manual editing session',
                created_by=request.user.user_id,
                r2_key=f'contracts/{request.user.tenant_id}/{contract.id}/v1.docx'
            )
            
            # Update session status
            session.status = 'completed'
            session.save()
            
            # Log final step
            ContractEditingStep.objects.create(
                session=session,
                step_type='saved',
                step_data={
                    'contract_id': str(contract.id),
                    'version_id': str(version.id),
                    'status': 'completed'
                }
            )
            
            return Response(
                {
                    'message': 'Contract created successfully',
                    'contract': {
                        'id': str(contract.id),
                        'title': contract.title,
                        'status': contract.status,
                        'contract_type': contract.contract_type,
                        'created_at': contract.created_at.isoformat(),
                        'version': {
                            'id': str(version.id),
                            'version_number': version.version_number,
                            'created_at': version.created_at.isoformat()
                        }
                    },
                    'session': self.get_serializer(session).data
                },
                status=status.HTTP_201_CREATED
            )
        
        except Exception as e:
            return Response(
                {'error': f'Failed to create contract: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _build_contract_html(self, template, form_data, clause_ids, constraints, tenant_id):
        """
        Build professional HTML preview of contract
        """
        html_content = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Arial', sans-serif;
                    margin: 40px;
                    line-height: 1.6;
                    color: #333;
                }}
                .contract-header {{
                    text-align: center;
                    margin-bottom: 30px;
                    border-bottom: 2px solid #000;
                    padding-bottom: 20px;
                }}
                h1 {{
                    margin: 0;
                    font-size: 24px;
                    text-transform: uppercase;
                }}
                .contract-date {{
                    margin-top: 10px;
                    font-style: italic;
                }}
                .section {{
                    margin: 30px 0;
                    page-break-inside: avoid;
                }}
                .section-title {{
                    font-weight: bold;
                    font-size: 14px;
                    margin-top: 20px;
                    margin-bottom: 10px;
                    text-transform: uppercase;
                }}
                .clause {{
                    margin: 15px 0;
                    padding: 10px;
                    border-left: 3px solid #007bff;
                    background-color: #f8f9fa;
                }}
                .form-field {{
                    margin: 8px 0;
                }}
                .form-label {{
                    font-weight: bold;
                    display: inline-block;
                    width: 200px;
                }}
                .constraint {{
                    background-color: #fff3cd;
                    padding: 8px;
                    margin: 5px 0;
                    border-radius: 3px;
                }}
                @media print {{
                    body {{ margin: 20px; }}
                }}
            </style>
        </head>
        <body>
            <div class="contract-header">
                <h1>{template.name}</h1>
                <div class="contract-date">Date: {datetime.now().strftime('%B %d, %Y')}</div>
            </div>
            
            <div class="section">
                <div class="section-title">Contract Information</div>
        """
        
        # Add form data
        for field_name, field_value in form_data.items():
            html_content += f"""
                <div class="form-field">
                    <span class="form-label">{field_name.replace('_', ' ').title()}:</span>
                    <span>{field_value}</span>
                </div>
            """
        
        # Add constraints
        if constraints:
            html_content += '<div class="section-title">Constraints & Versions</div>'
            for constraint_name, constraint_value in constraints.items():
                html_content += f"""
                    <div class="constraint">
                        <strong>{constraint_name.replace('_', ' ').title()}:</strong> {constraint_value}
                    </div>
                """
        
        # Add clauses
        html_content += '<div class="section-title">Contract Clauses</div>'
        
        clauses = Clause.objects.filter(
            clause_id__in=clause_ids,
            tenant_id=tenant_id,
            status='published'
        )
        
        for idx, clause in enumerate(clauses, 1):
            html_content += f"""
                <div class="clause">
                    <strong>Clause {idx}: {clause.name}</strong><br>
                    {clause.content[:200]}...
                </div>
            """
        
        html_content += """
            </div>
        </body>
        </html>
        """
        
        return html_content
    
    def _build_contract_text(self, template, form_data, clause_ids, constraints, tenant_id):
        """
        Build plain text preview of contract
        """
        text_content = f"""
{template.name.upper()}

Date: {datetime.now().strftime('%B %d, %Y')}

{'='*60}

CONTRACT INFORMATION
{'='*60}

"""
        
        # Add form data
        for field_name, field_value in form_data.items():
            text_content += f"{field_name.replace('_', ' ').title()}: {field_value}\n"
        
        # Add constraints
        if constraints:
            text_content += f"\n{'='*60}\nCONSTRAINTS & VERSIONS\n{'='*60}\n\n"
            for constraint_name, constraint_value in constraints.items():
                text_content += f"{constraint_name.replace('_', ' ').title()}: {constraint_value}\n"
        
        # Add clauses
        text_content += f"\n{'='*60}\nCONTRACT CLAUSES\n{'='*60}\n\n"
        
        clauses = Clause.objects.filter(
            clause_id__in=clause_ids,
            tenant_id=tenant_id,
            status='published'
        )
        
        for idx, clause in enumerate(clauses, 1):
            text_content += f"\nClause {idx}: {clause.name}\n"
            text_content += f"{'-'*40}\n"
            text_content += f"{clause.content[:300]}...\n"
        
        return text_content
    
    @action(detail=True, methods=['post'])
    def save_draft(self, request, pk=None):
        """
        POST /manual-sessions/{id}/save-draft/
        Save the current state as draft without finalizing
        """
        session = self.get_object()
        
        session.last_saved_at = timezone.now()
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='saved',
            step_data={
                'auto_saved': False,
                'timestamp': timezone.now().isoformat()
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Draft saved successfully',
                'last_saved_at': session.last_saved_at.isoformat(),
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['delete'])
    def discard(self, request, pk=None):
        """
        DELETE /manual-sessions/{id}/discard/
        Discard the editing session
        """
        session = self.get_object()
        session.status = 'abandoned'
        session.save()
        
        return Response(
            {'message': 'Session discarded successfully'},
            status=status.HTTP_200_OK
        )


# ============================================================================
# SECTION 4: R2 UPLOAD ENDPOINTS
# ============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_document(request):
    """
    POST /api/contracts/upload-document/
    
    Upload any document to Cloudflare R2 and get a downloadable link.
    
    Request (multipart/form-data):
        - file: The document file to upload (required)
        - filename: Optional custom filename
    
    Response:
    {
        "success": true,
        "file_id": "uuid",
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "original_filename": "document.pdf",
        "file_size": 123456,
        "uploaded_at": "2026-01-20T12:00:00Z"
    }
    """
    uploaded_file = request.FILES.get('file')
    
    if not uploaded_file:
        return Response(
            {
                'success': False,
                'error': 'No file provided',
                'message': 'Please provide a file in the request'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Get tenant ID from authenticated user
        tenant_id = str(request.user.tenant_id)
        
        # Get custom filename if provided, otherwise use original
        custom_filename = request.data.get('filename') or uploaded_file.name
        
        # Upload to R2
        r2_service = R2StorageService()
        r2_key = r2_service.upload_file(uploaded_file, tenant_id, custom_filename)
        
        # Generate download URL
        download_url = r2_service.generate_presigned_url(r2_key, expiration=3600)  # 1 hour
        
        # Get file size
        file_size = uploaded_file.size
        
        return Response({
            'success': True,
            'file_id': str(uuid.uuid4()),
            'r2_key': r2_key,
            'download_url': download_url,
            'original_filename': uploaded_file.name,
            'file_size': file_size,
            'uploaded_at': timezone.now().isoformat(),
            'message': 'File uploaded successfully to Cloudflare R2'
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {
                'success': False,
                'error': str(e),
                'message': 'Failed to upload file to Cloudflare R2'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_contract_document(request):
    """
    POST /api/contracts/upload-contract-document/
    
    Upload a contract document to Cloudflare R2 and optionally create a Contract record.
    
    Request (multipart/form-data):
        - file: The contract PDF/document to upload (required)
        - title: Contract title (optional)
        - contract_type: Type of contract (optional)
        - counterparty: Counterparty name (optional)
        - create_contract: Boolean - whether to create a Contract record (default: false)
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",  // Only if create_contract=true
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "original_filename": "contract.pdf",
        "file_size": 123456,
        "uploaded_at": "2026-01-20T12:00:00Z"
    }
    """
    uploaded_file = request.FILES.get('file')
    
    if not uploaded_file:
        return Response(
            {
                'success': False,
                'error': 'No file provided',
                'message': 'Please provide a file in the request'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Get tenant and user info
        tenant_id = str(request.user.tenant_id)
        user_id = str(request.user.user_id)
        
        # Get optional parameters
        title = request.data.get('title') or f'Uploaded Contract - {timezone.now().strftime("%Y-%m-%d")}'
        contract_type = request.data.get('contract_type')
        counterparty = request.data.get('counterparty')
        create_contract = request.data.get('create_contract', 'false').lower() in ['true', '1', 'yes']
        
        # Upload to R2
        r2_service = R2StorageService()
        r2_key = r2_service.upload_file(uploaded_file, tenant_id, uploaded_file.name)
        
        # Generate download URL
        download_url = r2_service.generate_presigned_url(r2_key, expiration=86400)  # 24 hours
        
        # Get file info
        file_size = uploaded_file.size
        file_bytes = uploaded_file.read()
        uploaded_file.seek(0)  # Reset for potential re-read
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        
        response_data = {
            'success': True,
            'r2_key': r2_key,
            'download_url': download_url,
            'original_filename': uploaded_file.name,
            'file_size': file_size,
            'uploaded_at': timezone.now().isoformat(),
            'message': 'Contract uploaded successfully to Cloudflare R2'
        }
        
        # Optionally create Contract record
        if create_contract:
            contract = Contract.objects.create(
                tenant_id=uuid.UUID(tenant_id),
                title=title,
                contract_type=contract_type or 'other',
                counterparty=counterparty,
                status='draft',
                created_by=uuid.UUID(user_id),
                document_r2_key=r2_key,
            )
            
            # Create first version
            ContractVersion.objects.create(
                contract=contract,
                version_number=1,
                r2_key=r2_key,
                template_id=uuid.uuid4(),  # Placeholder
                template_version=1,
                change_summary='Initial upload',
                created_by=uuid.UUID(user_id),
                file_size=file_size,
                file_hash=file_hash,
            )
            
            response_data['contract_id'] = str(contract.id)
            response_data['contract_title'] = contract.title
            response_data['contract_status'] = contract.status
        
        return Response(response_data, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        import traceback
        return Response(
            {
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'message': 'Failed to upload contract document'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_document_download_url(request):
    """
    GET /api/contracts/document-download-url/?r2_key=<r2_key>
    
    Get a downloadable link for a document stored in Cloudflare R2.
    
    Query Parameters:
        - r2_key: The R2 key of the document
        - expiration: Optional expiration time in seconds (default: 3600)
    
    Response:
    {
        "success": true,
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "expiration_seconds": 3600
    }
    """
    r2_key = request.query_params.get('r2_key')
    
    if not r2_key:
        return Response(
            {
                'success': False,
                'error': 'r2_key parameter is required',
                'message': 'Please provide an r2_key query parameter'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Get expiration time (default: 1 hour)
        expiration = int(request.query_params.get('expiration', 3600))
        
        # Generate download URL
        r2_service = R2StorageService()
        download_url = r2_service.generate_presigned_url(r2_key, expiration=expiration)
        
        return Response({
            'success': True,
            'r2_key': r2_key,
            'download_url': download_url,
            'expiration_seconds': expiration,
            'expires_at': timezone.now().isoformat()
        })
        
    except Exception as e:
        return Response(
            {
                'success': False,
                'error': str(e),
                'message': 'Failed to generate download URL'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_contract_download_url(request, contract_id):
    """
    GET /api/contracts/{contract_id}/download-url/
    
    Get a downloadable link for a specific contract.
    
    Path Parameters:
        - contract_id: UUID of the contract
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "contract_title": "My Contract",
        "version_number": 1,
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "file_size": 123456
    }
    """
    try:
        # Get contract
        contract = Contract.objects.get(
            id=contract_id,
            tenant_id=request.user.tenant_id
        )
        
        # Get latest version
        try:
            latest_version = contract.versions.latest('version_number')
            r2_key = latest_version.r2_key
            version_number = latest_version.version_number
            file_size = latest_version.file_size
        except ContractVersion.DoesNotExist:
            # Fallback to document_r2_key if no versions exist
            if contract.document_r2_key:
                r2_key = contract.document_r2_key
                version_number = contract.current_version
                file_size = None
            else:
                return Response(
                    {
                        'success': False,
                        'error': 'No document available for this contract',
                        'message': 'This contract does not have an uploaded document'
                    },
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Generate download URL
        r2_service = R2StorageService()
        download_url = r2_service.generate_presigned_url(r2_key, expiration=3600)
        
        return Response({
            'success': True,
            'contract_id': str(contract.id),
            'contract_title': contract.title,
            'version_number': version_number,
            'r2_key': r2_key,
            'download_url': download_url,
            'file_size': file_size
        })
        
    except Contract.DoesNotExist:
        return Response(
            {
                'success': False,
                'error': 'Contract not found',
                'message': f'No contract found with ID {contract_id}'
            },
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {
                'success': False,
                'error': str(e),
                'message': 'Failed to get contract download URL'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ============================================================================
# SECTION 5: HEALTH CHECK VIEWS
# ============================================================================

class HealthCheckView(APIView):
    """
    GET /api/v1/health/ - Health check endpoint
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """
        Basic health check
        """
        try:
            # Check database connection
            connection.ensure_connection()
            db_status = 'healthy'
        except Exception:
            db_status = 'unhealthy'
        
        return Response({
            'status': 'ok',
            'database': db_status,
            'service': 'CLM Backend API'
        })


# ============================================================================
# SECTION 6: SIGNNOW E-SIGNATURE VIEWS
# ============================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_contract(request):
    """
    Upload contract PDF to SignNow
    
    Request:
    {
        "contract_id": "uuid",
        "document_name": "Optional name (defaults to contract title)"
    }
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "signnow_document_id": "doc_id",
        "status": "draft",
        "message": "Contract uploaded successfully"
    }
    """
    try:
        api_service = get_signnow_api_service()
        contract_id = request.data.get("contract_id")
        if not contract_id:
            return Response(
                {"error": "contract_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get contract
        contract = get_object_or_404(Contract, id=contract_id)
        
        # Check if already has e-signature record
        if hasattr(contract, 'esignature_contract'):
            return Response(
                {
                    "error": "Contract already uploaded for signing",
                    "signnow_document_id": contract.esignature_contract.signnow_document_id,
                    "status": contract.esignature_contract.status
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get contract PDF from storage
        if not contract.document_r2_key:
            return Response(
                {"error": "No document file found for contract"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            pdf_content = default_storage.open(contract.document_r2_key, 'rb').read()
        except Exception as e:
            logger.error(f"Failed to read contract file: {str(e)}")
            return Response(
                {"error": "Failed to read contract file"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Upload to SignNow
        document_name = request.data.get("document_name", contract.title)
        signnow_response = api_service.upload_document(pdf_content, document_name)
        
        signnow_document_id = signnow_response.get("id")
        if not signnow_document_id:
            return Response(
                {"error": "Failed to get document ID from SignNow"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Create e-signature contract record
        esig = ESignatureContract.objects.create(
            contract=contract,
            signnow_document_id=signnow_document_id,
            status='draft',
            original_r2_key=contract.document_r2_key,
            signing_request_data={"document_name": document_name}
        )
        
        # Log event
        SigningAuditLog.objects.create(
            esignature_contract=esig,
            event='invite_sent',
            message=f'Document uploaded to SignNow: {signnow_document_id}',
            signnow_response=signnow_response,
            new_status='draft'
        )
        
        logger.info(f"Contract {contract_id} uploaded to SignNow: {signnow_document_id}")
        
        return Response(
            {
                "success": True,
                "contract_id": str(contract_id),
                "signnow_document_id": signnow_document_id,
                "status": "draft",
                "message": "Contract uploaded successfully"
            },
            status=status.HTTP_201_CREATED
        )
        
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. SEND FOR SIGNATURE
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_for_signature(request):
    """
    Send document for signatures
    
    Request:
    {
        "contract_id": "uuid",
        "signers": [
            {"email": "signer1@example.com", "name": "Signer 1"},
            {"email": "signer2@example.com", "name": "Signer 2"}
        ],
        "signing_order": "sequential" | "parallel",
        "expires_in_days": 30
    }
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "status": "sent",
        "signers_invited": 2,
        "message": "Invitations sent successfully"
    }
    """
    try:
        api_service = get_signnow_api_service()
        contract_id = request.data.get("contract_id")
        signers_data = request.data.get("signers", [])
        signing_order = request.data.get("signing_order", "sequential")
        expires_in_days = request.data.get("expires_in_days", 30)
        
        if not contract_id or not signers_data:
            return Response(
                {"error": "contract_id and signers are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        if esig.status != 'draft':
            return Response(
                {"error": f"Contract already sent (status: {esig.status})"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create SignNow invitations
        signnow_invites = api_service.create_invite(
            esig.signnow_document_id,
            signers_data,
            signing_order=signing_order
        )
        
        # Store signer information
        for idx, signer_info in enumerate(signers_data):
            Signer.objects.create(
                esignature_contract=esig,
                email=signer_info["email"],
                name=signer_info.get("name", ""),
                signing_order=idx + 1 if signing_order == "sequential" else 0,
                status='invited'
            )
        
        # Update e-signature contract
        esig.status = 'sent'
        esig.signing_order = signing_order
        esig.sent_at = timezone.now()
        esig.expires_at = timezone.now() + timedelta(days=expires_in_days)
        esig.signing_request_data = {
            "signers": signers_data,
            "signing_order": signing_order,
            "expires_in_days": expires_in_days
        }
        esig.save()
        
        # Log event
        SigningAuditLog.objects.create(
            esignature_contract=esig,
            event='invite_sent',
            message=f'Invitations sent to {len(signers_data)} signer(s)',
            signnow_response=signnow_invites,
            old_status='draft',
            new_status='sent'
        )
        
        logger.info(
            f"Sent contract {contract_id} for signature to {len(signers_data)} signers"
        )
        
        return Response(
            {
                "success": True,
                "contract_id": str(contract_id),
                "status": "sent",
                "signers_invited": len(signers_data),
                "expires_at": esig.expires_at.isoformat(),
                "message": "Invitations sent successfully"
            },
            status=status.HTTP_200_OK
        )
        
    except Exception as e:
        logger.error(f"Send for signature failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. GENERATE SIGNING URL
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_signing_url(request, contract_id):
    """
    Generate embedded signing URL for a specific signer
    
    Query Parameters:
    - signer_email: Email of signer (required)
    
    Response:
    {
        "success": true,
        "signing_url": "https://app.signnow.com/embedded-signing/...",
        "signer_email": "signer@example.com",
        "expires_at": "2026-02-18T..."
    }
    """
    try:
        api_service = get_signnow_api_service()
        signer_email = request.query_params.get("signer_email")
        if not signer_email:
            return Response(
                {"error": "signer_email query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        # Get signer
        signer = get_object_or_404(Signer, esignature_contract=esig, email=signer_email)
        
        # Generate signing link if not already cached
        if not signer.signing_url or (
            signer.signing_url_expires_at and 
            signer.signing_url_expires_at <= timezone.now()
        ):
            link_response = api_service.get_signing_link(
                esig.signnow_document_id,
                signer_email
            )
            
            signer.signing_url = link_response.get("signing_link")
            signer.signing_url_expires_at = timezone.now() + timedelta(hours=24)
            signer.save()
        
        return Response(
            {
                "success": True,
                "signing_url": signer.signing_url,
                "signer_email": signer_email,
                "expires_at": signer.signing_url_expires_at.isoformat() if signer.signing_url_expires_at else None,
                "message": "Signing URL generated successfully"
            },
            status=status.HTTP_200_OK
        )
        
    except Exception as e:
        logger.error(f"Failed to generate signing URL: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. CHECK STATUS (POLLING)
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_status(request, contract_id):
    """
    Get document status from database (and optionally update from SignNow)
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "status": "draft" | "sent" | "in_progress" | "completed" | "declined",
        "signers": [
            {
                "email": "signer1@example.com",
                "name": "John Doe",
                "status": "invited" | "viewed" | "in_progress" | "signed" | "declined",
                "signed_at": "2026-02-18T..." or null
            }
        ],
        "all_signed": false,
        "last_checked": "2026-02-18T..."
    }
    """
    try:
        api_service = get_signnow_api_service()
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        # Try to poll SignNow (optional - if fails, just return DB data)
        try:
            status_info = api_service.get_document_status(esig.signnow_document_id)
            
            # Update signer statuses from SignNow
            for signer_status in status_info["signers"]:
                try:
                    signer = Signer.objects.get(
                        esignature_contract=esig,
                        email=signer_status["email"]
                    )
                    
                    old_status = signer.status
                    new_status = signer_status["status"]
                    
                    signer.status = new_status
                    if new_status == "signed":
                        signer.has_signed = True
                        if not signer.signed_at:
                            signer.signed_at = timezone.now()
                    signer.save()
                    
                    # Log status change
                    if old_status != new_status:
                        SigningAuditLog.objects.create(
                            esignature_contract=esig,
                            signer=signer,
                            event='status_checked',
                            message=f'Status changed from {old_status} to {new_status}',
                            old_status=old_status,
                            new_status=new_status
                        )
                        
                except Signer.DoesNotExist:
                    pass
            
            # Update contract status if all signed
            old_contract_status = esig.status
            if status_info["is_completed"]:
                esig.status = "completed"
                if not esig.completed_at:
                    esig.completed_at = timezone.now()
            else:
                esig.status = status_info["status"]
            
            esig.last_status_check_at = timezone.now()
            esig.save()
            
            logger.info(f"Updated status from SignNow for contract {contract_id}: {esig.status}")
            
        except Exception as e:
            # SignNow API failed - just use database data
            logger.warning(f"Could not poll SignNow, using cached data: {str(e)}")
        
        # Build response from database
        signers_response = []
        for signer in esig.signers.all():
            signers_response.append({
                "email": signer.email,
                "name": signer.name,
                "status": signer.status,
                "signed_at": signer.signed_at.isoformat() if signer.signed_at else None,
                "has_signed": signer.has_signed
            })
        
        all_signed = all(s["has_signed"] for s in signers_response)
        
        logger.info(f"Returning status for contract {contract_id}: {esig.status}")
        
        return Response(
            {
                "success": True,
                "contract_id": str(contract_id),
                "status": esig.status,
                "signers": signers_response,
                "all_signed": all_signed,
                "last_checked": esig.last_status_check_at.isoformat() if esig.last_status_check_at else None
            },
            status=status.HTTP_200_OK
        )
        
    except Exception as e:
        logger.error(f"Status check failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. DOWNLOAD EXECUTED DOCUMENT
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_executed_document(request, contract_id):
    """
    Download signed PDF from SignNow and store immutable copy
    
    Response: PDF file download or JSON error
    {
        "error": "Contract not yet completed"
    }
    """
    try:
        api_service = get_signnow_api_service()
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        # If status is not completed, re-poll first
        if esig.status != "completed":
            # Re-check status
            status_info = api_service.get_document_status(esig.signnow_document_id)
            if not status_info["is_completed"]:
                return Response(
                    {
                        "error": "Contract not yet completed by all signers",
                        "current_status": esig.status,
                        "message": "Please try again after all signers have completed"
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Update status
            esig.status = "completed"
            esig.completed_at = timezone.now()
            esig.save()
        
        # Download PDF from SignNow
        pdf_content = api_service.download_document(esig.signnow_document_id)
        
        if not pdf_content:
            return Response(
                {"error": "Failed to download signed document"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Store immutable copy if not already stored
        if not esig.executed_r2_key:
            from django.core.files.base import ContentFile

            r2_key = f"signed-contracts/{contract_id}_executed.pdf"
            default_storage.save(r2_key, ContentFile(pdf_content))
            esig.executed_r2_key = r2_key
            esig.save()
        
        # Log download
        SigningAuditLog.objects.create(
            esignature_contract=esig,
            event='document_downloaded',
            message='Executed document downloaded',
            new_status='completed'
        )
        
        logger.info(f"Downloaded executed document for contract {contract_id}")
        
        # Return PDF file
        from django.http import FileResponse
        from io import BytesIO
        filename = f"signed_contract_{contract_id}.pdf"
        pdf_file = BytesIO(pdf_content)
        pdf_file.seek(0)
        return FileResponse(
            pdf_file,
            as_attachment=True,
            filename=filename,
            content_type='application/pdf'
        )
        
    except Exception as e:
        logger.error(f"Download failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )




# ========== GENERATION VIEWS ==========

class ContractTemplateViewSet(viewsets.ModelViewSet):
    """
    API endpoint for contract templates
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContractTemplateSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        return ContractTemplate.objects.filter(
            tenant_id=tenant_id,
            status='published'
        )
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
    
    def perform_create(self, serializer):
        """Set tenant_id and created_by when creating a template"""
        serializer.save(
            tenant_id=self.request.user.tenant_id,
            created_by=self.request.user.user_id
        )


class ClauseViewSet(viewsets.ModelViewSet):
    """
    API endpoint for clauses with alternative suggestions
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ClauseSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        contract_type = self.request.query_params.get('contract_type')

        ensure_tenant_clause_library_seeded(
            tenant_id=tenant_id,
            user_id=self.request.user.user_id,
            contract_type=contract_type,
            min_count=50,
        )

        queryset = Clause.objects.filter(
            tenant_id=tenant_id,
            status='published'
        )
        
        # Filter by contract type if provided
        if contract_type:
            queryset = queryset.filter(contract_type=contract_type)
        
        return queryset

    @action(detail=False, methods=['get'], url_path='constraints-library')
    def constraints_library(self, request):
        q = (request.query_params.get('q') or '').strip().lower()
        category = (request.query_params.get('category') or '').strip().lower()

        items = CONSTRAINT_LIBRARY
        if category:
            items = [x for x in items if str(x.get('category') or '').strip().lower() == category]
        if q:
            items = [
                x
                for x in items
                if q in str(x.get('label') or '').lower()
                or q in str(x.get('key') or '').lower()
                or q in str(x.get('category') or '').lower()
            ]

        return Response({'success': True, 'count': len(items), 'results': items}, status=status.HTTP_200_OK)
    
    def perform_create(self, serializer):
        """Set tenant_id and created_by when creating a clause"""
        serializer.save(
            tenant_id=self.request.user.tenant_id,
            created_by=self.request.user.user_id
        )
    
    @action(detail=True, methods=['post'], url_path='alternatives')
    def alternatives(self, request, pk=None):
        """
        POST /clauses/{id}/alternatives/
        Get alternative clause suggestions based on contract context
        
        Request:
        {
            "contract_type": "MSA",
            "contract_value": 5000000,
            "counterparty": "Acme Corp"
        }
        
        Response:
        {
            "alternatives": [
                {
                    "clause_id": "LIAB-002",
                    "name": "Strict Liability Clause",
                    "content": "...",
                    "rationale": "Higher value contracts require stricter liability",
                    "confidence": 0.92,
                    "source": "rule_based"
                }
            ]
        }
        """
        clause = self.get_object()
        tenant_id = self.request.user.tenant_id
        
        context = request.data
        contract_type = context.get('contract_type', 'Unknown')
        
        rule_engine = RuleEngine()
        suggestions = rule_engine.get_clause_suggestions(
            tenant_id, contract_type, context, clause.clause_id
        )
        
        suggestion_ids = [s['clause_id'] for s in suggestions]
        alt_clauses = Clause.objects.filter(
            tenant_id=tenant_id,
            clause_id__in=suggestion_ids,
            status='published'
        )
        
        clause_map = {c.clause_id: c for c in alt_clauses}
        
        result = []
        for suggestion in suggestions:
            alt_clause = clause_map.get(suggestion['clause_id'])
            if alt_clause:
                result.append({
                    **ClauseSerializer(alt_clause).data,
                    'rationale': suggestion['rationale'],
                    'confidence': suggestion['confidence'],
                    'source': suggestion['source']
                })
        
        return Response({'alternatives': result})
    
    @action(detail=False, methods=['post'], url_path='contract-suggestions')
    def contract_suggestions(self, request):
        """
        POST /clauses/contract-suggestions/
        Get clause suggestions for a contract
        
        Request:
        {
            "contract_id": "uuid"
        }
        """
        contract_id = request.data.get('contract_id')
        if not contract_id:
            return Response(
                {'error': 'contract_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            contract = Contract.objects.get(
                id=contract_id,
                tenant_id=request.user.tenant_id
            )
        except Exception:
            # Handle invalid UUID or missing contract
            return Response(
                {'error': 'Contract not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        rule_engine = RuleEngine()
        context = {
            'contract_type': contract.contract_type,
            'contract_value': float(contract.value or 0),
            'counterparty': contract.counterparty
        }
        
        # Get all published clauses for this contract type
        clauses = Clause.objects.filter(
            tenant_id=request.user.tenant_id,
            contract_type=contract.contract_type,
            status='published'
        )
        
        suggestions = []
        for clause in clauses:
            suggestions_for_clause = rule_engine.get_clause_suggestions(
                request.user.tenant_id,
                contract.contract_type,
                context,
                clause.clause_id
            )
            if suggestions_for_clause:
                suggestions.extend(suggestions_for_clause)
        
        return Response({'suggestions': suggestions})
    
    @action(detail=False, methods=['post'], url_path='bulk-suggestions')
    def bulk_suggestions(self, request):
        """
        POST /clauses/bulk-suggestions/
        Get clause suggestions for multiple contracts
        
        Request:
        {
            "contract_ids": ["uuid1", "uuid2"]
        }
        """
        contract_ids = request.data.get('contract_ids', [])
        
        if not contract_ids or not isinstance(contract_ids, list):
            return Response(
                {'suggestions': {}},
                status=status.HTTP_200_OK
            )
        
        # Filter out invalid UUIDs
        valid_ids = []
        for cid in contract_ids:
            try:
                uuid.UUID(str(cid))
                valid_ids.append(cid)
            except (ValueError, AttributeError):
                pass
        
        if not valid_ids:
            return Response(
                {'suggestions': {}},
                status=status.HTTP_200_OK
            )
        
        contracts = Contract.objects.filter(
            id__in=valid_ids,
            tenant_id=request.user.tenant_id
        )
        
        rule_engine = RuleEngine()
        suggestions = {}
        
        for contract in contracts:
            context = {
                'contract_type': contract.contract_type,
                'contract_value': float(contract.value or 0),
                'counterparty': contract.counterparty
            }
            
            # Get all published clauses for this contract type
            clauses = Clause.objects.filter(
                tenant_id=request.user.tenant_id,
                contract_type=contract.contract_type,
                status='published'
            )
            
            contract_suggestions = []
            for clause in clauses:
                suggestions_for_clause = rule_engine.get_clause_suggestions(
                    request.user.tenant_id,
                    contract.contract_type,
                    context,
                    clause.clause_id
                )
                if suggestions_for_clause:
                    contract_suggestions.extend(suggestions_for_clause)
            
            suggestions[str(contract.id)] = contract_suggestions
        
        return Response({'suggestions': suggestions})


class ContractViewSetLegacy(viewsets.ModelViewSet):
    """
    API endpoint for contracts with generation, approval, and version management
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ContractDetailSerializer
        elif self.action == 'generate':
            return ContractGenerateSerializer
        elif self.action == 'approve':
            return ContractApproveSerializer
        return ContractSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        return Contract.objects.filter(tenant_id=tenant_id)
    
    def perform_create(self, serializer):
        """Set tenant_id and created_by when creating a contract"""
        serializer.save(
            tenant_id=self.request.user.tenant_id,
            created_by=self.request.user.user_id
        )

    # ---------------------------------------------------------------------
    # Filesystem-backed template support (no DB templates)
    # ---------------------------------------------------------------------
    def _templates_dir(self) -> str:
        from django.conf import settings

        return os.path.join(settings.BASE_DIR, 'templates')

    def _sanitize_template_filename(self, name: str) -> str:
        base = os.path.basename(str(name or '')).strip()
        base = base.replace('\\', '').replace('/', '')
        base = re.sub(r'[^A-Za-z0-9 _.-]+', '', base)
        base = re.sub(r'\s+', '_', base).strip('_')
        if not base.lower().endswith('.txt'):
            base = f'{base}.txt' if base else 'Template.txt'
        return base

    def _infer_contract_type_from_filename(self, filename: str) -> str:
        name = (filename or '').lower()
        if 'nda' in name:
            return 'NDA'
        if 'msa' in name or 'master' in name:
            return 'MSA'
        if 'sow' in name or 'statement_of_work' in name or 'statement-of-work' in name:
            return 'SOW'
        if 'contractor' in name:
            return 'CONTRACTOR_AGREEMENT'
        if 'employ' in name:
            return 'EMPLOYMENT'
        if 'agency' in name:
            return 'AGENCY_AGREEMENT'
        if 'property' in name:
            return 'PROPERTY_MANAGEMENT'
        if 'purchase' in name:
            return 'PURCHASE_AGREEMENT'
        return 'SERVICE_AGREEMENT'

    def _render_template_text(self, raw_text: str, values: dict) -> str:
        rendered = raw_text or ''
        for key, value in (values or {}).items():
            placeholder = re.compile(r'\{\{\s*' + re.escape(str(key)) + r'\s*\}\}')
            rendered = placeholder.sub(str(value) if value is not None else '', rendered)
        return rendered

    def _assemble_additions_block(self, tenant_id, contract_type: str, selected_clause_ids, custom_clauses, constraints) -> str:
        selected_clause_ids = selected_clause_ids or []
        custom_clauses = custom_clauses or []
        constraints = constraints or []

        blocks = []

        if selected_clause_ids:
            clause_qs = Clause.objects.filter(
                tenant_id=tenant_id,
                status='published',
                clause_id__in=selected_clause_ids,
            )
            if contract_type:
                clause_qs = clause_qs.filter(contract_type=contract_type)

            clause_map = {c.clause_id: c for c in clause_qs}
            for cid in selected_clause_ids:
                clause = clause_map.get(cid)
                if not clause:
                    continue
                name = (clause.name or cid).strip()
                content = (clause.content or '').strip()
                if content:
                    blocks.append(f'{name}\n{content}')

        constraint_lines = []
        for c in constraints:
            if not isinstance(c, dict):
                continue
            n = (c.get('name') or '').strip()
            v = (c.get('value') or '').strip()
            if n and v:
                constraint_lines.append(f'- {n}: {v}')
        if constraint_lines:
            blocks.append('Constraints\n' + '\n'.join(constraint_lines))

        for cc in custom_clauses:
            if not isinstance(cc, dict):
                continue
            title = (cc.get('title') or '').strip() or 'Custom Clause'
            content = (cc.get('content') or '').strip()
            if content:
                blocks.append(f'{title}\n{content}')

        if not blocks:
            return ''

        return 'ADDITIONAL CLAUSES & CONSTRAINTS\n\n' + ('\n\n'.join(blocks))

    def _apply_additions(self, rendered_text: str, additions_block: str) -> str:
        if not additions_block:
            return rendered_text

        out = rendered_text or ''
        for slot in ('clauses_section', 'clauses', 'constraints_section', 'constraints'):
            token_rx = re.compile(r'\{\{\s*' + re.escape(slot) + r'\s*\}\}')
            if token_rx.search(out):
                return token_rx.sub(additions_block, out)

        return out + '\n\n---\n\n' + additions_block + '\n'

    @action(detail=False, methods=['post'], url_path='preview-from-file')
    def preview_from_file(self, request):
        tenant_id = request.user.tenant_id
        filename = request.data.get('filename')
        if not filename:
            return Response({'error': 'filename is required'}, status=status.HTTP_400_BAD_REQUEST)

        structured_inputs = request.data.get('structured_inputs') or {}
        if not isinstance(structured_inputs, dict):
            return Response({'error': 'structured_inputs must be an object'}, status=status.HTTP_400_BAD_REQUEST)

        selected_clauses = request.data.get('selected_clauses') or []
        if selected_clauses is not None and not isinstance(selected_clauses, list):
            return Response({'error': 'selected_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        custom_clauses = request.data.get('custom_clauses') or []
        if custom_clauses is not None and not isinstance(custom_clauses, list):
            return Response({'error': 'custom_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        constraints = request.data.get('constraints') or []
        if constraints is not None and not isinstance(constraints, list):
            return Response({'error': 'constraints must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        safe = self._sanitize_template_filename(filename)
        try:
            from django.db.models import Q
            from contracts.models import TemplateFile
            from contracts.utils.template_files_db import get_or_import_template_from_filesystem

            tmpl = TemplateFile.objects.filter(filename=safe).filter(
                Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True)
            ).first()
        except Exception:
            tmpl = None

        if not tmpl:
            try:
                tmpl = get_or_import_template_from_filesystem(filename=safe, tenant_id=tenant_id)
            except Exception:
                tmpl = None

        if not tmpl:
            return Response({'error': 'Template not found', 'filename': safe}, status=status.HTTP_404_NOT_FOUND)

        raw_text = tmpl.content or ''

        rendered = self._render_template_text(raw_text, structured_inputs)
        contract_type = (tmpl.contract_type or self._infer_contract_type_from_filename(safe))
        additions = self._assemble_additions_block(tenant_id, contract_type, selected_clauses, custom_clauses, constraints)
        rendered = self._apply_additions(rendered, additions)

        return Response(
            {
                'success': True,
                'filename': safe,
                'contract_type': contract_type,
                'raw_text': raw_text,
                'rendered_text': rendered,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'], url_path='generate-from-file')
    def generate_from_file(self, request):
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id

        filename = request.data.get('filename')
        if not filename:
            return Response({'error': 'filename is required'}, status=status.HTTP_400_BAD_REQUEST)

        structured_inputs = request.data.get('structured_inputs') or {}
        if not isinstance(structured_inputs, dict):
            return Response({'error': 'structured_inputs must be an object'}, status=status.HTTP_400_BAD_REQUEST)

        user_instructions = request.data.get('user_instructions')
        title = request.data.get('title')

        selected_clauses = request.data.get('selected_clauses') or []
        if selected_clauses is not None and not isinstance(selected_clauses, list):
            return Response({'error': 'selected_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        custom_clauses = request.data.get('custom_clauses') or []
        if custom_clauses is not None and not isinstance(custom_clauses, list):
            return Response({'error': 'custom_clauses must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        constraints = request.data.get('constraints') or []
        if constraints is not None and not isinstance(constraints, list):
            return Response({'error': 'constraints must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        safe = self._sanitize_template_filename(filename)
        try:
            from django.db.models import Q
            from contracts.models import TemplateFile
            from contracts.utils.template_files_db import get_or_import_template_from_filesystem

            tmpl = TemplateFile.objects.filter(filename=safe).filter(
                Q(tenant_id=tenant_id) | Q(tenant_id__isnull=True)
            ).first()
        except Exception:
            tmpl = None

        if not tmpl:
            try:
                tmpl = get_or_import_template_from_filesystem(filename=safe, tenant_id=tenant_id)
            except Exception:
                tmpl = None

        if not tmpl:
            return Response({'error': 'Template not found', 'filename': safe}, status=status.HTTP_404_NOT_FOUND)

        raw_text = tmpl.content or ''

        rendered = self._render_template_text(raw_text, structured_inputs)
        inferred_type = (tmpl.contract_type or self._infer_contract_type_from_filename(safe))
        additions = self._assemble_additions_block(tenant_id, inferred_type, selected_clauses, custom_clauses, constraints)
        rendered = self._apply_additions(rendered, additions)

        counterparty = (
            structured_inputs.get('counterparty')
            or structured_inputs.get('counterparty_name')
            or structured_inputs.get('receiving_party_name')
            or structured_inputs.get('client_name')
            or structured_inputs.get('provider_name')
            or structured_inputs.get('contractor_name')
        )

        clauses_payload = []
        for cid in selected_clauses or []:
            if isinstance(cid, str) and cid.strip():
                clauses_payload.append({'kind': 'library', 'clause_id': cid.strip()})
        for c in constraints or []:
            if isinstance(c, dict) and (c.get('name') or '').strip() and (c.get('value') or '').strip():
                clauses_payload.append({'kind': 'constraint', 'name': (c.get('name') or '').strip(), 'value': (c.get('value') or '').strip()})
        for c in custom_clauses or []:
            if isinstance(c, dict) and (c.get('content') or '').strip():
                clauses_payload.append({'kind': 'custom', 'title': (c.get('title') or 'Custom Clause').strip(), 'content': (c.get('content') or '').strip()})

        with transaction.atomic():
            contract = Contract.objects.create(
                tenant_id=tenant_id,
                created_by=user_id,
                title=(title or os.path.splitext(safe)[0]),
                status='draft',
                contract_type=inferred_type,
                counterparty=counterparty,
                form_inputs=structured_inputs,
                user_instructions=user_instructions,
                clauses=clauses_payload,
                metadata={
                    'template_filename': safe,
                    'template_source': 'template_files_db',
                    'raw_text': raw_text,
                    'rendered_text': rendered,
                },
            )

            WorkflowLog.objects.create(
                contract=contract,
                action='created',
                performed_by=user_id,
                comment=f'Created from template file {safe}',
            )

        return Response(
            {
                'contract': ContractDetailSerializer(contract).data,
                'rendered_text': rendered,
                'raw_text': raw_text,
            },
            status=status.HTTP_201_CREATED,
        )

    def create(self, request, *args, **kwargs):
        """Create contract.

        Supports multipart form uploads with a `file` field. If a file is provided,
        it is uploaded to Cloudflare R2 and stored as the initial ContractVersion.
        """
        uploaded_file = request.FILES.get('file')

        allowed_fields = [
            'title',
            'contract_type',
            'status',
            'value',
            'counterparty',
            'start_date',
            'end_date',
        ]
        payload = {k: request.data.get(k) for k in allowed_fields if k in request.data}

        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            contract = serializer.save(
                tenant_id=request.user.tenant_id,
                created_by=request.user.user_id,
            )

            if uploaded_file:
                file_bytes = uploaded_file.read()
                try:
                    uploaded_file.seek(0)
                except Exception:
                    pass

                file_hash = hashlib.sha256(file_bytes).hexdigest()
                file_size = getattr(uploaded_file, 'size', None)

                r2_service = R2StorageService()
                r2_key = r2_service.upload_file(uploaded_file, request.user.tenant_id, uploaded_file.name)

                template_id = contract.template_id or uuid.uuid4()
                template_version = getattr(contract.template, 'version', None) or 1

                ContractVersion.objects.create(
                    contract=contract,
                    version_number=1,
                    r2_key=r2_key,
                    template_id=template_id,
                    template_version=template_version,
                    change_summary='Initial document upload',
                    created_by=request.user.user_id,
                    file_size=file_size,
                    file_hash=file_hash,
                )

                contract.current_version = 1
                contract.save(update_fields=['current_version'])

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['get'], url_path='download-url')
    def download_url(self, request, pk=None):
        """GET /contracts/{id}/download-url/

        Returns a presigned URL for the latest uploaded/generated document.
        """
        contract = self.get_object()
        try:
            latest_version = contract.versions.latest('version_number')
        except ContractVersion.DoesNotExist:
            return Response(
                {'error': 'No document available for this contract'},
                status=status.HTTP_404_NOT_FOUND
            )

        r2_service = R2StorageService()
        url = r2_service.generate_presigned_url(latest_version.r2_key)
        return Response({
            'contract_id': str(contract.id),
            'version_number': latest_version.version_number,
            'r2_key': latest_version.r2_key,
            'download_url': url,
        })
    
    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        """GET /contracts/{id}/history/
        
        Get contract change history from audit logs
        """
        from audit_logs.models import AuditLogModel
        
        contract = self.get_object()
        history = AuditLogModel.objects.filter(
            entity_id=str(contract.id),
            entity_type='contract'
        ).order_by('-created_at')[:50]
        
        result = []
        for log in history:
            result.append({
                'id': str(log.id),
                'entity_type': log.entity_type,
                'entity_id': log.entity_id,
                'action': log.action,
                'performed_by': str(log.performed_by),
                'performer_email': getattr(log, 'performer_email', 'Unknown'),
                'changes': log.changes or {},
                'created_at': log.created_at.isoformat()
            })
        
        return Response({'history': result})
    
    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request):
        """
        GET /contracts/statistics/
        Get contract statistics for dashboard
        
        Response:
        {
            "total": 127,
            "draft": 45,
            "pending": 30,
            "approved": 40,
            "rejected": 5,
            "executed": 7,
            "monthly_trends": [
                {"month": "Jan", "approved": 12, "rejected": 2},
                ...
            ]
        }
        """
        from django.db.models import Count, Q
        from datetime import timedelta
        from django.utils import timezone
        
        tenant_id = request.user.tenant_id
        queryset = Contract.objects.filter(tenant_id=tenant_id)
        
        # Get status counts
        stats = queryset.aggregate(
            total=Count('id'),
            draft=Count('id', filter=Q(status='draft')),
            pending=Count('id', filter=Q(status='pending')),
            approved=Count('id', filter=Q(status='approved')),
            rejected=Count('id', filter=Q(status='rejected')),
            executed=Count('id', filter=Q(status='executed'))
        )
        
        # Get monthly trends for last 6 months
        monthly_trends = []
        for i in range(5, -1, -1):
            month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30 * i)
            month_end = (month_start + timedelta(days=32)).replace(day=1)
            
            month_contracts = queryset.filter(
                created_at__gte=month_start,
                created_at__lt=month_end
            )
            
            approved_count = month_contracts.filter(status='approved').count()
            rejected_count = month_contracts.filter(status='rejected').count()
            
            monthly_trends.append({
                'month': month_start.strftime('%b'),
                'approved': approved_count,
                'rejected': rejected_count
            })
        
        return Response({
            **stats,
            'monthly_trends': monthly_trends
        })
    
    @action(detail=False, methods=['get'], url_path='recent')
    def recent(self, request):
        """
        GET /contracts/recent/?limit=10
        Get recent contracts
        """
        tenant_id = request.user.tenant_id
        limit = int(request.query_params.get('limit', 10))
        
        contracts = Contract.objects.filter(
            tenant_id=tenant_id
        ).order_by('-updated_at')[:limit]
        
        # Get user names from authentication app
        from authentication.models import User
        user_ids = list(set([str(c.created_by) for c in contracts]))
        users = User.objects.filter(user_id__in=user_ids)
        user_map = {str(u.user_id): f"{u.first_name} {u.last_name}".strip() or u.email for u in users}
        
        result = []
        for contract in contracts:
            data = ContractSerializer(contract).data
            data['created_by_name'] = user_map.get(str(contract.created_by), 'Unknown')
            result.append(data)
        
        return Response(result)
    
    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        """
        POST /contracts/generate/
        Generate a new contract from template with clause assembly
        
        Request:
        {
            "template_id": "uuid",
            "structured_inputs": {
                "counterparty": "Acme Corp",
                "value": 5000000,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31"
            },
            "user_instructions": "Make termination stricter",
            "title": "MSA with Acme Corp",
            "selected_clauses": ["CONF-001", "TERM-001"]  # Optional
        }
        
        Response:
        {
            "contract": {...},
            "version": {...},
            "mandatory_clauses": [
                {
                    "clause_id": "CONF-001",
                    "message": "Confidentiality required for all contracts",
                    "rule_name": "Mandatory Confidentiality"
                }
            ],
            "clause_suggestions": {
                "CONF-001": [
                    {
                        "clause_id": "CONF-002",
                        "rationale": "Higher value contracts",
                        "confidence": 0.89
                    }
                ]
            },
            "validation_errors": []
        }
        """
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        
        template_id = request.data.get('template_id')
        structured_inputs = request.data.get('structured_inputs', {})
        user_instructions = request.data.get('user_instructions')
        title = request.data.get('title')
        selected_clauses = request.data.get('selected_clauses')
        
        if not template_id:
            return Response(
                {'error': 'template_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            generator = ContractGenerator(user_id, tenant_id)
            
            # Create contract
            contract = generator.generate_from_template(
                template_id=uuid.UUID(template_id),
                structured_inputs=structured_inputs,
                user_instructions=user_instructions,
                title=title
            )
            
            # Create first version with clause assembly
            version = generator.create_version(
                contract=contract,
                selected_clauses=selected_clauses,
                change_summary='Initial draft'
            )
            
            # Get context for suggestions and validation
            context = {
                'contract_type': contract.contract_type,
                'contract_value': float(contract.value) if contract.value else 0,
                **structured_inputs
            }
            
            rule_engine = RuleEngine()
            mandatory_clauses = rule_engine.get_mandatory_clauses(
                tenant_id, contract.contract_type, context
            )
            
            # Get all clause suggestions
            clause_suggestions = {}
            for clause_id in (selected_clauses or []):
                suggestions = rule_engine.get_clause_suggestions(
                    tenant_id, contract.contract_type, context, clause_id
                )
                if suggestions:
                    clause_suggestions[clause_id] = suggestions
            
            return Response({
                'contract': ContractDetailSerializer(contract).data,
                'version': ContractVersionSerializer(version).data,
                'mandatory_clauses': mandatory_clauses,
                'clause_suggestions': clause_suggestions,
                'validation_errors': []
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['get'], url_path='versions')
    def versions(self, request, pk=None):
        """
        GET /contracts/{id}/versions/
        Get all versions of a contract with clause provenance
        """
        contract = self.get_object()
        versions = contract.versions.all()
        serializer = ContractVersionSerializer(versions, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='create-version')
    def create_version(self, request, pk=None):
        """
        POST /contracts/{id}/create-version/
        Create a new version with different clauses
        
        Request:
        {
            "selected_clauses": ["CONF-001", "TERM-001", "LIAB-002"],
            "change_summary": "Updated liability clause to stricter version"
        }
        """
        contract = self.get_object()
        
        selected_clauses = request.data.get('selected_clauses', [])
        change_summary = request.data.get('change_summary', '')
        
        if not selected_clauses:
            return Response(
                {'error': 'selected_clauses is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            tenant_id = request.user.tenant_id
            user_id = request.user.user_id
            
            # Get latest version number
            latest_version = contract.versions.order_by('-version_number').first()
            version_number = (latest_version.version_number + 1) if latest_version else 1
            
            # Create version without requiring generator
            version = ContractVersion.objects.create(
                contract=contract,
                version_number=version_number,
                template_id=contract.template_id or uuid.uuid4(),
                template_version=1,
                change_summary=change_summary or f'Version {version_number}',
                created_by=user_id,
                file_size=0,
                file_hash='',
                r2_key=f'contracts/{contract.id}/v{version_number}.docx'
            )
            
            contract.current_version = version_number
            contract.save(update_fields=['current_version'])
            
            return Response(
                ContractVersionSerializer(version).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @action(detail=True, methods=['get'], url_path='versions/(?P<version_number>[0-9]+)/clauses')
    def version_clauses(self, request, pk=None, version_number=None):
        """
        GET /contracts/{id}/versions/{version_number}/clauses/
        Get all clauses with full provenance for a specific version
        
        Response:
        {
            "clauses": [
                {
                    "clause_id": "CONF-001",
                    "clause_version": 3,
                    "name": "Confidentiality Clause",
                    "content": "...",
                    "is_mandatory": true,
                    "position": 1,
                    "alternatives_suggested": [...],
                    "provenance": {
                        "source": "template",
                        "template_id": "uuid",
                        "template_version": 5,
                        "added_at": "2026-01-01T12:00:00Z"
                    }
                }
            ]
        }
        """
        contract = self.get_object()
        version = get_object_or_404(
            ContractVersion,
            contract=contract,
            version_number=version_number
        )
        
        clauses = version.clauses.all()
        
        data = []
        for cc in clauses:
            data.append({
                'clause_id': cc.clause_id,
                'clause_version': cc.clause_version,
                'name': cc.clause_name,
                'content': cc.clause_content,
                'is_mandatory': cc.is_mandatory,
                'position': cc.position,
                'alternatives_suggested': cc.alternatives_suggested,
                'provenance': {
                    'source': 'template',
                    'template_id': str(version.template_id),
                    'template_version': version.template_version,
                    'added_at': version.created_at.isoformat(),
                    'added_by': str(version.created_by)
                }
            })
        
        return Response({'clauses': data})
    
    @action(detail=False, methods=['post'], url_path='validate-clauses')
    def validate_clauses(self, request):
        """
        POST /contracts/validate-clauses/
        Validate a set of clauses against business rules
        
        Request:
        {
            "contract_type": "MSA",
            "contract_value": 5000000,
            "selected_clauses": ["CONF-001", "TERM-001"]
        }
        
        Response:
        {
            "is_valid": false,
            "errors": ["Mandatory clause missing: LIAB-001"],
            "warnings": [],
            "mandatory_clauses": [...]
        }
        """
        tenant_id = request.user.tenant_id
        
        contract_type = request.data.get('contract_type', '')
        context = request.data
        selected_clauses = request.data.get('selected_clauses', [])
        
        rule_engine = RuleEngine()
        
        is_valid, errors = rule_engine.validate_contract(
            tenant_id, contract_type, context, selected_clauses
        )
        
        mandatory = rule_engine.get_mandatory_clauses(
            tenant_id, contract_type, context
        )
        
        return Response({
            'is_valid': is_valid,
            'errors': errors,
            'warnings': [],
            'mandatory_clauses': mandatory
        })
    
    @action(detail=True, methods=['post'], url_path='approve')
    def approve(self, request, pk=None):
        """
        POST /contracts/{id}/approve
        Approve a contract for download/send/sign
        """
        contract = self.get_object()
        serializer = ContractApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        if not serializer.validated_data['reviewed']:
            return Response(
                {'error': 'You must review the contract before approving'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if contract.is_approved:
            return Response(
                {'error': 'Contract is already approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Approve the contract
        contract.is_approved = True
        contract.approved_by = request.user.user_id
        contract.approved_at = timezone.now()
        contract.save(update_fields=['is_approved', 'approved_by', 'approved_at'])
        
        # Create audit log entry with correct fields
        from audit_logs.models import AuditLogModel
        AuditLogModel.objects.create(
            tenant_id=request.user.tenant_id,
            user_id=request.user.user_id,
            entity_type='contract',
            entity_id=contract.id,
            action='update',
            changes={'is_approved': True, 'comments': serializer.validated_data.get('comments', '')}
        )
        
        return Response(
            ContractDetailSerializer(contract).data,
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['get'], url_path='download')
    def download(self, request, pk=None):
        """
        GET /contracts/{id}/download
        Download the latest version (requires approval)
        """
        contract = self.get_object()
        
        if not contract.is_approved:
            return Response(
                {'error': 'Contract must be approved before download'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        latest_version = contract.versions.first()
        if not latest_version:
            return Response(
                {'error': 'No version available'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # In production: Generate presigned R2 URL and redirect
        download_url = f"https://r2.example.com/{latest_version.r2_key}"
        
        return Response({
            'download_url': download_url,
            'filename': f"{contract.title}_v{latest_version.version_number}.docx",
            'version': latest_version.version_number
        })
    
    @action(detail=True, methods=['get'], url_path='versions/(?P<version_number>[0-9]+)')
    def version_detail(self, request, pk=None, version_number=None):
        """
        GET /contracts/{id}/versions/{version_number}
        Get specific version details
        """
        contract = self.get_object()
        version = get_object_or_404(
            ContractVersion,
            contract=contract,
            version_number=version_number
        )
        serializer = ContractVersionSerializer(version)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='new-version')
    def new_version(self, request, pk=None):
        """
        POST /contracts/{id}/new-version
        Create a new version with changes
        """
        contract = self.get_object()
        
        changes = request.data.get('changes', {})
        change_summary = request.data.get('change_summary', 'Updated contract')
        
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        
        service = ContractGenerationService(tenant_id, user_id)
        
        try:
            new_version = service.create_new_version(
                contract_id=str(contract.id),
                changes=changes,
                change_summary=change_summary
            )
            
            return Response(
                ContractVersionSerializer(new_version).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'error': 'Failed to create new version', 'details': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['post'], url_path='clone')
    def clone(self, request, pk=None):
        """
        POST /contracts/{id}/clone/
        Clone a contract to create a new copy
        
        Request:
        {
            "title": "New Contract Title"
        }
        """
        contract = self.get_object()
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        
        new_title = request.data.get('title', f"{contract.title} (Copy)")
        
        try:
            cloned_contract = Contract.objects.create(
                tenant_id=tenant_id,
                title=new_title,
                contract_type=contract.contract_type,
                status='draft',
                value=contract.value,
                counterparty=contract.counterparty,
                start_date=contract.start_date,
                end_date=contract.end_date,
                created_by=user_id,
                template_id=contract.template_id
            )
            
            # Clone latest version if exists
            latest_version = contract.versions.order_by('-version_number').first()
            if latest_version:
                ContractVersion.objects.create(
                    contract=cloned_contract,
                    version_number=1,
                    r2_key=latest_version.r2_key,
                    template_id=latest_version.template_id,
                    template_version=latest_version.template_version,
                    change_summary=f'Cloned from {contract.title}',
                    created_by=user_id,
                    file_size=latest_version.file_size,
                    file_hash=latest_version.file_hash
                )
                cloned_contract.current_version = 1
                cloned_contract.save(update_fields=['current_version'])
            
            return Response(
                ContractSerializer(cloned_contract).data,
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class GenerationJobViewSet(viewsets.ModelViewSet):
    """
    API endpoint for tracking async generation jobs
    """
    permission_classes = [IsAuthenticated]
    serializer_class = GenerationJobSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        # Filter by user's contracts
        user_id = self.request.user.user_id
        return GenerationJob.objects.filter(
            contract__tenant_id=tenant_id,
            contract__created_by=user_id
        )



# ========== MANUAL EDITING VIEWS ==========

class ContractEditingTemplateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for managing contract editing templates
    Users can browse available templates for manual editing
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContractEditingTemplateSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        return ContractEditingTemplate.objects.filter(
            tenant_id=tenant_id,
            is_active=True
        ).order_by('-created_at')
    
    @action(detail=False, methods=['get'])
    def by_category(self, request):
        """
        GET /manual-templates/by-category/?category=nda
        Get templates filtered by category
        """
        category = request.query_params.get('category')
        tenant_id = request.user.tenant_id
        
        if not category:
            return Response(
                {'error': 'category parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        templates = ContractEditingTemplate.objects.filter(
            tenant_id=tenant_id,
            category=category,
            is_active=True
        )
        
        serializer = self.get_serializer(templates, many=True)
        return Response({
            'category': category,
            'count': len(templates),
            'templates': serializer.data
        })
    
    @action(detail=False, methods=['get'])
    def by_type(self, request):
        """
        GET /manual-templates/by-type/?contract_type=nda
        Get templates filtered by contract type
        """
        contract_type = request.query_params.get('contract_type')
        tenant_id = request.user.tenant_id
        
        if not contract_type:
            return Response(
                {'error': 'contract_type parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        templates = ContractEditingTemplate.objects.filter(
            tenant_id=tenant_id,
            contract_type=contract_type.upper(),
            is_active=True
        )
        
        serializer = self.get_serializer(templates, many=True)
        return Response({
            'contract_type': contract_type,
            'count': len(templates),
            'templates': serializer.data
        })


class ContractEditingSessionViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing contract editing sessions
    Complete workflow: select template -> fill form -> select clauses -> preview -> finalize
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ContractEditingSessionSerializer
    
    def get_queryset(self):
        tenant_id = self.request.user.tenant_id
        user_id = self.request.user.user_id
        return ContractEditingSession.objects.filter(
            tenant_id=tenant_id,
            user_id=user_id
        ).order_by('-updated_at')
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        POST /manual-sessions/
        Create a new contract editing session
        
        Request body:
        {
            "template_id": "uuid",
            "initial_form_data": {...}
        }
        """
        tenant_id = request.user.tenant_id
        user_id = request.user.user_id
        template_id = request.data.get('template_id')
        initial_form_data = request.data.get('initial_form_data', {})
        
        # Validate template exists and is accessible
        try:
            template = ContractEditingTemplate.objects.get(
                id=template_id,
                tenant_id=tenant_id,
                is_active=True
            )
        except ContractEditingTemplate.DoesNotExist:
            return Response(
                {'error': 'Template not found or not accessible'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Create session
        session = ContractEditingSession.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            template_id=template_id,
            status='draft',
            form_data=initial_form_data
        )
        
        # Log first step
        ContractEditingStep.objects.create(
            session=session,
            step_type='template_selection',
            step_data={
                'template_id': str(template_id),
                'template_name': template.name,
                'contract_type': template.contract_type
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'])
    def detail(self, request, pk=None):
        """
        GET /manual-sessions/{id}/detail/
        Get detailed session information with all steps and edits
        """
        session = self.get_object()
        serializer = ContractEditingSessionDetailSerializer(session)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def fill_form(self, request, pk=None):
        """
        POST /manual-sessions/{id}/fill-form/
        Fill form fields for the contract
        
        Request body:
        {
            "form_data": {
                "party_a_name": "ACME Corp",
                "party_b_name": "Beta LLC",
                "contract_value": 50000,
                "effective_date": "2026-01-20"
            }
        }
        """
        session = self.get_object()
        form_data = request.data.get('form_data', {})
        
        if not form_data:
            return Response(
                {'error': 'form_data is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get template for validation
        template = ContractEditingTemplate.objects.get(id=session.template_id)
        
        # Validate all required fields are present
        validation_errors = {}
        for field_name, field_config in template.form_fields.items():
            if field_config.get('required') and field_name not in form_data:
                validation_errors[field_name] = 'This field is required'
        
        if validation_errors:
            return Response(
                {'errors': validation_errors},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update session
        session.form_data = form_data
        session.status = 'in_progress'
        session.last_saved_at = timezone.now()
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='form_fill',
            step_data=form_data
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Form data saved successfully',
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def select_clauses(self, request, pk=None):
        """
        POST /manual-sessions/{id}/select-clauses/
        Select clauses for the contract
        
        Request body:
        {
            "clause_ids": ["CONF-001", "TERM-001", "LIAB-001"],
            "custom_clause_content": {
                "CUSTOM-001": "Custom clause text here..."
            }
        }
        """
        session = self.get_object()
        clause_ids = request.data.get('clause_ids', [])
        custom_clauses = request.data.get('custom_clause_content', {})
        
        if not clause_ids:
            return Response(
                {'error': 'At least one clause must be selected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate all clause IDs exist
        invalid_clauses = []
        valid_clauses = []
        for clause_id in clause_ids:
            try:
                Clause.objects.get(
                    clause_id=clause_id,
                    tenant_id=request.user.tenant_id,
                    status='published'
                )
                valid_clauses.append(clause_id)
            except Clause.DoesNotExist:
                invalid_clauses.append(clause_id)
        
        if invalid_clauses:
            return Response(
                {
                    'error': 'Some clauses not found',
                    'invalid_clauses': invalid_clauses,
                    'valid_clauses': valid_clauses
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update session
        session.selected_clause_ids = clause_ids
        session.custom_clauses = custom_clauses
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='clause_selection',
            step_data={
                'clause_ids': clause_ids,
                'custom_clauses': bool(custom_clauses)
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Clauses selected successfully',
                'selected_count': len(valid_clauses),
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def define_constraints(self, request, pk=None):
        """
        POST /manual-sessions/{id}/define-constraints/
        Define constraints/versions for the contract
        
        Request body:
        {
            "constraints": {
                "payment_terms": "Net 30",
                "jurisdiction": "California",
                "confidentiality_period": "5 years"
            }
        }
        """
        session = self.get_object()
        constraints = request.data.get('constraints', {})
        
        if not constraints:
            return Response(
                {'error': 'At least one constraint must be defined'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update session
        session.constraints_config = constraints
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='constraint_definition',
            step_data=constraints
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Constraints defined successfully',
                'constraints_count': len(constraints),
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def generate_preview(self, request, pk=None):
        """
        POST /manual-sessions/{id}/generate-preview/
        Generate HTML and text preview of the contract
        
        Request body:
        {
            "form_data": {...},
            "selected_clause_ids": [...],
            "constraints_config": {...}
        }
        """
        session = self.get_object()
        
        form_data = request.data.get('form_data', session.form_data)
        clause_ids = request.data.get('selected_clause_ids', session.selected_clause_ids)
        constraints = request.data.get('constraints_config', session.constraints_config)
        
        if not form_data or not clause_ids:
            return Response(
                {
                    'error': 'Form data and clause IDs are required',
                    'current_form_data': bool(session.form_data),
                    'current_clause_ids': bool(session.selected_clause_ids)
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get template
        template = ContractEditingTemplate.objects.get(id=session.template_id)
        
        # Build contract content
        contract_html = self._build_contract_html(
            template, form_data, clause_ids, constraints, request.user.tenant_id
        )
        contract_text = self._build_contract_text(
            template, form_data, clause_ids, constraints, request.user.tenant_id
        )
        
        # Save preview
        preview, created = ContractPreview.objects.update_or_create(
            session=session,
            defaults={
                'preview_html': contract_html,
                'preview_text': contract_text,
                'form_data_snapshot': form_data,
                'clauses_snapshot': clause_ids,
                'constraints_snapshot': constraints
            }
        )
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='preview_generated',
            step_data={
                'preview_id': str(preview.id),
                'form_fields_count': len(form_data),
                'clauses_count': len(clause_ids)
            }
        )
        
        serializer = ContractPreviewSerializer(preview)
        return Response(
            {
                'message': 'Preview generated successfully',
                'preview': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def edit_after_preview(self, request, pk=None):
        """
        POST /manual-sessions/{id}/edit-after-preview/
        Make edits after reviewing the preview
        
        Request body:
        {
            "edit_type": "form_field",
            "field_name": "party_a_name",
            "old_value": "ACME Corp",
            "new_value": "ACME Corporation",
            "edit_reason": "Corrected company name spelling"
        }
        """
        session = self.get_object()
        
        edit_type = request.data.get('edit_type')
        field_name = request.data.get('field_name')
        old_value = request.data.get('old_value')
        new_value = request.data.get('new_value')
        edit_reason = request.data.get('edit_reason', '')
        
        if not edit_type:
            return Response(
                {'error': 'edit_type is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Apply edit based on type
        if edit_type == 'form_field':
            if field_name not in session.form_data:
                return Response(
                    {'error': f'Field {field_name} not found in form data'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            session.form_data[field_name] = new_value
        
        elif edit_type == 'clause_added':
            clause_id = request.data.get('clause_id')
            if clause_id not in session.selected_clause_ids:
                session.selected_clause_ids.append(clause_id)
        
        elif edit_type == 'clause_removed':
            clause_id = request.data.get('clause_id')
            if clause_id in session.selected_clause_ids:
                session.selected_clause_ids.remove(clause_id)
        
        elif edit_type == 'clause_content_edited':
            clause_id = request.data.get('clause_id')
            custom_content = request.data.get('custom_content')
            session.custom_clauses[clause_id] = custom_content
        
        session.save()
        
        # Log edit
        ContractEdits.objects.create(
            session=session,
            edit_type=edit_type,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            edit_reason=edit_reason
        )
        
        # Log as step
        ContractEditingStep.objects.create(
            session=session,
            step_type='field_edited',
            step_data={
                'edit_type': edit_type,
                'field_name': field_name,
                'edit_reason': edit_reason
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Edit applied successfully',
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['post'])
    def finalize_and_create(self, request, pk=None):
        """
        POST /manual-sessions/{id}/finalize-and-create/
        Finalize editing and create actual contract
        
        Request body:
        {
            "contract_title": "NDA with ACME Corp",
            "contract_description": "Non-disclosure agreement",
            "contract_value": 50000,
            "effective_date": "2026-01-20",
            "expiration_date": "2027-01-20",
            "additional_metadata": {...}
        }
        """
        session = self.get_object()
        
        # Validate session has required data
        if not session.form_data:
            return Response(
                {'error': 'Form data is required. Please fill the form first.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not session.selected_clause_ids:
            return Response(
                {'error': 'At least one clause must be selected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get template to use default clauses if needed
        template = ContractEditingTemplate.objects.get(id=session.template_id)
        
        # Use selected clauses or fall back to template defaults
        final_clause_ids = session.selected_clause_ids or template.mandatory_clauses
        final_constraints = session.constraints_config or {}
        
        # Create contract
        contract_data = {
            'title': request.data.get('contract_title', 'Untitled Contract'),
            'contract_type': template.contract_type,
            'status': 'draft',
            'generation_mode': 'manual',
            'form_inputs': session.form_data,
            'metadata': {
                'editing_session_id': str(session.id),
                'constraints': final_constraints,
                'custom_clauses': session.custom_clauses
            }
        }
        
        try:
            contract = Contract.objects.create(
                tenant_id=request.user.tenant_id,
                created_by=request.user.user_id,
                **contract_data
            )
            
            # Create contract version with selected clauses
            version = ContractVersion.objects.create(
                contract=contract,
                version_number=1,
                template_id=session.template_id,
                template_version=template.version,
                change_summary='Created from manual editing session',
                created_by=request.user.user_id,
                r2_key=f'contracts/{request.user.tenant_id}/{contract.id}/v1.docx'
            )
            
            # Update session status
            session.status = 'completed'
            session.save()
            
            # Log final step
            ContractEditingStep.objects.create(
                session=session,
                step_type='saved',
                step_data={
                    'contract_id': str(contract.id),
                    'version_id': str(version.id),
                    'status': 'completed'
                }
            )
            
            return Response(
                {
                    'message': 'Contract created successfully',
                    'contract': {
                        'id': str(contract.id),
                        'title': contract.title,
                        'status': contract.status,
                        'contract_type': contract.contract_type,
                        'created_at': contract.created_at.isoformat(),
                        'version': {
                            'id': str(version.id),
                            'version_number': version.version_number,
                            'created_at': version.created_at.isoformat()
                        }
                    },
                    'session': self.get_serializer(session).data
                },
                status=status.HTTP_201_CREATED
            )
        
        except Exception as e:
            return Response(
                {'error': f'Failed to create contract: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def _build_contract_html(self, template, form_data, clause_ids, constraints, tenant_id):
        """
        Build professional HTML preview of contract
        """
        html_content = f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Arial', sans-serif;
                    margin: 40px;
                    line-height: 1.6;
                    color: #333;
                }}
                .contract-header {{
                    text-align: center;
                    margin-bottom: 30px;
                    border-bottom: 2px solid #000;
                    padding-bottom: 20px;
                }}
                h1 {{
                    margin: 0;
                    font-size: 24px;
                    text-transform: uppercase;
                }}
                .contract-date {{
                    margin-top: 10px;
                    font-style: italic;
                }}
                .section {{
                    margin: 30px 0;
                    page-break-inside: avoid;
                }}
                .section-title {{
                    font-weight: bold;
                    font-size: 14px;
                    margin-top: 20px;
                    margin-bottom: 10px;
                    text-transform: uppercase;
                }}
                .clause {{
                    margin: 15px 0;
                    padding: 10px;
                    border-left: 3px solid #007bff;
                    background-color: #f8f9fa;
                }}
                .form-field {{
                    margin: 8px 0;
                }}
                .form-label {{
                    font-weight: bold;
                    display: inline-block;
                    width: 200px;
                }}
                .constraint {{
                    background-color: #fff3cd;
                    padding: 8px;
                    margin: 5px 0;
                    border-radius: 3px;
                }}
                @media print {{
                    body {{ margin: 20px; }}
                }}
            </style>
        </head>
        <body>
            <div class="contract-header">
                <h1>{template.name}</h1>
                <div class="contract-date">Date: {datetime.now().strftime('%B %d, %Y')}</div>
            </div>
            
            <div class="section">
                <div class="section-title">Contract Information</div>
        """
        
        # Add form data
        for field_name, field_value in form_data.items():
            html_content += f"""
                <div class="form-field">
                    <span class="form-label">{field_name.replace('_', ' ').title()}:</span>
                    <span>{field_value}</span>
                </div>
            """
        
        # Add constraints
        if constraints:
            html_content += '<div class="section-title">Constraints & Versions</div>'
            for constraint_name, constraint_value in constraints.items():
                html_content += f"""
                    <div class="constraint">
                        <strong>{constraint_name.replace('_', ' ').title()}:</strong> {constraint_value}
                    </div>
                """
        
        # Add clauses
        html_content += '<div class="section-title">Contract Clauses</div>'
        
        clauses = Clause.objects.filter(
            clause_id__in=clause_ids,
            tenant_id=tenant_id,
            status='published'
        )
        
        for idx, clause in enumerate(clauses, 1):
            html_content += f"""
                <div class="clause">
                    <strong>Clause {idx}: {clause.name}</strong><br>
                    {clause.content[:200]}...
                </div>
            """
        
        html_content += """
            </div>
        </body>
        </html>
        """
        
        return html_content
    
    def _build_contract_text(self, template, form_data, clause_ids, constraints, tenant_id):
        """
        Build plain text preview of contract
        """
        text_content = f"""
{template.name.upper()}

Date: {datetime.now().strftime('%B %d, %Y')}

{'='*60}

CONTRACT INFORMATION
{'='*60}

"""
        
        # Add form data
        for field_name, field_value in form_data.items():
            text_content += f"{field_name.replace('_', ' ').title()}: {field_value}\n"
        
        # Add constraints
        if constraints:
            text_content += f"\n{'='*60}\nCONSTRAINTS & VERSIONS\n{'='*60}\n\n"
            for constraint_name, constraint_value in constraints.items():
                text_content += f"{constraint_name.replace('_', ' ').title()}: {constraint_value}\n"
        
        # Add clauses
        text_content += f"\n{'='*60}\nCONTRACT CLAUSES\n{'='*60}\n\n"
        
        clauses = Clause.objects.filter(
            clause_id__in=clause_ids,
            tenant_id=tenant_id,
            status='published'
        )
        
        for idx, clause in enumerate(clauses, 1):
            text_content += f"\nClause {idx}: {clause.name}\n"
            text_content += f"{'-'*40}\n"
            text_content += f"{clause.content[:300]}...\n"
        
        return text_content
    
    @action(detail=True, methods=['post'])
    def save_draft(self, request, pk=None):
        """
        POST /manual-sessions/{id}/save-draft/
        Save the current state as draft without finalizing
        """
        session = self.get_object()
        
        session.last_saved_at = timezone.now()
        session.save()
        
        # Log step
        ContractEditingStep.objects.create(
            session=session,
            step_type='saved',
            step_data={
                'auto_saved': False,
                'timestamp': timezone.now().isoformat()
            }
        )
        
        serializer = self.get_serializer(session)
        return Response(
            {
                'message': 'Draft saved successfully',
                'last_saved_at': session.last_saved_at.isoformat(),
                'session': serializer.data
            },
            status=status.HTTP_200_OK
        )
    
    @action(detail=True, methods=['delete'])
    def discard(self, request, pk=None):
        """
        DELETE /manual-sessions/{id}/discard/
        Discard the editing session
        """
        session = self.get_object()
        session.status = 'abandoned'
        session.save()
        
        return Response(
            {'message': 'Session discarded successfully'},
            status=status.HTTP_200_OK
        )



# ========== R2 VIEWS ==========

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_document(request):
    """
    POST /api/contracts/upload-document/
    
    Upload any document to Cloudflare R2 and get a downloadable link.
    
    Request (multipart/form-data):
        - file: The document file to upload (required)
        - filename: Optional custom filename
    
    Response:
    {
        "success": true,
        "file_id": "uuid",
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "original_filename": "document.pdf",
        "file_size": 123456,
        "uploaded_at": "2026-01-20T12:00:00Z"
    }
    """
    uploaded_file = request.FILES.get('file')
    
    if not uploaded_file:
        return Response(
            {
                'success': False,
                'error': 'No file provided',
                'message': 'Please provide a file in the request'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Get tenant ID from authenticated user
        tenant_id = str(request.user.tenant_id)
        
        # Get custom filename if provided, otherwise use original
        custom_filename = request.data.get('filename') or uploaded_file.name
        
        # Upload to R2
        r2_service = R2StorageService()
        r2_key = r2_service.upload_file(uploaded_file, tenant_id, custom_filename)
        
        # Generate download URL
        download_url = r2_service.generate_presigned_url(r2_key, expiration=3600)  # 1 hour
        
        # Get file size
        file_size = uploaded_file.size
        
        return Response({
            'success': True,
            'file_id': str(uuid.uuid4()),
            'r2_key': r2_key,
            'download_url': download_url,
            'original_filename': uploaded_file.name,
            'file_size': file_size,
            'uploaded_at': timezone.now().isoformat(),
            'message': 'File uploaded successfully to Cloudflare R2'
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response(
            {
                'success': False,
                'error': str(e),
                'message': 'Failed to upload file to Cloudflare R2'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_contract_document(request):
    """
    POST /api/contracts/upload-contract-document/
    
    Upload a contract document to Cloudflare R2 and optionally create a Contract record.
    
    Request (multipart/form-data):
        - file: The contract PDF/document to upload (required)
        - title: Contract title (optional)
        - contract_type: Type of contract (optional)
        - counterparty: Counterparty name (optional)
        - create_contract: Boolean - whether to create a Contract record (default: false)
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",  // Only if create_contract=true
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "original_filename": "contract.pdf",
        "file_size": 123456,
        "uploaded_at": "2026-01-20T12:00:00Z"
    }
    """
    uploaded_file = request.FILES.get('file')
    
    if not uploaded_file:
        return Response(
            {
                'success': False,
                'error': 'No file provided',
                'message': 'Please provide a file in the request'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Get tenant and user info
        tenant_id = str(request.user.tenant_id)
        user_id = str(request.user.user_id)
        
        # Get optional parameters
        title = request.data.get('title') or f'Uploaded Contract - {timezone.now().strftime("%Y-%m-%d")}'
        contract_type = request.data.get('contract_type')
        counterparty = request.data.get('counterparty')
        create_contract = request.data.get('create_contract', 'false').lower() in ['true', '1', 'yes']
        
        # Upload to R2
        r2_service = R2StorageService()
        r2_key = r2_service.upload_file(uploaded_file, tenant_id, uploaded_file.name)
        
        # Generate download URL
        download_url = r2_service.generate_presigned_url(r2_key, expiration=86400)  # 24 hours
        
        # Get file info
        file_size = uploaded_file.size
        file_bytes = uploaded_file.read()
        uploaded_file.seek(0)  # Reset for potential re-read
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        
        response_data = {
            'success': True,
            'r2_key': r2_key,
            'download_url': download_url,
            'original_filename': uploaded_file.name,
            'file_size': file_size,
            'uploaded_at': timezone.now().isoformat(),
            'message': 'Contract uploaded successfully to Cloudflare R2'
        }
        
        # Optionally create Contract record
        if create_contract:
            contract = Contract.objects.create(
                tenant_id=uuid.UUID(tenant_id),
                title=title,
                contract_type=contract_type or 'other',
                counterparty=counterparty,
                status='draft',
                created_by=uuid.UUID(user_id),
                document_r2_key=r2_key,
            )
            
            # Create first version
            ContractVersion.objects.create(
                contract=contract,
                version_number=1,
                r2_key=r2_key,
                template_id=uuid.uuid4(),  # Placeholder
                template_version=1,
                change_summary='Initial upload',
                created_by=uuid.UUID(user_id),
                file_size=file_size,
                file_hash=file_hash,
            )
            
            response_data['contract_id'] = str(contract.id)
            response_data['contract_title'] = contract.title
            response_data['contract_status'] = contract.status
        
        return Response(response_data, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        import traceback
        return Response(
            {
                'success': False,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'message': 'Failed to upload contract document'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_document_download_url(request):
    """
    GET /api/contracts/document-download-url/?r2_key=<r2_key>
    
    Get a downloadable link for a document stored in Cloudflare R2.
    
    Query Parameters:
        - r2_key: The R2 key of the document
        - expiration: Optional expiration time in seconds (default: 3600)
    
    Response:
    {
        "success": true,
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "expiration_seconds": 3600
    }
    """
    r2_key = request.query_params.get('r2_key')
    
    if not r2_key:
        return Response(
            {
                'success': False,
                'error': 'r2_key parameter is required',
                'message': 'Please provide an r2_key query parameter'
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        # Get expiration time (default: 1 hour)
        expiration = int(request.query_params.get('expiration', 3600))
        
        # Generate download URL
        r2_service = R2StorageService()
        download_url = r2_service.generate_presigned_url(r2_key, expiration=expiration)
        
        return Response({
            'success': True,
            'r2_key': r2_key,
            'download_url': download_url,
            'expiration_seconds': expiration,
            'expires_at': timezone.now().isoformat()
        })
        
    except Exception as e:
        return Response(
            {
                'success': False,
                'error': str(e),
                'message': 'Failed to generate download URL'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_contract_download_url(request, contract_id):
    """
    GET /api/contracts/{contract_id}/download-url/
    
    Get a downloadable link for a specific contract.
    
    Path Parameters:
        - contract_id: UUID of the contract
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "contract_title": "My Contract",
        "version_number": 1,
        "r2_key": "tenant_id/contracts/uuid.pdf",
        "download_url": "https://...",
        "file_size": 123456
    }
    """
    try:
        # Get contract
        contract = Contract.objects.get(
            id=contract_id,
            tenant_id=request.user.tenant_id
        )
        
        # Get latest version
        try:
            latest_version = contract.versions.latest('version_number')
            r2_key = latest_version.r2_key
            version_number = latest_version.version_number
            file_size = latest_version.file_size
        except ContractVersion.DoesNotExist:
            # Fallback to document_r2_key if no versions exist
            if contract.document_r2_key:
                r2_key = contract.document_r2_key
                version_number = contract.current_version
                file_size = None
            else:
                return Response(
                    {
                        'success': False,
                        'error': 'No document available for this contract',
                        'message': 'This contract does not have an uploaded document'
                    },
                    status=status.HTTP_404_NOT_FOUND
                )
        
        # Generate download URL
        r2_service = R2StorageService()
        download_url = r2_service.generate_presigned_url(r2_key, expiration=3600)
        
        return Response({
            'success': True,
            'contract_id': str(contract.id),
            'contract_title': contract.title,
            'version_number': version_number,
            'r2_key': r2_key,
            'download_url': download_url,
            'file_size': file_size
        })
        
    except Contract.DoesNotExist:
        return Response(
            {
                'success': False,
                'error': 'Contract not found',
                'message': f'No contract found with ID {contract_id}'
            },
            status=status.HTTP_404_NOT_FOUND
        )
    except Exception as e:
        return Response(
            {
                'success': False,
                'error': str(e),
                'message': 'Failed to get contract download URL'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )



# ========== HEALTH VIEWS ==========

class HealthCheckView(APIView):
    """
    GET /api/v1/health/ - Health check endpoint
    """
    permission_classes = [AllowAny]
    
    def get(self, request):
        """
        Basic health check
        """
        try:
            # Check database connection
            connection.ensure_connection()
            db_status = 'healthy'
        except Exception:
            db_status = 'unhealthy'
        
        return Response({
            'status': 'ok',
            'database': db_status,
            'service': 'CLM Backend API'
        })



# ========== SIGNNOW VIEWS ==========

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_contract(request):
    """
    Upload contract PDF to SignNow
    
    Request:
    {
        "contract_id": "uuid",
        "document_name": "Optional name (defaults to contract title)"
    }
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "signnow_document_id": "doc_id",
        "status": "draft",
        "message": "Contract uploaded successfully"
    }
    """
    try:
        api_service = get_signnow_api_service()
        contract_id = request.data.get("contract_id")
        if not contract_id:
            return Response(
                {"error": "contract_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get contract
        contract = get_object_or_404(Contract, id=contract_id)
        
        # Check if already has e-signature record
        if hasattr(contract, 'esignature_contract'):
            return Response(
                {
                    "error": "Contract already uploaded for signing",
                    "signnow_document_id": contract.esignature_contract.signnow_document_id,
                    "status": contract.esignature_contract.status
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get contract PDF from storage
        if not contract.document_r2_key:
            return Response(
                {"error": "No document file found for contract"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            pdf_content = default_storage.open(contract.document_r2_key, 'rb').read()
        except Exception as e:
            logger.error(f"Failed to read contract file: {str(e)}")
            return Response(
                {"error": "Failed to read contract file"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Upload to SignNow
        document_name = request.data.get("document_name", contract.title)
        signnow_response = api_service.upload_document(pdf_content, document_name)
        
        signnow_document_id = signnow_response.get("id")
        if not signnow_document_id:
            return Response(
                {"error": "Failed to get document ID from SignNow"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Create e-signature contract record
        esig = ESignatureContract.objects.create(
            contract=contract,
            signnow_document_id=signnow_document_id,
            status='draft',
            original_r2_key=contract.document_r2_key,
            signing_request_data={"document_name": document_name}
        )
        
        # Log event
        SigningAuditLog.objects.create(
            esignature_contract=esig,
            event='invite_sent',
            message=f'Document uploaded to SignNow: {signnow_document_id}',
            signnow_response=signnow_response,
            new_status='draft'
        )
        
        logger.info(f"Contract {contract_id} uploaded to SignNow: {signnow_document_id}")
        
        return Response(
            {
                "success": True,
                "contract_id": str(contract_id),
                "signnow_document_id": signnow_document_id,
                "status": "draft",
                "message": "Contract uploaded successfully"
            },
            status=status.HTTP_201_CREATED
        )
        
    except Exception as e:
        logger.error(f"Upload failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. SEND FOR SIGNATURE
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_for_signature(request):
    """
    Send document for signatures
    
    Request:
    {
        "contract_id": "uuid",
        "signers": [
            {"email": "signer1@example.com", "name": "Signer 1"},
            {"email": "signer2@example.com", "name": "Signer 2"}
        ],
        "signing_order": "sequential" | "parallel",
        "expires_in_days": 30
    }
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "status": "sent",
        "signers_invited": 2,
        "message": "Invitations sent successfully"
    }
    """
    try:
        api_service = get_signnow_api_service()
        contract_id = request.data.get("contract_id")
        signers_data = request.data.get("signers", [])
        signing_order = request.data.get("signing_order", "sequential")
        expires_in_days = request.data.get("expires_in_days", 30)
        
        if not contract_id or not signers_data:
            return Response(
                {"error": "contract_id and signers are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        if esig.status != 'draft':
            return Response(
                {"error": f"Contract already sent (status: {esig.status})"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create SignNow invitations
        signnow_invites = api_service.create_invite(
            esig.signnow_document_id,
            signers_data,
            signing_order=signing_order
        )
        
        # Store signer information
        for idx, signer_info in enumerate(signers_data):
            Signer.objects.create(
                esignature_contract=esig,
                email=signer_info["email"],
                name=signer_info.get("name", ""),
                signing_order=idx + 1 if signing_order == "sequential" else 0,
                status='invited'
            )
        
        # Update e-signature contract
        esig.status = 'sent'
        esig.signing_order = signing_order
        esig.sent_at = timezone.now()
        esig.expires_at = timezone.now() + timedelta(days=expires_in_days)
        esig.signing_request_data = {
            "signers": signers_data,
            "signing_order": signing_order,
            "expires_in_days": expires_in_days
        }
        esig.save()
        
        # Log event
        SigningAuditLog.objects.create(
            esignature_contract=esig,
            event='invite_sent',
            message=f'Invitations sent to {len(signers_data)} signer(s)',
            signnow_response=signnow_invites,
            old_status='draft',
            new_status='sent'
        )
        
        logger.info(
            f"Sent contract {contract_id} for signature to {len(signers_data)} signers"
        )
        
        return Response(
            {
                "success": True,
                "contract_id": str(contract_id),
                "status": "sent",
                "signers_invited": len(signers_data),
                "expires_at": esig.expires_at.isoformat(),
                "message": "Invitations sent successfully"
            },
            status=status.HTTP_200_OK
        )
        
    except Exception as e:
        logger.error(f"Send for signature failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. GENERATE SIGNING URL
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_signing_url(request, contract_id):
    """
    Generate embedded signing URL for a specific signer
    
    Query Parameters:
    - signer_email: Email of signer (required)
    
    Response:
    {
        "success": true,
        "signing_url": "https://app.signnow.com/embedded-signing/...",
        "signer_email": "signer@example.com",
        "expires_at": "2026-02-18T..."
    }
    """
    try:
        api_service = get_signnow_api_service()
        signer_email = request.query_params.get("signer_email")
        if not signer_email:
            return Response(
                {"error": "signer_email query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        # Get signer
        signer = get_object_or_404(Signer, esignature_contract=esig, email=signer_email)
        
        # Generate signing link if not already cached
        if not signer.signing_url or (
            signer.signing_url_expires_at and 
            signer.signing_url_expires_at <= timezone.now()
        ):
            link_response = api_service.get_signing_link(
                esig.signnow_document_id,
                signer_email
            )
            
            signer.signing_url = link_response.get("signing_link")
            signer.signing_url_expires_at = timezone.now() + timedelta(hours=24)
            signer.save()
        
        return Response(
            {
                "success": True,
                "signing_url": signer.signing_url,
                "signer_email": signer_email,
                "expires_at": signer.signing_url_expires_at.isoformat() if signer.signing_url_expires_at else None,
                "message": "Signing URL generated successfully"
            },
            status=status.HTTP_200_OK
        )
        
    except Exception as e:
        logger.error(f"Failed to generate signing URL: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. CHECK STATUS (POLLING)
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_status(request, contract_id):
    """
    Get document status from database (and optionally update from SignNow)
    
    Response:
    {
        "success": true,
        "contract_id": "uuid",
        "status": "draft" | "sent" | "in_progress" | "completed" | "declined",
        "signers": [
            {
                "email": "signer1@example.com",
                "name": "John Doe",
                "status": "invited" | "viewed" | "in_progress" | "signed" | "declined",
                "signed_at": "2026-02-18T..." or null
            }
        ],
        "all_signed": false,
        "last_checked": "2026-02-18T..."
    }
    """
    try:
        api_service = get_signnow_api_service()
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        # Try to poll SignNow (optional - if fails, just return DB data)
        try:
            status_info = api_service.get_document_status(esig.signnow_document_id)
            
            # Update signer statuses from SignNow
            for signer_status in status_info["signers"]:
                try:
                    signer = Signer.objects.get(
                        esignature_contract=esig,
                        email=signer_status["email"]
                    )
                    
                    old_status = signer.status
                    new_status = signer_status["status"]
                    
                    signer.status = new_status
                    if new_status == "signed":
                        signer.has_signed = True
                        if not signer.signed_at:
                            signer.signed_at = timezone.now()
                    signer.save()
                    
                    # Log status change
                    if old_status != new_status:
                        SigningAuditLog.objects.create(
                            esignature_contract=esig,
                            signer=signer,
                            event='status_checked',
                            message=f'Status changed from {old_status} to {new_status}',
                            old_status=old_status,
                            new_status=new_status
                        )
                        
                except Signer.DoesNotExist:
                    pass
            
            # Update contract status if all signed
            old_contract_status = esig.status
            if status_info["is_completed"]:
                esig.status = "completed"
                if not esig.completed_at:
                    esig.completed_at = timezone.now()
            else:
                esig.status = status_info["status"]
            
            esig.last_status_check_at = timezone.now()
            esig.save()
            
            logger.info(f"Updated status from SignNow for contract {contract_id}: {esig.status}")
            
        except Exception as e:
            # SignNow API failed - just use database data
            logger.warning(f"Could not poll SignNow, using cached data: {str(e)}")
        
        # Build response from database
        signers_response = []
        for signer in esig.signers.all():
            signers_response.append({
                "email": signer.email,
                "name": signer.name,
                "status": signer.status,
                "signed_at": signer.signed_at.isoformat() if signer.signed_at else None,
                "has_signed": signer.has_signed
            })
        
        all_signed = all(s["has_signed"] for s in signers_response)
        
        logger.info(f"Returning status for contract {contract_id}: {esig.status}")
        
        return Response(
            {
                "success": True,
                "contract_id": str(contract_id),
                "status": esig.status,
                "signers": signers_response,
                "all_signed": all_signed,
                "last_checked": esig.last_status_check_at.isoformat() if esig.last_status_check_at else None
            },
            status=status.HTTP_200_OK
        )
        
    except Exception as e:
        logger.error(f"Status check failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. DOWNLOAD EXECUTED DOCUMENT
# ═══════════════════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_executed_document(request, contract_id):
    """
    Download signed PDF from SignNow and store immutable copy
    
    Response: PDF file download or JSON error
    {
        "error": "Contract not yet completed"
    }
    """
    try:
        api_service = get_signnow_api_service()
        # Get e-signature contract
        esig = get_object_or_404(ESignatureContract, contract_id=contract_id)
        
        # If status is not completed, re-poll first
        if esig.status != "completed":
            # Re-check status
            status_info = api_service.get_document_status(esig.signnow_document_id)
            if not status_info["is_completed"]:
                return Response(
                    {
                        "error": "Contract not yet completed by all signers",
                        "current_status": esig.status,
                        "message": "Please try again after all signers have completed"
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Update status
            esig.status = "completed"
            esig.completed_at = timezone.now()
            esig.save()
        
        # Download PDF from SignNow
        pdf_content = api_service.download_document(esig.signnow_document_id)
        
        if not pdf_content:
            return Response(
                {"error": "Failed to download signed document"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        # Store immutable copy if not already stored
        if not esig.executed_r2_key:
            from django.core.files.base import ContentFile

            r2_key = f"signed-contracts/{contract_id}_executed.pdf"
            default_storage.save(r2_key, ContentFile(pdf_content))
            esig.executed_r2_key = r2_key
            esig.save()
        
        # Log download
        SigningAuditLog.objects.create(
            esignature_contract=esig,
            event='document_downloaded',
            message='Executed document downloaded',
            new_status='completed'
        )
        
        logger.info(f"Downloaded executed document for contract {contract_id}")
        
        # Return PDF file
        from django.http import FileResponse
        from io import BytesIO
        filename = f"signed_contract_{contract_id}.pdf"
        pdf_file = BytesIO(pdf_content)
        pdf_file.seek(0)
        return FileResponse(
            pdf_file,
            as_attachment=True,
            filename=filename,
            content_type='application/pdf'
        )
        
    except Exception as e:
        logger.error(f"Download failed: {str(e)}", exc_info=True)
        return Response(
            {"error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

