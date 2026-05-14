#!/usr/bin/env python3
"""Batch monitor X posts for a list of AI people."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


# ── Grok (grok2api) helpers ──────────────────────────────────────────────────

GROK2API_URL = os.environ.get('GROK2API_URL', 'http://127.0.0.1:8100')
GROK2API_KEY = os.environ.get('GROK2API_KEY', 'grok2api')
GROK2API_MODEL = os.environ.get('GROK2API_MODEL', 'grok-3')


def _grok_chat(messages: list[dict], timeout: int = 60) -> str:
    """Call grok2api chat completions, return assistant text."""
    payload = json.dumps({
        'model': GROK2API_MODEL,
        'messages': messages,
        'stream': False,
    }).encode('utf-8')
    req = urllib.request.Request(
        f'{GROK2API_URL}/v1/chat/completions',
        data=payload,
        headers={
            'Authorization': f'Bearer {GROK2API_KEY}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content']


def fetch_person_grok(person: 'Person', limit: int, timeout_s: int) -> 'FetchResult':
    """Fetch X posts for a person using Grok's native X search."""
    prompt = (
        f'搜索X（Twitter）上 @{person.handle}（{person.name}）最近发布的推文。\n'
        f'返回最近 {limit} 条推文，以JSON数组返回，格式：\n'
        '[{"text":"推文原文","created_at":"时间字符串","url":"推文链接"}]\n'
        '只返回JSON数组，不要包含任何其他说明文字。'
    )
    try:
        raw = _grok_chat([{'role': 'user', 'content': prompt}], timeout=timeout_s)
        # Build a fake FetchResult with the raw text as stdout
        from dataclasses import replace as _replace
        result = FetchResult(
            person=person, ok=bool(raw.strip()),
            command=['grok2api', 'x_search', person.handle],
            stdout=raw.strip(), stderr='',
        )
        return result
    except Exception as exc:
        return FetchResult(
            person=person, ok=False,
            command=['grok2api', 'x_search', person.handle],
            stdout='', stderr=str(exc),
        )

# ── Output cleaning helpers ──────────────────────────────────────────────────

_ANSI = re.compile(r'\x1b\[[0-9;]*[mGKHF]|\x1b\[\??\d+[hl]|\r')
_LOG_LINE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')
_META_PREFIXES = ('Elapsed:', 'Error:', 'Source:', 'Warning:', 'INFO:', 'WARN', 'DEBUG')


def _strip_ansi(text: str) -> str:
    return _ANSI.sub('', text)


def _is_meta_line(line: str) -> bool:
    s = _strip_ansi(line).strip()
    if not s or s in ('[]', '{}'):
        return True
    if _LOG_LINE.match(s):
        return True
    return any(s.startswith(p) for p in _META_PREFIXES)


def _extract_json(text: str) -> Any:
    """Try to parse JSON from text that may contain leading log lines."""
    clean = _strip_ansi(text).strip()
    parsed = _try_json(clean)
    if parsed is not None:
        return parsed
    # Scan for first '[' or '{' and try from there
    for i, ch in enumerate(clean):
        if ch in ('[', '{'):
            parsed = _try_json(clean[i:])
            if parsed is not None:
                return parsed
    return None


def _try_json(s: str) -> Any:
    # raw_decode parses the first valid JSON value and ignores trailing content
    # (e.g. "Elapsed: 14.56s | Source: twitter search" after the JSON array)
    try:
        obj, _ = json.JSONDecoder().raw_decode(s.strip())
        return obj
    except Exception:
        return None

DEFAULT_PEOPLE_FILE = "data/x_ai_people_39.csv"
DEFAULT_OUTPUT_DIR = "outputs/x-ai-watchlist"


@dataclass
class Person:
    name: str
    handle: str
    enabled: bool = True


@dataclass
class FetchResult:
    person: Person
    ok: bool
    command: list[str]
    stdout: str
    stderr: str


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def load_people(path: Path) -> list[Person]:
    if not path.exists():
        raise FileNotFoundError(f"people file not found: {path}")

    people: list[Person] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"name", "x_handle"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"people csv missing columns: {', '.join(sorted(missing))}")

        for row in reader:
            name = (row.get("name") or "").strip()
            handle = (row.get("x_handle") or "").strip().lstrip("@")
            if not name or not handle:
                continue
            enabled = parse_bool(row.get("enabled", "1"))
            people.append(Person(name=name, handle=handle, enabled=enabled))

    return [p for p in people if p.enabled]


