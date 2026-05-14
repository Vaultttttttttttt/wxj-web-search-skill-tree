"""Structured MLflow span/metric logging for the MIPE evolution pipeline.

Provides hierarchical span nesting so the MLflow UI shows:

  MIPE Evolution
    ├── Phase 0: Seed Generation
    ├── Phase 1a: Island-DP
    │     ├── Evolver (×2)
    │     └── Tournament (×2 judge calls)
    ├── Phase 1b: Island-T
    ├── Phase 2: Assembly
    ├── Stage 3: Global Refinement
    ├── Phase 3: Validity Check
    ├── Sentinel-1 (post-retrieve)
    └── Sentinel-2 (post-think)

Usage:
    span_logger = MIPESpanLogger(mlflow_manager)

    with span_logger.phase("Phase 1a: Island-DP"):
        # evolver + tournament calls (auto-traced by DSPy autolog)
        ...
    span_logger.log_phase_metric("phase_1a_dp_best_fitness", 7.5)
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

from loguru import logger


class MIPESpanLogger:
    """Lightweight wrapper that adds MIPE-specific structure to MLflow traces.

    When MLflow is unavailable or disabled, all methods are safe no-ops.
    """

    def __init__(self, mlflow_manager: Optional[Any] = None) -> None:
        self._manager = mlflow_manager
        self._mlflow = None
        self._start_span = None
        self._enabled = False

        if mlflow_manager is not None:
            ml = getattr(mlflow_manager, "_mlflow", None)
            if ml and getattr(mlflow_manager, "_initialized", False):
                self._mlflow = ml
                try:
                    from mlflow.tracing.fluent import start_span
                    self._start_span = start_span
                    self._enabled = True
                except ImportError:
                    pass

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Phase-level span (parent span for a group of LLM calls)
    # ------------------------------------------------------------------

    @contextmanager
    def phase(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """Create a named parent span for a MIPE phase.

        All DSPy LLM calls inside this context manager will be nested
        under this span in the MLflow trace view.

        Args:
            name: Human-readable phase name (e.g. "Phase 1a: Island-DP")
            attributes: Optional extra attributes to attach.
        """
        if not self._enabled or self._start_span is None:
            yield None
            return

        t0 = time.monotonic()
        span_ctx = None
        try:
            span_ctx = self._start_span(name)
            span = span_ctx.__enter__()

            attrs = {
                "roma.component": "mipe",
                "roma.phase": name,
            }
            if attributes:
                attrs.update(attributes)
            span.set_attributes(attrs)

            yield span

        except Exception:
            import sys
            exc_info = sys.exc_info()
            if span_ctx is not None:
                try:
                    span_ctx.__exit__(*exc_info)
                except Exception:
                    pass
                span_ctx = None
            raise

        finally:
            elapsed = time.monotonic() - t0
            if span_ctx is not None:
                try:
                    span_ctx.__exit__(None, None, None)
                except Exception as e:
                    logger.debug(f"Failed to close MIPE span '{name}': {e}")
            logger.debug(f"[MIPE-SPAN] {name} completed in {elapsed:.1f}s")

    # ------------------------------------------------------------------
    # Metric logging (appears in MLflow Run Metrics tab)
    # ------------------------------------------------------------------

    def log_metric(self, key: str, value: float) -> None:
        """Log a single numeric metric to the active MLflow run."""
        if not self._enabled:
            return
        try:
            self._mlflow.log_metric(key, value)
        except Exception as e:
            logger.debug(f"[MIPE-SPAN] Failed to log metric {key}: {e}")

    def log_metrics(self, metrics: Dict[str, float]) -> None:
        """Log multiple metrics at once."""
        if not self._enabled:
            return
        try:
            self._mlflow.log_metrics(metrics)
        except Exception as e:
            logger.debug(f"[MIPE-SPAN] Failed to log metrics: {e}")

    def log_param(self, key: str, value: Any) -> None:
        """Log a single parameter to the active MLflow run."""
        if not self._enabled:
            return
        try:
            self._mlflow.log_param(key, value)
        except Exception as e:
            logger.debug(f"[MIPE-SPAN] Failed to log param {key}: {e}")

    def log_params(self, params: Dict[str, Any]) -> None:
        """Log multiple parameters at once."""
        if not self._enabled:
            return
        try:
            self._mlflow.log_params(params)
        except Exception as e:
            logger.debug(f"[MIPE-SPAN] Failed to log params: {e}")

    # ------------------------------------------------------------------
    # Convenience: log MIPE summary at the end of the pipeline
    # ------------------------------------------------------------------

    def log_mipe_summary(
        self,
        succeeded: bool,
        p0_fitness: float,
        final_fitness: float,
        dp_best_fitness: float,
        t_best_fitness: float,
        elapsed_s: float,
        winning_strategies: Optional[Dict[str, str]] = None,
        edit_script_stats: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log the full MIPE summary as metrics + params."""
        self.log_metrics({
            "mipe.succeeded": 1.0 if succeeded else 0.0,
            "mipe.p0_fitness": p0_fitness,
            "mipe.final_fitness": final_fitness,
            "mipe.dp_best_fitness": dp_best_fitness,
            "mipe.t_best_fitness": t_best_fitness,
            "mipe.fitness_gain": final_fitness - p0_fitness,
            "mipe.gain_dp": dp_best_fitness - p0_fitness,
            "mipe.gain_topology": t_best_fitness - dp_best_fitness,
            "mipe.elapsed_s": elapsed_s,
        })
        if winning_strategies:
            for island, strategy in winning_strategies.items():
                self.log_param(f"mipe.winning_strategy.{island}", strategy)

        if edit_script_stats:
            es = edit_script_stats
            self.log_metrics({
                "mipe.edit_script.total_edits": float(es.get("total_edits", 0)),
                "mipe.edit_script.prompt_edits": float(es.get("prompt_edits", 0)),
                "mipe.edit_script.topo_edits": float(es.get("topo_edits", 0)),
                "mipe.edit_script.parse_failures": float(es.get("parse_failures", 0)),
                "mipe.edit_script.consistency_fixes": float(es.get("consistency_fixes", 0)),
            })
            self.log_param(
                "mipe.edit_script.merge_mode",
                es.get("merge_mode", "unknown"),
            )

    def log_sentinel_result(
        self,
        checkpoint_type: str,
        adjustment_needed: bool,
        adjustment_type: str,
        affected_count: int,
        confidence: float,
    ) -> None:
        """Log a Sentinel checkpoint outcome as metrics."""
        prefix = f"sentinel.{checkpoint_type}"
        self.log_metrics({
            f"{prefix}.triggered": 1.0 if adjustment_needed else 0.0,
            f"{prefix}.affected_nodes": float(affected_count),
            f"{prefix}.confidence": confidence,
        })
        self.log_param(f"{prefix}.adjustment_type", adjustment_type)
