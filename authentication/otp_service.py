import os
import secrets
import logging
from datetime import timedelta
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger(__name__)

class OTPService:
    """Service for managing OTP generation and email sending"""
    
    OTP_LENGTH = 6
    OTP_VALIDITY_MINUTES = 10
    MAX_ATTEMPTS = 5
    
    @staticmethod
    def generate_otp():
        """Generate a random 6-digit OTP"""
        return ''.join(secrets.choice('0123456789') for _ in range(OTPService.OTP_LENGTH))
    
    @staticmethod
    def send_login_otp(user, otp):
        """Send OTP for login via email"""
        try:
            subject = "Your CLM Login OTP"
            message = f"""
Hello {user.first_name or user.email},

Your one-time password (OTP) for CLM login is:

{otp}

This OTP is valid for {OTPService.OTP_VALIDITY_MINUTES} minutes.

If you didn't request this, please ignore this email.

Best regards,
CLM Team
            """
            
            result = send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            logger.info(f"Login OTP sent to {user.email}, result: {result}")
            return True
        except Exception as e:
            logger.error(f"Error sending login OTP to {user.email}: {str(e)}")
            print(f"Error sending login OTP: {e}")
            return False
    
    @staticmethod
    def send_password_reset_otp(user, otp):
        """Send OTP for password reset via email"""
        try:
            subject = "Your CLM Password Reset OTP"
            message = f"""
Hello {user.first_name or user.email},

Your one-time password (OTP) for password reset is:

{otp}

This OTP is valid for {OTPService.OTP_VALIDITY_MINUTES} minutes.

If you didn't request this, please ignore this email and your password will remain unchanged.

Best regards,
CLM Team
            """
            
            result = send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            logger.info(f"Password reset OTP sent to {user.email}, result: {result}")
            return True
        except Exception as e:
            logger.error(f"Error sending password reset OTP to {user.email}: {str(e)}")
            print(f"Error sending password reset OTP: {e}")
            return False
    
    @staticmethod
    def send_welcome_email(user):
        """Send welcome email to new user"""
        try:
            subject = "Welcome to CLM"
            message = f"""
Hello {user.first_name or user.email},

Welcome to Contract Lifecycle Management (CLM) system!

Your account has been successfully created. You can now log in with your email and password.

Email: {user.email}

If you have any questions, please contact our support team.

Best regards,
CLM Team
            """
            
            result = send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            logger.info(f"Welcome email sent to {user.email}, result: {result}")
            return True
        except Exception as e:
            logger.error(f"Error sending welcome email to {user.email}: {str(e)}")
            print(f"Error sending welcome email: {e}")
            return False
    
    @staticmethod
    def send_email_otp(email):
        """Send OTP for email verification via email"""
        try:
            from .models import User
            # Try to get user by email
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return {'message': 'User not found', 'success': False}
            
            # Generate OTP
            otp = OTPService.generate_otp()
            user.login_otp = otp
            user.otp_created_at = timezone.now()
            user.otp_attempts = 0
            user.save(update_fields=['login_otp', 'otp_created_at', 'otp_attempts'])
            
            subject = "Email Verification OTP"
            message = f"""
Hello {user.first_name or user.email},

Your one-time password (OTP) for email verification is:

{otp}

This OTP is valid for {OTPService.OTP_VALIDITY_MINUTES} minutes.

If you didn't request this, please ignore this email.

Best regards,
CLM Team
            """
            
            result = send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            logger.info(f"Email verification OTP sent to {email}, result: {result}")
            return {'message': f'OTP sent to {email}. Valid for {OTPService.OTP_VALIDITY_MINUTES} minutes', 'success': True}
        except Exception as e:
            logger.error(f"Error sending email OTP to {email}: {str(e)}")
            print(f"Error sending email OTP: {e}")
            return {'message': f'Error sending OTP: {str(e)}', 'success': False}

    @staticmethod
    def send_email_change_otp(user, new_email, otp):
        """Send OTP for changing the account email address."""
        try:
            subject = "Confirm your CLM email change"
            message = f"""
Hello {user.first_name or user.email},

You requested to change your CLM account email to:

{new_email}

Your verification code is:

{otp}

This OTP is valid for {OTPService.OTP_VALIDITY_MINUTES} minutes.

If you did not request this change, you can safely ignore this email.

Best regards,
CLM Team
            """

            result = send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [new_email],
                fail_silently=False,
            )
            logger.info(f"Email change OTP sent to {new_email}, result: {result}")
            return {'message': f'OTP sent to {new_email}. Valid for {OTPService.OTP_VALIDITY_MINUTES} minutes', 'success': True}
        except Exception as e:
            logger.error(f"Error sending email change OTP to {new_email}: {str(e)}")
            return {'message': f'Error sending OTP: {str(e)}', 'success': False}
    
    @staticmethod
    def verify_otp(user, otp, otp_type='login'):
        """Verify OTP with expiry and attempt checks"""
        if otp_type == 'login':
            stored_otp = user.login_otp
        else:
            stored_otp = user.password_reset_otp
        
        # Check if OTP is set
        if not stored_otp:
            return False, "OTP not requested"
        
        # Check OTP attempts
        if user.otp_attempts >= OTPService.MAX_ATTEMPTS:
            return False, "Too many attempts. Please request a new OTP"
        
        # Check OTP expiry
        if user.otp_created_at:
            expiry_time = user.otp_created_at + timedelta(minutes=OTPService.OTP_VALIDITY_MINUTES)
            if timezone.now() > expiry_time:
                return False, "OTP has expired"
        
        # Verify OTP
        if str(stored_otp) != str(otp).strip():
            user.otp_attempts += 1
            user.save(update_fields=['otp_attempts'])
            return False, f"Invalid OTP ({OTPService.MAX_ATTEMPTS - user.otp_attempts} attempts remaining)"
        
        return True, "OTP verified successfully"
    
    @staticmethod
    def clear_otp(user, otp_type='login'):
        """Clear OTP after successful verification"""
        if otp_type == 'login':
            user.login_otp = None
        else:
            user.password_reset_otp = None
        user.otp_attempts = 0
        user.save(update_fields=[f'{otp_type}_otp', 'otp_attempts'])
