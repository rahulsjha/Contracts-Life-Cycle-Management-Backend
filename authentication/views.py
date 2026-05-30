"""
Authentication views with real OTP implementation
"""
import hashlib
import hmac
import mimetypes
from urllib.parse import urlencode

from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from drf_spectacular.utils import extend_schema
import secrets
from datetime import timedelta
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from .models import User
from .otp_service import OTPService
from .r2_service import R2StorageService
from tenants.models import TenantModel

from .openapi_serializers import (
    ForgotPasswordRequestSerializer,
    AvatarResponseSerializer,
    GoogleLoginRequestSerializer,
    LoginRequestSerializer,
    LoginResponseSerializer,
    MessageResponseSerializer,
    RequestEmailChangeSerializer,
    RefreshTokenRequestSerializer,
    RefreshTokenResponseSerializer,
    RegisterRequestSerializer,
    RegisterResponseSerializer,
    RequestOTPRequestSerializer,
    UpdateProfileRequestSerializer,
    UpdateProfileResponseSerializer,
    ResetPasswordRequestSerializer,
    UserContextSerializer,
    VerifyEmailOTPRequestSerializer,
    VerifyEmailOTPResponseSerializer,
    VerifyEmailChangeSerializer,
    VerifyPasswordResetOTPRequestSerializer,
    VerifyPasswordResetOTPResponseSerializer,
)
import uuid
import os
import logging

try:
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
except Exception:  # pragma: no cover
    google_id_token = None
    google_requests = None


logger = logging.getLogger(__name__)


def _resolve_tenant_id_for_email(email: str):
    tenant_id = None

    email_domain = (email.split('@', 1)[1] if '@' in email else '').strip().lower()
    if email_domain:
        tenant = TenantModel.objects.filter(domain=email_domain).first()
        if tenant:
            tenant_id = tenant.id

    if tenant_id is None:
        tenant = TenantModel.objects.filter(status='active').order_by('created_at').first()
        if tenant:
            tenant_id = tenant.id

    if tenant_id is None:
        base_domain = email_domain or 'tenant.local'
        domain = base_domain
        suffix = 1
        while TenantModel.objects.filter(domain=domain).exists():
            suffix += 1
            domain = f"{base_domain}-{suffix}"
        tenant = TenantModel.objects.create(
            name=f"Tenant {domain}",
            domain=domain,
            status='active',
            subscription_plan='free',
        )
        tenant_id = tenant.id

    return tenant_id


def _bootstrap_admin_if_enabled(user: User) -> None:
    """Best-effort: promote allowlisted emails to staff in dev/staging."""
    try:
        if not bool(getattr(settings, 'ENABLE_BOOTSTRAP_ADMINS', False)):
            return
        allow = getattr(settings, 'BOOTSTRAP_ADMIN_EMAILS', None)
        if not allow:
            return
        email = (getattr(user, 'email', '') or '').strip().lower()
        if not email or email not in set(allow):
            return
        if not getattr(user, 'is_staff', False):
            user.is_staff = True
            user.save(update_fields=['is_staff'])
    except Exception:
        # Never block auth flows on bootstrap promotion.
        return


def _tenant_id_claim(user: User) -> str | None:
    """Return a safe tenant_id claim value for JWT payloads.

    Important: do NOT stringify None ("None"), because many views filter UUIDFields
    using this value and Django will raise a ValueError (500) for invalid UUIDs.
    """

    tenant_id = getattr(user, 'tenant_id', None)
    return str(tenant_id) if tenant_id else None


def _split_full_name(full_name: str) -> tuple[str, str]:
    cleaned = ' '.join(str(full_name or '').split())
    if not cleaned:
        return '', ''
    parts = cleaned.split(' ')
    first_name = parts[0].strip()
    last_name = ' '.join(parts[1:]).strip() if len(parts) > 1 else ''
    return first_name, last_name


def _get_auth_user(request) -> User:
    request_user = getattr(request, 'user', None)
    if isinstance(request_user, User):
        return request_user

    user_id = getattr(request_user, 'user_id', None) or getattr(request_user, 'pk', None)
    if not user_id:
        raise User.DoesNotExist

    return User.objects.get(user_id=str(user_id))


