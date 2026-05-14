"""StrategyStore — lightweight persistence for mutation strategy win-rates.

Designed as the Phase 1 backbone for MutationStrategyTracker.  The full
Phase 4 PlanArchive (with Blueprint storage and embedding retrieval) will
be implemented separately and can reuse or wrap this store.

Storage layout (under ``storage_dir``):
  strategy_win_rates.json   — dict: {island: {strategy: float}}  (EMA-smoothed)
  evolution_log.jsonl       — append-only JSONL: one record per MIPE run

The store is intentionally file-based and dependency-free (no DB, no
embedding model) so it can run in any environment.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_WIN_RATES: Dict[str, Dict[str, float]] = {
    "topology": {
        "add_node": 0.5,
        "split_node": 0.5,
        "redirect_deps": 0.5,
        "delete_node": 0.5,
    },
    "dynamic_prompt": {
        "set_scope_boundary": 0.5,
        "set_evidence_spec": 0.5,
        "set_execution_method": 0.5,
        "set_depth_allocation": 0.5,
        "transfer_anchor": 0.5,
    },
}


class StrategyStore:
    """Persists mutation strategy win-rates and evolution run logs.

    Thread-safety: This class is NOT thread-safe. In the MIPE flow each
    run is sequential-within-a-process, so this is acceptable.
    """

    WIN_RATES_FILE = "strategy_win_rates.json"
    EVOLUTION_LOG_FILE = "evolution_log.jsonl"

    def __init__(self, storage_dir: str = ".mipe_store") -> None:
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._win_rates_path = self._dir / self.WIN_RATES_FILE
        self._log_path = self._dir / self.EVOLUTION_LOG_FILE

    # ------------------------------------------------------------------
    # Strategy Win Rates (for MutationStrategyTracker)
    # ------------------------------------------------------------------

    def load_strategy_win_rates(self) -> Optional[Dict[str, Dict[str, float]]]:
        """Load persisted win rates. Returns None if no data exists yet."""
        if not self._win_rates_path.exists():
            return None
        try:
            with open(self._win_rates_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
        return None

    def save_strategy_win_rates(
        self, win_rates: Dict[str, Dict[str, float]]
    ) -> None:
        """Persist current win rates atomically."""
        tmp = self._win_rates_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(win_rates, f, ensure_ascii=False, indent=2)
            tmp.replace(self._win_rates_path)
        except Exception as e:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise e

    def total_runs(self) -> int:
        """Count the number of evolution runs recorded so far."""
        if not self._log_path.exists():
            return 0
        count = 0
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Evolution Log (append-only, for analysis & future flywheel)
    # ------------------------------------------------------------------

    def append_evolution_log(
        self,
        *,
        goal: str,
        succeeded: bool,
        winning_strategy_trajectory: Dict[str, str],
        final_fitness: float,
        failure_reason: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one MIPE run record to the evolution log.

        Args:
            goal: Original user query.
            succeeded: Whether P_final beat P₀ in the validity check.
            winning_strategy_trajectory: {island_id: strategy_name} for
                each island's winning strategy in this run.
            final_fitness: P_final fitness score (or P₀ fitness if fell back).
            failure_reason: Set when succeeded=False; describes why.
            extra: Additional metadata (e.g. run duration, LLM call count).
        """
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "goal_preview": goal[:120],
            "succeeded": succeeded,
            "winning_strategy_trajectory": winning_strategy_trajectory,
            "final_fitness": final_fitness,
        }
        if failure_reason:
            record["failure_reason"] = failure_reason
        if extra:
            record.update(extra)

        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def recent_logs(self, n: int = 20) -> List[Dict[str, Any]]:
        """Return the last *n* evolution log entries (newest last)."""
        if not self._log_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
        return records[-n:]
