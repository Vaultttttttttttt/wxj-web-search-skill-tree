"""ExecutionService for managing solver lifecycle and background tasks."""

import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from uuid import uuid4

from loguru import logger

from roma_dspy.api.schemas import (
    ApprovedResearchPlan,
    ResearchPlanDirectives,
    ResearchPlanDraftResponse,
)
from roma_dspy.core.engine.solve import RecursiveSolver
from roma_dspy.core.storage.postgres_storage import PostgresStorage
from roma_dspy.core.engine.dag import TaskDAG
from roma_dspy.config.manager import ConfigManager
from roma_dspy.types import ExecutionStatus, TaskStatus


class ExecutionCache:
    """
    In-memory cache for execution status with TTL.

    Reduces database load for frequent polling.
    """

    def __init__(self, ttl_seconds: int = 5):
        """
        Initialize cache with TTL.

        Args:
            ttl_seconds: Time-to-live for cached entries
        """
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._timestamps: Dict[str, datetime] = {}

    def get(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """Get cached execution data if not expired."""
        if execution_id not in self._cache:
            return None

        timestamp = self._timestamps[execution_id]
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()

        if age > self.ttl_seconds:
            # Expired
            del self._cache[execution_id]
            del self._timestamps[execution_id]
            return None

        return self._cache[execution_id]

    def set(self, execution_id: str, data: Dict[str, Any]) -> None:
        """Cache execution data with current timestamp."""
        self._cache[execution_id] = data
        self._timestamps[execution_id] = datetime.now(timezone.utc)

    def invalidate(self, execution_id: str) -> None:
        """Invalidate cached entry."""
        self._cache.pop(execution_id, None)
        self._timestamps.pop(execution_id, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        self._timestamps.clear()

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)


class ExecutionService:
    """
    Manages execution lifecycle for RecursiveSolver.

    Responsibilities:
    - Background task management
    - In-memory status caching
    - Error propagation
    - Execution cleanup

    NOT responsible for:
    - Checkpoint management (use RecursiveSolver directly)
    - Visualization (use visualizer classes directly)
    """

    def __init__(
        self,
        storage: PostgresStorage,
        config_manager: ConfigManager,
        cache_ttl_seconds: int = 5,
    ):
        """
        Initialize ExecutionService.

        Args:
            storage: PostgresStorage instance
            config_manager: ConfigManager instance
            cache_ttl_seconds: Cache TTL in seconds (default: 5)
        """
        self.storage = storage
        self.config_manager = config_manager
        self.cache = ExecutionCache(ttl_seconds=cache_ttl_seconds)

        # Track background tasks
        self._background_tasks: Dict[str, asyncio.Task] = {}
        self._plan_drafts: Dict[str, ResearchPlanDraftResponse] = {}

        logger.info("ExecutionService initialized")

    @staticmethod
    def _execution_metadata_from_record(execution: Optional[Any]) -> Dict[str, Any]:
        """Safely extract execution metadata from a storage record."""
        metadata = getattr(execution, "execution_metadata", None) if execution else None
        return dict(metadata) if isinstance(metadata, dict) else {}

    async def reconcile_orphaned_execution(
        self, execution_id: str, execution: Optional[Any] = None
    ) -> Optional[Any]:
        """Mark stale RUNNING executions as failed when no worker task exists."""
        execution = execution or await self.storage.get_execution(execution_id)
        if not execution:
            return None

        if execution.status != ExecutionStatus.RUNNING.value or self.is_running(
            execution_id
        ):
            return execution

        merged_metadata = {
            **self._execution_metadata_from_record(execution),
            "error": (
                "Execution was interrupted before completion because its background "
                "worker task is no longer active."
            ),
            "error_type": "ExecutionInterruptedError",
            "interrupted": True,
            "interrupted_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.storage.update_execution(
            execution_id=execution_id,
            status=ExecutionStatus.FAILED.value,
            execution_metadata=merged_metadata,
        )
        self.cache.invalidate(execution_id)
        logger.warning(
            f"Reconciled orphaned execution {execution_id}: "
            "status running -> failed (worker task missing)"
        )
        return await self.storage.get_execution(execution_id)

    def _load_config_bundle(
        self,
        config_profile: str,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> tuple[Any, Dict[str, Any], str]:
        """Load config and return the serialized bundle used by executions."""
        config = self.config_manager.load_config(
            profile=config_profile, overrides=config_overrides or {}
        )

        experiment_name = "unknown"
        if config.observability and config.observability.mlflow:
            experiment_name = getattr(
                config.observability.mlflow, "experiment_name", "unknown"
            )

        if hasattr(config, "to_dict"):
            config_dict = config.to_dict()
        elif hasattr(config, "model_dump"):
            config_dict = config.model_dump()
        else:
            try:
                config_dict = dict(config)
            except (TypeError, ValueError):
                config_dict = getattr(config, "__dict__", str(config))

        return config, config_dict, experiment_name

    @staticmethod
    def _normalize_goal(goal: str, user_notes: Optional[str] = None) -> str:
        """Build the goal string passed to planning/execution."""
        notes = (user_notes or "").strip()
        if not notes:
            return goal
        return (
            f"{goal}\n\n"
            "用户补充要求：\n"
            f"{notes}"
        )

    @staticmethod
    def _strip_goal_user_notes(goal: str) -> str:
        """Remove the appended user-notes block from a previously normalized goal."""
        marker = "\n\n用户补充要求：\n"
        base_goal, separator, _ = goal.partition(marker)
        if separator:
            return base_goal.strip()
        return goal.strip()

    @classmethod
    def _build_execution_goal(
        cls, goal: str, approved_plan: Optional[ApprovedResearchPlan] = None
    ) -> str:
        """Build the effective goal string for a confirmed execution."""
        if not approved_plan:
            return goal

        base_goal = (approved_plan.refined_goal or goal or approved_plan.goal).strip()
        return cls._normalize_goal(
            cls._strip_goal_user_notes(base_goal), approved_plan.user_notes
        )

    @staticmethod
    def _build_plan_summary(
        subtasks: list[Any], dependencies_graph: Optional[Dict[str, list[str]]]
    ) -> Dict[str, Any]:
        """Create a small summary payload for the UI."""
        task_type_counts: Dict[str, int] = {}
        for subtask in subtasks:
            raw_task_type = (
                subtask.get("task_type")
                if isinstance(subtask, dict)
                else getattr(subtask, "task_type", None)
            )
            task_type = getattr(raw_task_type, "value", str(raw_task_type))
            task_type_counts[task_type] = task_type_counts.get(task_type, 0) + 1

        dependency_edges = sum(
            len(dep_indices) for dep_indices in (dependencies_graph or {}).values()
        )
        return {
            "subtask_count": len(subtasks),
            "dependency_edge_count": dependency_edges,
            "task_type_counts": task_type_counts,
        }

    async def draft_research_plan(
        self,
        goal: str,
        max_depth: int = 2,
        config_profile: str = "default",
        config_overrides: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user_notes: Optional[str] = None,
    ) -> ResearchPlanDraftResponse:
        """Generate a root research plan without starting execution."""
        plan_id = str(uuid4())
        normalized_goal = self._normalize_goal(goal, user_notes)
        config, _, _ = self._load_config_bundle(config_profile, config_overrides)

        solver = RecursiveSolver(config=config)
        plan_payload = await solver.async_draft_research_plan(normalized_goal, depth=0)

        directives = ResearchPlanDirectives(**plan_payload.get("directives", {}))
        subtasks = plan_payload.get("subtasks", [])
        dependencies_graph = plan_payload.get("dependencies_graph")
        now = datetime.now(timezone.utc)

        draft = ResearchPlanDraftResponse(
            plan_id=plan_id,
            goal=goal,
            refined_goal=normalized_goal,
            subtasks=subtasks,
            dependencies_graph=dependencies_graph,
            directives=directives,
            max_depth=max_depth,
            config_profile=config_profile,
            config_overrides=config_overrides,
            metadata=metadata or {},
            user_notes=user_notes,
            summary=self._build_plan_summary(subtasks, dependencies_graph),
            created_at=now,
            updated_at=now,
        )
        self._plan_drafts[plan_id] = draft
        return draft

    def get_research_plan(self, plan_id: str) -> Optional[ResearchPlanDraftResponse]:
        """Return a cached plan draft if it exists."""
        return self._plan_drafts.get(plan_id)

    async def start_execution(
        self,
        goal: str,
        max_depth: int = 2,
        config_profile: str = "default",
        config_overrides: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        approved_plan: Optional[ApprovedResearchPlan] = None,
    ) -> str:
        """
        Start a new execution in the background.

        Args:
            goal: Task goal to decompose and execute
            max_depth: Maximum recursion depth
            config_profile: Configuration profile name (required)
            config_overrides: Configuration overrides
            metadata: Additional metadata

        Returns:
            Execution ID
        """
        execution_id = str(uuid4())

        # Log config overrides for debugging
        if config_overrides:
            logger.info(f"[Execution {execution_id[:8]}] Config overrides received: {config_overrides}")
        
        config, config_dict, experiment_name = self._load_config_bundle(
            config_profile, config_overrides
        )
        effective_goal = self._build_execution_goal(goal, approved_plan)

        # Enhance metadata with profile and experiment info
        enhanced_metadata = metadata or {}
        enhanced_metadata.update(
            {
                "profile_name": config_profile,
                "experiment_name": experiment_name,
            }
        )
        if approved_plan:
            enhanced_metadata["research_plan"] = {
                "plan_id": approved_plan.plan_id,
                "refined_goal": approved_plan.refined_goal,
                "clarifications": approved_plan.clarifications,
                "user_notes": approved_plan.user_notes,
                "subtask_count": len(approved_plan.subtasks),
            }

        await self.storage.create_execution(
            execution_id=execution_id,
            initial_goal=effective_goal,
            max_depth=max_depth,
            profile=config_profile,
            experiment_name=experiment_name,
            config=config_dict,
            metadata=enhanced_metadata,
        )

        # Start background task
        task = asyncio.create_task(
            self._run_execution(
                execution_id,
                effective_goal,
                max_depth,
                config,
                approved_plan=approved_plan,
            )
        )
        self._background_tasks[execution_id] = task

        logger.info(
            f"Started execution {execution_id} (profile={config_profile}, experiment={experiment_name}) for goal: {effective_goal[:100]}"
        )
        return execution_id

    async def _run_execution(
        self,
        execution_id: str,
        goal: str,
        max_depth: int,
        config: Any,
        approved_plan: Optional[ApprovedResearchPlan] = None,
    ) -> None:
        """
        Run execution in background with error handling.

        Args:
            execution_id: Execution ID
            goal: Task goal
            max_depth: Maximum recursion depth
            config: Configuration object
        """
        try:
            # Update status to running
            await self.storage.update_execution(
                execution_id=execution_id, status=ExecutionStatus.RUNNING.value
            )
            self.cache.invalidate(execution_id)

            # Create solver
            solver = RecursiveSolver(config=config)

            # Create DAG with the same execution_id so that file storage
            # directories match the ID returned to the frontend.
            dag = TaskDAG(execution_id=execution_id)

            # Execute
            logger.info(f"Executing {execution_id}")
            result = await solver.async_solve(
                goal, dag=dag, depth=0, approved_plan=approved_plan.model_dump()
                if approved_plan
                else None
            )

            # DAG snapshot now saved via checkpoints (see checkpoint_manager)
            # Determine execution status based on task result
            # Check if result contains error indicators
            execution_failed = False

            if result:
                # Check explicit FAILED status (though currently not set by code)
                if result.status == TaskStatus.FAILED:
                    execution_failed = True

                # Check if result contains error text (common pattern for tool failures)
                if result.result and isinstance(result.result, str):
                    error_indicators = [
                        "error",
                        "failed",
                        "exception",
                        "invalid",
                        "not found",
                        "does not exist",
                    ]
                    result_lower = result.result.lower()
                    if any(indicator in result_lower for indicator in error_indicators):
                        execution_failed = True
                        logger.warning(
                            f"Execution {execution_id} result contains error indicators: {result.result[:200]}"
                        )

            if execution_failed:
                # Task failed - mark execution as failed
                execution_status = ExecutionStatus.FAILED.value
                logger.warning(f"Execution {execution_id} marked as failed")
            else:
                # Task completed successfully
                execution_status = ExecutionStatus.COMPLETED.value
                logger.info(f"Execution {execution_id} completed successfully")

            # Update execution status with final result
            await self.storage.update_execution(
                execution_id=execution_id,
                status=execution_status,
                final_result={"result": result.result, "status": result.status.value}
                if result
                else None,
            )
            self.cache.invalidate(execution_id)

        except Exception as e:
            import traceback
            logger.error(f"Execution {execution_id} failed: {e}")
            logger.error(f"Execution {execution_id} traceback:\n{traceback.format_exc()}")

            # Update status to failed - merge error info with existing metadata
            try:
                execution = await self.storage.get_execution(execution_id)

                # Safely merge metadata
                existing_metadata = {}
                if (
                    execution
                    and hasattr(execution, "execution_metadata")
                    and execution.execution_metadata
                ):
                    existing_metadata = (
                        execution.execution_metadata
                        if isinstance(execution.execution_metadata, dict)
                        else {}
                    )

                merged_metadata = {
                    **existing_metadata,
                    "error": str(e),
                    "error_type": type(e).__name__,
                }

                await self.storage.update_execution(
                    execution_id=execution_id,
                    status=ExecutionStatus.FAILED.value,
                    execution_metadata=merged_metadata,
                )
                self.cache.invalidate(execution_id)
            except Exception as storage_error:
                logger.error(
                    f"Failed to update execution {execution_id} status: {storage_error}"
                )

        finally:
            # Cleanup background task reference
            self._background_tasks.pop(execution_id, None)

            # Periodic cleanup if too many completed tasks
            if len(self._background_tasks) > 100:
                await self.cleanup_completed_tasks()

    async def get_execution_status(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        Get execution status with caching.

        Args:
            execution_id: Execution ID

        Returns:
            Execution status dictionary or None if not found
        """
        # Check cache first
        cached = self.cache.get(execution_id)
        if cached:
            if (
                cached.get("status") == ExecutionStatus.RUNNING.value
                and not self.is_running(execution_id)
            ):
                self.cache.invalidate(execution_id)
            else:
                return cached

        # Fetch from storage
        execution = await self.storage.get_execution(execution_id)
        if not execution:
            return None

        execution = await self.reconcile_orphaned_execution(execution_id, execution)
        if not execution:
            return None

        status_data = {
            "execution_id": execution.execution_id,
            "status": execution.status,
            "initial_goal": execution.initial_goal,
            "total_tasks": execution.total_tasks,
            "completed_tasks": execution.completed_tasks,
            "failed_tasks": execution.failed_tasks,
            "created_at": execution.created_at.isoformat(),
            "updated_at": execution.updated_at.isoformat(),
        }

        # Cache it
        self.cache.set(execution_id, status_data)

        return status_data

    async def cancel_execution(self, execution_id: str) -> bool:
        """
        Cancel a running execution.

        Args:
            execution_id: Execution ID to cancel

        Returns:
            True if cancelled (or already in terminal state), False if not running
        """
        terminal_statuses = {
            ExecutionStatus.CANCELLED.value,
            ExecutionStatus.COMPLETED.value,
            ExecutionStatus.FAILED.value,
        }

        task = self._background_tasks.get(execution_id)
        if not task:
            # Task not in memory — could be a service restart. Check DB status.
            execution = await self.storage.get_execution(execution_id)
            if not execution:
                logger.warning(f"No task or DB record found for execution {execution_id}")
                return False

            current_status = (execution.status or "").lower()
            if current_status in terminal_statuses:
                # Already in a terminal state, treat as success (idempotent)
                logger.info(
                    f"Execution {execution_id} already in terminal state '{current_status}', treating cancel as no-op"
                )
                return True

            # Still marked as running but task is gone (service restart). Force cancel.
            logger.warning(
                f"Execution {execution_id} has status '{current_status}' but no active task "
                "(likely due to service restart). Forcing status to cancelled."
            )
            await self.storage.update_execution(
                execution_id=execution_id, status=ExecutionStatus.CANCELLED.value
            )
            self.cache.invalidate(execution_id)
            return True

        # Cancel the in-memory asyncio task
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            logger.info(f"Execution {execution_id} cancelled")

        # Update status
        await self.storage.update_execution(
            execution_id=execution_id, status=ExecutionStatus.CANCELLED.value
        )
        self.cache.invalidate(execution_id)

        # Cleanup
        self._background_tasks.pop(execution_id, None)

        return True

    def is_running(self, execution_id: str) -> bool:
        """Check if execution is currently running in background."""
        task = self._background_tasks.get(execution_id)
        return task is not None and not task.done()

    def get_active_executions(self) -> list[str]:
        """Get list of active execution IDs."""
        return [
            exec_id
            for exec_id, task in self._background_tasks.items()
            if not task.done()
        ]

    async def cleanup_completed_tasks(self) -> int:
        """
        Clean up completed background tasks.

        Returns:
            Number of tasks cleaned up
        """
        completed = [
            exec_id for exec_id, task in self._background_tasks.items() if task.done()
        ]

        for exec_id in completed:
            self._background_tasks.pop(exec_id)

        return len(completed)

    async def shutdown(self) -> None:
        """Shutdown service and cancel all running tasks."""
        logger.info("Shutting down ExecutionService")

        # Cancel all running tasks
        for exec_id, task in list(self._background_tasks.items()):
            if not task.done():
                logger.info(f"Cancelling execution {exec_id}")
                task.cancel()

                try:
                    await task
                except asyncio.CancelledError:
                    try:
                        execution = await self.storage.get_execution(exec_id)
                        if (
                            execution
                            and execution.status == ExecutionStatus.RUNNING.value
                        ):
                            await self.storage.update_execution(
                                execution_id=exec_id,
                                status=ExecutionStatus.CANCELLED.value,
                                execution_metadata={
                                    **self._execution_metadata_from_record(execution),
                                    "cancelled_by": "service_shutdown",
                                    "cancelled_at": datetime.now(
                                        timezone.utc
                                    ).isoformat(),
                                },
                            )
                            self.cache.invalidate(exec_id)
                    except Exception as storage_error:
                        logger.error(
                            f"Failed to mark execution {exec_id} cancelled during shutdown: "
                            f"{storage_error}"
                        )

        # Clear cache
        self.cache.clear()

        logger.info("ExecutionService shutdown complete")
