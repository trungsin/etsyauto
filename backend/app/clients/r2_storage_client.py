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

    def upload_template_image(self, file_bytes: bytes, key: str | None = None) -> str:
        """Upload a template base image to R2 under 'templates/' prefix.

        Args:
            file_bytes: Raw image bytes.
            key: Optional explicit object key. If None, a UUID key is generated.

        Returns:
            Public URL string.
        """
        import uuid
        if key is None:
            key = f"templates/{uuid.uuid4().hex}.png"
        elif not key.startswith("templates/"):
            key = f"templates/{key}"
        return self.upload_image(file_bytes, key)

    def delete_object(self, key: str) -> None:
        """Delete an object from R2 by key. Logs warning on failure but does not raise.

        Args:
            key: Object key to delete (e.g. 'templates/abc123.png').
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
            logger.info("Deleted R2 object: %s", key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 delete failed for key %s: %s", key, exc)

    def object_exists(self, key: str) -> bool:
        """Return True if an object with *key* exists in the bucket.

        Uses head_object which is a cheap Class B read operation.

        Args:
            key: Object key to check.

        Returns:
            True if the object exists, False otherwise.
        """
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except self._client.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            logger.warning("R2 head_object error for key %s: %s", key, exc)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 object_exists unexpected error for key %s: %s", key, exc)
            return False

    def get_public_url(self, key: str) -> str:
        """Return the public URL for an object key.

        Args:
            key: Object key (e.g. 'composites/1-2.png').

        Returns:
            Public URL string (R2_PUBLIC_URL/key).
        """
        return f"{self._public_url}/{key}"

    def list_objects(self, prefix: str) -> list[str]:
        """List all object keys under *prefix* (handles pagination).

        Args:
            prefix: Key prefix to filter by (e.g. 'composites/1-').

        Returns:
            List of object key strings matching the prefix.
        """
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("R2 list_objects failed for prefix %s: %s", prefix, exc)
        return keys
