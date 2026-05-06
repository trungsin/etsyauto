"""Cloudflare R2 storage client — S3-compatible upload via boto3."""
import logging
import mimetypes

import boto3
from botocore.config import Config

from app.config import settings

logger = logging.getLogger(__name__)


class R2StorageClient:
    """Thin wrapper around boto3 S3 client pointing at Cloudflare R2 endpoint.

    Requires env vars: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT,
    R2_BUCKET_NAME, R2_PUBLIC_URL.
    """

    def __init__(self) -> None:
        if not settings.r2_endpoint or not settings.r2_access_key_id:
            raise ValueError(
                "R2 not configured: missing R2_ENDPOINT or R2_ACCESS_KEY_ID in settings"
            )

        self._bucket = settings.r2_bucket_name
        self._public_url = settings.r2_public_url.rstrip("/")

        self._client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )

    def upload_image(self, file_bytes: bytes, key: str) -> str:
        """Upload raw bytes to R2 under *key* and return the public URL.

        Args:
            file_bytes: Raw image bytes.
            key: Object key inside the bucket (e.g. 'mockups/abc123.png').

        Returns:
            Public URL string (R2_PUBLIC_URL/key).

        Raises:
            botocore.exceptions.ClientError: on upload failure.
        """
        content_type, _ = mimetypes.guess_type(key)
        if not content_type:
            content_type = "image/png"

        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
        )

        public_url = f"{self._public_url}/{key}"
        logger.info("Uploaded %d bytes to R2: %s", len(file_bytes), public_url)
        return public_url
