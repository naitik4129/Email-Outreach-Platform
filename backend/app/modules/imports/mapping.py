from __future__ import annotations

from app.core.errors import AppError
from app.schemas.imports import (
    LEADS_MAPPABLE_FIELDS,
    SUPPRESSION_MAPPABLE_FIELDS,
    ImportKind,
    ImportMappingIn,
)

_RESERVED_FILENAME_CHARS = set('/\\<>:"|?*')


def validate_mapping(
    import_kind: ImportKind, mapping: ImportMappingIn, headers: list[str]
) -> dict[str, str]:
    """Reject anything not on the approved lead/suppression allow-list.

    CLAUDE.md Phase 3 task, "Column Mapping": only approved lead fields may
    be import targets; workspace_id/id/created_at/version/internal state are
    never reachable because they are simply not in either allow-list.
    """
    allowed = (
        LEADS_MAPPABLE_FIELDS if import_kind == "LEADS" else SUPPRESSION_MAPPABLE_FIELDS
    )
    columns = mapping.columns
    unknown_targets = set(columns) - allowed
    if unknown_targets:
        raise AppError(
            "invalid_mapping",
            f"Unsupported mapping target(s): {', '.join(sorted(unknown_targets))}",
            status_code=422,
        )
    if "email" not in columns:
        raise AppError(
            "invalid_mapping", "Email column mapping is required", status_code=422
        )
    header_set = set(headers)
    for target, source_header in columns.items():
        if source_header not in header_set:
            raise AppError(
                "invalid_mapping",
                f"Mapped column '{source_header}' was not found in the file",
                status_code=422,
            )
    return dict(columns)


def sanitize_source_filename(value: str | None) -> str | None:
    """Best-effort display-only label -- never used to build a storage path.

    Strips any path-like content and control/reserved characters so a
    malicious filename can never do anything more than display oddly.
    """
    if value is None:
        return None
    name = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in name if ch not in _RESERVED_FILENAME_CHARS and ch.isprintable())
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    return cleaned[:200]
