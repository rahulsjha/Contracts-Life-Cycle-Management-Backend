from django.db import models
from tenants.models import TenantModel
from authentication.models import User
import uuid


class RepositoryModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    document_type = models.CharField(max_length=100)
    file_path = models.CharField(max_length=500)
    file_size = models.BigIntegerField(default=0)
    mime_type = models.CharField(max_length=100)
    created_by = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'repository'
        app_label = 'repository'


class RepositoryFolderModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField()
    name = models.CharField(max_length=255)
    parent_id = models.UUIDField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'repository_folders'
        app_label = 'repository'


# ============================================================================
# PRODUCTION DOCUMENT MANAGEMENT MODELS
# ============================================================================

class Document(models.Model):
    """Core Document model for storing uploaded files with full text extraction"""
    
    STATUS_CHOICES = [
        ('uploaded', 'Uploaded'),
        ('processing', 'Processing'),
        ('processed', 'Processed'),
        ('failed', 'Failed'),
    ]
    
    DOCUMENT_TYPE_CHOICES = [
        ('contract', 'Contract'),
        ('template', 'Template'),
        ('agreement', 'Agreement'),
        ('policy', 'Policy'),
        ('other', 'Other'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, related_name='documents')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='uploaded_documents')
    filename = models.CharField(max_length=500)
    file_type = models.CharField(max_length=50)  # pdf, docx, txt, etc.
    file_size = models.BigIntegerField()  # in bytes
    r2_key = models.CharField(max_length=500, unique=True) 
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES, default='other')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='uploaded')
    processing_error = models.TextField(null=True, blank=True)
    full_text = models.TextField(null=True, blank=True)  
    extracted_metadata = models.JSONField(default=dict, null=True, blank=True)
    
    # Timestamps
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'documents'
        app_label = 'repository'
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['tenant', '-uploaded_at']),
            models.Index(fields=['status']),
            models.Index(fields=['r2_key']),
        ]
    
    def __str__(self):
        return f"{self.filename} ({self.status})"


class DocumentChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='chunks')
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, related_name='document_chunks')
    
    # Chunk Content
    chunk_number = models.IntegerField()
    text = models.TextField()
    start_char_index = models.IntegerField()
    end_char_index = models.IntegerField()
    embedding = models.JSONField(null=True, blank=True)
    is_processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'document_chunks'
        app_label = 'repository'
        ordering = ['document', 'chunk_number']
        indexes = [
            models.Index(fields=['document', 'chunk_number']),
            models.Index(fields=['tenant']),
        ]
    
    def __str__(self):
        return f"Chunk {self.chunk_number} of {self.document.filename}"


class DocumentMetadata(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name='metadata')
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE)
    
    # Extracted Fields
    parties = models.JSONField(default=list, blank=True)
    contract_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, null=True, blank=True)
    
    # Dates
    effective_date = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    
    # Clauses and Obligations
    identified_clauses = models.JSONField(default=list, blank=True)
    obligations = models.JSONField(default=list, blank=True)
    
    # Risk Assessment
    risk_score = models.IntegerField(null=True, blank=True)  # 0-100
    high_risk_items = models.JSONField(default=list, blank=True)
    
    # Summary
    summary = models.TextField(null=True, blank=True)
    
    extracted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'document_metadata'
        app_label = 'repository'
    
    def __str__(self):
        return f"Metadata for {self.document.filename}"
