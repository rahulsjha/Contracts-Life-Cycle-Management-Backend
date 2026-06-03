"""Private (per-user) uploads stored in Cloudflare R2 only.

Supports PDF + TXT uploads with JWT auth.
No document bytes are stored in the DB or filesystem.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.r2_service import R2StorageService


MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB
ALLOWED_EXTENSIONS = {"pdf", "txt", "docx", "md"}


def _get_user_ids(request) -> Dict[str, Optional[str]]:
    user = request.user
    tenant_id = getattr(user, "tenant_id", None)
    user_id = getattr(user, "user_id", None)
    return {
        "tenant_id": str(tenant_id) if tenant_id else None,
        "user_id": str(user_id) if user_id else None,
    }


def _private_prefix(tenant_id: str, user_id: str) -> str:
    return f"{tenant_id}/private_uploads/{user_id}/"


def _key_to_filename(key: str) -> str:
    base = os.path.basename(key or "")
    if "--" in base:
        return base.split("--", 1)[1]
    return base


def _key_to_file_type(key: str) -> str:
    filename = _key_to_filename(key)
    if "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return "unknown"


class PrivateUploadsView(APIView):
    """R2-only private uploads (per user).

    GET    /api/v1/private-uploads/            -> list current user's uploads
    POST   /api/v1/private-uploads/            -> upload a new file (multipart)
    DELETE /api/v1/private-uploads/?key=...    -> delete an upload
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)
    throttle_scope = 'uploads'

    def get(self, request):
        ids = _get_user_ids(request)
        tenant_id = ids["tenant_id"]
        user_id = ids["user_id"]
        if not tenant_id or not user_id:
            return Response({"success": False, "error": "User missing tenant/user id"}, status=status.HTTP_400_BAD_REQUEST)

        prefix = _private_prefix(tenant_id, user_id)
        try:
            r2 = R2StorageService()
            objects = r2.list_objects(prefix=prefix, max_keys=500)

            results: List[Dict[str, Any]] = []
            for obj in objects:
                key = obj.get("key") or ""
                results.append(
                    {
                        "key": key,
                        "filename": _key_to_filename(key),
                        "file_type": _key_to_file_type(key),
                        "size": obj.get("size") or 0,
                        "uploaded_at": obj.get("last_modified"),
                    }
                )

            # Sort newest first (R2 returns lexicographic in some cases)
            results.sort(key=lambda x: x.get("uploaded_at") or "", reverse=True)

            return Response({"success": True, "count": len(results), "results": results}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response(
                {"success": False, "error": 'No file provided. Use form field name "file".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if getattr(file_obj, "size", 0) > MAX_UPLOAD_BYTES:
            return Response(
                {"success": False, "error": "File too large. Max size is 25MB."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        filename = getattr(file_obj, "name", "") or "upload"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ALLOWED_EXTENSIONS:
            return Response(
                {"success": False, "error": "Only .pdf, .txt and .docx files are supported."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ids = _get_user_ids(request)
        tenant_id = ids["tenant_id"]
        user_id = ids["user_id"]
        if not tenant_id or not user_id:
            return Response({"success": False, "error": "User missing tenant/user id"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            r2 = R2StorageService()
            info = r2.upload_private_file(file_obj, tenant_id=tenant_id, user_id=user_id, filename=filename)
            return Response(
                {
                    "success": True,
                    "file": {
                        "key": info.get("key"),
                        "filename": info.get("filename"),
                        "file_type": ext,
                        "size": getattr(file_obj, "size", 0),
                    },
                },
                status=status.HTTP_201_CREATED,
            )
        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request):
        key = request.query_params.get("key")
        if not key:
            return Response({"success": False, "error": "Missing key"}, status=status.HTTP_400_BAD_REQUEST)

        ids = _get_user_ids(request)
        tenant_id = ids["tenant_id"]
        user_id = ids["user_id"]
        if not tenant_id or not user_id:
            return Response({"success": False, "error": "User missing tenant/user id"}, status=status.HTTP_400_BAD_REQUEST)

        prefix = _private_prefix(tenant_id, user_id)
        if not str(key).startswith(prefix):
            return Response({"success": False, "error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            r2 = R2StorageService()
            r2.delete_file(str(key))
            return Response({"success": True}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class PrivateUploadsUrlView(APIView):
    """Presigned URL for preview/download.

    GET /api/v1/private-uploads/url/?key=...
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = 'uploads'

    def get(self, request):
        key = request.query_params.get("key")
        if not key:
            return Response({"success": False, "error": "Missing key"}, status=status.HTTP_400_BAD_REQUEST)

        ids = _get_user_ids(request)
        tenant_id = ids["tenant_id"]
        user_id = ids["user_id"]
        if not tenant_id or not user_id:
            return Response({"success": False, "error": "User missing tenant/user id"}, status=status.HTTP_400_BAD_REQUEST)

        prefix = _private_prefix(tenant_id, user_id)
        if not str(key).startswith(prefix):
            return Response({"success": False, "error": "Access denied"}, status=status.HTTP_403_FORBIDDEN)

        try:
            r2 = R2StorageService()
            url = r2.generate_presigned_url(str(key), expiration=3600)
            return Response({"success": True, "key": str(key), "url": url, "expires_in": 3600}, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
