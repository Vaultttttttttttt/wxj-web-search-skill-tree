"""DSPy JSONAdapter parse-repair patch.

Adds a secondary repair step for malformed LM outputs by calling a
deterministic repair model (default: qwen3.5-plus), then re-parsing.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from loguru import logger

try:  # pragma: no cover - optional dependency wiring
    import litellm
except Exception:  # pragma: no cover
    litellm = None  # type: ignore[assignment]

try:  # pragma: no cover - DSPy import may fail in limited envs
    from dspy.adapters.json_adapter import JSONAdapter
except Exception:  # pragma: no cover
    JSONAdapter = None  # type: ignore[assignment]


_thread_local = threading.local()


def _repair_enabled() -> bool:
    return os.getenv("ROMA_JSON_REPAIR_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _output_field_names(signature: Any) -> list[str]:
    output_fields = getattr(signature, "output_fields", None)
    if isinstance(output_fields, dict):
        return list(output_fields.keys())
    return []


def _extract_litellm_text(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if choices is None and isinstance(response, dict):
        choices = response.get("choices")
    if not choices:
        return ""

    first = choices[0]
    message = getattr(first, "message", None)
    if message is None and isinstance(first, dict):
        message = first.get("message")

    if isinstance(message, dict):
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict):
                    chunks.append(str(item.get("text", "")))
                else:
                    chunks.append(str(item))
            return "".join(chunks)
        return str(content)

    if isinstance(message, str):
        return message
    return ""


def _repair_with_qwen(raw_text: str, fields: list[str]) -> Optional[str]:
    if litellm is None:
        return None
    if not fields:
        return None

    repair_model = os.getenv("ROMA_JSON_REPAIR_MODEL", "openai/qwen3.5-plus")
    timeout = int(os.getenv("ROMA_JSON_REPAIR_TIMEOUT", "25"))
    max_tokens = int(os.getenv("ROMA_JSON_REPAIR_MAX_TOKENS", "1200"))
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")

    system_prompt = (
        "You repair malformed model outputs into strict JSON.\n"
        "Return ONLY one JSON object.\n"
        "Do not include markdown fences.\n"
        "Required keys must match exactly.\n"
        "Keep original intent; only fix structure and missing fields."
    )
    user_prompt = (
        f"Required keys (exact): {fields}\n"
        "Rules:\n"
        "- Output must be a valid JSON object.\n"
        "- Include all required keys, no extra keys.\n"
        "- If a value is unknown, use safe default.\n"
        "- `next_tool_args` must be an object.\n\n"
        "Raw model output:\n"
        f"{raw_text}"
    )

    kwargs: dict[str, Any] = {
        "model": repair_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "timeout": timeout,
        "num_retries": 1,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    try:
        resp = litellm.completion(**kwargs)
        text = _extract_litellm_text(resp).strip()
        return text or None
    except Exception as e:  # pragma: no cover - network/provider dependent
        logger.warning(f"[JSONRepair] qwen repair call failed: {e}")
        return None


def _minimal_fallback_json(fields: list[str], raw_text: str) -> Optional[str]:
    if set(fields) == {"next_thought", "next_tool_name", "next_tool_args"}:
        fallback = {
            "next_thought": f"Repair fallback applied due to malformed LM output: {raw_text[:120]}",
            "next_tool_name": "finish",
            "next_tool_args": {},
        }
        return json.dumps(fallback, ensure_ascii=False)
    return None


def patch_dspy_json_adapter_repair() -> None:
    """Monkey-patch JSONAdapter.parse with qwen-based repair fallback."""
    if JSONAdapter is None:
        return
    if getattr(JSONAdapter, "_roma_json_repair_patch_applied", False):
        return

    original_parse = JSONAdapter.parse

    def patched_parse(self: Any, signature: Any, completion: str) -> dict[str, Any]:
        try:
            return original_parse(self, signature, completion)
        except Exception as original_error:
            if not _repair_enabled():
                raise

            if getattr(_thread_local, "in_json_repair", False):
                raise

            fields = _output_field_names(signature)
            if not fields:
                raise

            _thread_local.in_json_repair = True
            try:
                repaired = _repair_with_qwen(str(completion), fields)
                if repaired:
                    try:
                        parsed = original_parse(self, signature, repaired)
                        logger.info(
                            f"[JSONRepair] repaired malformed output using qwen for fields={fields}"
                        )
                        return parsed
                    except Exception:
                        pass

                minimal = _minimal_fallback_json(fields, str(completion))
                if minimal:
                    parsed = original_parse(self, signature, minimal)
                    logger.warning(
                        f"[JSONRepair] used minimal fallback JSON for fields={fields}"
                    )
                    return parsed

                raise original_error
            finally:
                _thread_local.in_json_repair = False

    JSONAdapter.parse = patched_parse  # type: ignore[assignment]
    JSONAdapter._roma_json_repair_patch_applied = True  # type: ignore[attr-defined]


__all__ = ["patch_dspy_json_adapter_repair"]

