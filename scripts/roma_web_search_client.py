#!/usr/bin/env python3
"""Small stdlib-only CLI for the ROMA Web Search API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8099"
DEFAULT_MODEL = "roma-web-search"


def env_default(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)


def build_payload(args: argparse.Namespace, stream: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.query}],
        "top_n": args.top_n,
        "stream": stream,
    }
    if args.mode:
        payload["mode"] = args.mode
    if stream:
        payload["stream_chunk_chars"] = args.stream_chunk_chars
        payload["stream_chunk_delay_ms"] = args.stream_chunk_delay_ms
    return payload


def request_json(
    method: str,
    url: str,
    api_key: str | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            text = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(text) if text else {}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            parsed = {"error": text}
        return exc.code, parsed


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def command_health(args: argparse.Namespace) -> int:
    status, data = request_json("GET", f"{args.base_url.rstrip('/')}/healthz")
    print_json({"http_status": status, "data": data})
    return 0 if status < 400 else 1


def command_sync(args: argparse.Namespace) -> int:
    status, data = request_json(
        "POST",
        f"{args.base_url.rstrip('/')}/web-search/v1/chat/completions",
        args.api_key,
        build_payload(args, stream=False),
    )
    if args.content_only:
        print(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
    else:
        print_json({"http_status": status, "data": data})
    return 0 if status < 400 else 1


def command_stream(args: argparse.Namespace) -> int:
    url = f"{args.base_url.rstrip('/')}/web-search/v1/chat/completions"
    body = json.dumps(build_payload(args, stream=True), ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {args.api_key}",
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                if not line.startswith("data: "):
                    continue
                data_text = line[6:].strip()
                if data_text == "[DONE]":
                    print("\n[DONE]")
                    break
                try:
                    chunk = json.loads(data_text)
                except json.JSONDecodeError:
                    print(data_text)
                    continue
                piece = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if piece:
                    print(piece, end="", flush=True)
        return 0
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        print(text, file=sys.stderr)
        return 1


def command_create_task(args: argparse.Namespace) -> int:
    status, data = request_json(
        "POST",
        f"{args.base_url.rstrip('/')}/web-search/v1/create_task",
        args.api_key,
        build_payload(args, stream=False),
    )
    print_json({"http_status": status, "data": data})
    return 0 if status < 400 else 1


def command_poll(args: argparse.Namespace) -> int:
    url = f"{args.base_url.rstrip('/')}/web-search/v1/query_task"
    query = urllib.parse.urlencode({"task_id": args.task_id})
    while True:
        status, data = request_json("GET", f"{url}?{query}", args.api_key)
        if args.wait:
            task = data.get("data", {})
            task_status = task.get("status", "unknown")
            print_json(
                {
                    "http_status": status,
                    "task_id": task.get("task_id", args.task_id),
                    "status": task_status,
                    "progress": task.get("progress"),
                    "result_count": task.get("statistics", {}).get("result_count"),
                }
            )
            if task_status in {"completed", "failed"}:
                if args.content_only and task.get("result"):
                    print(task["result"].get("content", ""))
                elif not args.summary_only:
                    print_json({"data": data})
                return 0 if task_status == "completed" else 1
            time.sleep(args.interval)
            continue

        print_json({"http_status": status, "data": data})
        return 0 if status < 400 else 1


def add_common_request_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", help="Search question")
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--mode", default=None, help="Optional retrieval mode")
    parser.add_argument("--model", default=DEFAULT_MODEL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ROMA Web Search API client")
    parser.add_argument(
        "--base-url",
        default=env_default("ROMA_WEB_SEARCH_BASE_URL", DEFAULT_BASE_URL),
        help="API base URL",
    )
    parser.add_argument(
        "--api-key",
        default=env_default("ROMA_WEB_SEARCH_API_KEY", "sk-your-api-key"),
        help="API key",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Check /healthz")
    health.set_defaults(func=command_health)

    sync = subparsers.add_parser("sync", help="Run a synchronous search")
    add_common_request_args(sync)
    sync.add_argument("--content-only", action="store_true")
    sync.set_defaults(func=command_sync)

    stream = subparsers.add_parser("stream", help="Run an SSE streaming search")
    add_common_request_args(stream)
    stream.add_argument("--stream-chunk-chars", type=int, default=180)
    stream.add_argument("--stream-chunk-delay-ms", type=int, default=80)
    stream.set_defaults(func=command_stream)

    create_task = subparsers.add_parser("create-task", help="Create an async task")
    add_common_request_args(create_task)
    create_task.set_defaults(func=command_create_task)

    poll = subparsers.add_parser("poll", help="Poll an async task")
    poll.add_argument("task_id")
    poll.add_argument("--wait", action="store_true")
    poll.add_argument("--interval", type=float, default=2.5)
    poll.add_argument("--summary-only", action="store_true")
    poll.add_argument("--content-only", action="store_true")
    poll.set_defaults(func=command_poll)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