def which(commands: list[str]) -> str | None:
    for cmd in commands:
        if shutil_which(cmd):
            return cmd
    return None


def shutil_which(cmd: str) -> str | None:
    from shutil import which as _which

    return _which(cmd)


def run_capture(cmd: list[str], timeout_s: int) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"timeout after {timeout_s}s"
    except Exception as exc:  # pragma: no cover
        return False, "", str(exc)

    ok = proc.returncode == 0
    return ok, proc.stdout.strip(), proc.stderr.strip()


def build_variants(driver: str, handle: str, limit: int, timeout_s: int) -> list[list[str]]:
    if driver in {"opencli-rs", "opencli"}:
        return [
            [driver, "twitter", "search", f"from:{handle}", "--limit", str(limit), "--format", "json"],
            [driver, "twitter", "search", f"from:{handle}", "--format", "json"],
            [driver, "twitter", "search", f"from:{handle}"],
            [driver, "twitter", "profile", handle, "--format", "json"],
            [driver, "twitter", "profile", handle],
        ]

    if driver == "bird":
        return [
            ["bird", "search", f"from:{handle}"],
            ["bird", "profile", handle],
        ]

    raise ValueError(f"unsupported driver: {driver}")


def pick_driver(requested: str) -> str:
    if requested == "grok":
        return "grok"
    if requested != "auto":
        if not which([requested]):
            raise RuntimeError(f"driver '{requested}' not found in PATH")
        return requested

    # auto: prefer grok (no browser needed), fallback to opencli
    try:
        _grok_chat([{'role': 'user', 'content': 'hi'}], timeout=10)
        return "grok"
    except Exception:
        pass
    found = which(["opencli-rs", "opencli", "bird"])
    if not found:
        raise RuntimeError(
            "no supported driver found. "
            "Either start grok2api (docker compose up -d grok2api) "
            "or install opencli-rs/opencli."
        )
    return found


def fetch_person(driver: str, person: Person, limit: int, timeout_s: int) -> FetchResult:
    if driver == "grok":
        return fetch_person_grok(person, limit, timeout_s)

    for cmd in build_variants(driver, person.handle, limit, timeout_s):
        ok, out, err = run_capture(cmd, timeout_s)
        if ok and out:
            return FetchResult(person=person, ok=True, command=cmd, stdout=out, stderr=err)

    return FetchResult(person=person, ok=False, command=build_variants(driver, person.handle, limit, timeout_s)[0], stdout="", stderr="all variants failed")


