#!/usr/bin/env python3
"""Check ROMA Web Search deployment paths, keys, and network reachability.

This script is intentionally stdlib-only so it can run before optional search
dependencies are installed. It never prints secret values.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable


SCRIPT_ROOT = Path(__file__).resolve().parents[1]


PATH_DEFAULTS = {
    "WEB_API_KEYS_FILE": "./api_keys.txt",
    "WEB_API_ARTIFACT_DIR": "./outputs",
    "WEB_API_HISTORY_FILE": "./outputs/search_history.json",
    "ROMA_SRC_ROOT": "./vendor/ROMA_v2/src",
    "WEB_SEARCH_SKILL_ROOT": "./skills/web-search-innospark-tree",
    "WEB_SEARCH_UNION_ROOT": "./skills/union-search-skill",
    "WEB_SEARCH_NEWS_AGGREGATOR_ROOT": "./skills/news-aggregator-skill",
    "ACADEMIC_RESEARCH_SKILLS_ROOT": "./skills/academic-research-skills",
    "GOOGLE_SCHOLAR_SKILLS_ROOT": "./skills/gs-skills",
}

KEY_GROUPS = {
    "core": [
        "TAVILY_API_KEY",
        "SERPAPI_API_KEY",
        "SERPAPI_API_KEY_1",
        "SERPER_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_SEARCH_ENGINE_ID",
        "METASO_API_KEY",
        "VOLCENGINE_API_KEY",
    ],
    "academic": ["S2_API_KEY", "CROSSREF_MAILTO", "OPENALEX_MAILTO"],
    "platform": ["TIKHUB_TOKEN", "YOUTUBE_API_KEY", "GITHUB_TOKEN"],
    "optional": ["JINA_API_KEY", "BRAVE_API_KEY", "GROK_API_KEY", "XAI_API_KEY", "EXA_API_KEY"],
}

NETWORK_HOSTS = [
    ("Tavily", "api.tavily.com", 443),
    ("SerpAPI", "serpapi.com", 443),
    ("Google APIs", "www.googleapis.com", 443),
    ("Semantic Scholar", "api.semanticscholar.org", 443),
    ("OpenAlex", "api.openalex.org", 443),
    ("Crossref", "api.crossref.org", 443),
    ("DuckDuckGo", "duckduckgo.com", 443),
    ("Google Scholar", "scholar.google.com", 443),
    ("Metaso", "metaso.cn", 443),
    ("Volcengine", "open.feedcoopapi.com", 443),
    ("GitHub API", "api.github.com", 443),
    ("YouTube API", "youtube.googleapis.com", 443),
]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
            os.environ.setdefault(key, value)
    return values


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = SCRIPT_ROOT / path
    return path.resolve()


def mask_presence(value: str | None) -> str:
    if not value:
        return "missing"
    if any(token in value.lower() for token in ("your_", "placeholder", "xxxx", "your-")):
        return "placeholder"
    return "set"


def tcp_probe(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            context = ssl.create_default_context()
            with context.wrap_socket(sock, server_hostname=host):
                return True, "ok"
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return False, f"{type(exc).__name__}: {exc}"


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
    timeout: float = 12.0,
) -> tuple[bool, int | None, str]:
    body = None
    req_headers = headers or {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req_headers = {"Content-Type": "application/json", **req_headers}
    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(512)
            return True, response.status, "ok"
    except urllib.error.HTTPError as exc:
        detail = exc.read(300).decode("utf-8", errors="ignore").replace("\n", " ")
        return False, exc.code, detail[:240]
    except Exception as exc:  # noqa: BLE001 - diagnostic script
        return False, None, f"{type(exc).__name__}: {exc}"


def print_table(rows: Iterable[tuple[str, str, str]]) -> None:
    rows = list(rows)
    width = max([len(row[0]) for row in rows] + [4])
    for name, status, detail in rows:
        print(f"{name:<{width}}  {status:<11}  {detail}")


def check_paths(env: dict[str, str]) -> int:
    print("\n[paths]")
    rows = []
    failures = 0
    for key, default in PATH_DEFAULTS.items():
        raw = env.get(key) or default
        path = resolve_path(raw)
        should_exist = key not in {"WEB_API_ARTIFACT_DIR", "WEB_API_HISTORY_FILE"}
        exists = path.exists() if should_exist else path.parent.exists()
        if not exists:
            failures += 1
        rows.append((key, "ok" if exists else "missing", str(path)))
    print_table(rows)
    return failures


def check_keys() -> int:
    print("\n[keys]")
    failures = 0
    rows = []
    for group, keys in KEY_GROUPS.items():
        for key in keys:
            status = mask_presence(os.environ.get(key))
            if group == "core" and status == "missing":
                failures += 1
            rows.append((key, status, group))
    print_table(rows)
    return failures


def check_network(timeout: float) -> int:
    print("\n[network tcp/tls]")
    failures = 0
    rows = []
    for label, host, port in NETWORK_HOSTS:
        ok, detail = tcp_probe(host, port, timeout)
        if not ok:
            failures += 1
        rows.append((label, "ok" if ok else "failed", detail))
    print_table(rows)
    return failures


def check_api(timeout: float) -> int:
    print("\n[api probes]")
    rows = []
    failures = 0

    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        ok, status, detail = request_json(
            "https://api.tavily.com/search",
            method="POST",
            headers={"Authorization": f"Bearer {tavily_key}"},
            payload={"query": "Singapore IT2000", "max_results": 1, "search_depth": "basic"},
            timeout=timeout,
        )
        rows.append(("Tavily search", "ok" if ok else "failed", f"HTTP {status}: {detail}"))
        failures += 0 if ok else 1
    else:
        rows.append(("Tavily search", "skipped", "TAVILY_API_KEY missing"))

    serpapi_key = os.environ.get("SERPAPI_API_KEY") or os.environ.get("SERPAPI_API_KEY_1")
    if serpapi_key:
        url = "https://serpapi.com/account?" + urllib.parse.urlencode({"api_key": serpapi_key})
        ok, status, detail = request_json(url, timeout=timeout)
        rows.append(("SerpAPI account", "ok" if ok else "failed", f"HTTP {status}: {detail}"))
        failures += 0 if ok else 1
    else:
        rows.append(("SerpAPI account", "skipped", "SERPAPI_API_KEY missing"))

    ok, status, detail = request_json(
        "https://api.semanticscholar.org/graph/v1/paper/search?"
        + urllib.parse.urlencode({"query": "Singapore IT2000", "limit": 1, "fields": "title,url"}),
        timeout=timeout,
    )
    rows.append(("Semantic Scholar", "ok" if ok else "failed", f"HTTP {status}: {detail}"))
    failures += 0 if ok else 1

    ok, status, detail = request_json("https://api.openalex.org/works?search=Singapore%20IT2000&per-page=1", timeout=timeout)
    rows.append(("OpenAlex", "ok" if ok else "failed", f"HTTP {status}: {detail}"))
    failures += 0 if ok else 1

    ok, status, detail = request_json("https://duckduckgo.com/html/?q=Singapore+IT2000", timeout=timeout)
    rows.append(("DuckDuckGo HTML", "ok" if ok else "failed", f"HTTP {status}: {detail}"))
    failures += 0 if ok else 1

    ok, status, detail = request_json("https://scholar.google.com/scholar?q=Singapore+IT2000", timeout=timeout)
    # Scholar commonly returns 403/429/captcha on servers; mark as diagnostic, not fatal.
    rows.append(("Google Scholar page", "ok" if ok else "limited", f"HTTP {status}: {detail}"))

    print_table(rows)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Check ROMA Web Search deployment environment.")
    parser.add_argument("--env-file", default=str(SCRIPT_ROOT / ".env"), help="Path to .env file.")
    parser.add_argument("--live", action="store_true", help="Check TCP/TLS reachability for common providers.")
    parser.add_argument("--api-probe", action="store_true", help="Run small provider API probes. May consume minimal quota.")
    parser.add_argument("--timeout", type=float, default=8.0, help="Network timeout in seconds.")
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser().resolve()
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"ROMA Web Search environment check @ {started}")
    print(f"script_root={SCRIPT_ROOT}")
    print(f"env_file={env_path} ({'exists' if env_path.exists() else 'missing'})")

    env = load_env(env_path)
    failures = 0
    failures += check_paths(env)
    failures += check_keys()
    if args.live:
        failures += check_network(args.timeout)
    if args.api_probe:
        failures += check_api(args.timeout)

    print("\n[summary]")
    if failures:
        print(f"status=attention_needed failures={failures}")
        return 1
    print("status=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
