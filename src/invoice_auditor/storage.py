"""Storage for uploaded originals, addressed by organization and SHA-256.

Content addressing makes storage idempotent (the same file uploaded twice is stored once) and
keeps every tenant's files in its own namespace.

Two backends: the local file system for a server that has one, and the database for serverless
deployments, where the file system is read-only outside /tmp and never survives an invocation.
"""

import asyncio
import os
import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .models import Document

if TYPE_CHECKING:  # pragma: no cover - import cycle: Database builds on Settings, not on storage
    from .db import Database

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DocumentStore(ABC):
    """Where uploaded originals live. `save` returns the storage URL recorded on the invoice."""

    scheme: str

    @abstractmethod
    async def save(self, db: "Database", organization_id: UUID, sha256: str, content: bytes) -> str: ...

    @abstractmethod
    async def fetch(self, db: "Database", storage_url: str | None, organization_id: UUID) -> Path | bytes | None:
        """The stored original as a path or bytes, or None if this store doesn't hold it."""

    def owns(self, storage_url: str | None) -> bool:
        """True when this service stored the file and can serve it back."""
        return bool(storage_url and storage_url.startswith(self.scheme))

    def _parts(self, storage_url: str | None, organization_id: UUID) -> str | None:
        """The SHA-256 in a storage URL, if it belongs to this store and this organization."""
        if not self.owns(storage_url):
            return None
        owner, _, sha256 = storage_url[len(self.scheme) :].partition("/")  # type: ignore[index]
        if owner != str(organization_id) or not _SHA256.fullmatch(sha256):
            return None
        return sha256


class LocalDocumentStore(DocumentStore):
    scheme = "local://"

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, organization_id: UUID, sha256: str) -> Path:
        if not _SHA256.fullmatch(sha256):
            raise ValueError("not a SHA-256 hex digest")
        return self.root / str(organization_id) / sha256[:2] / sha256

    async def save(self, db: "Database", organization_id: UUID, sha256: str, content: bytes) -> str:
        path = self._path(organization_id, sha256)

        def write() -> None:
            if path.is_file():
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(f"{path.name}.{uuid.uuid4().hex}.partial")
            partial.write_bytes(content)
            os.replace(partial, path)  # atomic: readers never see half a file

        await asyncio.to_thread(write)
        return f"{self.scheme}{organization_id}/{sha256}"

    async def fetch(self, db: "Database", storage_url: str | None, organization_id: UUID) -> Path | None:
        sha256 = self._parts(storage_url, organization_id)
        if sha256 is None:
            return None
        path = self._path(organization_id, sha256)
        return path if path.is_file() else None

    # Kept for the local-only checks that predate the database backend.
    def locate(self, storage_url: str | None, organization_id: UUID) -> Path | None:
        sha256 = self._parts(storage_url, organization_id)
        if sha256 is None:
            return None
        path = self._path(organization_id, sha256)
        return path if path.is_file() else None


class DatabaseDocumentStore(DocumentStore):
    """Originals as rows in `documents`. The only durable option on a read-only file system."""

    scheme = "db://"

    async def save(self, db: "Database", organization_id: UUID, sha256: str, content: bytes) -> str:
        if not _SHA256.fullmatch(sha256):
            raise ValueError("not a SHA-256 hex digest")
        async with db.session() as session:
            # The same bytes may arrive twice at once; the first writer wins and the second is a
            # no-op, which is exactly the local store's behaviour.
            await session.execute(
                insert(Document)
                .values(organization_id=organization_id, sha256=sha256, size_bytes=len(content), content=content)
                .on_conflict_do_nothing(index_elements=[Document.organization_id, Document.sha256])
            )
            await session.commit()
        return f"{self.scheme}{organization_id}/{sha256}"

    async def fetch(self, db: "Database", storage_url: str | None, organization_id: UUID) -> bytes | None:
        sha256 = self._parts(storage_url, organization_id)
        if sha256 is None:
            return None
        async with db.session() as session:
            return await session.scalar(
                select(Document.content).where(
                    Document.organization_id == organization_id, Document.sha256 == sha256
                )
            )
