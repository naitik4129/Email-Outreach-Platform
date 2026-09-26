from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass

from app.core.errors import AppError

# Per-step limits. The byte cap keeps a message comfortably under Microsoft
# Graph's ~4 MB JSON sendMail limit once attachments are base64-encoded.
MAX_FILE_BYTES = 2_621_440  # 2.5 MiB (also the DB CHECK on size_bytes)
MAX_TOTAL_BYTES = 2_621_440
MAX_ATTACHMENTS = 5
MAX_INLINE_IMAGES = 10

_PNG = b"\x89PNG\r\n\x1a\n"

# extension -> (stored content type, kind). The stored type comes from THIS
# table, never from what the client claimed.
_TYPES: dict[str, tuple[str, str]] = {
    "png": ("image/png", "png"),
    "jpg": ("image/jpeg", "jpeg"),
    "jpeg": ("image/jpeg", "jpeg"),
    "gif": ("image/gif", "gif"),
    "webp": ("image/webp", "webp"),
    "pdf": ("application/pdf", "pdf"),
    "txt": ("text/plain", "text"),
    "csv": ("text/csv", "text"),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "ooxml:word/",
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "ooxml:xl/",
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "ooxml:ppt/",
    ),
}

_CONTROL_OR_SEPARATOR = re.compile(r"[\x00-\x1f\x7f/\\]")
_KEY_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_CID_REF = re.compile(r"""src\s*=\s*["']cid:([A-Za-z0-9_.@-]{1,128})["']""", re.I)


@dataclass(frozen=True)
class ValidatedFile:
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    is_image: bool


def sanitize_filename(raw: str | None) -> str:
    """Display filename: no paths, no control characters, bounded length."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_OR_SEPARATOR.sub("_", name).strip().strip(".")
    if not name:
        return "attachment"
    if len(name) > 255:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 255 - len(ext) - 1] + dot + ext) if dot and stem else name[:255]
    return name


def storage_safe_name(filename: str) -> str:
    """ASCII-only, path-free fragment for the object key. Runs of dots are
    collapsed: the storage_key CHECK forbids "..", and a traversal-looking
    fragment has no place in an object path anyway."""
    fragment = re.sub(r"\.{2,}", ".", _KEY_UNSAFE.sub("_", filename))
    return fragment[:80] or "file"


def _matches_signature(kind: str, data: bytes) -> bool:
    # Images must match THEIR OWN format: a PNG named .jpg is rejected rather
    # than stored under a content type that is wrong for its bytes.
    if kind == "png":
        return data.startswith(_PNG)
    if kind == "jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if kind == "gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if kind == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if kind == "pdf":
        return data.startswith(b"%PDF-")
    if kind == "text":
        if b"\x00" in data:
            return False
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    if kind.startswith("ooxml:"):
        if not data.startswith(b"PK\x03\x04"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile:
            return False
        # Names only -- nothing is extracted, so a zip bomb can't expand.
        return "[Content_Types].xml" in names and any(
            n.startswith(kind.removeprefix("ooxml:")) for n in names
        )
    return False


def _reject(message: str, *, code: str = "attachment_invalid") -> AppError:
    return AppError(code, message, status_code=422)


def validate_upload(
    *, filename: str | None, data: bytes, disposition: str
) -> ValidatedFile:
    """Decide whether an uploaded file may be attached.

    Checks, in order: size, extension allow-list, that the bytes really are that
    kind of file (magic bytes / structure), and that an inline file is an image.
    """
    if not data:
        raise _reject("The file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise _reject(
            "The file is too large (maximum 2.5 MB)", code="attachment_too_large"
        )
    clean_name = sanitize_filename(filename)
    ext = clean_name.rpartition(".")[2].lower() if "." in clean_name else ""
    entry = _TYPES.get(ext)
    if entry is None:
        raise _reject(
            "This file type isn't allowed. Attach images, PDF, text/CSV, or "
            "Word/Excel/PowerPoint files.",
            code="attachment_type_not_allowed",
        )
    content_type, kind = entry
    if not _matches_signature(kind, data):
        raise _reject(
            "The file contents don't match its type",
            code="attachment_type_mismatch",
        )
    is_image = content_type.startswith("image/")
    if disposition == "INLINE" and not is_image:
        raise _reject("Only images can be inserted into the email body")
    return ValidatedFile(
        filename=clean_name,
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        is_image=is_image,
    )


def extract_cid_references(body_html: str | None) -> set[str]:
    """Content ids referenced as <img src="cid:..."> in a body."""
    return set(_CID_REF.findall(body_html or ""))
