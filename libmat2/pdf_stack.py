"""PDF Info-dictionary stack helpers for rewrite_stack.

Uses pypdf for raw Info dict copy/inject. mat2 flushing lives in rewrite_stack.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping
from zoneinfo import ZoneInfo

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, create_string_object

# Keys whose values are conventionally PDF date strings.
DATE_KEYS = frozenset({"/CreationDate", "/ModDate"})

_PDF_DATE_RE = re.compile(
    r"^D:"
    r"(?P<Y>\d{4})"
    r"(?P<m>\d{2})?"
    r"(?P<d>\d{2})?"
    r"(?P<H>\d{2})?"
    r"(?P<M>\d{2})?"
    r"(?P<S>\d{2})?"
    r"(?P<tz>[Zz]|[+-]\d{2}'?\d{2}'?)?$"
)

_ISO_CANDIDATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)


class PdfStackError(Exception):
    """Raised when PDF Info stack operations fail."""


def normalize_info_key(key: str) -> str:
    """Ensure a PDF Info key starts with ``/``."""
    key = str(key).strip()
    if not key:
        raise PdfStackError("metadata key must not be empty")
    return key if key.startswith("/") else f"/{key}"


def _to_plain(value: Any) -> Any:
    """Convert pypdf objects into JSON-friendly Python values."""
    if value is None:
        return None
    if isinstance(value, NameObject):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


def _assert_pdf_magic(pdf_path: str) -> None:
    try:
        with open(pdf_path, "rb") as handle:
            header = handle.read(5)
    except OSError as exc:
        raise PdfStackError(f"unreadable path: {pdf_path}: {exc}") from exc
    if header != b"%PDF-":
        raise PdfStackError(f"not a valid PDF (missing %PDF- header): {pdf_path}")


def extract_info_stack(pdf_path: str) -> dict[str, Any]:
    """Copy the PDF Info dictionary into a mutable plain dict.

    :raises PdfStackError: if the file is missing or not a readable PDF
    """
    _assert_pdf_magic(pdf_path)
    try:
        reader = PdfReader(pdf_path, strict=False)
    except Exception as exc:  # noqa: BLE001 — surface as PdfStackError
        raise PdfStackError(f"not a valid PDF or unreadable: {pdf_path}: {exc}") from exc

    meta = reader.metadata
    if not meta:
        return {}

    stack: dict[str, Any] = {}
    for key in meta.keys():
        stack[normalize_info_key(str(key))] = _to_plain(meta[key])
    return stack


def to_pdf_date(value: Any, *, assume_tz: str | None = None) -> str:
    """Convert ISO-8601 / Unix timestamps into ``D:YYYYMMDDHHmmSS+HH'mm'``.

    Values that already look like PDF dates are returned unchanged (after a
    light normalization of the timezone suffix). Non-date-like strings are
    returned as ``str(value)``.
    """
    if value is None:
        raise PdfStackError("cannot format None as a PDF date")

    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return _format_pdf_date(dt)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return _format_pdf_date(dt)

    text = str(value).strip()
    if not text:
        raise PdfStackError("cannot format empty string as a PDF date")

    if text.startswith("D:"):
        return _normalize_existing_pdf_date(text)

    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        dt = datetime.fromtimestamp(float(text), tz=timezone.utc)
        return _format_pdf_date(dt)

    if _ISO_CANDIDATE_RE.match(text):
        normalized = text.replace("Z", "+00:00").replace("z", "+00:00")
        # Allow space separator.
        if " " in normalized and "T" not in normalized:
            normalized = normalized.replace(" ", "T", 1)
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise PdfStackError(f"invalid ISO timestamp: {value!r}") from exc
        if dt.tzinfo is None:
            if assume_tz:
                dt = dt.replace(tzinfo=ZoneInfo(assume_tz))
            else:
                dt = dt.replace(tzinfo=timezone.utc)
        return _format_pdf_date(dt)

    return text


def _normalize_existing_pdf_date(text: str) -> str:
    match = _PDF_DATE_RE.match(text)
    if not match:
        return text
    # Already a PDF date; keep body, normalize tz apostrophes if present.
    tz = match.group("tz")
    if not tz or tz in ("Z", "z"):
        return text if tz not in ("Z", "z") else text[:-1] + "+00'00'"
    # Ensure +HH'mm' form.
    cleaned = tz.replace("'", "")
    if len(cleaned) == 5 and cleaned[0] in "+-":
        return text[: match.start("tz")] + f"{cleaned[0]}{cleaned[1:3]}'{cleaned[3:5]}'"
    return text


def _format_pdf_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    offset = dt.utcoffset()
    if offset is None:
        offset = timezone.utc.utcoffset(dt) or timezone.utc.utcoffset(None)  # type: ignore[arg-type]
    total_seconds = int(offset.total_seconds()) if offset is not None else 0
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, rem = divmod(total_seconds, 3600)
    minutes = rem // 60
    return (
        f"D:{dt.year:04d}{dt.month:02d}{dt.day:02d}"
        f"{dt.hour:02d}{dt.minute:02d}{dt.second:02d}"
        f"{sign}{hours:02d}'{minutes:02d}'"
    )


def apply_overrides(
    stack: Mapping[str, Any],
    overrides: Mapping[str, Any] | None = None,
    *,
    delete_keys: list[str] | None = None,
    format_dates: bool = True,
) -> dict[str, Any]:
    """Return a new stack with overrides applied.

    - Missing leading ``/`` on keys is added automatically.
    - A value of ``None`` deletes the key.
    - ``delete_keys`` also removes keys (after normalize).
    - When ``format_dates`` is True, values for ``/CreationDate`` and
      ``/ModDate`` (and newly set date keys) are passed through ``to_pdf_date``.
    """
    result: dict[str, Any] = {
        normalize_info_key(k): v for k, v in dict(stack).items()
    }

    for key in delete_keys or []:
        result.pop(normalize_info_key(key), None)

    if overrides:
        for raw_key, value in overrides.items():
            key = normalize_info_key(raw_key)
            if value is None:
                result.pop(key, None)
                continue
            if format_dates and key in DATE_KEYS:
                result[key] = to_pdf_date(value)
            else:
                result[key] = value

    if format_dates:
        for key in list(result):
            if key in DATE_KEYS and result[key] is not None:
                result[key] = to_pdf_date(result[key])

    return result


def inject_info_stack(pdf_path: str, output_path: str, stack: Mapping[str, Any]) -> None:
    """Write ``stack`` as the sole Info dictionary of ``pdf_path`` → ``output_path``.

    Residual Info keys left by cairo/mat2 are replaced entirely so the final
    document only contains the edited stack.
    """
    try:
        writer = PdfWriter(clone_from=pdf_path)
    except Exception as exc:  # noqa: BLE001
        raise PdfStackError(f"failed to open cleaned PDF for injection: {pdf_path}: {exc}") from exc

    info = DictionaryObject()
    for raw_key, value in stack.items():
        key = normalize_info_key(raw_key)
        if value is None:
            continue
        # Preserve name objects for /Trapped-style values that already look like names.
        if isinstance(value, str) and value.startswith("/") and key == "/Trapped":
            info[NameObject(key)] = NameObject(value)
        else:
            info[NameObject(key)] = create_string_object(str(value))

    writer._info = info  # replace residual cairo producer/creator entirely

    try:
        with open(output_path, "wb") as handle:
            writer.write(handle)
    except OSError as exc:
        raise PdfStackError(f"failed to write output PDF: {output_path}: {exc}") from exc


def stack_to_jsonable(stack: MutableMapping[str, Any] | Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-serializable copy of the stack."""
    return {str(k): _to_plain(v) for k, v in stack.items()}
