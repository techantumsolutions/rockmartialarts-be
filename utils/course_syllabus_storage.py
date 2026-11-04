"""
M11-S01 private PDF storage for course syllabi.

Files live under backend storage (not FE public/uploads).
Also mirrored to MongoDB GridFS so shared Atlas DBs work across local/VPS hosts.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException, UploadFile
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

MAX_SYLLABUS_PDF_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
GRIDFS_BUCKET = "course_syllabus_files"

logger = logging.getLogger(__name__)


def syllabus_storage_root() -> Path:
    env = os.getenv("SYLLABUS_STORAGE_ROOT")
    if env:
        return Path(env)
    be_root = Path(__file__).resolve().parents[1]
    return be_root / "storage" / "private" / "syllabi"


def _safe_filename(original: str) -> str:
    name = Path(original).stem[:60]
    ext = Path(original).suffix.lower() or ".pdf"
    if ext != ".pdf":
        ext = ".pdf"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "syllabus"
    return f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe}{ext}"


async def read_and_validate_pdf(file: UploadFile) -> dict:
    original = file.filename or "syllabus.pdf"
    ext = Path(original).suffix.lower()
    content_type = (file.content_type or "").lower().strip() or "application/pdf"

    if ext and ext != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed for syllabus")
    if content_type not in ALLOWED_CONTENT_TYPES and content_type != "application/octet-stream":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid content type '{content_type}'. Expected application/pdf.",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > MAX_SYLLABUS_PDF_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"PDF too large ({len(data) / (1024 * 1024):.1f} MB). Max 20 MB.",
        )
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="File is not a valid PDF")

    stored = _safe_filename(original)
    return {
        "data": data,
        "original_filename": original,
        "stored_filename": stored,
        "content_type": "application/pdf",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _assert_under_root(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid storage path") from exc


def write_syllabus_bytes(*, course_id: str, stored_filename: str, data: bytes) -> str:
    """
    Persist PDF under private storage. Returns storage_key relative to root
    (e.g. {course_id}/{stored_filename}).
    """
    root = syllabus_storage_root()
    course_dir = root / course_id
    course_dir.mkdir(parents=True, exist_ok=True)
    dest = course_dir / stored_filename
    _assert_under_root(dest, root)
    dest.write_bytes(data)
    return f"{course_id}/{stored_filename}"


def resolve_syllabus_path(storage_key: str) -> Path:
    root = syllabus_storage_root().resolve()
    key = (storage_key or "").replace("\\", "/").lstrip("/")
    if ".." in key.split("/"):
        raise HTTPException(status_code=400, detail="Invalid storage key")
    path = (root / key).resolve()
    _assert_under_root(path, root)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "Syllabus file not found on disk. "
                "The PDF metadata exists but the file is missing on this server — "
                "please re-upload the syllabus from the course admin page."
            ),
        )
    return path


def _gridfs_bucket(db) -> AsyncIOMotorGridFSBucket:
    return AsyncIOMotorGridFSBucket(db, bucket_name=GRIDFS_BUCKET)


async def save_syllabus_gridfs(
    db,
    *,
    course_id: str,
    storage_key: str,
    stored_filename: str,
    data: bytes,
    sha256: str,
) -> str:
    """Store PDF in GridFS; returns gridfs file id as string."""
    bucket = _gridfs_bucket(db)
    file_id = await bucket.upload_from_stream(
        stored_filename,
        data,
        metadata={
            "course_id": course_id,
            "storage_key": storage_key,
            "sha256": sha256,
            "content_type": "application/pdf",
        },
    )
    return str(file_id)


async def read_syllabus_gridfs(db, gridfs_id: str) -> bytes:
    bucket = _gridfs_bucket(db)
    try:
        stream = await bucket.open_download_stream(ObjectId(gridfs_id))
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=(
                "Syllabus file not found on disk. "
                "Please re-upload the syllabus from the course admin page."
            ),
        ) from exc
    return await stream.read()


async def persist_syllabus_file(
    db,
    *,
    course_id: str,
    stored_filename: str,
    data: bytes,
    sha256: str,
) -> Tuple[str, Optional[str]]:
    """
    Write PDF to local private storage and mirror to GridFS (shared DB).
    Returns (storage_key, gridfs_id).
    """
    storage_key = write_syllabus_bytes(
        course_id=course_id,
        stored_filename=stored_filename,
        data=data,
    )
    gridfs_id: Optional[str] = None
    try:
        gridfs_id = await save_syllabus_gridfs(
            db,
            course_id=course_id,
            storage_key=storage_key,
            stored_filename=stored_filename,
            data=data,
            sha256=sha256,
        )
    except Exception:
        logger.exception("Failed to mirror syllabus PDF to GridFS (disk copy kept)")
    return storage_key, gridfs_id


def _rehydrate_disk(doc: dict[str, Any], data: bytes) -> None:
    """Best-effort: restore local disk copy for subsequent requests."""
    try:
        storage_key = doc.get("storage_key") or ""
        course_id = str(doc.get("course_id") or "")
        stored_filename = str(doc.get("stored_filename") or Path(storage_key).name)
        if course_id and stored_filename:
            write_syllabus_bytes(
                course_id=course_id,
                stored_filename=stored_filename,
                data=data,
            )
    except Exception:
        logger.exception("Failed to rehydrate syllabus PDF to local disk")


async def load_syllabus_bytes(db, doc: dict[str, Any]) -> bytes:
    """Load PDF bytes from disk, then embedded bytes, then GridFS."""
    storage_key = doc.get("storage_key") or ""
    try:
        path = resolve_syllabus_path(storage_key)
        return path.read_bytes()
    except HTTPException as disk_exc:
        if disk_exc.status_code not in (404, 400):
            raise

        embedded = doc.get("file_bytes")
        if embedded is not None:
            data = bytes(embedded)
            if data.startswith(b"%PDF"):
                _rehydrate_disk(doc, data)
                return data

        gridfs_id = doc.get("gridfs_id")
        if gridfs_id:
            data = await read_syllabus_gridfs(db, str(gridfs_id))
            _rehydrate_disk(doc, data)
            return data

        raise HTTPException(
            status_code=404,
            detail=(
                "Syllabus file not found on disk. "
                "The PDF was saved on another server (shared database). "
                "Please re-upload the syllabus from the course admin page on this environment."
            ),
        ) from disk_exc
