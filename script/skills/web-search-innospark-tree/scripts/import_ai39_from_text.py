#!/usr/bin/env python3
"""Import people list from pasted article text to CSV.

Usage:
  python3 scripts/import_ai39_from_text.py --input article.txt --output data/x_ai_people_39.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

LINE_RE = re.compile(r"^\s*(?:\d+[\.)、:]|[-*•])?\s*(.+?)\s*$")
HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{2,32})")


def parse_names(lines: list[str]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        if len(text) > 120:
            continue

        m = LINE_RE.match(text)
        if not m:
            continue
        body = m.group(1).strip()
        if not body:
            continue

        handle_match = HANDLE_RE.search(body)
        handle = handle_match.group(1) if handle_match else ""

        name = HANDLE_RE.sub("", body)
        name = re.sub(r"\([^)]*\)", "", name)
        name = re.sub(r"（[^）]*）", "", name)
        name = re.sub(r"\s{2,}", " ", name).strip(" -|,，。:：")

        if not name:
            continue

        dedupe_key = name.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append((name, handle))

    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Import AI people list from text")
    parser.add_argument("--input", required=True, help="Path to pasted text file")
    parser.add_argument("--output", default="data/x_ai_people_39.csv", help="Output CSV path")
    parser.add_argument("--limit", type=int, default=39, help="Max rows (default 39)")
    args = parser.parse_args()

    content = Path(args.input).read_text(encoding="utf-8")
    rows = parse_names(content.splitlines())[: args.limit]

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "x_handle", "enabled", "notes"])
        for name, handle in rows:
            writer.writerow([name, handle, 1, "imported from article text"])

    print(f"wrote {len(rows)} rows to {out}")
    if len(rows) < args.limit:
        print("warning: parsed fewer rows than expected; please manually补齐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
