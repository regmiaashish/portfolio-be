"""Image and résumé storage for the content admin: S3, behind a small interface tests can fake.

Since Phase 6.1 the admin uploads through the API, which writes to S3 server-side (no bucket
CORS needed). The presigned POST path is kept for compatibility.
"""

import asyncio
from dataclasses import dataclass
from typing import Protocol

from loguru import logger

from src.core.config import Settings

UPLOAD_EXPIRY_SECONDS = 300
DOWNLOAD_EXPIRY_SECONDS = 300


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    fields: dict[str, str]


class StorageService(Protocol):
    def presign_upload(self, key: str, content_type: str, max_bytes: int) -> PresignedUpload: ...

    def presign_get(self, key: str, filename: str, content_type: str) -> str: ...

    async def put_object(
        self, key: str, data: bytes, content_type: str, cache_control: str
    ) -> None: ...

    async def delete(self, key: str) -> None: ...


class S3Storage:
    def __init__(self, app_settings: Settings):
        # Imported lazily: boto3 is slow to import and only needed when uploads are enabled.
        import boto3
        from botocore.config import Config

        self.bucket = app_settings.s3_bucket_name
        region = app_settings.aws_region or None
        self.client = boto3.client(
            "s3",
            region_name=region,
            # The regional endpoint: browsers POSTing to the global one get redirected (and
            # the signature breaks) for buckets outside us-east-1.
            endpoint_url=f"https://s3.{region}.amazonaws.com" if region else None,
            aws_access_key_id=app_settings.aws_access_key_id or None,
            aws_secret_access_key=app_settings.aws_secret_access_key.get_secret_value() or None,
            config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
        )

    def presign_upload(self, key: str, content_type: str, max_bytes: int) -> PresignedUpload:
        # Signing is local (no network call). boto3 adds the bucket and key conditions itself.
        presigned = self.client.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=UPLOAD_EXPIRY_SECONDS,
        )
        return PresignedUpload(url=presigned["url"], fields=dict(presigned["fields"]))

    def presign_get(self, key: str, filename: str, content_type: str) -> str:
        """A short-lived download link that makes browsers save the file as `filename`."""
        # Signing is local (no network call); `filename` is already ASCII-safe.
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
                "ResponseContentType": content_type,
            },
            ExpiresIn=DOWNLOAD_EXPIRY_SECONDS,
        )

    async def put_object(
        self, key: str, data: bytes, content_type: str, cache_control: str
    ) -> None:
        # boto3 is synchronous; keep the network call off the event loop.
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl=cache_control,
        )

    async def delete(self, key: str) -> None:
        # boto3 is synchronous; keep the network call off the event loop.
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)


def create_storage(app_settings: Settings) -> StorageService | None:
    """S3 storage when a bucket is configured, otherwise None (uploads disabled)."""
    if not app_settings.uploads_enabled:
        logger.info("S3_BUCKET_NAME is empty: image uploads are disabled")
        return None
    return S3Storage(app_settings)
