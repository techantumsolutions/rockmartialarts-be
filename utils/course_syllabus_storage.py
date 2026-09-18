"""
M11-S01 private PDF storage for course syllabi.

Files live under backend storage (not FE public/uploads).
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

MAX_SYLLABUS_PDF_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


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


def write_syllabus_bytes(*, course_id: str, stored_filename: str, data: bytes) -> str:
    """
    Persist PDF under private storage. Returns storage_key relative to root
    (e.g. {course_id}/{stored_filename}).
    """
    root = syllabus_storage_root()
    course_dir = root / course_id
    course_dir.mkdir(parents=True, exist_ok=True)
    dest = course_dir / stored_filename
    # prevent path traversal
    if not str(dest.resolve()).startswith(str(root.resolve())):
        raise HTTPException(status_code=400, detail="Invalid storage path")
    dest.write_bytes(data)
    return f"{course_id}/{stored_filename}"


def resolve_syllabus_path(storage_key: str) -> Path:
    root = syllabus_storage_root().resolve()
    key = (storage_key or "").replace("\\", "/").lstrip("/")
    if ".." in key.split("/"):
        raise HTTPException(status_code=400, detail="Invalid storage key")
    path = (root / key).resolve()
    if not str(path).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Invalid storage key")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Syllabus file not found on disk")
    return path
