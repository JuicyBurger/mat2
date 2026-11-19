#!/usr/bin/env python3
"""CLI for libmat2.rewrite_stack — copy → flush → edit → inject PDF Info metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from libmat2.pdf_stack import PdfStackError, normalize_info_key, stack_to_jsonable
from libmat2.rewrite_stack import RewriteStackError, dump_pdf_stack, rewrite_pdf_stack


def _parse_set_argument(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"--set expects KEY=VALUE, got {raw!r}"
        )
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("--set key must not be empty")
    return normalize_info_key(key), value


def _load_json_overrides(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RewriteStackError(f"failed to read JSON config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RewriteStackError("JSON config must be an object of key/value pairs")
    return {normalize_info_key(str(k)): v for k, v in data.items()}


def create_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a PDF Info metadata stack, flush hidden metadata with local "
            "libmat2, apply overrides, and inject the edited stack."
        )
    )
    parser.add_argument("-i", "--input", required=True, help="input PDF path")
    parser.add_argument(
        "-o",
        "--output",
        help="output PDF path (required unless --dump-only)",
    )
    parser.add_argument(
        "--set",
        dest="sets",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        type=_parse_set_argument,
        help='set/update an Info key (repeatable), e.g. --set Author="Jane Doe"',
    )
    parser.add_argument(
        "--delete",
        dest="deletes",
        action="append",
        default=[],
        metavar="KEY",
        help="delete a copied Info key from the stack (repeatable)",
    )
    parser.add_argument(
        "--json",
        dest="json_config",
        metavar="CONFIG.json",
        help="optional JSON object of bulk metadata overrides (null deletes)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="full mat2 rasterization (lightweight_cleaning=False)",
    )
    parser.add_argument(
        "--dump-only",
        action="store_true",
        help="print the extracted Info stack as JSON and exit (no writes)",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="refuse to overwrite the input path",
    )
    parser.add_argument(
        "--no-format-dates",
        action="store_true",
        help="do not auto-convert ISO/Unix values for CreationDate/ModDate",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_arg_parser().parse_args(argv)

    try:
        if args.dump_only:
            stack = dump_pdf_stack(args.input)
            print(json.dumps(stack, indent=2, ensure_ascii=False, default=str))
            return 0

        if not args.output:
            raise RewriteStackError("--output / -o is required unless --dump-only")

        overrides: dict[str, Any] = {}
        if args.json_config:
            overrides.update(_load_json_overrides(args.json_config))
        for key, value in args.sets:
            overrides[key] = value
        for key in args.deletes:
            overrides[normalize_info_key(key)] = None

        final_stack = rewrite_pdf_stack(
            args.input,
            args.output,
            overrides,
            lightweight=not args.strict,
            keep_original_unmodified=args.keep_original,
            format_dates=not args.no_format_dates,
        )
        print(
            json.dumps(
                {
                    "input": str(Path(args.input).resolve()),
                    "output": str(Path(args.output).resolve()),
                    "lightweight": not args.strict,
                    "stack": stack_to_jsonable(final_stack),
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return 0
    except (RewriteStackError, PdfStackError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
