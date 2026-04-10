from django.urls import path

from .admin_views import (
    AdminMeView,
    AdminUsersView,
    AdminPromoteUserView,
    AdminDemoteUserView,
    AdminAnalyticsView,
    AdminActivityView,
    AdminFeatureUsageView,
    AdminUserRegistrationView,
    AdminUserFeatureUsageView,
)

urlpatterns = [
    path('me/', AdminMeView.as_view(), name='admin-me'),
    path('me', AdminMeView.as_view(), name='admin-me-noslash'),
    path('analytics/', AdminAnalyticsView.as_view(), name='admin-analytics'),
    path('analytics', AdminAnalyticsView.as_view(), name='admin-analytics-noslash'),
    path('activity/', AdminActivityView.as_view(), name='admin-activity'),
    path('activity', AdminActivityView.as_view(), name='admin-activity-noslash'),
    path('feature-usage/', AdminFeatureUsageView.as_view(), name='admin-feature-usage'),
    path('feature-usage', AdminFeatureUsageView.as_view(), name='admin-feature-usage-noslash'),
    path('user-registration/', AdminUserRegistrationView.as_view(), name='admin-user-registration'),
    path('user-registration', AdminUserRegistrationView.as_view(), name='admin-user-registration-noslash'),
    path('user-feature-usage/', AdminUserFeatureUsageView.as_view(), name='admin-user-feature-usage'),
    path('user-feature-usage', AdminUserFeatureUsageView.as_view(), name='admin-user-feature-usage-noslash'),
    path('users/', AdminUsersView.as_view(), name='admin-users'),
    path('users', AdminUsersView.as_view(), name='admin-users-noslash'),
    path('users/promote/', AdminPromoteUserView.as_view(), name='admin-users-promote'),
    path('users/promote', AdminPromoteUserView.as_view(), name='admin-users-promote-noslash'),
    path('users/demote/', AdminDemoteUserView.as_view(), name='admin-users-demote'),
    path('users/demote', AdminDemoteUserView.as_view(), name='admin-users-demote-noslash'),
]
