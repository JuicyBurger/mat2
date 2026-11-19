"""Rewrite a PDF metadata stack on top of local libmat2 + pypdf.

Pipeline
--------
1. Copy the PDF Info dictionary into a mutable stack (pypdf).
2. Flush hidden metadata / XMP / tracking with ``parser_factory``.
3. Apply user overrides (add / update / delete) and optional PDF-date formatting.
4. Inject the edited stack into the cleaned PDF and remove ``*.cleaned.pdf`` artifacts.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from . import parser_factory
from .pdf_stack import (
    PdfStackError,
    apply_overrides,
    extract_info_stack,
    inject_info_stack,
    stack_to_jsonable,
)


class RewriteStackError(Exception):
    """Raised when the rewrite pipeline fails."""


def rewrite_pdf_stack(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    overrides: Mapping[str, Any] | None = None,
    *,
    lightweight: bool = True,
    keep_original_unmodified: bool = False,
    delete_keys: list[str] | None = None,
    format_dates: bool = True,
) -> dict[str, Any]:
    """Copy → flush (mat2) → edit → inject PDF metadata stack.

    Parameters
    ----------
    input_path:
        Source PDF.
    output_path:
        Destination PDF. May equal ``input_path`` only when
        ``keep_original_unmodified`` is False.
    overrides:
        Mapping of Info keys → values. Use ``None`` to delete a key.
        Leading ``/`` is optional (``Author`` → ``/Author``).
    lightweight:
        Maps to mat2 ``parser.lightweight_cleaning``. ``True`` preserves
        selectable text; ``False`` fully rasterizes pages.
    keep_original_unmodified:
        When True, refuse to overwrite the input path.
    delete_keys:
        Extra keys to strip from the copied stack.
    format_dates:
        Auto-convert ISO/Unix values for ``/CreationDate`` and ``/ModDate``.

    Returns
    -------
    dict
        The final Info stack written into the output PDF.
    """
    src = Path(input_path).resolve()
    dst = Path(output_path).resolve()

    if not src.is_file():
        raise RewriteStackError(f"input file not found: {src}")
    if src.suffix.lower() != ".pdf":
        raise RewriteStackError(f"input is not a PDF path: {src}")
    if keep_original_unmodified and src == dst:
        raise RewriteStackError(
            "keep_original_unmodified=True refuses to overwrite the input path"
        )

    try:
        original_stack = extract_info_stack(str(src))
    except PdfStackError as exc:
        raise RewriteStackError(str(exc)) from exc

    edited_stack = apply_overrides(
        original_stack,
        overrides,
        delete_keys=delete_keys,
        format_dates=format_dates,
    )

    workdir = tempfile.mkdtemp(prefix="rewrite-stack-")
    cleaned_path: Path | None = None
    try:
        work_input = Path(workdir) / src.name
        shutil.copy2(src, work_input)

        try:
            parser, mimetype = parser_factory.get_parser(str(work_input))
        except ValueError as exc:
            raise RewriteStackError(
                f"libmat2 failed to parse PDF: {src}: {exc}"
            ) from exc

        if parser is None:
            raise RewriteStackError(
                f"libmat2 has no parser for {src} (mimetype={mimetype!r})"
            )
        if mimetype != "application/pdf":
            raise RewriteStackError(
                f"expected application/pdf, got {mimetype!r} for {src}"
            )

        parser.lightweight_cleaning = bool(lightweight)
        try:
            ok = parser.remove_all()
        except (ValueError, RuntimeError) as exc:
            raise RewriteStackError(
                f"libmat2 failed while flushing metadata: {exc}"
            ) from exc
        if ok is not True:
            raise RewriteStackError("libmat2 remove_all() returned a non-success status")

        cleaned_path = Path(parser.output_filename)
        if not cleaned_path.is_file():
            raise RewriteStackError(
                f"libmat2 did not produce cleaned output at {cleaned_path}"
            )

        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = Path(workdir) / "injected.pdf"
        try:
            inject_info_stack(str(cleaned_path), str(tmp_out), edited_stack)
        except PdfStackError as exc:
            raise RewriteStackError(str(exc)) from exc

        shutil.move(str(tmp_out), str(dst))
        return edited_stack
    finally:
        if cleaned_path is not None and cleaned_path.is_file():
            try:
                cleaned_path.unlink()
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


def dump_pdf_stack(input_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Extract and return the Info stack without modifying any files."""
    src = Path(input_path).resolve()
    if not src.is_file():
        raise RewriteStackError(f"input file not found: {src}")
    try:
        return stack_to_jsonable(extract_info_stack(str(src)))
    except PdfStackError as exc:
        raise RewriteStackError(str(exc)) from exc
