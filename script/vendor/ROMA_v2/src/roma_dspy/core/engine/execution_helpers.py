"""Artifact scanning, parallel scheduling and error-context helpers."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional, TYPE_CHECKING

from loguru import logger

from roma_dspy.core.artifacts.filesystem_scanner import (
    scan_execution_directory,
    auto_register_scanned_files,
)
from roma_dspy.core.artifacts.text_parser import parse_and_register_artifacts
from roma_dspy.core.context import ExecutionContext
from roma_dspy.types import AgentType, ModuleResult, TaskStatus, TokenMetrics

if TYPE_CHECKING:
    from roma_dspy.core.engine.dag import TaskDAG
    from roma_dspy.core.signatures import TaskNode


class ExecutionHelpersMixin:
    """Mixin that provides artifact scanning, parallel scheduling, and error helpers.

    Requires subclass to expose ``context_store`` (ContextStore).
    """

    # ------------------------------------------------------------------
    # Artifact detection
    # ------------------------------------------------------------------

    async def _run_text_parser(self, task: "TaskNode", result: Any) -> None:
        """Detect explicit artifact declarations in LLM output."""
        try:
            text: Optional[str] = None
            if hasattr(result, "output") and result.output:
                text = str(result.output)
            elif isinstance(result, str):
                text = result

            if not text:
                return

            await parse_and_register_artifacts(text, task.task_id)

        except Exception as e:
            logger.debug(f"Text parser failed for task {task.task_id}: {e}")

    async def _run_filesystem_scanner(
        self, task: "TaskNode", start_time: float
    ) -> None:
        """Detect and register artifacts created on disk during execution.

        Only scans known artifact subdirectories to avoid picking up pre-existing
        codebase files.
        """
        try:
            ctx = ExecutionContext.get()
            if not ctx or not ctx.file_storage:
                return

            scanner_config = ctx.file_storage.config.filesystem_scanner
            if not scanner_config.enabled:
                logger.debug("Filesystem scanner disabled via config")
                return

            artifact_subdirs = [
                ctx.file_storage.ARTIFACTS_SUBDIR,
                ctx.file_storage.OUTPUTS_SUBDIR,
                ctx.file_storage.RESULTS_SUBDIR,
                ctx.file_storage.LOGS_SUBDIR,
            ]

            scan_dirs = []
            for subdir in artifact_subdirs:
                scan_path = Path(ctx.file_storage.root) / subdir
                if scan_path.exists():
                    scan_dirs.append(scan_path)
                else:
                    logger.debug(
                        f"Artifact subdir does not exist, skipping: {scan_path}"
                    )

            if not scan_dirs:
                logger.debug(
                    "No artifact subdirectories exist yet - skipping filesystem scan"
                )
                return

            logger.debug(
                f"Filesystem scanner will scan {len(scan_dirs)} artifact "
                f"subdirectories: {[str(d) for d in scan_dirs]}"
            )

            filter_time: Optional[float] = None
            if scanner_config.filter_by_mtime:
                filter_time = start_time - scanner_config.mtime_buffer_seconds
                logger.debug(
                    f"Applying mtime filter: files modified after {filter_time} "
                    f"(start_time={start_time}, "
                    f"buffer={scanner_config.mtime_buffer_seconds}s)"
                )
            else:
                logger.debug("Mtime filtering disabled - will include all files")

            found_files = []
            for scan_dir in scan_dirs:
                found_files.extend(scan_execution_directory(scan_dir, filter_time))

            if found_files:
                await auto_register_scanned_files(
                    file_paths=[str(f) for f in found_files],
                    execution_id=task.task_id,
                )
                logger.info(
                    f"Filesystem scanner registered artifacts from "
                    f"{len(scan_dirs)} subdirectories",
                    total_files=len(found_files),
                    subdirs=[d.name for d in scan_dirs],
                )
            else:
                logger.debug("Filesystem scanner found no new files to register")

        except Exception as e:
            logger.debug(f"Filesystem scanner failed for task {task.task_id}: {e}")

    # ------------------------------------------------------------------
    # Module result recording
    # ------------------------------------------------------------------

    def _record_module_result(
        self,
        task: "TaskNode",
        module_name: str,
        input_data: Any,
        output_data: Any,
        duration: float,
        metadata: Optional[dict] = None,
        token_metrics: Optional[TokenMetrics] = None,
        messages: Optional[list] = None,
    ) -> "TaskNode":
        module_result = ModuleResult(
            module_name=module_name,
            input=input_data,
            output=output_data,
            timestamp=datetime.now(),
            duration=duration,
            metadata=metadata or {},
            token_metrics=token_metrics,
            messages=messages,
        )
        return task.record_module_execution(module_name, module_result)

    # ------------------------------------------------------------------
    # Parallel scheduling
    # ------------------------------------------------------------------

    def _get_ready_tasks(
        self,
        subgraph: "TaskDAG",
        pending: set,
        completed: set,
    ) -> List["TaskNode"]:
        """Return tasks whose dependencies are all completed."""
        ready: List["TaskNode"] = []
        for task_id in pending:
            task = subgraph.get_node(task_id)
            dependencies = subgraph.get_task_dependencies(task_id)
            dep_ids = [dep.task_id for dep in dependencies]
            unmet = [did for did in dep_ids if did not in completed]
            if not unmet:
                ready.append(task)
            else:
                logger.debug(
                    f"[ready_check] {task_id[:8]}({task.task_type.value}) "
                    f"blocked by {len(unmet)} unmet deps: "
                    f"{[d[:8] for d in unmet]}"
                )
        return ready

    async def _execute_tasks_parallel(
        self,
        tasks: Iterable["TaskNode"],
        subgraph: "TaskDAG",
        solve_fn: Any,
    ) -> List["TaskNode"]:
        """Execute a wave of independent tasks with concurrency control.

        Tasks are processed in chunks of ``runtime.max_concurrency`` to
        avoid overwhelming the LLM API with too many simultaneous requests.
        """
        task_list = [
            t for t in tasks
            if t.status in (TaskStatus.PENDING, TaskStatus.READY)
        ]
        if not task_list:
            return []

        max_concurrent = 5
        cfg = getattr(self, "config", None)
        if cfg and hasattr(cfg, "runtime"):
            max_concurrent = getattr(cfg.runtime, "max_concurrency", 5)

        if len(task_list) <= max_concurrent:
            coros = [solve_fn(t, subgraph, t.depth) for t in task_list]
            outcomes = await asyncio.gather(*coros, return_exceptions=True)
            return self._resolve_parallel_outcomes(task_list, outcomes)

        logger.info(
            f"[parallel] Chunking {len(task_list)} ready tasks into "
            f"batches of {max_concurrent}"
        )
        results: List["TaskNode"] = []
        for i in range(0, len(task_list), max_concurrent):
            chunk = task_list[i : i + max_concurrent]
            coros = [solve_fn(t, subgraph, t.depth) for t in chunk]
            outcomes = await asyncio.gather(*coros, return_exceptions=True)
            results.extend(self._resolve_parallel_outcomes(chunk, outcomes))
        return results

    def _resolve_parallel_outcomes(
        self,
        tasks: List["TaskNode"],
        outcomes: List[Any],
    ) -> List["TaskNode"]:
        """Resolve parallel execution outcomes and tolerate skippable server errors."""
        resolved: List["TaskNode"] = []
        hard_errors: List[Exception] = []

        for task, outcome in zip(tasks, outcomes):
            if isinstance(outcome, Exception):
                if self._should_skip_content_filter_errors() and self._is_skippable_content_filter_error(outcome):
                    resolved.append(
                        self._mark_task_as_skipped_for_content_filter(task, outcome)
                    )
                    continue
                hard_errors.append(outcome)
                continue
            resolved.append(outcome)

        if hard_errors:
            raise hard_errors[0]

        return resolved

    def _should_skip_content_filter_errors(self) -> bool:
        """Read runtime toggle for tolerating provider-side content filter errors."""
        cfg = getattr(self, "config", None)
        runtime_cfg = getattr(cfg, "runtime", None) if cfg else None
        return bool(getattr(runtime_cfg, "skip_content_filter_badrequest", True))

    @staticmethod
    def _is_skippable_content_filter_error(error: Exception) -> bool:
        """Check whether an exception matches server-side content filter errors."""
        checks = {
            "datainspectionfailed",
            "input text data may contain inappropriate content",
            "litellm.badrequesterror",
            "badrequesterror",
            "openaiexception - <400>",
        }

        visited: set[int] = set()
        stack: List[BaseException] = [error]

        while stack:
            current = stack.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))

            text = str(current).lower()
            if any(token in text for token in checks):
                return True

            cause = getattr(current, "__cause__", None)
            context = getattr(current, "__context__", None)
            if cause is not None:
                stack.append(cause)
            if context is not None:
                stack.append(context)

        return False

    def _mark_task_as_skipped_for_content_filter(
        self,
        task: "TaskNode",
        error: Exception,
    ) -> "TaskNode":
        """Convert a failed subtask into a tolerated 'skipped' completed task."""
        metadata = dict(task.metadata or {})
        metadata.update(
            {
                "skipped_due_to_content_filter": True,
                "skip_reason": "server_content_filter_bad_request",
                "skip_error": str(error)[:500],
            }
        )

        logger.warning(
            f"[parallel] Skipping task {task.task_id[:8]} due to content-filter "
            f"BadRequest; execution will continue."
        )
        return task.restore_state(status=TaskStatus.COMPLETED, result="", metadata=metadata)

    # ------------------------------------------------------------------
    # Error context enrichment
    # ------------------------------------------------------------------

    def _enhance_error_context(
        self,
        error: Exception,
        agent_type: AgentType,
        task: Optional["TaskNode"],
    ) -> None:
        """Enrich an exception with agent-type and task-id context."""
        task_id = task.task_id if task is not None else "unknown"
        error_msg = (
            f"[{agent_type.value.upper()}] Task '{task_id}' failed: {str(error)}"
        )
        if hasattr(error, "args") and error.args:
            error.args = (error_msg,) + error.args[1:]
        else:
            error.args = (error_msg,)
