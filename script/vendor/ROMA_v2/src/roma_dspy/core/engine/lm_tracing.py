"""LM call tracing: token extraction and Postgres persistence."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional


class LmTracingMixin:
    """Mixin that provides LM trace extraction and persistence helpers.

    Requires subclass to expose ``context_store`` (ContextStore).
    """

    # ------------------------------------------------------------------
    # Token usage extraction
    # ------------------------------------------------------------------

    def _extract_token_usage(self, result: Any) -> tuple[int, int, int]:
        """Extract token usage from a DSPy prediction result.

        Returns:
            Tuple of (prompt_tokens, completion_tokens, total_tokens).
        """
        usage = getattr(result, "get_lm_usage", lambda: None)()
        if not usage or not isinstance(usage, dict):
            return 0, 0, 0

        for model_usage in usage.values():
            if isinstance(model_usage, dict):
                prompt = model_usage.get("prompt_tokens", 0)
                completion = model_usage.get("completion_tokens", 0)
                total = model_usage.get("total_tokens", prompt + completion)
                return prompt, completion, total

        return 0, 0, 0

    # ------------------------------------------------------------------
    # LM Trace Persistence
    # ------------------------------------------------------------------

    async def _persist_lm_trace(
        self,
        execution_id: str,
        postgres: Any,
        module: Any,
        result: Any,
        start_time: float,
        task_id: str,
    ) -> None:
        """Persist LM call trace to Postgres with retry logic for FK violations."""
        from loguru import logger

        max_retries = 3
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                latency_ms = int((time.time() - start_time) * 1000)
                prompt_tokens, completion_tokens, total_tokens = (
                    self._extract_token_usage(result)
                )

                lm = getattr(module, "lm", None) or getattr(module, "_lm", None)
                model = getattr(lm, "model", "unknown") if lm else "unknown"
                temperature = (
                    getattr(lm, "kwargs", {}).get("temperature") if lm else None
                )
                max_tokens = (
                    getattr(lm, "kwargs", {}).get("max_tokens") if lm else None
                )

                usage = getattr(result, "get_lm_usage", lambda: {})()
                cost_usd = usage.get("cost") if isinstance(usage, dict) else None
                if not cost_usd and hasattr(result, "metrics"):
                    cost_usd = getattr(result.metrics, "cost", None)

                await postgres.save_lm_trace(
                    execution_id=execution_id,
                    task_id=task_id,
                    module_name=module.__class__.__name__.lower(),
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost_usd,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    prediction_strategy=str(
                        getattr(module, "_prediction_strategy", None)
                    ),
                    latency_ms=latency_ms,
                    metadata={"success": True},
                )
                return

            except Exception as e:
                error_str = str(e).lower()
                is_fk_violation = any(
                    k in error_str
                    for k in ("foreign key", "fkey", "violates foreign key constraint")
                )

                if is_fk_violation and attempt < max_retries - 1:
                    logger.warning(
                        f"FK violation on attempt {attempt + 1}/{max_retries}, retrying..."
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.warning(f"Failed to persist LM trace: {e}")
                    return
