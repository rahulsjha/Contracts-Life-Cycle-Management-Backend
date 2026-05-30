"""
Cloudflare R2 Storage Service
"""
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
import uuid
import mimetypes
import re
import hashlib
import unicodedata
from urllib.parse import quote
from typing import Any, Dict, List, Optional


class R2StorageService:
    """
    Service for interacting with Cloudflare R2 storage
    """
    
    def __init__(self):
        required = {
            'R2_ENDPOINT_URL': getattr(settings, 'R2_ENDPOINT_URL', ''),
            'R2_ACCESS_KEY_ID': getattr(settings, 'R2_ACCESS_KEY_ID', ''),
            'R2_SECRET_ACCESS_KEY': getattr(settings, 'R2_SECRET_ACCESS_KEY', ''),
            'R2_BUCKET_NAME': getattr(settings, 'R2_BUCKET_NAME', ''),
        }
        missing = [k for k, v in required.items() if not str(v or '').strip()]
        if missing:
            raise Exception(
                'Cloudflare R2 is not configured. Missing: ' + ', '.join(missing)
            )

        self.client = boto3.client(
            's3',
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            config=Config(
                signature_version='s3v4',
                connect_timeout=int(getattr(settings, 'R2_CONNECT_TIMEOUT', 5) or 5),
                read_timeout=int(getattr(settings, 'R2_READ_TIMEOUT', 30) or 30),
                retries={'max_attempts': 3, 'mode': 'standard'},
            ),
            region_name='auto'
        )
        self.bucket_name = settings.R2_BUCKET_NAME
    
    def upload_file(self, file_obj, tenant_id, filename=None):
        """
        Upload a file to R2
        
        Args:
            file_obj: Django UploadedFile object
            tenant_id: UUID of the tenant
            filename: Optional custom filename
        
        Returns:
            str: The R2 key (path) of the uploaded file
        """
        if not filename:
            filename = file_obj.name
        
        # Generate unique key with tenant isolation
        file_extension = filename.split('.')[-1] if '.' in filename else ''
        unique_id = str(uuid.uuid4())
        r2_key = f"{tenant_id}/contracts/{unique_id}.{file_extension}"
        
        # Detect content type
        content_type, _ = mimetypes.guess_type(filename)
        if not content_type:
            content_type = 'application/octet-stream'
        
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=r2_key,
                Body=file_obj.read(),
                ContentType=content_type,
                Metadata=self._sanitize_metadata(
                    {
                        'tenant_id': str(tenant_id),
                        'original_filename': filename,
                    }
                ),
            )
            return r2_key
        except ClientError as e:
            raise Exception(f"Failed to upload file to R2: {str(e)}")

    @staticmethod
    def _sanitize_metadata_value(value: Any, *, max_len: int = 1024) -> str:
        """Ensure R2/S3 metadata values are ASCII-only.

        Botocore validates metadata values as ASCII. We percent-encode UTF-8
        so we can preserve information while staying within ASCII.
        """
        if value is None:
            return ''
        raw = str(value)
        # Normalize to reduce surprises (e.g. different dash variants)
        raw = unicodedata.normalize('NFKC', raw)
        # Percent-encode any non-safe chars into ASCII bytes.
        # Keep some common filename chars readable.
        encoded = quote(raw, safe=" ._()-")

        if max_len and len(encoded) > max_len:
            digest = hashlib.sha1(encoded.encode('ascii', errors='ignore')).hexdigest()[:10]
            keep = max(0, max_len - (len(digest) + len('__trunc__')))
            encoded = f"{encoded[:keep]}__trunc__{digest}"
        return encoded

    @classmethod
    def _sanitize_metadata(cls, metadata: Optional[Dict[str, Any]]) -> Dict[str, str]:
        md_in = metadata or {}
        md_out: Dict[str, str] = {}
        for k, v in md_in.items():
            if not k:
                continue
            if v is None:
                continue
            md_out[str(k)] = cls._sanitize_metadata_value(v)
        return md_out

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str = 'application/octet-stream',
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """Upload raw bytes to a specific R2 key.

        This is used for deterministic objects (e.g. editor snapshots) where callers
        control the full key path.
        """
        if not key or not str(key).strip():
            raise Exception('R2 key is required')
        if body is None:
            body = b''

        md = self._sanitize_metadata(metadata)

        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=str(key),
                Body=body,
                ContentType=content_type or 'application/octet-stream',
                Metadata=md,
            )
            return str(key)
        except ClientError as e:
            raise Exception(f"Failed to upload bytes to R2: {str(e)}")

    def put_text(
        self,
        key: str,
        text: str,
        *,
        content_type: str = 'text/plain; charset=utf-8',
        metadata: Optional[Dict[str, str]] = None,
        encoding: str = 'utf-8',
    ) -> str:
        """Upload text content to a specific R2 key."""
        return self.put_bytes(
            key,
            (text or '').encode(encoding, errors='replace'),
            content_type=content_type,
            metadata=metadata,
        )

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        name = (filename or '').strip()
        name = name.replace('\\', '').replace('/', '')
        name = re.sub(r'[^A-Za-z0-9._ -]+', '', name)
        name = re.sub(r'\s+', '_', name).strip('_')
        return name or f"file_{uuid.uuid4()}"

    def upload_private_file(self, file_obj, tenant_id: str, user_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
        """Upload a private file under a per-user prefix.

        Storage is R2-only. No local filesystem writes.
        """
        original_name = filename or getattr(file_obj, 'name', '') or 'upload'
        safe_name = self.sanitize_filename(original_name)
        ext = safe_name.split('.')[-1].lower() if '.' in safe_name else ''
        unique_id = str(uuid.uuid4())
        r2_key = f"{tenant_id}/private_uploads/{user_id}/{unique_id}--{safe_name}"

        content_type, _ = mimetypes.guess_type(safe_name)
        if not content_type:
            content_type = 'application/octet-stream'

        self.client.put_object(
            Bucket=self.bucket_name,
            Key=r2_key,
            Body=file_obj.read(),
            ContentType=content_type,
            Metadata=self._sanitize_metadata(
                {
                    'tenant_id': str(tenant_id),
                    'user_id': str(user_id),
                    'original_filename': original_name,
                }
            ),
        )

        return {
            'key': r2_key,
            'filename': safe_name,
            'content_type': content_type,
        }

    def upload_review_contract_file(self, file_obj, tenant_id: str, user_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
        """Upload a contract intended for review/validation under a dedicated prefix."""
        original_name = filename or getattr(file_obj, 'name', '') or 'review'
        safe_name = self.sanitize_filename(original_name)
        ext = safe_name.split('.')[-1].lower() if '.' in safe_name else ''
        unique_id = str(uuid.uuid4())
        r2_key = f"{tenant_id}/review_contracts/{user_id}/{unique_id}--{safe_name}"

        content_type, _ = mimetypes.guess_type(safe_name)
        if not content_type:
            content_type = 'application/octet-stream'

        self.client.put_object(
            Bucket=self.bucket_name,
            Key=r2_key,
            Body=file_obj.read(),
            ContentType=content_type,
            Metadata=self._sanitize_metadata(
                {
                    'tenant_id': str(tenant_id),
                    'user_id': str(user_id),
                    'original_filename': original_name,
                    'purpose': 'review_contract',
                    'file_ext': ext,
                }
            ),
        )

        return {
            'key': r2_key,
            'filename': safe_name,
            'content_type': content_type,
        }

    def list_objects(self, prefix: str, max_keys: int = 200) -> List[Dict[str, Any]]:
        """List objects under a prefix."""
        try:
            resp = self.client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix, MaxKeys=max_keys)
            contents = resp.get('Contents') or []
            results: List[Dict[str, Any]] = []
            for obj in contents:
                results.append(
                    {
                        'key': obj.get('Key'),
                        'size': int(obj.get('Size') or 0),
                        'last_modified': obj.get('LastModified').isoformat() if obj.get('LastModified') else None,
                    }
                )
            return results
        except ClientError as e:
            raise Exception(f"Failed to list objects: {str(e)}")
    
    def generate_presigned_url(self, r2_key, expiration=3600):
        """
        Generate a presigned URL for secure file access
        
        Args:
            r2_key: The R2 key (path) of the file
            expiration: URL expiration time in seconds (default: 1 hour)
        
        Returns:
            str: Presigned URL
        """
        # If a public base URL is configured, return a stable public URL (no expiry).
        public_base = getattr(settings, 'R2_PUBLIC_URL', '') or getattr(settings, 'R2_PUBLIC_BASE_URL', '')
        if public_base:
            public_base = public_base.rstrip('/')
            # Many deployments expose the bucket under the public base URL using the pattern:
            # {R2_PUBLIC_URL}/{bucket}/{key} or sometimes {R2_PUBLIC_URL}/{key} depending on setup.
            # Try the common pattern first including bucket name.
            try:
                return f"{public_base}/{self.bucket_name}/{r2_key}"
            except Exception:
                return f"{public_base}/{r2_key}"

        try:
            url = self.client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': r2_key
                },
                ExpiresIn=expiration
            )
            return url
        except ClientError as e:
            raise Exception(f"Failed to generate presigned URL: {str(e)}")

    def get_file_bytes(self, r2_key: str) -> bytes:
        """Download an object from R2 and return its bytes."""
        try:
            resp = self.client.get_object(Bucket=self.bucket_name, Key=r2_key)
            body = resp.get('Body')
            return body.read() if body else b''
        except ClientError as e:
            raise Exception(f"Failed to download file from R2: {str(e)}")
    
    def delete_file(self, r2_key):
        """
        Delete a file from R2
        
        Args:
            r2_key: The R2 key (path) of the file to delete
        
        Returns:
            bool: True if successful
        """
        try:
            self.client.delete_object(
                Bucket=self.bucket_name,
                Key=r2_key
            )
            return True
        except ClientError as e:
            raise Exception(f"Failed to delete file from R2: {str(e)}")
    
    def file_exists(self, r2_key):
        """
        Check if a file exists in R2
        
        Args:
            r2_key: The R2 key (path) of the file
        
        Returns:
            bool: True if file exists
        """
        try:
            self.client.head_object(
                Bucket=self.bucket_name,
                Key=r2_key
            )
            return True
        except ClientError:
            return False
