from rest_framework import serializers
from .models import ReviewContract


class RiskScoreSummarySerializer(serializers.Serializer):
    """Risk score and analysis summary"""
    risk_score = serializers.IntegerField(help_text="Overall risk score 0-100")
    risk_level = serializers.CharField(help_text="LOW, MEDIUM, or HIGH")
    clauses_count = serializers.IntegerField(help_text="Number of clauses analyzed")
    obligations_count = serializers.IntegerField(help_text="Number of obligations")
    constraints_count = serializers.IntegerField(help_text="Number of constraints")


class ReviewSuggestionSerializer(serializers.Serializer):
    """Review suggestion for acceptance/rejection"""
    title = serializers.CharField()
    text = serializers.CharField()
    type = serializers.CharField(help_text="'accept', 'review', or 'reject'")
    priority = serializers.CharField(help_text="'high', 'medium', or 'low'")


class ClauseRiskSerializer(serializers.Serializer):
    """Individual clause with risk assessment"""
    title = serializers.CharField()
    text = serializers.CharField()
    risk = serializers.CharField(help_text="low, medium, high")
    reason = serializers.CharField(required=False, allow_blank=True)


class ReviewContractListSerializer(serializers.ModelSerializer):
    """Lightweight list view of review contracts"""
    analysis_summary = serializers.SerializerMethodField()

    class Meta:
        model = ReviewContract
        fields = [
            'id',
            'title',
            'original_filename',
            'contract_type',
            'file_type',
            'size_bytes',
            'status',
            'review_status',
            'error_message',
            'analysis_summary',
            'created_at',
            'updated_at',
            'reviewed_at',
        ]

    def get_analysis_summary(self, obj):
        analysis = obj.analysis or {}
        summary = analysis.get('analysis_summary', {})
        if summary:
            return {
                'risk_score': summary.get('risk_score'),
                'risk_level': summary.get('risk_level'),
                'clauses_count': summary.get('clauses_count'),
                'obligations_count': summary.get('obligations_count'),
                'constraints_count': summary.get('constraints_count'),
            }
        return None


class ReviewContractDetailSerializer(serializers.ModelSerializer):
    """Comprehensive review contract with all risk details"""
    analysis_summary = serializers.SerializerMethodField()
    high_risk_clauses = serializers.SerializerMethodField()
    medium_risk_clauses = serializers.SerializerMethodField()
    clauses = serializers.SerializerMethodField()
    suggestions = serializers.SerializerMethodField()
    insights = serializers.SerializerMethodField()
    obligations = serializers.SerializerMethodField()
    constraints = serializers.SerializerMethodField()
    parties = serializers.SerializerMethodField()
    dates = serializers.SerializerMethodField()
    values = serializers.SerializerMethodField()
    jurisdiction = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = ReviewContract
        fields = [
            'id',
            'title',
            'original_filename',
            'contract_type',
            'file_type',
            'size_bytes',
            'r2_key',
            'status',
            'review_status',
            'error_message',
            'analysis_summary',
            'parties',
            'dates',
            'values',
            'jurisdiction',
            'summary',
            'clauses',
            'high_risk_clauses',
            'medium_risk_clauses',
            'suggestions',
            'insights',
            'obligations',
            'constraints',
            'review_text',
            'review_notes',
            'reviewer_id',
            'created_at',
            'updated_at',
            'reviewed_at',
        ]

    def get_analysis_summary(self, obj):
        """Extract and format risk scores"""
        analysis = obj.analysis or {}
        summary = analysis.get('analysis_summary', {})
        return {
            'risk_score': summary.get('risk_score', 0),
            'risk_level': summary.get('risk_level', 'UNKNOWN'),
            'clauses_count': summary.get('clauses_count', 0),
            'obligations_count': summary.get('obligations_count', 0),
            'constraints_count': summary.get('constraints_count', 0),
        }

    def get_high_risk_clauses(self, obj):
        """Get all high-risk clauses"""
        analysis = obj.analysis or {}
        clauses = analysis.get('clauses', [])
        high_risk = [c for c in clauses if isinstance(c, dict) and c.get('risk') == 'high']
        return high_risk[:10]  # Limit to top 10

    def get_medium_risk_clauses(self, obj):
        """Get all medium-risk clauses"""
        analysis = obj.analysis or {}
        clauses = analysis.get('clauses', [])
        medium_risk = [c for c in clauses if isinstance(c, dict) and c.get('risk') == 'medium']
        return medium_risk[:10]  # Limit to top 10

    def get_clauses(self, obj):
        """Get all extracted clauses (may be empty if AI extraction is not configured)."""
        analysis = obj.analysis or {}
        return analysis.get('clauses', [])

    def get_suggestions(self, obj):
        """Get professional suggestions for review decision"""
        analysis = obj.analysis or {}
        base_suggestions = analysis.get('suggestions', [])
        
        # Add AI-generated review suggestions based on risk level
        summary = analysis.get('analysis_summary', {})
        risk_score = summary.get('risk_score', 0)
        risk_level = summary.get('risk_level', 'UNKNOWN')
        
        suggestions = list(base_suggestions) if isinstance(base_suggestions, list) else []
        
        # Add decision suggestions
        if risk_level == 'HIGH':
            suggestions.append({
                'type': 'reject',
                'priority': 'high',
                'title': 'High Risk Assessment',
                'text': f'This contract has a high risk score ({risk_score}/100). Consider requesting significant modifications before acceptance.'
            })
        elif risk_level == 'MEDIUM':
            suggestions.append({
                'type': 'review',
                'priority': 'medium',
                'title': 'Medium Risk - Review Required',
                'text': f'This contract has a medium risk score ({risk_score}/100). Key clauses should be reviewed with legal counsel.'
            })
        else:
            suggestions.append({
                'type': 'accept',
                'priority': 'low',
                'title': 'Low Risk Profile',
                'text': f'This contract has a low risk score ({risk_score}/100) and appears to be suitable for acceptance.'
            })
        
        return suggestions

    def get_insights(self, obj):
        """Get important insights from analysis"""
        analysis = obj.analysis or {}
        return analysis.get('insights', [])

    def get_obligations(self, obj):
        """Get all obligations from the contract"""
        analysis = obj.analysis or {}
        return analysis.get('obligations', [])

    def get_constraints(self, obj):
        """Get all constraints from the contract"""
        analysis = obj.analysis or {}
        return analysis.get('constraints', [])

    def get_parties(self, obj):
        analysis = obj.analysis or {}
        return analysis.get('parties', [])

    def get_dates(self, obj):
        analysis = obj.analysis or {}
        return analysis.get('dates', [])

    def get_values(self, obj):
        analysis = obj.analysis or {}
        return analysis.get('values', [])

    def get_jurisdiction(self, obj):
        analysis = obj.analysis or {}
        return analysis.get('jurisdiction')

    def get_summary(self, obj):
        analysis = obj.analysis or {}
        return analysis.get('summary', '')


class ReviewContractCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    analyze = serializers.BooleanField(required=False, default=True)


class ReviewAcceptanceSerializer(serializers.Serializer):
    """Accept a reviewed contract"""
    notes = serializers.CharField(required=False, allow_blank=True, help_text="Optional reviewer notes")


class ReviewRejectionSerializer(serializers.Serializer):
    """Reject a reviewed contract"""
    notes = serializers.CharField(required=True, help_text="Reason for rejection")
    suggested_changes = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="List of suggested changes"
    )
