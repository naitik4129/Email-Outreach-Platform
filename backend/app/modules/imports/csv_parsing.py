from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass

from app.core.config import Settings
from app.core.errors import AppError


@dataclass(frozen=True)
class ParsedCsvPreview:
    headers: list[str]
    sample_rows: list[dict[str, str]]
    total_rows: int
    warnings: list[str]


def decode_csv_bytes(data: bytes) -> str:
    """Decode uploaded bytes as UTF-8, transparently stripping a BOM if present.

    Anything else is rejected outright rather than guessed at -- CLAUDE.md
    Phase 3 task, "CSV Parsing Safety": reject unsupported/broken encodings
    safely instead of silently producing corrupted leads.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError(
            "invalid_csv", "File must be UTF-8 encoded text", status_code=422
        ) from exc


def _clean_row(row: list[str]) -> bool:
    """True if this csv.reader row is a real data row (not a blank line)."""
    return bool(row) and not (len(row) == 1 and row[0].strip() == "")


def _row_to_dict(headers: list[str], row: list[str]) -> dict[str, str]:
    return {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}


def _read_headers(reader: csv.reader, settings: Settings) -> list[str]:
    try:
        raw_headers = next(reader)
    except StopIteration as exc:
        raise AppError("invalid_csv", "File is empty", status_code=422) from exc

    headers = [h.strip() for h in raw_headers]
    if not headers or any(not h for h in headers):
        raise AppError(
            "invalid_csv", "File must have a non-empty header row", status_code=422
        )
    if len(headers) > settings.import_max_columns:
        raise AppError("invalid_csv", "File has too many columns", status_code=422)
    if len(set(headers)) != len(headers):
        raise AppError(
            "invalid_csv", "File has duplicate column headers", status_code=422
        )
    return headers


def parse_csv_preview(text: str, *, settings: Settings) -> ParsedCsvPreview:
    """One bounded streaming pass: headers, a sample, and a strict row count.

    Used both at upload time (to build the preview) and again at confirm time
    (to re-derive total_rows/headers from the actual stored bytes rather than
    trusting anything the client echoed back). Aborts as soon as the row
    count, column count, or a field length exceeds the configured bound, so a
    huge or malicious file is rejected cheaply rather than fully ingested.
    """
    reader = csv.reader(io.StringIO(text))
    headers = _read_headers(reader, settings)

    sample_rows: list[dict[str, str]] = []
    warnings: list[str] = []
    total_rows = 0
    for row in reader:
        if not _clean_row(row):
            continue
        total_rows += 1
        if total_rows > settings.import_max_rows:
            raise AppError(
                "invalid_csv",
                f"File exceeds the maximum of {settings.import_max_rows} rows",
                status_code=422,
            )
        if len(row) > len(headers):
            warnings.append(
                f"Row {total_rows} has more columns than headers; extra values ignored"
            )
        for cell in row:
            if len(cell) > settings.import_max_field_chars:
                raise AppError(
                    "invalid_csv",
                    f"Row {total_rows} has a field longer than "
                    f"{settings.import_max_field_chars} characters",
                    status_code=422,
                )
        if len(sample_rows) < settings.import_preview_row_limit:
            sample_rows.append(_row_to_dict(headers, row))

    return ParsedCsvPreview(
        headers=headers,
        sample_rows=sample_rows,
        total_rows=total_rows,
        warnings=warnings,
    )


def iter_csv_rows(
    text: str, *, start_row: int = 0
) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield (1-indexed row_number, row-as-dict) pairs after `start_row`.

    `start_row` is the durable `import_jobs.row_cursor` checkpoint: a
    redelivered/resumed worker task re-opens the file from byte zero but
    skips every row already recorded in import_row_results, so it never
    reprocesses (and never duplicates) a row that already has a result.
    """
    reader = csv.reader(io.StringIO(text))
    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        return
    row_number = 0
    for row in reader:
        if not _clean_row(row):
            continue
        row_number += 1
        if row_number <= start_row:
            continue
        yield row_number, _row_to_dict(headers, row)
