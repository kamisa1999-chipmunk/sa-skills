#!/usr/bin/env python3
"""Refresh local SA template snapshots from Confluence.

Token is never printed. Writes named .txt files next to README.md.
"""

from __future__ import annotations

import shutil
import sys
import warnings
from pathlib import Path

TEMPLATES = (
    ("640047269", "list.txt"),
    ("762574742", "ui.txt"),
    ("649174595", "http.txt"),
    ("721139215", "grpc.txt"),
    ("671395204", "kafka.txt"),
    ("662138588", "db.txt"),
    ("700188117", "admin.txt"),
    ("649174579", "feature.txt"),
)

SKILL_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = SKILL_DIR / "templates"
DUMP = Path(__file__).resolve().parents[2] / "sa-review/scripts/dump_confluence_page.py"


def main() -> None:
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    if not DUMP.is_file():
        raise SystemExit(f"Нет скрипта выгрузки: {DUMP}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = OUT_DIR / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    import subprocess

    failed = []
    for page_id, name in TEMPLATES:
        proc = subprocess.run(
            [sys.executable, str(DUMP), page_id, "--out-dir", str(raw)],
            capture_output=True,
            text=True,
        )
        src = raw / f"dump_{page_id}.txt"
        if proc.returncode != 0 or not src.is_file():
            failed.append(page_id)
            print(f"FAIL {page_id}: {(proc.stderr or proc.stdout).strip()[:300]}")
            continue
        dest = OUT_DIR / name
        shutil.copyfile(src, dest)
        head = src.read_text(encoding="utf-8").splitlines()[:3]
        print(f"OK {name}  {page_id}  {head[0] if head else ''}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
