from rest_framework import serializers


class UserContextSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    email = serializers.EmailField(allow_null=True, required=False)
    full_name = serializers.CharField(allow_blank=True, required=False)
    first_name = serializers.CharField(allow_blank=True, required=False)
    last_name = serializers.CharField(allow_blank=True, required=False)
    tenant_id = serializers.CharField(allow_null=True, required=False)
    avatar_url = serializers.CharField(allow_null=True, required=False)
    images = serializers.ListField(child=serializers.DictField(), required=False)
    pending_email = serializers.EmailField(allow_null=True, required=False)
    is_admin = serializers.BooleanField()
    is_superadmin = serializers.BooleanField()


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class LoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserContextSerializer()


class RegisterRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    full_name = serializers.CharField(required=False, allow_blank=True)
    company = serializers.CharField(required=False, allow_blank=True)
    tenant_id = serializers.CharField(required=False, allow_blank=True)
    tenant_domain = serializers.CharField(required=False, allow_blank=True)


class RegisterResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    pending_verification = serializers.BooleanField()
    email = serializers.EmailField()


class RefreshTokenRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class RefreshTokenResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class RequestOTPRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class VerifyEmailOTPRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField()


class VerifyEmailOTPResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserContextSerializer()


class UpdateProfileRequestSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, allow_blank=True)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)


class UpdateProfileResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    user = UserContextSerializer()


class RequestEmailChangeSerializer(serializers.Serializer):
    new_email = serializers.EmailField()


class VerifyEmailChangeSerializer(serializers.Serializer):
    new_email = serializers.EmailField()
    otp = serializers.CharField()


class AvatarResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    user = UserContextSerializer()


class GoogleLoginRequestSerializer(serializers.Serializer):
    credential = serializers.CharField(required=False, allow_blank=True)
    id_token = serializers.CharField(required=False, allow_blank=True)


class ForgotPasswordRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class VerifyPasswordResetOTPRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField()


class VerifyPasswordResetOTPResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    verified = serializers.BooleanField()


class ResetPasswordRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    otp = serializers.CharField()
    password = serializers.CharField()
