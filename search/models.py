from django.db import models
from django.contrib.postgres.search import SearchVectorField
from django.contrib.postgres.indexes import GinIndex
import uuid


class SearchIndexModel(models.Model):
    """
    Search Index Model
    Stores indexed content for full-text, semantic, and faceted search
    """
    
    ENTITY_TYPES = [
        ('contract', 'Contract'),
        ('clause', 'Clause'),
        ('template', 'Template'),
        ('workflow', 'Workflow'),
        ('document', 'Document'),
        ('approval', 'Approval'),
        ('audit_log', 'Audit Log'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField(db_index=True)
    entity_type = models.CharField(max_length=50, choices=ENTITY_TYPES)
    entity_id = models.UUIDField()
    title = models.CharField(max_length=500, db_index=True)
    content = models.TextField()
    keywords = models.JSONField(default=list, help_text="Array of searchable keywords")
    metadata = models.JSONField(default=dict, help_text="Additional metadata for filtering")
    
    # Full-text search vector (PostgreSQL)
    search_vector = SearchVectorField(null=True, blank=True)
    
    # Semantic embedding (for pgvector - optional)
    embedding = models.JSONField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    indexed_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'search_indices'
        app_label = 'search'
        indexes = [
            GinIndex(fields=['search_vector'], name='search_index_gin'),
            GinIndex(
                fields=['title'],
                name='search_title_trgm_gin',
                opclasses=['gin_trgm_ops'],
            ),
            models.Index(fields=['tenant_id', 'entity_type'], name='tenant_entity_idx'),
            models.Index(fields=['entity_type', 'entity_id'], name='entity_lookup_idx'),
        ]
    
    def __str__(self):
        return f"{self.entity_type}: {self.title}"


class SearchAnalyticsModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField()
    user_id = models.UUIDField()
    query = models.CharField(max_length=255)
    query_type = models.CharField(
        max_length=20,
        choices=[
            ('full_text', 'Full-Text'),
            ('semantic', 'Semantic'),
            ('hybrid', 'Hybrid'),
            ('faceted', 'Faceted'),
        ]
    )
    results_count = models.IntegerField(default=0)
    response_time_ms = models.IntegerField(default=0)
    clicked_result_id = models.UUIDField(null=True, blank=True)
    clicked_result_type = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    
    class Meta:
        db_table = 'search_analytics'
        app_label = 'search'
        indexes = [
            models.Index(fields=['tenant_id', 'created_at'], name='search_analytics_tenant_date'),
            models.Index(fields=['query_type'], name='search_analytics_type'),
        ]
    
    def __str__(self):
        return f"Search: {self.query}"
