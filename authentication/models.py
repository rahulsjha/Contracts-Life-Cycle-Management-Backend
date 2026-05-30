from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
import uuid


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    user_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    email = models.EmailField(unique=True, max_length=255)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    avatar_r2_key = models.CharField(max_length=500, blank=True, null=True)
    # Stores a list of uploaded images for the user. Each entry is a dict
    # with keys such as: r2_key, purpose, uploaded_at
    images = models.JSONField(blank=True, default=list)
    pending_email = models.EmailField(blank=True, null=True)
    pending_email_otp = models.CharField(max_length=10, blank=True, null=True)
    pending_email_otp_created_at = models.DateTimeField(blank=True, null=True)
    pending_email_otp_attempts = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    last_login = models.DateTimeField(blank=True, null=True)
    date_joined = models.DateTimeField(auto_now_add=True)
    reset_token = models.CharField(max_length=255, blank=True, null=True)
    reset_token_expiry = models.DateTimeField(blank=True, null=True)
    otp_attempts = models.IntegerField(default=0)
    password_reset_otp = models.CharField(max_length=10, blank=True, null=True)
    login_otp = models.CharField(max_length=10, blank=True, null=True)
    otp_created_at = models.DateTimeField(blank=True, null=True)

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    class Meta:
        db_table = 'clm_users'

    def __str__(self):
        return self.email

    @property
    def full_name(self):
        parts = [self.first_name or '', self.last_name or '']
        return ' '.join(part.strip() for part in parts if part and part.strip()).strip()

    @property
    def display_name(self):
        return self.full_name or self.email
