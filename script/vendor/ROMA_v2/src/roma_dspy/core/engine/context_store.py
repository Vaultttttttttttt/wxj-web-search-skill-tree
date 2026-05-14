"""Thread-safe storage for task execution contexts."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from roma_dspy.core.signatures.base_models.plan_blueprint import PlanBlueprint


_NULL_LIKE = frozenset({"null", "none", "n/a", "nil", "undefined", ""})


def _sanitize_directive(value: Any) -> Optional[str]:
    """Return *None* if *value* is empty or a null-like string from LLM output."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    return None if value.strip().lower() in _NULL_LIKE else value


class ContextStore:
    """Thread-safe storage for task execution contexts with O(1) lookup."""

    def __init__(self) -> None:
        self._store: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        # Map subgraph_id -> {index -> task_id}
        self._index_maps: Dict[str, Dict[int, str]] = {}

        # Execution context for LM tracing
        self._execution_id: Optional[str] = None
        self._postgres_storage: Optional[Any] = None

        # Global report_policy from root Planner (injected into nested Planner context)
        self._report_policy: Optional[str] = None

        # PlanBlueprint context from MIPE evolution (injected into runtime Planner)
        self._plan_blueprint_context: Optional[str] = None

        # Full PlanBlueprint object for node-scoped context generation
        self._plan_blueprint: Optional["PlanBlueprint"] = None

        # MLflow manager reference for Sentinel span logging
        self._mlflow_manager: Optional[Any] = None

    # ------------------------------------------------------------------
    # report_policy storage (global Planner strategy, NOT injected into executors)
    # ------------------------------------------------------------------

    def store_report_policy(self, policy: str) -> None:
        """Store the root Planner's report_policy for nested Planner context injection."""
        self._report_policy = policy

    def get_report_policy(self) -> Optional[str]:
        """Retrieve the root Planner's report_policy."""
        return self._report_policy

    # ------------------------------------------------------------------
    # PlanBlueprint context (MIPE evolution output, injected into Planner)
    # ------------------------------------------------------------------

    def store_plan_blueprint_context(self, context: str) -> None:
        """Store serialized PlanBlueprint for runtime Planner context injection."""
        self._plan_blueprint_context = context

    def get_plan_blueprint_context(self) -> Optional[str]:
        """Retrieve serialized PlanBlueprint context."""
        return self._plan_blueprint_context

    # ------------------------------------------------------------------
    # Full PlanBlueprint object (for node-scoped context in Sub-Planner)
    # ------------------------------------------------------------------

    def store_plan_blueprint(self, blueprint: "PlanBlueprint") -> None:
        """Store the full PlanBlueprint for node-scoped Sub-Planner context."""
        self._plan_blueprint = blueprint

    def get_plan_blueprint(self) -> Optional["PlanBlueprint"]:
        """Retrieve the full PlanBlueprint object."""
        return self._plan_blueprint

    # ------------------------------------------------------------------
    # MLflow manager reference (for Sentinel/runtime span logging)
    # ------------------------------------------------------------------

    def set_mlflow_manager(self, manager: Any) -> None:
        """Store MLflow manager reference for runtime components."""
        self._mlflow_manager = manager

    def get_mlflow_manager(self) -> Optional[Any]:
        """Retrieve MLflow manager for span logging."""
        return self._mlflow_manager

    # ------------------------------------------------------------------
    # Pickle / deepcopy support
    # ------------------------------------------------------------------

    def __getstate__(self) -> dict:
        """Drop unpickleable asyncio.Lock during serialization."""
        state = self.__dict__.copy()
        state.pop("_lock", None)
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Result storage
    # ------------------------------------------------------------------

    async def store_result(self, task_id: str, result: str) -> None:
        """Store task result in a thread-safe manner."""
        async with self._lock:
            self._store[task_id] = result

    def store_result_sync(self, task_id: str, result: str) -> None:
        """Store task result synchronously without async locking.

        WARNING: Not thread-safe. Use only in synchronous execution contexts
        where async locking is not available.
        """
        self._store[task_id] = result

    def get_result(self, task_id: str) -> Optional[str]:
        """Retrieve task result with O(1) lookup."""
        return self._store.get(task_id)

    # ------------------------------------------------------------------
    # Index ↔ task_id mapping (within subgraphs)
    # ------------------------------------------------------------------

    def register_index_mapping(
        self, subgraph_id: str, index: int, task_id: str
    ) -> None:
        """Register mapping between subtask index and task_id for a subgraph."""
        if subgraph_id not in self._index_maps:
            self._index_maps[subgraph_id] = {}
        self._index_maps[subgraph_id][index] = task_id

    def get_task_id_from_index(self, subgraph_id: str, index: int) -> Optional[str]:
        """Get task_id from subtask index within a subgraph."""
        return self._index_maps.get(subgraph_id, {}).get(index)

    def get_task_index(self, subgraph_id: str, task_id: str) -> Optional[int]:
        """Get the index of a task within its subgraph."""
        index_map = self._index_maps.get(subgraph_id, {})
        for idx, tid in index_map.items():
            if tid == task_id:
                return idx
        return None

    # ------------------------------------------------------------------
    # Execution context for LM tracing
    # ------------------------------------------------------------------

    def set_execution_context(
        self, execution_id: str, postgres_storage: Optional[Any] = None
    ) -> None:
        """Set execution context for LM trace persistence."""
        self._execution_id = execution_id
        self._postgres_storage = postgres_storage

    def get_execution_context(self) -> tuple[Optional[str], Optional[Any]]:
        """Get execution context for LM tracing."""
        return self._execution_id, self._postgres_storage

    # ------------------------------------------------------------------
    # Context aggregation helpers
    # ------------------------------------------------------------------

    def get_context_for_dependencies(self, dep_ids: List[str]) -> str:
        """Build context string from dependency task results."""
        contexts = []
        for dep_id in dep_ids:
            result = self.get_result(dep_id)
            if result:
                contexts.append(f"[Task {dep_id[:8]}]: {result}")
        return "\n\n".join(contexts) if contexts else ""

    def get_context_for_dependency_indices(
        self, subgraph_id: str, dep_indices: List[str]
    ) -> str:
        """Build context string from dependency indices within a subgraph."""
        contexts = []
        index_map = self._index_maps.get(subgraph_id, {})

        for dep_idx_str in dep_indices:
            try:
                dep_idx = int(dep_idx_str)
                task_id = index_map.get(dep_idx)
                if task_id:
                    result = self.get_result(task_id)
                    if result:
                        contexts.append(f"[Subtask {dep_idx}]: {result}")
            except (ValueError, TypeError):
                continue

        return "\n\n".join(contexts) if contexts else ""

    def clear_subgraph(self, task_ids: List[str]) -> None:
        """Clear results for specific tasks to free memory."""
        for task_id in task_ids:
            self._store.pop(task_id, None)

    def get_all_contexts(self) -> Dict[str, str]:
        """Get all stored contexts for inspection/debugging."""
        return dict(self._store)

    def get_context_summary(self) -> str:
        """Get human-readable summary of all stored contexts."""
        if not self._store:
            return "No contexts stored yet."

        lines = ["Context Store Summary:", "=" * 80]
        for task_id, result in self._store.items():
            lines.append(f"\nTask ID: {task_id[:8]}...")
            result_str = str(result) if not isinstance(result, str) else result
            lines.append(
                f"Result: {result_str[:200]}{'...' if len(result_str) > 200 else ''}"
            )
            lines.append("-" * 80)
        return "\n".join(lines)
