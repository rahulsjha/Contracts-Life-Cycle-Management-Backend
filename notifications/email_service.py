"""
Email Notification Service

Handles sending emails for various approval workflows and notifications.
Supports HTML templates and clickable approval actions.
"""

import os
import smtplib
import logging
import mimetypes
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class EmailService:
    """
    Service for sending email notifications
    Supports multiple templates for different notification types
    """
    
    def __init__(self):
        """Initialize email service with SMTP configuration"""
        self.smtp_host = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('EMAIL_PORT', 587))
        self.sender_email = (
            (os.getenv('EMAIL_HOST_USER') or '').strip()
            or (os.getenv('GMAIL') or '').strip()
            or (os.getenv('DEFAULT_FROM_EMAIL') or '').strip()
            or 'noreply@example.com'
        )
        self.sender_password = (
            (os.getenv('EMAIL_HOST_PASSWORD') or '').strip()
            or (os.getenv('APP_PASSWORD') or '').strip()
        )
        # `app_url` is intended to be the *frontend* URL for clickable links.
        self.app_url = (
            (os.getenv('FRONTEND_BASE_URL') or '').strip()
            or (os.getenv('APP_URL') or '').strip()
            or 'http://localhost:3000'
        ).rstrip('/')
    
    def send_approval_request_email(
        self,
        recipient_email: str,
        recipient_name: str,
        approver_name: str,
        document_title: str,
        document_type: str,
        approval_id: str,
        requester_name: str,
        priority: str = 'normal'
    ) -> bool:
        """
        Send approval request notification email
        
        Args:
            recipient_email: Approver's email
            recipient_name: Approver's name
            approver_name: Name of the approver
            document_title: Title of the document
            document_type: Type of document (contract, etc)
            approval_id: ID of the approval request
            requester_name: Name of person requesting approval
            priority: Priority level (normal, high, urgent)
        
        Returns:
            True if email sent successfully
        """
        try:
            subject = f"🔔 Approval Request: {document_title}"
            
            # Create HTML email body with clickable action buttons
            html_body = self._get_approval_request_template(
                recipient_name=recipient_name,
                approver_name=approver_name,
                document_title=document_title,
                document_type=document_type,
                requester_name=requester_name,
                approval_id=approval_id,
                priority=priority
            )
            
            return self._send_email(
                recipient_email=recipient_email,
                subject=subject,
                html_body=html_body,
                notification_type='approval_request'
            )
        except Exception as e:
            logger.error(f"Failed to send approval request email: {str(e)}")
            return False
    
    def send_approval_approved_email(
        self,
        recipient_email: str,
        recipient_name: str,
        document_title: str,
        approver_name: str,
        approval_comment: str = ""
    ) -> bool:
        """
        Send approval approved notification
        
        Args:
            recipient_email: Recipient's email
            recipient_name: Recipient's name
            document_title: Title of approved document
            approver_name: Name of approver
            approval_comment: Optional comment from approver
        
        Returns:
            True if email sent successfully
        """
        try:
            subject = f"✅ Approval Approved: {document_title}"
            
            html_body = self._get_approval_approved_template(
                recipient_name=recipient_name,
                document_title=document_title,
                approver_name=approver_name,
                approval_comment=approval_comment
            )
            
            return self._send_email(
                recipient_email=recipient_email,
                subject=subject,
                html_body=html_body,
                notification_type='approval_approved'
            )
        except Exception as e:
            logger.error(f"Failed to send approval approved email: {str(e)}")
            return False
    
    def send_approval_rejected_email(
        self,
        recipient_email: str,
        recipient_name: str,
        document_title: str,
        approver_name: str,
        rejection_reason: str = ""
    ) -> bool:
        """
        Send approval rejected notification
        
        Args:
            recipient_email: Recipient's email
            recipient_name: Recipient's name
            document_title: Title of rejected document
            approver_name: Name of approver
            rejection_reason: Reason for rejection
        
        Returns:
            True if email sent successfully
        """
        try:
            subject = f"❌ Approval Rejected: {document_title}"
            
            html_body = self._get_approval_rejected_template(
                recipient_name=recipient_name,
                document_title=document_title,
                approver_name=approver_name,
                rejection_reason=rejection_reason
            )
            
            return self._send_email(
                recipient_email=recipient_email,
                subject=subject,
                html_body=html_body,
                notification_type='approval_rejected'
            )
        except Exception as e:
            logger.error(f"Failed to send approval rejected email: {str(e)}")
            return False

    def send_inhouse_signature_invite_email(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        contract_title: str,
        signing_url: str,
        expires_at_iso: str | None = None,
        sender_name: str | None = None,
    ) -> bool:
        """Send an in-house e-sign invite email with a magic signing link."""
        try:
            subject = f"✍️ Signature Requested: {contract_title}"
            html_body = self._get_inhouse_invite_template(
                recipient_name=recipient_name,
                contract_title=contract_title,
                signing_url=signing_url,
                expires_at_iso=expires_at_iso,
                sender_name=sender_name,
            )
            return self._send_email(
                recipient_email=recipient_email,
                subject=subject,
                html_body=html_body,
                notification_type='inhouse_esign_invite',
            )
        except Exception as e:
            logger.error(f"Failed to send inhouse invite email: {str(e)}")
            return False

    def send_inhouse_signing_completed_email(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        contract_title: str,
        completed_at_iso: str | None,
        attachments: list[dict],
    ) -> bool:
        """Send completion email with executed PDF + certificate attachments."""
        try:
            subject = f"✅ Completed: {contract_title}"
            html_body = self._get_inhouse_completed_template(
                recipient_name=recipient_name,
                contract_title=contract_title,
                completed_at_iso=completed_at_iso,
            )
            return self._send_email(
                recipient_email=recipient_email,
                subject=subject,
                html_body=html_body,
                notification_type='inhouse_esign_completed',
                attachments=attachments,
            )
        except Exception as e:
            logger.error(f"Failed to send inhouse completion email: {str(e)}")
            return False

    def send_contract_overdue_email(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        contract_title: str,
        counterparty: str | None,
        obligations: list[dict],
        contract_url: str | None = None,
    ) -> bool:
        subject = f"⏰ Overdue items: {contract_title}"
        html_body = self._get_contract_overdue_template(
            recipient_name=recipient_name,
            contract_title=contract_title,
            counterparty=counterparty,
            obligations=obligations,
            contract_url=contract_url,
        )
        return self._send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_body=html_body,
            notification_type='contract_overdue',
        )

    def send_contract_renewal_email(
        self,
        *,
        recipient_email: str,
        recipient_name: str,
        contract_title: str,
        counterparty: str | None,
        term_brief: dict,
        contract_url: str | None = None,
    ) -> bool:
        subject = f"🔁 Renewal brief: {contract_title}"
        html_body = self._get_contract_renewal_template(
            recipient_name=recipient_name,
            contract_title=contract_title,
            counterparty=counterparty,
            term_brief=term_brief,
            contract_url=contract_url,
        )
        return self._send_email(
            recipient_email=recipient_email,
            subject=subject,
            html_body=html_body,
            notification_type='contract_renewal',
        )
    
    def _send_email(
        self,
        recipient_email: str,
        subject: str,
        html_body: str,
        notification_type: str = 'general',
        attachments: Optional[List[Dict]] = None,
    ) -> bool:
        """
        Internal method to send email via SMTP
        
        Args:
            recipient_email: Recipient's email address
            subject: Email subject
            html_body: HTML email body
            notification_type: Type of notification
        
        Returns:
            True if sent successfully
        """
        try:
            attachments = attachments or []

            # Create message
            # - mixed: allows attachments
            # - alternative: html/plain body
            msg = MIMEMultipart('mixed')
            msg['Subject'] = subject
            msg['From'] = self.sender_email
            msg['To'] = recipient_email
            msg['X-Notification-Type'] = notification_type
            msg['X-Timestamp'] = datetime.now().isoformat()
            
            alt = MIMEMultipart('alternative')
            plain_fallback = "This email requires an HTML-capable client."
            alt.attach(MIMEText(plain_fallback, 'plain'))
            alt.attach(MIMEText(html_body, 'html'))
            msg.attach(alt)

            for att in attachments:
                if not isinstance(att, dict):
                    continue
                filename = str(att.get('filename') or '').strip() or 'attachment'
                content = att.get('content')
                if content is None:
                    continue
                if isinstance(content, str):
                    content_bytes = content.encode('utf-8')
                else:
                    content_bytes = bytes(content)

                content_type = str(att.get('content_type') or '').strip()
                if not content_type:
                    guessed, _ = mimetypes.guess_type(filename)
                    content_type = guessed or 'application/octet-stream'

                maintype, _, subtype = content_type.partition('/')
                if maintype != 'application':
                    subtype = 'octet-stream'
                mime_part = MIMEApplication(content_bytes, _subtype=subtype or 'octet-stream')
                mime_part.add_header('Content-Disposition', 'attachment', filename=filename)
                msg.attach(mime_part)
            
            # Send via SMTP
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                if self.sender_password:
                    server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
            
            logger.info(f"Email sent successfully to {recipient_email}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {str(e)}")
            return False

    def _get_inhouse_invite_template(
        self,
        *,
        recipient_name: str,
        contract_title: str,
        signing_url: str,
        expires_at_iso: str | None,
        sender_name: str | None,
    ) -> str:
        expires_line = (
            f"<p style=\"margin: 6px 0; color: #555;\"><strong>Link expires:</strong> {expires_at_iso}</p>"
            if expires_at_iso
            else ""
        )
        sender_line = (
            f"<p style=\"margin: 6px 0; color: #555;\"><strong>Requested by:</strong> {sender_name}</p>"
            if sender_name
            else ""
        )

        safe_contract_title = str(contract_title or '').strip() or 'Contract'

        return f"""<!DOCTYPE html>
<html>
    <head>
        <meta charset=\"UTF-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
        <title>Signature requested</title>
    </head>
    <body style=\"margin:0;padding:0;background:#f3f4f6;\">
        <div style=\"display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;\">Please review and sign: {safe_contract_title}</div>

        <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f3f4f6;padding:24px 12px;\">
            <tr>
                <td align=\"center\">
                    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:640px;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;\">
                        <tr>
                            <td style=\"padding:18px 20px;background:linear-gradient(135deg,#0f172a 0%,#4f46e5 100%);color:#ffffff;\">
                                <div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,Arial,sans-serif;\">
                                    <div style=\"font-size:12px;letter-spacing:0.12em;text-transform:uppercase;opacity:0.9;\">Contract signature</div>
                                    <div style=\"margin-top:6px;font-size:22px;font-weight:800;line-height:1.25;\">Signature requested</div>
                                </div>
                            </td>
                        </tr>

                        <tr>
                            <td style=\"padding:22px 20px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,Arial,sans-serif;color:#111827;\">
                                <p style=\"margin:0 0 12px 0;font-size:14px;\">Hi <strong>{recipient_name}</strong>,</p>
                                <p style=\"margin:0 0 14px 0;font-size:14px;color:#374151;\">You have been invited to review and sign the following contract:</p>

                                <div style=\"border:1px solid #e5e7eb;background:#f9fafb;border-radius:14px;padding:14px 14px;\">
                                    <div style=\"font-size:12px;color:#6b7280;\">Contract</div>
                                    <div style=\"margin-top:4px;font-size:16px;font-weight:700;color:#111827;\">{safe_contract_title}</div>
                                    <div style=\"margin-top:10px;font-size:13px;color:#4b5563;\">
                                        {sender_line}
                                        {expires_line}
                                    </div>
                                </div>

                                <div style=\"margin:18px 0 12px 0;\">
                                    <a href=\"{signing_url}\" style=\"display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:12px;font-weight:700;font-size:14px;\">Review &amp; Sign</a>
                                </div>

                                <p style=\"margin:0 0 10px 0;font-size:12px;color:#6b7280;\">If the button doesn’t work, copy and paste this link into your browser:</p>
                                <p style=\"margin:0 0 16px 0;font-size:12px;word-break:break-all;color:#111827;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:12px;padding:10px 12px;\">{signing_url}</p>

                                <p style=\"margin:0;font-size:12px;color:#9ca3af;\">This is an automated message. If you weren’t expecting this request, you can ignore this email.</p>
                            </td>
                        </tr>
                    </table>

                    <div style=\"max-width:640px;margin:10px auto 0 auto;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,Arial,sans-serif;font-size:11px;color:#9ca3af;\">
                        Please do not reply to this email.
                    </div>
                </td>
            </tr>
        </table>
    </body>
</html>
"""

    def _get_contract_overdue_template(
        self,
        *,
        recipient_name: str,
        contract_title: str,
        counterparty: str | None,
        obligations: list[dict],
        contract_url: str | None,
    ) -> str:
        safe_title = str(contract_title or '').strip() or 'Contract'
        safe_counterparty = (str(counterparty).strip() if counterparty else '')
        items = obligations if isinstance(obligations, list) else []

        rows = []
        for o in items[:25]:
            if not isinstance(o, dict):
                continue
            action = str(o.get('action') or o.get('obligation') or '').strip()
            due = str(o.get('due_date') or o.get('due') or '').strip()
            owner = str(o.get('owner') or o.get('party') or '').strip()
            if not action:
                continue
            meta = ' · '.join([p for p in [owner, due] if p])
            meta_span = f'<span style="color:#6b7280;"> ({meta})</span>' if meta else ''
            rows.append(f'<li style="margin:6px 0;"><strong>{action}</strong>{meta_span}</li>')

        link = (
            f"<p style=\"margin:14px 0;\"><a href=\"{contract_url}\" style=\"display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;padding:10px 14px;border-radius:10px;font-weight:700;\">Open contract</a></p>"
            if contract_url
            else ''
        )

        header = f"<p style=\"margin:0 0 10px 0;\">Hi <strong>{recipient_name}</strong>,</p>"
        subtitle = f"<p style=\"margin:0 0 12px 0;color:#374151;\">These items look overdue for <strong>{safe_title}</strong>{(f' with <strong>{safe_counterparty}</strong>' if safe_counterparty else '')}.</p>"

        list_html = (
            f"<ul style=\"margin:10px 0 0 18px;padding:0;color:#111827;\">{''.join(rows)}</ul>"
            if rows
            else "<p style=\"margin:0;color:#6b7280;\">No structured obligations were found, but the contract appears time-sensitive.</p>"
        )

        return (
            "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,Arial,sans-serif;color:#111827;\">"
            + header
            + subtitle
            + list_html
            + link
            + "<p style=\"margin:14px 0 0 0;color:#6b7280;font-size:12px;\">This reminder was generated automatically by Lawflow.</p>"
            + "</div>"
        )

    def _get_contract_renewal_template(
        self,
        *,
        recipient_name: str,
        contract_title: str,
        counterparty: str | None,
        term_brief: dict,
        contract_url: str | None,
    ) -> str:
        safe_title = str(contract_title or '').strip() or 'Contract'
        safe_counterparty = (str(counterparty).strip() if counterparty else '')
        b = term_brief if isinstance(term_brief, dict) else {}

        def row(label: str, value: str) -> str:
            v = (value or '').strip()
            if not v:
                return ''
            return f"<tr><td style=\"padding:6px 10px;color:#6b7280;font-size:12px;\">{label}</td><td style=\"padding:6px 10px;color:#111827;font-size:12px;\">{v}</td></tr>"

        table_rows = ''.join(
            [
                row('Status', str(b.get('status') or '')),
                row('Type', str(b.get('contract_type') or '')),
                row('Value', str(b.get('value') or '')),
                row('Start date', str(b.get('start_date') or '')),
                row('End date', str(b.get('end_date') or '')),
            ]
        )

        link = (
            f"<p style=\"margin:14px 0;\"><a href=\"{contract_url}\" style=\"display:inline-block;background:#4f46e5;color:#fff;text-decoration:none;padding:10px 14px;border-radius:10px;font-weight:700;\">Open contract</a></p>"
            if contract_url
            else ''
        )

        return (
            "<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,Arial,sans-serif;color:#111827;\">"
            + f"<p style=\"margin:0 0 10px 0;\">Hi <strong>{recipient_name}</strong>,</p>"
            + f"<p style=\"margin:0 0 12px 0;color:#374151;\">Here’s a renewal brief for <strong>{safe_title}</strong>{(f' with <strong>{safe_counterparty}</strong>' if safe_counterparty else '')}.</p>"
            + f"<table style=\"border-collapse:collapse;width:100%;max-width:640px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;\">{table_rows}</table>"
            + link
            + "<p style=\"margin:14px 0 0 0;color:#6b7280;font-size:12px;\">This brief was generated automatically by Lawflow.</p>"
            + "</div>"
        )

    def _get_inhouse_completed_template(
        self,
        *,
        recipient_name: str,
        contract_title: str,
        completed_at_iso: str | None,
    ) -> str:
        completed_line = (
            f"<p style=\"margin: 6px 0; color: #555;\"><strong>Completed at:</strong> {completed_at_iso}</p>"
            if completed_at_iso
            else ""
        )

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset=\"UTF-8\">
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #111; }}
                .container {{ max-width: 640px; margin: 0 auto; background-color: #f5f5f5; padding: 22px; border-radius: 10px; }}
                .header {{ background: linear-gradient(135deg, #16a34a 0%, #059669 100%); color: white; padding: 20px; border-radius: 10px 10px 0 0; }}
                .header h1 {{ margin: 0; font-size: 20px; }}
                .content {{ background-color: white; padding: 26px; border-radius: 0 0 10px 10px; }}
                .card {{ background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 10px; padding: 14px 16px; margin: 14px 0; }}
                .muted {{ color: #6b7280; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class=\"container\">
                <div class=\"header\">
                    <h1>Contract fully executed</h1>
                </div>
                <div class=\"content\">
                    <p>Hi <strong>{recipient_name}</strong>,</p>
                    <p>The contract below has been fully signed and executed.</p>
                    <div class=\"card\">
                        <p style=\"margin: 6px 0;\"><strong>Contract:</strong> {contract_title}</p>
                        {completed_line}
                    </div>
                    <p>Attached: executed PDF and completion certificate.</p>
                    <p class=\"muted\">This is an automated message. Please do not reply.</p>
                </div>
            </div>
        </body>
        </html>
        """
    
    def _get_approval_request_template(
        self,
        recipient_name: str,
        approver_name: str,
        document_title: str,
        document_type: str,
        requester_name: str,
        approval_id: str,
        priority: str
    ) -> str:
        """Generate HTML template for approval request email"""
        
        priority_color = {
            'urgent': '#dc3545',
            'high': '#fd7e14',
            'normal': '#0056b3'
        }.get(priority, '#0056b3')
        
        approve_url = f"{self.app_url}/approvals/{approval_id}/approve"
        reject_url = f"{self.app_url}/approvals/{approval_id}/reject"
        view_url = f"{self.app_url}/approvals/{approval_id}"
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #f5f5f5;
                    padding: 20px;
                    border-radius: 8px;
                }}
                .header {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 8px 8px 0 0;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                }}
                .content {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 0 0 8px 8px;
                    margin-bottom: 20px;
                }}
                .priority-badge {{
                    display: inline-block;
                    background-color: {priority_color};
                    color: white;
                    padding: 8px 16px;
                    border-radius: 4px;
                    font-weight: bold;
                    margin-bottom: 15px;
                    text-transform: uppercase;
                    font-size: 12px;
                }}
                .info-box {{
                    background-color: #f0f0f0;
                    border-left: 4px solid #667eea;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 4px;
                }}
                .info-box strong {{
                    color: #667eea;
                }}
                .action-buttons {{
                    margin: 30px 0;
                    text-align: center;
                }}
                .btn {{
                    display: inline-block;
                    padding: 12px 30px;
                    margin: 0 10px;
                    border-radius: 4px;
                    text-decoration: none;
                    font-weight: bold;
                    font-size: 14px;
                    transition: all 0.3s ease;
                }}
                .btn-approve {{
                    background-color: #28a745;
                    color: white;
                }}
                .btn-approve:hover {{
                    background-color: #218838;
                    text-decoration: none;
                }}
                .btn-reject {{
                    background-color: #dc3545;
                    color: white;
                }}
                .btn-reject:hover {{
                    background-color: #c82333;
                    text-decoration: none;
                }}
                .btn-view {{
                    background-color: #0056b3;
                    color: white;
                }}
                .btn-view:hover {{
                    background-color: #004085;
                    text-decoration: none;
                }}
                .footer {{
                    background-color: #f5f5f5;
                    padding: 15px;
                    text-align: center;
                    font-size: 12px;
                    color: #666;
                    border-radius: 4px;
                }}
                .document-details {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 20px 0;
                }}
                .document-details tr {{
                    border-bottom: 1px solid #ddd;
                }}
                .document-details td {{
                    padding: 12px;
                }}
                .document-details td:first-child {{
                    font-weight: bold;
                    color: #667eea;
                    width: 30%;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📋 Approval Request</h1>
                </div>
                
                <div class="content">
                    <p>Hi <strong>{recipient_name}</strong>,</p>
                    
                    <p>You have received a new approval request from <strong>{requester_name}</strong>.</p>
                    
                    <div class="priority-badge">Priority: {priority.upper()}</div>
                    
                    <div class="info-box">
                        <strong>Document Details:</strong>
                        <table class="document-details">
                            <tr>
                                <td>Document Type:</td>
                                <td>{document_type}</td>
                            </tr>
                            <tr>
                                <td>Document Title:</td>
                                <td><strong>{document_title}</strong></td>
                            </tr>
                            <tr>
                                <td>Requested By:</td>
                                <td>{requester_name}</td>
                            </tr>
                            <tr>
                                <td>Request Date:</td>
                                <td>{datetime.now().strftime('%B %d, %Y at %I:%M %p')}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <p>Please review the document and take action:</p>
                    
                    <div class="action-buttons">
                        <a href="{approve_url}" class="btn btn-approve">✓ APPROVE</a>
                        <a href="{reject_url}" class="btn btn-reject">✗ REJECT</a>
                        <a href="{view_url}" class="btn btn-view">📄 VIEW DETAILS</a>
                    </div>
                    
                    <p style="color: #666; font-size: 13px; margin-top: 30px;">
                        <strong>Note:</strong> You can also approve or reject this request directly by clicking the buttons above. 
                        No login required for quick actions.
                    </p>
                </div>
                
                <div class="footer">
                    <p>© 2026 Contract Lifecycle Management System</p>
                    <p>This is an automated notification. Please do not reply to this email.</p>
                    <p>Request ID: {approval_id}</p>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    
    def _get_approval_approved_template(
        self,
        recipient_name: str,
        document_title: str,
        approver_name: str,
        approval_comment: str
    ) -> str:
        """Generate HTML template for approval approved email"""
        
        view_url = f"{self.app_url}/documents/view"
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #f5f5f5;
                    padding: 20px;
                    border-radius: 8px;
                }}
                .header {{
                    background: linear-gradient(135deg, #28a745 0%, #20c997 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 8px 8px 0 0;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                }}
                .content {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 0 0 8px 8px;
                    margin-bottom: 20px;
                }}
                .success-message {{
                    background-color: #d4edda;
                    border-left: 4px solid #28a745;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 4px;
                    color: #155724;
                }}
                .info-box {{
                    background-color: #f0f0f0;
                    border-left: 4px solid #28a745;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 4px;
                }}
                .info-box strong {{
                    color: #28a745;
                }}
                .action-button {{
                    display: inline-block;
                    padding: 12px 30px;
                    margin: 20px 0;
                    background-color: #28a745;
                    color: white;
                    text-decoration: none;
                    border-radius: 4px;
                    font-weight: bold;
                }}
                .action-button:hover {{
                    background-color: #218838;
                    text-decoration: none;
                }}
                .footer {{
                    background-color: #f5f5f5;
                    padding: 15px;
                    text-align: center;
                    font-size: 12px;
                    color: #666;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>✅ Approval Approved</h1>
                </div>
                
                <div class="content">
                    <p>Hi <strong>{recipient_name}</strong>,</p>
                    
                    <div class="success-message">
                        <strong>Good News!</strong> Your document "<strong>{document_title}</strong>" 
                        has been approved by <strong>{approver_name}</strong>.
                    </div>
                    
                    <div class="info-box">
                        <p><strong>Document:</strong> {document_title}</p>
                        <p><strong>Approved By:</strong> {approver_name}</p>
                        <p><strong>Approval Date:</strong> {datetime.now().strftime('%B %d, %Y at %I:%M %p')}</p>
                        {f'<p><strong>Comment:</strong> {approval_comment}</p>' if approval_comment else ''}
                    </div>
                    
                    <p>Your document has successfully passed the approval stage and is now ready for the next steps.</p>
                    
                    <center>
                        <a href="{view_url}" class="action-button">📄 View Document</a>
                    </center>
                </div>
                
                <div class="footer">
                    <p>© 2026 Contract Lifecycle Management System</p>
                    <p>This is an automated notification. Please do not reply to this email.</p>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    
    def _get_approval_rejected_template(
        self,
        recipient_name: str,
        document_title: str,
        approver_name: str,
        rejection_reason: str
    ) -> str:
        """Generate HTML template for approval rejected email"""
        
        resubmit_url = f"{self.app_url}/documents/revise"
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #f5f5f5;
                    padding: 20px;
                    border-radius: 8px;
                }}
                .header {{
                    background: linear-gradient(135deg, #dc3545 0%, #fd7e14 100%);
                    color: white;
                    padding: 20px;
                    border-radius: 8px 8px 0 0;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                }}
                .content {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 0 0 8px 8px;
                    margin-bottom: 20px;
                }}
                .rejection-message {{
                    background-color: #f8d7da;
                    border-left: 4px solid #dc3545;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 4px;
                    color: #721c24;
                }}
                .reason-box {{
                    background-color: #fff3cd;
                    border-left: 4px solid #fd7e14;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 4px;
                }}
                .reason-box strong {{
                    color: #856404;
                }}
                .action-button {{
                    display: inline-block;
                    padding: 12px 30px;
                    margin: 20px 0;
                    background-color: #fd7e14;
                    color: white;
                    text-decoration: none;
                    border-radius: 4px;
                    font-weight: bold;
                }}
                .action-button:hover {{
                    background-color: #e0670b;
                    text-decoration: none;
                }}
                .footer {{
                    background-color: #f5f5f5;
                    padding: 15px;
                    text-align: center;
                    font-size: 12px;
                    color: #666;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>❌ Approval Rejected</h1>
                </div>
                
                <div class="content">
                    <p>Hi <strong>{recipient_name}</strong>,</p>
                    
                    <div class="rejection-message">
                        <strong>Action Required</strong> - Your document "<strong>{document_title}</strong>" 
                        has been rejected by <strong>{approver_name}</strong>.
                    </div>
                    
                    {f'''
                    <div class="reason-box">
                        <strong>Reason for Rejection:</strong>
                        <p>{rejection_reason}</p>
                    </div>
                    ''' if rejection_reason else ''}
                    
                    <p>Please review the feedback above and make the necessary revisions to your document. 
                    Once you've made the changes, you can resubmit it for approval.</p>
                    
                    <center>
                        <a href="{resubmit_url}" class="action-button">📝 Revise & Resubmit</a>
                    </center>
                </div>
                
                <div class="footer">
                    <p>© 2026 Contract Lifecycle Management System</p>
                    <p>This is an automated notification. Please do not reply to this email.</p>
                </div>
            </div>
        </body>
        </html>
        """
        return html
