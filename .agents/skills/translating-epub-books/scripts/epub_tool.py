#!/usr/bin/env python3
"""Mechanical epub extract/build helper — no translation logic here.

Usage:
  epub_tool.py extract <book.epub> <out_dir>
  epub_tool.py build <in_dir> <book.epub>
"""
import sys
import zipfile
from pathlib import Path


def extract(epub_path, out_dir):
    out_dir = Path(out_dir)
    with zipfile.ZipFile(epub_path) as zf:
        zf.extractall(out_dir)
    print(f"Extracted {epub_path} -> {out_dir}")


def build(in_dir, epub_path):
    in_dir = Path(in_dir)
    mimetype = in_dir / "mimetype"
    if not mimetype.exists():
        sys.exit(f"missing {mimetype} — is {in_dir} an extracted epub root?")

    with zipfile.ZipFile(epub_path, "w") as zf:
        # mimetype must be first and STORED (uncompressed), or some readers reject the epub.
        zf.write(mimetype, "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(in_dir.rglob("*")):
            if path.is_dir() or path == mimetype:
                continue
            arcname = path.relative_to(in_dir).as_posix()
            zf.write(path, arcname, compress_type=zipfile.ZIP_DEFLATED)
    print(f"Built {epub_path} from {in_dir}")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("extract", "build"):
        sys.exit(__doc__)
    cmd, a, b = sys.argv[1:]
    (extract if cmd == "extract" else build)(a, b)
