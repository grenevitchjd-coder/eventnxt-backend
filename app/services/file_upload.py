"""
Real file upload to Cloudflare R2 (S3-compatible object storage) — used
for event banner photos. Requires R2_* config vars to be set (an actual
R2 bucket + API token, created on Cloudflare's dashboard); without them,
upload attempts fail with a clear error rather than a confusing one.
"""

import secrets
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, UploadFile

from app.config import settings

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_FILE_SIZE_BYTES = 8 * 1024 * 1024  # 8 MB


def _r2_client():
    if not all(
        [
            settings.r2_account_id,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
            settings.r2_bucket_name,
        ]
    ):
        raise HTTPException(
            status_code=503,
            detail="File upload isn't configured yet — R2 credentials are missing on the server.",
        )
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


async def upload_banner_photo(file: UploadFile) -> str:
    """
    Validates and uploads an image, returns its public URL. Raises
    HTTPException on any problem (bad file type, too large, R2 unreachable)
    so callers can just await this and use the result directly.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file.content_type}'. Use JPEG, PNG, WEBP, or GIF.",
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Image is too large — max 8 MB.")

    extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else "jpg"
    key = f"event-banners/{uuid.uuid4()}-{secrets.token_hex(4)}.{extension}"

    client = _r2_client()
    try:
        client.put_object(
            Bucket=settings.r2_bucket_name,
            Key=key,
            Body=contents,
            ContentType=file.content_type,
        )
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=502, detail=f"Upload to storage failed: {e}")

    return f"{settings.r2_public_url_base.rstrip('/')}/{key}"