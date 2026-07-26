from typing import Protocol

from supabase import Client

from app.core.config import Settings
from app.core.supabase import create_supabase_admin


class ExportStorage(Protocol):
    def upload(self, object_key: str, content: bytes) -> None: ...

    def create_download_url(self, object_key: str, ttl_seconds: int) -> str: ...

    def delete(self, object_key: str) -> None: ...


class SupabaseExportStorage:
    def __init__(self, client: Client, bucket: str) -> None:
        self._bucket = client.storage.from_(bucket)

    def upload(self, object_key: str, content: bytes) -> None:
        self._bucket.upload(
            path=object_key,
            file=content,
            file_options={
                "content-type": "application/zip",
                "cache-control": "no-store",
                "upsert": "true",
            },
        )

    def create_download_url(self, object_key: str, ttl_seconds: int) -> str:
        response = self._bucket.create_signed_url(
            object_key,
            ttl_seconds,
            {"download": True},
        )
        url = response.get("signedUrl") or response.get("signedURL")
        if not url:
            raise RuntimeError("Storage did not return a signed URL")
        return url

    def delete(self, object_key: str) -> None:
        self._bucket.remove([object_key])


def create_export_storage(settings: Settings) -> ExportStorage:
    return SupabaseExportStorage(
        create_supabase_admin(settings),
        settings.export_storage_bucket,
    )
