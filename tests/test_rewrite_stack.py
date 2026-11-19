"""Verification for rewrite_stack: capture → mat2 flush → inject edited Info stack."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from libmat2.rewrite_stack import dump_pdf_stack, rewrite_pdf_stack


class TestRewriteStack(unittest.TestCase):
    def setUp(self) -> None:
        self.dirty = Path(__file__).resolve().parent / "data" / "dirty.pdf"
        self.assertTrue(self.dirty.is_file())
        self._tmpdir = tempfile.TemporaryDirectory(prefix="test-rewrite-stack-")
        self.addCleanup(self._tmpdir.cleanup)
        self.out = Path(self._tmpdir.name) / "out.pdf"

    def test_dump_captures_original_info_keys(self) -> None:
        stack = dump_pdf_stack(self.dirty)
        self.assertIn("/Producer", stack)
        self.assertIn("/CreationDate", stack)
        self.assertTrue(str(stack["/CreationDate"]).startswith("D:"))

    def test_rewrite_flushes_hidden_and_injects_edited_stack(self) -> None:
        original = dump_pdf_stack(self.dirty)
        self.assertIn("/PTEX.Fullbanner", original)
        self.assertIn("/Application", original)

        final = rewrite_pdf_stack(
            self.dirty,
            self.out,
            {
                "Title": "Rewrite Stack Test",
                "Author": "Jane Doe",
                "Producer": "rewrite_stack-test",
                "CreationDate": "2024-01-15T10:30:00+08:00",
                "/PTEX.Fullbanner": None,
                "/Application": None,
                "/Trapped": None,
            },
            lightweight=True,
            keep_original_unmodified=True,
        )

        meta = {
            str(k): str(v)
            for k, v in dict(PdfReader(str(self.out)).metadata or {}).items()
        }

        # Edited values present.
        self.assertEqual(meta.get("/Title"), "Rewrite Stack Test")
        self.assertEqual(meta.get("/Author"), "Jane Doe")
        self.assertEqual(meta.get("/Producer"), "rewrite_stack-test")
        self.assertTrue(meta["/CreationDate"].startswith("D:20240115103000+08"))

        # Hidden / deleted tracking keys gone; no cairo residue.
        self.assertNotIn("/PTEX.Fullbanner", meta)
        self.assertNotIn("/Application", meta)
        self.assertNotIn("cairo", meta.get("/Producer", "").lower())

        # Final stack matches what we injected.
        self.assertEqual(meta, {k: str(v) for k, v in final.items()})

        # No leftover mat2 artifact next to the fixture or in the workdir.
        self.assertFalse((self.dirty.parent / "dirty.cleaned.pdf").exists())
        self.assertEqual(
            [p.name for p in Path(self._tmpdir.name).glob("*.cleaned.pdf")],
            [],
        )
        # Original fixture left untouched.
        self.assertTrue(self.dirty.is_file())
        self.assertEqual(dump_pdf_stack(self.dirty), original)


if __name__ == "__main__":
    unittest.main()
