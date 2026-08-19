"""
Azure Blob Storage client — manages document uploads and retrieval.
Used by the Knowledge Base ingestion pipeline to store source documents.
"""
from __future__ import annotations

import io
from pathlib import Path
from structlog import get_logger

logger = get_logger(__name__)


class BlobStorageClient:
    """
    Azure Blob Storage wrapper for Knowledge Base documents.
    Falls back to local filesystem storage for development.
    """

    CONTAINER_NAME = "kb-documents"

    def __init__(self, connection_string: str | None = None, local_path: str = "./blob_storage"):
        self._connection_string = connection_string
        self._local_path = Path(local_path)
        self._client = None
        self._available = False
        if connection_string:
            self._try_init(connection_string)

    def _try_init(self, connection_string: str) -> None:
        try:
            from azure.storage.blob.aio import BlobServiceClient
            self._client = BlobServiceClient.from_connection_string(connection_string)
            self._available = True
            logger.info("blob_storage_connected")
        except ImportError:
            logger.warning("blob_storage_unavailable", reason="azure-storage-blob not installed")
        except Exception as e:
            logger.warning("blob_storage_init_failed", error=str(e), fallback="local")

    async def upload(
        self,
        file_data: bytes,
        blob_name: str,
        content_type: str = "application/octet-stream",
        overwrite: bool = True,
    ) -> str:
        """Upload file bytes and return the blob URL/path."""
        if self._available and self._client:
            return await self._upload_azure(file_data, blob_name, content_type, overwrite)
        return self._upload_local(file_data, blob_name)

    async def _upload_azure(self, data: bytes, name: str, content_type: str, overwrite: bool) -> str:
        try:
            container = self._client.get_container_client(self.CONTAINER_NAME)
            try:
                await container.create_container()
            except Exception:
                pass  # Already exists
            blob = container.get_blob_client(name)
            await blob.upload_blob(
                data,
                overwrite=overwrite,
                content_settings={"content_type": content_type},
            )
            url = blob.url
            logger.info("blob_uploaded", name=name, size=len(data))
            return url
        except Exception as e:
            logger.error("blob_upload_failed", name=name, error=str(e))
            return self._upload_local(data, name)

    def _upload_local(self, data: bytes, name: str) -> str:
        """Local filesystem fallback for development."""
        dest = self._local_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        logger.debug("blob_uploaded_local", path=str(dest))
        return str(dest)

    async def download(self, blob_name: str) -> bytes:
        """Download file bytes by blob name."""
        if self._available and self._client:
            try:
                container = self._client.get_container_client(self.CONTAINER_NAME)
                blob = container.get_blob_client(blob_name)
                stream = await blob.download_blob()
                return await stream.readall()
            except Exception as e:
                logger.error("blob_download_failed", name=blob_name, error=str(e))

        # Fallback to local
        local_file = self._local_path / blob_name
        if local_file.exists():
            return local_file.read_bytes()
        raise FileNotFoundError(f"Blob not found: {blob_name}")

    async def delete(self, blob_name: str) -> bool:
        """Delete a blob by name."""
        if self._available and self._client:
            try:
                container = self._client.get_container_client(self.CONTAINER_NAME)
                blob = container.get_blob_client(blob_name)
                await blob.delete_blob()
                return True
            except Exception as e:
                logger.warning("blob_delete_failed", name=blob_name, error=str(e))
                return False

        local_file = self._local_path / blob_name
        if local_file.exists():
            local_file.unlink()
            return True
        return False

    async def get_url(self, blob_name: str, expiry_hours: int = 24) -> str:
        """Generate a SAS URL for temporary access."""
        if not self._available:
            return f"local://{blob_name}"
        try:
            from datetime import datetime, timedelta, timezone
            from azure.storage.blob import generate_blob_sas, BlobSasPermissions
            from azure.storage.blob.aio import BlobServiceClient as SyncClient

            expiry = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
            # SAS token generation requires sync client for key access
            sas = generate_blob_sas(
                account_name=self._client.account_name,
                container_name=self.CONTAINER_NAME,
                blob_name=blob_name,
                account_key=self._client.credential.account_key,
                permission=BlobSasPermissions(read=True),
                expiry=expiry,
            )
            return f"https://{self._client.account_name}.blob.core.windows.net/{self.CONTAINER_NAME}/{blob_name}?{sas}"
        except Exception as e:
            logger.warning("sas_url_generation_failed", error=str(e))
            return f"local://{blob_name}"


# Module-level singleton
_blob_client: BlobStorageClient | None = None


def get_blob_client() -> BlobStorageClient:
    global _blob_client
    if _blob_client is None:
        try:
            from app.core.config import settings
            conn_str_secret = getattr(settings, "AZURE_STORAGE_CONNECTION_STRING", None)
            conn_str = conn_str_secret.get_secret_value() if conn_str_secret else None
            _blob_client = BlobStorageClient(connection_string=conn_str)
        except Exception:
            _blob_client = BlobStorageClient()
    return _blob_client
