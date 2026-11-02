import os
import time
import uuid
from pathlib import Path
from fastapi import HTTPException, UploadFile

# Where uploaded files are stored (served by nginx / Next.js public)
# In production this typically points to the deployed Next.js public/uploads.
# In local development you can override via UPLOAD_ROOT env var, e.g.:
# UPLOAD_ROOT=/Users/you/Documents/Projects/rockmartialarts/rockmartialarts-fe/public/uploads
def _default_upload_root() -> Path:
    """
    Prefer sibling rockmartialarts-fe/public/uploads next to rockmartialarts-be.
    Fallback to production deploy path when monorepo layout is absent.
    """
    be_root = Path(__file__).resolve().parents[1]  # .../rockmartialarts-be
    local_public = be_root.parent / "rockmartialarts-fe" / "public" / "uploads"
    try:
        if local_public.parent.exists():
            return local_public
    except OSError:
        pass
    return Path("/var/www/rockmartialarts-fe/public/uploads")


UPLOAD_ROOT = Path(os.getenv("UPLOAD_ROOT", str(_default_upload_root())))

ALLOWED_IMAGES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_VIDEOS = {"video/mp4", "video/webm"}
ALLOWED_DOCS = {"application/pdf"}

MAX_IMAGE_SIZE = 10 * 1024 * 1024   # 10 MB
MAX_VIDEO_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_DOC_SIZE = 20 * 1024 * 1024     # 20 MB


def _safe_filename(original: str) -> str:
    """Return a filesystem-safe version of the original filename."""
    name = Path(original).stem[:60]
    ext = Path(original).suffix.lower()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return f"{int(time.time())}_{uuid.uuid4().hex[:8]}_{safe}{ext}"


class UploadController:
    @staticmethod
    async def upload_file(file: UploadFile, current_user: dict):
        if not current_user:
            raise HTTPException(status_code=401, detail="Authentication required")

        content_type = file.content_type or ""
        filename = file.filename or "file"

        # Determine category and size limit
        if content_type in ALLOWED_IMAGES:
            sub = "images"
            max_size = MAX_IMAGE_SIZE
        elif content_type in ALLOWED_VIDEOS:
            sub = "videos"
            max_size = MAX_VIDEO_SIZE
        elif content_type in ALLOWED_DOCS:
            sub = "documents"
            max_size = MAX_DOC_SIZE
        else:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{content_type}' not allowed. Accepted: images (jpg/png/webp), videos (mp4/webm), documents (pdf)."
            )

        # Read file
        data = await file.read()
        if len(data) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"File too large ({len(data)/(1024*1024):.1f} MB). Max for {sub}: {max_size/(1024*1024):.0f} MB."
            )

        dest_dir = UPLOAD_ROOT / sub
        dest_dir.mkdir(parents=True, exist_ok=True)

        safe_name = _safe_filename(filename)
        dest_path = dest_dir / safe_name
        dest_path.write_bytes(data)

        file_url = f"/uploads/{sub}/{safe_name}"

        return {
            "message": "File uploaded successfully",
            "file_url": file_url,
            "filename": safe_name,
            "content_type": content_type,
            "size": len(data),
        }
