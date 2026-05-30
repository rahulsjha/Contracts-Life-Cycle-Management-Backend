"""
URL configuration for authentication app
"""
from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    TokenView,
    RegisterView,
    CurrentUserView,
    ForgotPasswordView,
    ResetPasswordView,
    LogoutView,
    RefreshTokenView,
    VerifyPasswordResetOTPView,
    ResendPasswordResetOTPView,
    RequestLoginOTPView,
    VerifyEmailOTPView,
    RequestEmailChangeView,
    VerifyEmailChangeView,
    AvatarUploadView,
    GoogleLoginView,
)

urlpatterns = [
    path('login/', TokenView.as_view(), name='login'),
    path('register/', RegisterView.as_view(), name='register'),
    path('google/', GoogleLoginView.as_view(), name='google_login'),
    path('me/', CurrentUserView.as_view(), name='current_user'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('reset-password/', ResetPasswordView.as_view(), name='reset_password'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('refresh/', RefreshTokenView.as_view(), name='refresh'),
    path('verify-password-reset-otp/', VerifyPasswordResetOTPView.as_view(), name='verify_password_reset_otp'),
    path('resend-password-reset-otp/', ResendPasswordResetOTPView.as_view(), name='resend_password_reset_otp'),
    path('request-login-otp/', RequestLoginOTPView.as_view(), name='request_login_otp'),
    path('verify-email-otp/', VerifyEmailOTPView.as_view(), name='verify_email_otp'),
    path('email-change/request/', RequestEmailChangeView.as_view(), name='request_email_change'),
    path('email-change/verify/', VerifyEmailChangeView.as_view(), name='verify_email_change'),
    path('avatar/', AvatarUploadView.as_view(), name='avatar'),
]