def _sign_media_url(user_id: str, r2_key: str) -> str:
    payload = f"{str(user_id)}:{str(r2_key)}".encode('utf-8')
    secret = (getattr(settings, 'SECRET_KEY', '') or '').encode('utf-8')
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _build_media_url(user_id: str, r2_key: str) -> str:
    qs = urlencode(
        {
            'user_id': str(user_id),
            'key': str(r2_key),
            'sig': _sign_media_url(user_id, r2_key),
        }
    )
    return f"/api/auth/media/?{qs}"


def _absolute_media_url(request, user_id: str, r2_key: str) -> str:
    relative_url = _build_media_url(user_id, r2_key)
    try:
        if request is not None:
            return request.build_absolute_uri(relative_url)
    except Exception:
        pass
    backend_base = (getattr(settings, 'BACKEND_BASE_URL', '') or '').strip().rstrip('/')
    return f"{backend_base}{relative_url}" if backend_base else relative_url


def _user_owns_media_key(user: User, r2_key: str) -> bool:
    if not r2_key:
        return False
    if (getattr(user, 'avatar_r2_key', None) or '').strip() == str(r2_key).strip():
        return True
    for item in (getattr(user, 'images', None) or []):
        if isinstance(item, dict) and str(item.get('r2_key') or '').strip() == str(r2_key).strip():
            return True
    return False


def _avatar_url_for_user(user: User, request=None):
    avatar_key = (getattr(user, 'avatar_r2_key', None) or '').strip()
    if not avatar_key:
        return None

    try:
        return _absolute_media_url(request, str(getattr(user, 'user_id', None) or ''), avatar_key)
    except Exception as exc:
        logger.warning('Unable to generate avatar URL for %s: %s', getattr(user, 'email', None), exc)
        return None


def _serialize_user_context(user: User, request=None) -> dict:
    tenant_id = _tenant_id_claim(user)
    is_admin = bool(getattr(user, 'is_admin', False) or getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))
    is_superadmin = bool(getattr(user, 'is_superadmin', False) or getattr(user, 'is_superuser', False))
    first_name = getattr(user, 'first_name', '') or ''
    last_name = getattr(user, 'last_name', '') or ''
    full_name = getattr(user, 'full_name', None) or ' '.join(part for part in [first_name.strip(), last_name.strip()] if part).strip()

    # Build images list with presigned URLs where possible.
    images_list = []
    try:
        raw_images = getattr(user, 'images', None) or []
        for item in raw_images:
            if not isinstance(item, dict):
                continue
            r2_key = item.get('r2_key') or item.get('r2key') or item.get('key')
            url = None
            if r2_key:
                try:
                    url = _absolute_media_url(request, str(getattr(user, 'user_id', None) or ''), r2_key)
                except Exception:
                    url = None
            images_list.append({
                'r2_key': r2_key,
                'url': url,
                'purpose': item.get('purpose'),
                'uploaded_at': item.get('uploaded_at'),
            })
    except Exception:
        images_list = []

    return {
        'user_id': str(getattr(user, 'user_id', None) or getattr(user, 'pk', None) or ''),
        'email': getattr(user, 'email', None),
        'full_name': full_name,
        'first_name': first_name,
        'last_name': last_name,
        'tenant_id': tenant_id,
        'avatar_url': _avatar_url_for_user(user, request=request),
        'images': images_list,
        'pending_email': getattr(user, 'pending_email', None),
        'is_admin': is_admin,
        'is_superadmin': is_superadmin,
    }