def maybe_parse_json(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except Exception:
        return None


def extract_items(payload: Any, limit: int) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def walk(node: Any) -> None:
        if len(items) >= limit:
            return
        if isinstance(node, dict):
            text = None
            for key in ("full_text", "text", "content", "body"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
            if text:
                items.append(
                    {
                        "text": text,
                        "created_at": str(node.get("created_at") or node.get("time") or ""),
                        "url": str(node.get("url") or ""),
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return items[:limit]


def resolve_item_url(raw_url: str, handle: str, text: str) -> str:
    url = (raw_url or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return f"https://x.com{url}"
    if "x.com/" in url and not url.startswith("http"):
        return f"https://{url.lstrip('/')}"
    if text:
        query = quote_plus(f"from:{handle} {text[:80]}")
        return f"https://x.com/search?q={query}&src=typed_query&f=live"
    return f"https://x.com/{handle}"


def write_result_files(results: list[FetchResult], output_dir: Path, preview_per_person: int) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_dir / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, str]] = []

    for result in results:
        safe_handle = result.person.handle.replace("/", "_")
        raw_file = run_dir / f"{safe_handle}.raw.txt"
        raw_file.write_text(result.stdout if result.stdout else result.stderr, encoding="utf-8")

        preview_items: list[dict[str, str]] = []
        if result.ok and result.stdout:
            parsed = _extract_json(result.stdout)
            if parsed is not None:
                preview_items = extract_items(parsed, preview_per_person)

            if not preview_items:
                # Fallback: use plain-text lines, but filter out log/meta noise
                clean = _strip_ansi(result.stdout)
                lines = [ln.strip() for ln in clean.splitlines()
                         if ln.strip() and not _is_meta_line(ln)]
                preview_items = [{"text": ln, "created_at": "", "url": ""}
                                 for ln in lines[:preview_per_person]]

        normalized_items: list[dict[str, str]] = []
        for item in preview_items:
            text = str(item.get("text") or "").strip()
            created_at = str(item.get("created_at") or "").strip()
            url = resolve_item_url(str(item.get("url") or ""), result.person.handle, text)
            normalized_items.append(
                {
                    "text": text,
                    "created_at": created_at,
                    "url": url,
                }
            )

        preview_file = run_dir / f"{safe_handle}.preview.md"
        with preview_file.open("w", encoding="utf-8") as f:
            f.write(f"# {result.person.name} (@{result.person.handle})\n\n")
            f.write(f"- status: {'ok' if result.ok else 'failed'}\n")
            f.write(f"- command: `{shlex.join(result.command)}`\n\n")
            if normalized_items:
                for idx, item in enumerate(normalized_items, start=1):
                    f.write(f"{idx}. {item['text']}\n")
                    if item.get("created_at"):
                        f.write(f"   - time: {item['created_at']}\n")
                    if item.get("url"):
                        f.write(f"   - url: {item['url']}\n")
            else:
                f.write("(no parsed items)\n")

        summary_rows.append(
            {
                "name": result.person.name,
                "x_handle": result.person.handle,
                "status": "ok" if result.ok else "failed",
                "command": shlex.join(result.command),
                "raw_file": raw_file.name,
                "preview_file": preview_file.name,
                "profile_url": f"https://x.com/{result.person.handle}",
                "items": normalized_items,
            }
        )

    summary_json = run_dir / "summary.json"
    summary_json.write_text(json.dumps(summary_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    feed_json = run_dir / "feed.json"
    feed_json.write_text(
        json.dumps(
            {
                "generated_at": ts,
                "people": summary_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_md = run_dir / "summary.md"
    with summary_md.open("w", encoding="utf-8") as f:
        f.write(f"# X AI Watchlist Summary ({ts})\n\n")
        f.write("| Name | Handle | Status | Preview |\n")
        f.write("|---|---|---|---|\n")
        for row in summary_rows:
            f.write(
                f"| {row['name']} | @{row['x_handle']} | {row['status']} | {row['preview_file']} |\n"
            )

    latest = output_dir / "latest"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(run_dir.name)

    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor a watchlist of AI people on X")
    parser.add_argument("--people-file", default=DEFAULT_PEOPLE_FILE, help="CSV file with name,x_handle,enabled")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--limit", type=int, default=5, help="Target items per person")
    parser.add_argument("--preview-per-person", type=int, default=5, help="Preview lines per person")
    parser.add_argument("--max-people", type=int, default=0, help="Only process first N people (0 = all)")
    parser.add_argument("--driver", default="auto", choices=["auto", "grok", "opencli-rs", "opencli", "bird"], help="Fetch backend (grok=grok2api, auto=prefer grok then opencli)")
    parser.add_argument("--timeout", type=int, default=20, help="Timeout seconds per command")
    parser.add_argument("--parallel", type=int, default=5, help="Max parallel workers")

    args = parser.parse_args()

    people_path = Path(args.people_file)
    out_dir = Path(args.output_dir)

    try:
        people = load_people(people_path)
        if args.max_people > 0:
            people = people[: args.max_people]
        if not people:
            raise RuntimeError("no enabled people found in csv")

        driver = pick_driver(args.driver)
        print(f"driver={driver} people={len(people)}")

        workers = min(args.parallel, len(people))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda p: fetch_person(driver, p, args.limit, args.timeout), people))
        run_dir = write_result_files(results, out_dir, args.preview_per_person)

        ok_count = sum(1 for r in results if r.ok)
        print(f"done: ok={ok_count} failed={len(results)-ok_count} output={run_dir}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
