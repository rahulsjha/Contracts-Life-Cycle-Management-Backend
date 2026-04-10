from rest_framework import serializers
from .models import (
    Contract, ContractVersion, ContractTemplate, Clause,
    GenerationJob, BusinessRule, ContractClause, WorkflowLog,
    ESignatureContract, Signer, SigningAuditLog,
    ContractEditingSession, ContractEditingTemplate, ContractPreview,
    ContractEditingStep, ContractEdits, ContractFieldValidationRule
)


class ContractTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractTemplate
        fields = '__all__'
        read_only_fields = ['id', 'tenant_id', 'created_by', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        # Auto-populate tenant_id and created_by from request context
        if 'tenant_id' not in validated_data and self.context.get('request'):
            validated_data['tenant_id'] = self.context['request'].user.tenant_id
        if 'created_by' not in validated_data and self.context.get('request'):
            validated_data['created_by'] = self.context['request'].user.user_id
        return super().create(validated_data)


class ContractTemplateListSerializer(serializers.ModelSerializer):
    """Small payload serializer for listing contract templates."""

    class Meta:
        model = ContractTemplate
        fields = [
            'id',
            'name',
            'contract_type',
            'description',
            'version',
            'status',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class ClauseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Clause
        fields = '__all__'
        read_only_fields = ['id', 'tenant_id', 'created_by', 'created_at', 'updated_at']


class ContractSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contract
        fields = [
            'id',
            'tenant_id',
            'title',
            'contract_type',
            'status',
            'value',
            'counterparty',
            'start_date',
            'end_date',
            'form_inputs',
            'user_instructions',
            'clauses',
            'metadata',
            'created_by',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'tenant_id', 'created_by', 'created_at', 'updated_at']


class ContractListSerializer(serializers.ModelSerializer):
    """Small payload serializer for listing contracts.

    Intentionally excludes large fields like `metadata` (often contains rendered_html),
    `clauses`, and `signed_pdf` (binary) to keep list responses fast.
    """

    class Meta:
        model = Contract
        fields = [
            'id',
            'title',
            'contract_type',
            'status',
            'value',
            'counterparty',
            'start_date',
            'end_date',
            'last_edited_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class ContractVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractVersion
        fields = '__all__'
        read_only_fields = ['id', 'created_at']


class ContractClauseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractClause
        fields = '__all__'


class ContractDetailSerializer(serializers.ModelSerializer):
    latest_version = serializers.SerializerMethodField()
    metadata = serializers.SerializerMethodField()
    # rendered_text/rendered_html are stored in `metadata`.
    # Avoid duplicating large payloads at the top-level.
    
    class Meta:
        model = Contract
        fields = [
            'id',
            'tenant_id',
            'title',
            'contract_type',
            'status',
            'value',
            'counterparty',
            'start_date',
            'end_date',
            'form_inputs',
            'user_instructions',
            'clauses',
            'metadata',
            'is_approved',
            'approved_by',
            'approved_at',
            'created_by',
            'created_at',
            'updated_at',
            'latest_version',
        ]
        read_only_fields = ['id', 'tenant_id', 'created_by', 'created_at', 'updated_at']
    
    def get_latest_version(self, obj):
        try:
            latest = obj.versions.latest('version_number')
            return ContractVersionSerializer(latest).data
        except ContractVersion.DoesNotExist:
            return None

    def get_metadata(self, obj):
        """Return metadata but strip large rendered fields.

        Some older rows stored huge `rendered_html` / `rendered_text` in metadata.
        Returning them in GET /contracts/{id}/ makes responses massive and can
        destabilize DB connections. The editor should fetch full content from
        /contracts/{id}/content/ instead.

        If the view annotated a stripped JSONB (e.g. `_metadata_stripped`), prefer it
        to avoid fetching the full metadata column.
        """
        md = getattr(obj, '_metadata_stripped', None)
        if md is None:
            md = obj.metadata
        if not isinstance(md, dict):
            return {}

        # Always remove large rendered fields from the detail payload.
        out = dict(md)
        out.pop('rendered_html', None)
        out.pop('rendered_text', None)
        out.pop('raw_text', None)

        # Additionally, drop any single string value that is clearly too large.
        # (Defensive for other accidentally-stored blobs.)
        try:
            for k, v in list(out.items()):
                if isinstance(v, str) and len(v) > 20000:
                    out[k] = v[:20000]
                    out[f'{k}_truncated'] = True
        except Exception:
            pass

        return out

    # rendered_text/rendered_html intentionally omitted from API response fields.


class GenerationJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = GenerationJob
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class BusinessRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessRule
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at']


class WorkflowLogSerializer(serializers.ModelSerializer):
    """Serializer for WorkflowLog model"""
    class Meta:
        model = WorkflowLog
        fields = '__all__'
        read_only_fields = ['id', 'timestamp']


class ContractDecisionSerializer(serializers.Serializer):
    """Serializer for contract decision/approval"""
    contract_id = serializers.UUIDField(required=True)
    decision = serializers.ChoiceField(choices=['approve', 'reject'], required=True)
    comments = serializers.CharField(required=False, allow_blank=True)


class ContractGenerateSerializer(serializers.Serializer):
    template_id = serializers.UUIDField(required=True)
    structured_inputs = serializers.JSONField(required=False, default=dict)
    user_instructions = serializers.CharField(required=False, allow_blank=True)
    title = serializers.CharField(required=False, allow_blank=True)
    selected_clauses = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True
    )


class ContractApproveSerializer(serializers.Serializer):
    reviewed = serializers.BooleanField(required=True)
    comments = serializers.CharField(required=False, allow_blank=True)



# ========== MANUAL EDITING SERIALIZERS ==========

class ContractEditingTemplateSerializer(serializers.ModelSerializer):
    """
    Serialize contract editing template with all form and clause configurations
    """
    class Meta:
        model = ContractEditingTemplate
        fields = [
            'id', 'base_template_id', 'name', 'description', 'category',
            'contract_type', 'form_fields', 'default_values', 'mandatory_clauses',
            'optional_clauses', 'clause_order', 'constraint_templates',
            'contract_content_template', 'styling_config', 'preview_sample',
            'is_active', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ContractFieldValidationRuleSerializer(serializers.ModelSerializer):
    """
    Serialize field validation rules
    """
    class Meta:
        model = ContractFieldValidationRule
        fields = ['id', 'template', 'field_name', 'rule_type', 'rule_value', 'error_message']
        read_only_fields = ['id']


class ContractEditsSerializer(serializers.ModelSerializer):
    """
    Serialize contract edits
    """
    class Meta:
        model = ContractEdits
        fields = ['id', 'session', 'edit_type', 'field_name', 'old_value', 
                  'new_value', 'edit_reason', 'timestamp']
        read_only_fields = ['id', 'timestamp']


class ContractEditingStepSerializer(serializers.ModelSerializer):
    """
    Serialize editing steps
    """
    class Meta:
        model = ContractEditingStep
        fields = ['id', 'session', 'step_type', 'step_data', 'timestamp']
        read_only_fields = ['id', 'timestamp']


class ContractPreviewSerializer(serializers.ModelSerializer):
    """
    Serialize contract preview
    """
    class Meta:
        model = ContractPreview
        fields = ['id', 'session', 'preview_html', 'preview_text', 
                  'generated_at', 'form_data_snapshot', 'clauses_snapshot',
                  'constraints_snapshot']
        read_only_fields = ['id', 'generated_at']


class ContractEditingSessionDetailSerializer(serializers.ModelSerializer):
    """
    Detailed session serializer with steps and edits
    """
    steps = ContractEditingStepSerializer(many=True, read_only=True)
    edits = ContractEditsSerializer(many=True, read_only=True)
    preview = ContractPreviewSerializer(read_only=True)
    
    class Meta:
        model = ContractEditingSession
        fields = ['id', 'tenant_id', 'user_id', 'template_id', 'status',
                  'form_data', 'selected_clause_ids', 'custom_clauses',
                  'constraints_config', 'created_at', 'updated_at',
                  'last_saved_at', 'steps', 'edits', 'preview']
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_saved_at']


class ContractEditingSessionSerializer(serializers.ModelSerializer):
    """
    Basic session serializer
    """
    class Meta:
        model = ContractEditingSession
        fields = ['id', 'tenant_id', 'user_id', 'template_id', 'status',
                  'form_data', 'selected_clause_ids', 'custom_clauses',
                  'constraints_config', 'created_at', 'updated_at', 'last_saved_at']
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_saved_at']


class FormFieldSubmissionSerializer(serializers.Serializer):
    """
    Validate form field submission
    """
    field_name = serializers.CharField(max_length=255)
    field_value = serializers.JSONField()
    
    def validate(self, data):
        # Additional validation logic can be added here
        return data


class ClauseSelectionSerializer(serializers.Serializer):
    """
    Validate clause selection
    """
    clause_ids = serializers.ListField(
        child=serializers.CharField(max_length=100)
    )
    custom_clause_content = serializers.JSONField(required=False, allow_null=True)
    
    def validate_clause_ids(self, value):
        if not value:
            raise serializers.ValidationError("At least one clause must be selected")
        return value


class ConstraintDefinitionSerializer(serializers.Serializer):
    """
    Validate constraint/version definitions
    """
    constraint_name = serializers.CharField(max_length=255)
    constraint_value = serializers.JSONField()
    applies_to_clauses = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )


class ContractPreviewRequestSerializer(serializers.Serializer):
    """
    Request parameters for generating contract preview
    """
    form_data = serializers.JSONField()
    selected_clause_ids = serializers.ListField(
        child=serializers.CharField()
    )
    custom_clauses = serializers.JSONField(required=False)
    constraints_config = serializers.JSONField(required=False)


class ContractEditAfterPreviewSerializer(serializers.Serializer):
    """
    Validate edits made after preview
    """
    edit_type = serializers.ChoiceField(
        choices=['form_field', 'clause_added', 'clause_removed', 
                 'clause_content_edited', 'constraint_added', 'constraint_modified']
    )
    field_name = serializers.CharField(max_length=255, required=False)
    old_value = serializers.JSONField(required=False, allow_null=True)
    new_value = serializers.JSONField(required=False, allow_null=True)
    edit_reason = serializers.CharField(required=False, max_length=500)


class FinalizedContractSerializer(serializers.Serializer):
    """
    Finalize contract after editing
    """
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, max_length=1000)
    contract_value = serializers.DecimalField(
        max_digits=15, decimal_places=2, required=False, allow_null=True
    )
    effective_date = serializers.DateField(required=False)
    expiration_date = serializers.DateField(required=False)
    additional_metadata = serializers.JSONField(required=False)

class ContractSummaryEmailRequestSerializer(serializers.Serializer):
    """Request payload for sending summary/reminder emails from the dashboard."""

    kind = serializers.ChoiceField(choices=['overdue', 'renewal'])
    recipient_email = serializers.EmailField()
    recipient_name = serializers.CharField(required=False, allow_blank=True, default='')
# ========== SIGNNOW SERIALIZERS ==========

class SignerSerializer(serializers.ModelSerializer):
    """Serializer for Signer model"""
    
    class Meta:
        model = Signer
        fields = [
            'id',
            'email',
            'name',
            'signing_order',
            'status',
            'has_signed',
            'signed_at',
            'invited_at',
            'signing_url',
            'signing_url_expires_at',
            'declined_reason',
        ]
        read_only_fields = ['id', 'invited_at', 'signed_at']


class SigningAuditLogSerializer(serializers.ModelSerializer):
    """Serializer for SigningAuditLog model"""
    
    signer_email = serializers.CharField(source='signer.email', read_only=True)
    
    class Meta:
        model = SigningAuditLog
        fields = [
            'id',
            'event',
            'message',
            'signer_email',
            'old_status',
            'new_status',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class ESignatureContractSerializer(serializers.ModelSerializer):
    """Serializer for ESignatureContract model"""
    
    signers = SignerSerializer(many=True, read_only=True)
    audit_logs = SigningAuditLogSerializer(many=True, read_only=True)
    contract_title = serializers.CharField(source='contract.title', read_only=True)
    
    class Meta:
        model = ESignatureContract
        fields = [
            'id',
            'contract_title',
            'signnow_document_id',
            'status',
            'signing_order',
            'sent_at',
            'completed_at',
            'expires_at',
            'signers',
            'audit_logs',
            'last_status_check_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'signnow_document_id',
            'sent_at',
            'completed_at',
            'created_at',
            'updated_at',
        ]