class ProfileMediaView(APIView):
    """GET /api/auth/media/?user_id=...&key=...&sig=... - stream a profile image."""
    permission_classes = [AllowAny]

    def get(self, request):
        user_id = str(request.query_params.get('user_id') or '').strip()
        r2_key = str(request.query_params.get('key') or '').strip()
        sig = str(request.query_params.get('sig') or '').strip()

        if not user_id or not r2_key or not sig:
            return Response({'error': 'Missing media parameters'}, status=status.HTTP_400_BAD_REQUEST)

        expected_sig = _sign_media_url(user_id, r2_key)
        if not hmac.compare_digest(expected_sig, sig):
            return Response({'error': 'Invalid media signature'}, status=status.HTTP_403_FORBIDDEN)

        try:
            user = User.objects.get(user_id=user_id)
        except User.DoesNotExist:
            return Response({'error': 'Media not found'}, status=status.HTTP_404_NOT_FOUND)

        if not _user_owns_media_key(user, r2_key):
            return Response({'error': 'Media not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            content = R2StorageService().get_file_bytes(r2_key)
        except Exception as exc:
            logger.warning('Unable to fetch media for %s: %s', r2_key, exc)
            return Response({'error': 'Media not available'}, status=status.HTTP_404_NOT_FOUND)

        content_type, _ = mimetypes.guess_type(r2_key)
        response = HttpResponse(content, content_type=content_type or 'application/octet-stream')
        response['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response


def _build_auth_response(user: User, request=None) -> dict:
    refresh = RefreshToken.for_user(user)
    is_admin = bool(user.is_staff or user.is_superuser)
    is_superadmin = bool(user.is_superuser)
    tenant_id = _tenant_id_claim(user)

    refresh['email'] = user.email
    refresh['tenant_id'] = tenant_id
    refresh['is_admin'] = is_admin
    refresh['is_superadmin'] = is_superadmin

    access = refresh.access_token
    access['email'] = user.email
    access['tenant_id'] = tenant_id
    access['is_admin'] = is_admin
    access['is_superadmin'] = is_superadmin

    return {
        'access': str(access),
        'refresh': str(refresh),
        'user': _serialize_user_context(user, request=request),
    }


def _clear_pending_email_change(user: User):
    user.pending_email = None
    user.pending_email_otp = None
    user.pending_email_otp_created_at = None
    user.pending_email_otp_attempts = 0


def _propagate_email_change(old_email: str, new_email: str) -> None:
    old_email = str(old_email or '').strip().lower()
    new_email = str(new_email or '').strip().lower()
    if not old_email or not new_email or old_email == new_email:
        return

    try:
        from contracts.models import TemplateFile

        TemplateFile.objects.filter(created_by_email__iexact=old_email).update(created_by_email=new_email)
        TemplateFile.objects.filter(signature_fields_updated_by_email__iexact=old_email).update(signature_fields_updated_by_email=new_email)
    except Exception as exc:
        logger.warning('Profile email propagation skipped for %s -> %s: %s', old_email, new_email, exc)


class TokenView(APIView):
    """POST /api/auth/login/ - Authenticate user and generate JWT token"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=LoginRequestSerializer, responses={200: LoginResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')
        
        if not email or not password:
            return Response({'error': 'Email and password required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.check_password(password):
            return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

        # OTP-gated signup: if credentials are correct but account is inactive,
        # resend verification OTP and prompt the client to verify.
        if not user.is_active:
            otp = OTPService.generate_otp()
            user.login_otp = otp
            user.otp_created_at = timezone.now()
            user.otp_attempts = 0
            user.save(update_fields=['login_otp', 'otp_created_at', 'otp_attempts'])
            OTPService.send_login_otp(user, otp)
            return Response(
                {
                    'error': 'Account not verified. OTP sent to email.',
                    'pending_verification': True,
                    'email': user.email,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        _bootstrap_admin_if_enabled(user)

        auth_payload = _build_auth_response(user, request=request)
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        return Response(auth_payload, status=status.HTTP_200_OK)


class RegisterView(APIView):
    """POST /api/auth/register/ - Register new user"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=RegisterRequestSerializer, responses={201: RegisterResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        password = request.data.get('password', '')
        full_name = request.data.get('full_name', '').strip()
        company = request.data.get('company', '').strip()
        tenant_id_raw = (request.data.get('tenant_id') or '').strip()
        tenant_domain = (request.data.get('tenant_domain') or '').strip().lower()
        
        if not email or not password:
            return Response({'error': 'Email and password required'}, status=status.HTTP_400_BAD_REQUEST)
        if len(password) < 6:
            return Response({'error': 'Password minimum 6 chars'}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(email=email).exists():
            return Response({'error': 'User exists'}, status=status.HTTP_400_BAD_REQUEST)

        tenant_id = None
        if tenant_id_raw:
            try:
                tenant_id = uuid.UUID(tenant_id_raw)
            except ValueError:
                return Response({'error': 'Invalid tenant_id'}, status=status.HTTP_400_BAD_REQUEST)

        if tenant_id is None and tenant_domain:
            tenant = TenantModel.objects.filter(domain=tenant_domain).first()
            if tenant:
                tenant_id = tenant.id

        if tenant_id is None:
            email_domain = (email.split('@', 1)[1] if '@' in email else '').strip().lower()
            if email_domain:
                tenant = TenantModel.objects.filter(domain=email_domain).first()
                if tenant:
                    tenant_id = tenant.id

        if tenant_id is None:
            # Default to first active tenant if one exists (single-tenant friendly).
            tenant = TenantModel.objects.filter(status='active').order_by('created_at').first()
            if tenant:
                tenant_id = tenant.id

        if tenant_id is None:
            # Last resort: create a new tenant inferred from email domain.
            email_domain = (email.split('@', 1)[1] if '@' in email else 'tenant.local').strip().lower() or 'tenant.local'
            base_domain = email_domain
            domain = base_domain
            suffix = 1
            while TenantModel.objects.filter(domain=domain).exists():
                suffix += 1
                domain = f"{base_domain}-{suffix}"
            tenant = TenantModel.objects.create(
                name=f"Tenant {domain}",
                domain=domain,
                status='active',
                subscription_plan='free',
            )
            tenant_id = tenant.id

        first_name, last_name = _split_full_name(full_name)
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            tenant_id=tenant_id,
            is_active=False,
        )
        user.set_password(password)
        user.save()

        _bootstrap_admin_if_enabled(user)

        # Send OTP for email verification (account remains inactive until verified)
        otp_result = OTPService.send_email_otp(user.email)
        otp_message = otp_result.get('message', 'OTP sent to email')

        # Optionally, store company info via tenant name for new tenants (best-effort).
        # This avoids schema changes while capturing the organization label for signup.
        if company and tenant_id:
            try:
                tenant = TenantModel.objects.filter(id=tenant_id).first()
                if tenant and (tenant.name.startswith('Tenant ') or tenant.name == tenant.domain):
                    tenant.name = company
                    tenant.save(update_fields=['name'])
            except Exception:
                pass

        return Response({
            'message': f'Registration started. {otp_message}',
            'pending_verification': True,
            'email': user.email,
        }, status=status.HTTP_201_CREATED)


class CurrentUserView(APIView):
    """GET /api/auth/me/ - Get current user"""
    permission_classes = [IsAuthenticated]
    
    @extend_schema(responses={200: UserContextSerializer})
    def get(self, request):
        try:
            user = _get_auth_user(request)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        return Response(_serialize_user_context(user, request=request), status=status.HTTP_200_OK)

    @extend_schema(request=UpdateProfileRequestSerializer, responses={200: UpdateProfileResponseSerializer})
    def patch(self, request):
        try:
            user = _get_auth_user(request)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        full_name = str(request.data.get('full_name', '') or '').strip()
        first_name = str(request.data.get('first_name', '') or '').strip()
        last_name = str(request.data.get('last_name', '') or '').strip()

        if full_name:
            first_name, last_name = _split_full_name(full_name)

        if not full_name and not first_name and not last_name:
            return Response({'error': 'Profile name required'}, status=status.HTTP_400_BAD_REQUEST)

        user.first_name = first_name
        user.last_name = last_name
        user.save(update_fields=['first_name', 'last_name'])
        return Response({'message': 'Profile updated', 'user': _serialize_user_context(user, request=request)}, status=status.HTTP_200_OK)


class RequestEmailChangeView(APIView):
    """POST /api/auth/email-change/request/ - Start email change verification"""
    permission_classes = [IsAuthenticated]
    throttle_scope = 'auth'

    @extend_schema(request=RequestEmailChangeSerializer, responses={200: MessageResponseSerializer})
    def post(self, request):
        try:
            user = _get_auth_user(request)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        new_email = str(request.data.get('new_email', '') or '').strip().lower()
        if not new_email:
            return Response({'error': 'New email required'}, status=status.HTTP_400_BAD_REQUEST)
        if new_email == (user.email or '').strip().lower():
            return Response({'error': 'New email must be different from the current email'}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(email=new_email).exclude(user_id=user.user_id).exists():
            return Response({'error': 'Email already in use'}, status=status.HTTP_400_BAD_REQUEST)

        otp = OTPService.generate_otp()
        user.pending_email = new_email
        user.pending_email_otp = otp
        user.pending_email_otp_created_at = timezone.now()
        user.pending_email_otp_attempts = 0
        user.save(update_fields=['pending_email', 'pending_email_otp', 'pending_email_otp_created_at', 'pending_email_otp_attempts'])

        otp_result = OTPService.send_email_change_otp(user, new_email, otp)
        if not otp_result.get('success'):
            _clear_pending_email_change(user)
            user.save(update_fields=['pending_email', 'pending_email_otp', 'pending_email_otp_created_at', 'pending_email_otp_attempts'])
            return Response({'error': otp_result.get('message', 'Failed to send OTP')}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'message': otp_result.get('message', 'OTP sent to new email')}, status=status.HTTP_200_OK)


class VerifyEmailChangeView(APIView):
    """POST /api/auth/email-change/verify/ - Confirm the new email with OTP"""
    permission_classes = [IsAuthenticated]
    throttle_scope = 'auth'

    @extend_schema(request=VerifyEmailChangeSerializer, responses={200: LoginResponseSerializer})
    def post(self, request):
        try:
            user = _get_auth_user(request)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        new_email = str(request.data.get('new_email', '') or '').strip().lower()
        otp = str(request.data.get('otp', '') or '').strip()

        if not new_email or not otp:
            return Response({'error': 'New email and OTP required'}, status=status.HTTP_400_BAD_REQUEST)
        if not user.pending_email or str(user.pending_email).strip().lower() != new_email:
            return Response({'error': 'No pending email change for this address'}, status=status.HTTP_400_BAD_REQUEST)
        if not user.pending_email_otp:
            return Response({'error': 'Email change OTP not requested'}, status=status.HTTP_400_BAD_REQUEST)
        if user.pending_email_otp_attempts >= OTPService.MAX_ATTEMPTS:
            return Response({'error': 'Too many attempts. Please request a new email change OTP'}, status=status.HTTP_400_BAD_REQUEST)

        if user.pending_email_otp_created_at:
            expiry_time = user.pending_email_otp_created_at + timedelta(minutes=OTPService.OTP_VALIDITY_MINUTES)
            if timezone.now() > expiry_time:
                return Response({'error': 'Email change OTP has expired'}, status=status.HTTP_400_BAD_REQUEST)

        if str(user.pending_email_otp).strip() != otp:
            user.pending_email_otp_attempts += 1
            user.save(update_fields=['pending_email_otp_attempts'])
            remaining = max(0, OTPService.MAX_ATTEMPTS - user.pending_email_otp_attempts)
            return Response({'error': f'Invalid OTP ({remaining} attempts remaining)'}, status=status.HTTP_400_BAD_REQUEST)

        old_email = user.email
        user.email = new_email
        _clear_pending_email_change(user)
        user.save(update_fields=['email', 'pending_email', 'pending_email_otp', 'pending_email_otp_created_at', 'pending_email_otp_attempts'])
        _propagate_email_change(old_email, new_email)

        return Response(_build_auth_response(user, request=request), status=status.HTTP_200_OK)


class AvatarUploadView(APIView):
    """POST /api/auth/avatar/ - Upload or replace profile avatar"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(responses={200: AvatarResponseSerializer})
    def post(self, request):
        try:
            user = _get_auth_user(request)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        avatar_file = request.FILES.get('avatar')
        if not avatar_file:
            return Response({'error': 'Avatar file required'}, status=status.HTTP_400_BAD_REQUEST)

        old_key = (getattr(user, 'avatar_r2_key', None) or '').strip() or None
        safe_name = R2StorageService.sanitize_filename(getattr(avatar_file, 'name', '') or 'avatar')
        key = f"{user.tenant_id}/avatars/{user.user_id}/{uuid.uuid4()}--{safe_name}"

        try:
            storage = R2StorageService()
            uploaded_key = storage.put_bytes(
                key,
                avatar_file.read(),
                content_type=getattr(avatar_file, 'content_type', None) or 'application/octet-stream',
                metadata={
                    'tenant_id': str(user.tenant_id),
                    'user_id': str(user.user_id),
                    'purpose': 'profile_avatar',
                    'original_filename': safe_name,
                },
            )
        except Exception as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        user.avatar_r2_key = uploaded_key
        # If request asks to store image in the user's images storage, append metadata.
        add_to_images_raw = request.data.get('add_to_images', '')
        add_to_images = str(add_to_images_raw).strip().lower() in ('1', 'true', 'yes', 'y')
        purpose = str(request.data.get('purpose', '') or '').strip() or 'user_uploaded'

        if add_to_images:
            try:
                current_images = getattr(user, 'images', None) or []
                entry = {
                    'r2_key': uploaded_key,
                    'purpose': purpose,
                    'uploaded_at': timezone.now().isoformat(),
                }
                current_images.append(entry)
                user.images = current_images
                user.save(update_fields=['avatar_r2_key', 'images'])
            except Exception:
                # Fallback: still save avatar key
                user.save(update_fields=['avatar_r2_key'])
        else:
            user.save(update_fields=['avatar_r2_key'])

        if old_key and old_key != uploaded_key:
            try:
                storage.delete_file(old_key)
            except Exception:
                logger.warning('Failed to remove previous avatar object for user %s', user.user_id)

        # Optionally handle inline email change request in the same endpoint.
        new_email = str(request.data.get('new_email', '') or '').strip().lower()
        email_otp_result = None
        if new_email:
            # Validate new email
            if new_email == (user.email or '').strip().lower():
                email_otp_result = {'success': False, 'message': 'New email must be different from current email'}
            elif User.objects.filter(email=new_email).exclude(user_id=user.user_id).exists():
                email_otp_result = {'success': False, 'message': 'Email already in use'}
            else:
                otp = OTPService.generate_otp()
                user.pending_email = new_email
                user.pending_email_otp = otp
                user.pending_email_otp_created_at = timezone.now()
                user.pending_email_otp_attempts = 0
                user.save(update_fields=['pending_email', 'pending_email_otp', 'pending_email_otp_created_at', 'pending_email_otp_attempts'])

                email_otp_result = OTPService.send_email_change_otp(user, new_email, otp)
                if not email_otp_result.get('success'):
                    # clear pending if send failed
                    _clear_pending_email_change(user)
                    user.save(update_fields=['pending_email', 'pending_email_otp', 'pending_email_otp_created_at', 'pending_email_otp_attempts'])

        resp = {'message': 'Avatar updated', 'user': _serialize_user_context(user, request=request)}
        if email_otp_result is not None:
            resp['email_change'] = email_otp_result

        return Response(resp, status=status.HTTP_200_OK)

    @extend_schema(responses={200: AvatarResponseSerializer})
    def delete(self, request):
        try:
            user = _get_auth_user(request)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        old_key = (getattr(user, 'avatar_r2_key', None) or '').strip() or None
        user.avatar_r2_key = None
        user.save(update_fields=['avatar_r2_key'])

        if old_key:
            try:
                R2StorageService().delete_file(old_key)
            except Exception:
                logger.warning('Failed to delete avatar object for user %s', user.user_id)

        return Response({'message': 'Avatar removed', 'user': _serialize_user_context(user, request=request)}, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST /api/auth/logout/ - Logout"""
    permission_classes = [IsAuthenticated]
    
    @extend_schema(responses={200: MessageResponseSerializer})
    def post(self, request):
        return Response({'message': 'Logged out'}, status=status.HTTP_200_OK)


class RefreshTokenView(APIView):
    """POST /api/auth/refresh/ - Refresh token"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=RefreshTokenRequestSerializer, responses={200: RefreshTokenResponseSerializer})
    def post(self, request):
        refresh_token = request.data.get('refresh', '')
        if not refresh_token:
            return Response({'error': 'Refresh token required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            refresh = RefreshToken(refresh_token)
            return Response({'access': str(refresh.access_token), 'refresh': str(refresh)}, status=status.HTTP_200_OK)
        except (InvalidToken, TokenError):
            return Response({'error': 'Invalid token'}, status=status.HTTP_401_UNAUTHORIZED)


class RequestLoginOTPView(APIView):
    """POST /api/auth/request-login-otp/ - Request login OTP"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=RequestOTPRequestSerializer, responses={200: MessageResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        if not email:
            return Response({'error': 'Email required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Used for both login-OTP and email verification resend.
            user = User.objects.get(email=email)
            otp = OTPService.generate_otp()
            user.login_otp = otp
            user.otp_created_at = timezone.now()
            user.otp_attempts = 0
            user.save(update_fields=['login_otp', 'otp_created_at', 'otp_attempts'])
            OTPService.send_login_otp(user, otp)
        except User.DoesNotExist:
            pass
        
        return Response({'message': 'OTP sent if email exists'}, status=status.HTTP_200_OK)


class VerifyEmailOTPView(APIView):
    """POST /api/auth/verify-email-otp/ - Verify login OTP"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=VerifyEmailOTPRequestSerializer, responses={200: VerifyEmailOTPResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        otp = request.data.get('otp', '').strip()
        
        if not email or not otp:
            return Response({'error': 'Email and OTP required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Allow verifying OTP for newly-registered (inactive) users as well.
            user = User.objects.get(email=email)
            is_valid, msg = OTPService.verify_otp(user, otp, 'login')
            if not is_valid:
                return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

            # Activate user on successful email verification.
            if not user.is_active:
                user.is_active = True
                user.save(update_fields=['is_active'])
                OTPService.send_welcome_email(user)

            OTPService.clear_otp(user, 'login')
            auth_payload = _build_auth_response(user, request=request)
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])
            
            return Response(auth_payload, status=status.HTTP_200_OK)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)


class GoogleLoginView(APIView):
    """POST /api/auth/google/ - Login/register with Google ID token (credential)"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'

    def check_throttles(self, request):
        """Fail-open throttling for Google auth.

        Google sign-in should not hard-fail if cache/Redis is temporarily unavailable.
        """
        try:
            return super().check_throttles(request)
        except Exception as exc:
            logger.warning("GoogleLoginView throttle backend unavailable; skipping throttle: %s", exc)
            return

    @extend_schema(request=GoogleLoginRequestSerializer, responses={200: LoginResponseSerializer})
    def post(self, request):
        if google_id_token is None or google_requests is None:
            return Response(
                {'error': 'Google auth not configured on server'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        credential = request.data.get('credential') or request.data.get('id_token')
        if not credential:
            return Response({'error': 'Google credential required'}, status=status.HTTP_400_BAD_REQUEST)

        def _clean(v):
            if v is None:
                return None
            v = str(v).strip()
            return v or None

        # Support multiple env var names for smoother deployments.
        # Backend uses GOOGLE_CLIENT_ID; frontend uses NEXT_PUBLIC_GOOGLE_CLIENT_ID.
        # Some deployments may have legacy/typo keys as well.
        primary_client_id = (
            _clean(getattr(settings, 'GOOGLE_CLIENT_ID', None))
            or _clean(os.getenv('GOOGLE_CLIENT_ID'))
            or _clean(os.getenv('NEXT_PUBLIC_GOOGLE_CLIENT_ID'))
            or _clean(os.getenv('Google_reidirect'))
        )
        extra_client_ids_raw = os.getenv('GOOGLE_CLIENT_IDS', '')
        extra_client_ids = [c.strip() for c in extra_client_ids_raw.split(',') if c.strip()]
        client_ids = [c for c in [primary_client_id, *extra_client_ids] if c]
        if not client_ids:
            return Response(
                {'error': 'GOOGLE_CLIENT_ID not set on server'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            info = None
            last_error = None
            for client_id in client_ids:
                try:
                    info = google_id_token.verify_oauth2_token(
                        credential,
                        google_requests.Request(),
                        client_id,
                        clock_skew_in_seconds=10,
                    )
                    break
                except Exception as e:
                    last_error = e
                    continue

            if not info:
                if last_error:
                    logger.warning('Google token verification failed', exc_info=last_error)
                if getattr(settings, 'DEBUG', False) and last_error:
                    return Response(
                        {
                            'error': f'Invalid Google token: {last_error}',
                            'detail': str(last_error),
                        },
                        status=status.HTTP_401_UNAUTHORIZED,
                    )
                return Response({'error': 'Invalid Google token'}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as e:
            logger.exception('Unexpected error verifying Google token')
            if getattr(settings, 'DEBUG', False):
                return Response(
                    {
                        'error': f'Invalid Google token: {e}',
                        'detail': str(e),
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            return Response({'error': 'Invalid Google token'}, status=status.HTTP_401_UNAUTHORIZED)

        email = (info.get('email') or '').strip().lower()
        email_verified = bool(info.get('email_verified'))
        if not email:
            return Response({'error': 'Google account email not available'}, status=status.HTTP_400_BAD_REQUEST)
        if not email_verified:
            return Response({'error': 'Google email not verified'}, status=status.HTTP_400_BAD_REQUEST)

        given_name = (info.get('given_name') or '').strip()
        family_name = (info.get('family_name') or '').strip()

        user, _created = User.objects.get_or_create(
            email=email,
            defaults={
                'first_name': given_name,
                'last_name': family_name,
                'tenant_id': _resolve_tenant_id_for_email(email),
                'is_active': True,
            },
        )

        if not user.is_active:
            user.is_active = True
            user.save(update_fields=['is_active'])

        _bootstrap_admin_if_enabled(user)

        auth_payload = _build_auth_response(user, request=request)

        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        return Response(
            {
                'access': auth_payload['access'],
                'refresh': auth_payload['refresh'],
                'user': auth_payload['user'],
            },
            status=status.HTTP_200_OK,
        )


class ForgotPasswordView(APIView):
    """POST /api/auth/forgot-password/ - Request password reset"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=ForgotPasswordRequestSerializer, responses={200: MessageResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        if not email:
            return Response({'error': 'Email required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(email=email, is_active=True)
            otp = OTPService.generate_otp()
            user.password_reset_otp = otp
            user.otp_created_at = timezone.now()
            user.otp_attempts = 0
            user.save(update_fields=['password_reset_otp', 'otp_created_at', 'otp_attempts'])
            OTPService.send_password_reset_otp(user, otp)
        except User.DoesNotExist:
            pass
        
        return Response({'message': 'Reset OTP sent if email exists'}, status=status.HTTP_200_OK)


class VerifyPasswordResetOTPView(APIView):
    """POST /api/auth/verify-password-reset-otp/ - Verify reset OTP"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=VerifyPasswordResetOTPRequestSerializer, responses={200: VerifyPasswordResetOTPResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        otp = request.data.get('otp', '').strip()
        
        if not email or not otp:
            return Response({'error': 'Email and OTP required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(email=email, is_active=True)
            is_valid, msg = OTPService.verify_otp(user, otp, 'password_reset')
            if not is_valid:
                return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'message': 'OTP verified', 'verified': True}, status=status.HTTP_200_OK)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)


class ResendPasswordResetOTPView(APIView):
    """POST /api/auth/resend-password-reset-otp/ - Resend OTP"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=RequestOTPRequestSerializer, responses={200: MessageResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        if not email:
            return Response({'error': 'Email required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(email=email, is_active=True)
            otp = OTPService.generate_otp()
            user.password_reset_otp = otp
            user.otp_created_at = timezone.now()
            user.otp_attempts = 0
            user.save(update_fields=['password_reset_otp', 'otp_created_at', 'otp_attempts'])
            OTPService.send_password_reset_otp(user, otp)
        except User.DoesNotExist:
            pass
        
        return Response({'message': 'OTP resent'}, status=status.HTTP_200_OK)


class ResetPasswordView(APIView):
    """POST /api/auth/reset-password/ - Reset password"""
    permission_classes = [AllowAny]
    throttle_scope = 'auth'
    
    @extend_schema(request=ResetPasswordRequestSerializer, responses={200: MessageResponseSerializer})
    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        otp = request.data.get('otp', '').strip()
        password = request.data.get('password', '')
        
        if not email or not otp or not password:
            return Response({'error': 'Email, OTP, password required'}, status=status.HTTP_400_BAD_REQUEST)
        if len(password) < 6:
            return Response({'error': 'Password min 6 chars'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            user = User.objects.get(email=email, is_active=True)
            is_valid, msg = OTPService.verify_otp(user, otp, 'password_reset')
            if not is_valid:
                return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)
            
            user.set_password(password)
            OTPService.clear_otp(user, 'password_reset')
            user.save()
            return Response({'message': 'Password reset'}, status=status.HTTP_200_OK)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)