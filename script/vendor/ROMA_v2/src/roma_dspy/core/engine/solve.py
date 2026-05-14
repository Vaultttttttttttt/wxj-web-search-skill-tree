"""
Recursive solver for hierarchical task decomposition with depth constraints.
"""

import asyncio
import re
import threading
import warnings
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Callable, Optional, Union, Tuple, Dict, List, TYPE_CHECKING

import dspy
from loguru import logger

from roma_dspy.core.engine import TaskDAG
from roma_dspy.core.engine.event_loop import EventLoopController
from roma_dspy.core.engine.runtime import ModuleRuntime
from roma_dspy.core.registry import AgentRegistry
from roma_dspy.core.factory.agent_factory import AgentFactory
from roma_dspy.core.signatures import SubTask, TaskNode
from roma_dspy.core.storage import FileStorage
from roma_dspy.core.context import ContextManager, ExecutionContext
from roma_dspy.types import TaskStatus, TaskType, AgentType, ExecutionEventType, NodeType
from roma_dspy.types.checkpoint_types import CheckpointTrigger
from roma_dspy.types.checkpoint_models import CheckpointConfig
from roma_dspy.resilience.checkpoint_manager import CheckpointManager
from roma_dspy.config.schemas.root import ROMAConfig
from roma_dspy.core.observability import ObservabilityManager
from roma_dspy.tools.base.manager import ToolkitManager
from roma_dspy.utils.lazy_imports import HAS_PERSISTENCE, HAS_MLFLOW

if TYPE_CHECKING:
    pass

# Suppress DSPy warnings about forward() usage
warnings.filterwarnings("ignore", message="Calling module.forward.*is discouraged")


class RecursiveSolver:
    """
    Implements recursive hierarchical task decomposition algorithm.

    Key features:
    - Maximum recursion depth constraint with forced execution
    - Comprehensive execution tracking for all modules
    - State-based execution flow
    - Nested DAG management for hierarchical decomposition
    - Async and sync execution support
    - Integrated visualization support
    """

    def __init__(
        self,
        config: Optional["ROMAConfig"] = None,
        registry: Optional[AgentRegistry] = None,
        max_depth: Optional[int] = None,
        enable_logging: bool = False,
        enable_checkpoints: bool = True,
        checkpoint_config: Optional[CheckpointConfig] = None,
    ):
        """
        Initialize the recursive solver.

        Args:
            config: ROMAConfig instance with complete configuration
            registry: Pre-configured AgentRegistry (overrides config)
            max_depth: Maximum recursion depth (overrides config)
            enable_logging: Whether to enable debug logging
            enable_checkpoints: Whether to enable checkpointing
            checkpoint_config: Checkpoint configuration (overrides config)
        """
        # Store config for later use (needed for FileStorage creation)
        self.config = config

        # Initialize registry from config or use provided
        if registry is not None:
            self.registry = registry
            self.max_depth = max_depth or 2
        elif config is not None:
            factory = AgentFactory()
            self.registry = AgentRegistry()
            self.registry.initialize_from_config(config, factory)
            self.max_depth = max_depth or config.runtime.max_depth
        else:
            raise ValueError("Either 'config' or 'registry' must be provided")

        # Initialize Postgres storage if enabled and available
        self.postgres_storage = None
        if (
            config
            and config.storage
            and config.storage.postgres
            and config.storage.postgres.enabled
        ):
            if HAS_PERSISTENCE:
                from roma_dspy.core.storage import PostgresStorage

                self.postgres_storage = PostgresStorage(config.storage.postgres)
                logger.info("PostgreSQL persistence enabled")
            else:
                logger.warning(
                    "PostgreSQL persistence requested but dependencies not installed. "
                    "Install with: uv pip install roma-dspy[persistence]"
                )

        # Initialize checkpoint system
        self.checkpoint_enabled = enable_checkpoints
        checkpoint_cfg = checkpoint_config or (
            config.resilience.checkpoint if config else CheckpointConfig()
        )
        self.checkpoint_manager = (
            CheckpointManager(checkpoint_cfg, postgres_storage=self.postgres_storage)
            if enable_checkpoints
            else None
        )

        # Initialize MLflow tracing if enabled and available
        self.mlflow_manager = None
        if (
            config
            and config.observability
            and config.observability.mlflow
            and config.observability.mlflow.enabled
        ):
            if HAS_MLFLOW:
                from roma_dspy.core.observability import MLflowManager

                self.mlflow_manager = MLflowManager(config.observability.mlflow)
                self.mlflow_manager.initialize()
                logger.info("MLflow observability enabled")
            else:
                logger.warning(
                    "MLflow observability requested but dependencies not installed. "
                    "Install with: uv pip install roma-dspy[observability]"
                )

        # Initialize runtime with registry and config
        self.runtime = ModuleRuntime(registry=self.registry, config=config)

        # Initialize observability manager
        from roma_dspy.core.observability import ObservabilityManager

        self.observability = ObservabilityManager(
            postgres_storage=self.postgres_storage,
            mlflow_manager=self.mlflow_manager,
            runtime=self.runtime,
        )

        # Initialize toolkit manager
        self.toolkit_manager = ToolkitManager.get_instance()

        # Configure logging (managed by loguru now)
        # enable_logging controls whether debug logs are shown
        self.enable_logging = enable_logging

        # Note: Loguru configuration is handled in logging_config.py
        # The enable_logging flag is used for checkpoint metadata

        # Configure DSPy cache system
        if config and config.runtime.cache.enabled:
            self._configure_dspy_cache(config.runtime.cache)
        elif not config:
            # Registry mode: use default cache config
            from roma_dspy.config.schemas.base import CacheConfig

            self._configure_dspy_cache(CacheConfig())

        # Thread-safe storage for last_dag (fixes GEPA parallel execution race condition)
        self._local = threading.local()

    @property
    def last_dag(self) -> Optional[TaskDAG]:
        """Get last DAG for current thread (thread-safe)."""
        return getattr(self._local, "last_dag", None)

    @last_dag.setter
    def last_dag(self, value: Optional[TaskDAG]) -> None:
        """Set last DAG for current thread (thread-safe)."""
        self._local.last_dag = value

    def get_total_input_tokens(self) -> int:
        """
        Get total input tokens (prompt tokens) from last execution.

        Aggregates token usage from all task execution histories in the last DAG.
        Returns 0 if no execution has been performed yet.

        Returns:
            Total prompt tokens used across all modules

        Example:
            result = await solver.async_solve("Analyze this data")
            input_tokens = solver.get_total_input_tokens()
            output_tokens = solver.get_total_output_tokens()
            print(f"Used {input_tokens} input + {output_tokens} output tokens")
        """
        total = 0
        if self.last_dag:
            for task in self.last_dag.get_all_tasks(include_subgraphs=True):
                if task.execution_history:
                    for module_result in task.execution_history.values():
                        if module_result.token_metrics:
                            total += module_result.token_metrics.prompt_tokens
        return total

    def get_total_output_tokens(self) -> int:
        """
        Get total output tokens (completion tokens) from last execution.

        Aggregates token usage from all task execution histories in the last DAG.
        Returns 0 if no execution has been performed yet.

        Returns:
            Total completion tokens used across all modules

        Example:
            result = await solver.async_solve("Analyze this data")
            input_tokens = solver.get_total_input_tokens()
            output_tokens = solver.get_total_output_tokens()
            print(f"Used {input_tokens} input + {output_tokens} output tokens")
        """
        total = 0
        if self.last_dag:
            for task in self.last_dag.get_all_tasks(include_subgraphs=True):
                if task.execution_history:
                    for module_result in task.execution_history.values():
                        if module_result.token_metrics:
                            total += module_result.token_metrics.completion_tokens
        return total

    def __getstate__(self):
        """
        Custom pickle serialization to handle unpicklable objects.

        GEPA (and other DSPy optimizers) use multiprocessing which requires
        pickling the solver. We exclude unpicklable objects like threading.local,
        database connections, locks, and singleton managers.

        The config and registry are preserved since they're needed to recreate
        the solver in the new process.
        """
        state = self.__dict__.copy()

        # Remove ALL unpicklable objects
        state.pop("_local", None)  # threading.local
        state.pop("postgres_storage", None)  # Has _ThreadLocalState
        state.pop("checkpoint_manager", None)  # Has _ThreadLocalState
        state.pop("mlflow_manager", None)  # Has module object
        state.pop("observability", None)  # Has _ThreadLocalState
        state.pop("runtime", None)  # Has _thread.lock
        state.pop("toolkit_manager", None)  # Has _thread.lock
        state.pop("registry", None)  # Has _thread.lock

        return state

    def __setstate__(self, state):
        """
        Custom pickle deserialization to restore unpicklable objects.

        After unpickling, we recreate the excluded objects. Since GEPA runs
        in separate processes, each process will have its own instances of
        these objects. This matches the initialization logic in __init__.
        """
        self.__dict__.update(state)

        # Recreate threading.local
        self._local = threading.local()

        # Recreate registry from config
        self.registry = AgentRegistry()
        if self.config:
            factory = AgentFactory()
            self.registry.initialize_from_config(self.config, factory)

        # Recreate PostgresStorage if it was enabled and available
        if (
            self.config
            and self.config.storage
            and self.config.storage.postgres
            and self.config.storage.postgres.enabled
        ):
            if HAS_PERSISTENCE:
                from roma_dspy.core.storage import PostgresStorage

                self.postgres_storage = PostgresStorage(self.config.storage.postgres)
            else:
                logger.warning("PostgreSQL not available - persistence disabled")
                self.postgres_storage = None
        else:
            self.postgres_storage = None

        # Recreate checkpoint system
        checkpoint_cfg = (
            self.config.resilience.checkpoint if self.config else CheckpointConfig()
        )
        self.checkpoint_manager = (
            CheckpointManager(checkpoint_cfg, postgres_storage=self.postgres_storage)
            if self.checkpoint_enabled
            else None
        )

        # Recreate MLflow tracing if enabled and available
        if (
            self.config
            and self.config.observability
            and self.config.observability.mlflow
            and self.config.observability.mlflow.enabled
        ):
            if HAS_MLFLOW:
                from roma_dspy.core.observability import MLflowManager

                self.mlflow_manager = MLflowManager(self.config.observability.mlflow)
                self.mlflow_manager.initialize()
            else:
                logger.warning("MLflow not available - observability disabled")
                self.mlflow_manager = None
        else:
            self.mlflow_manager = None

        # Recreate runtime with registry and config
        self.runtime = ModuleRuntime(registry=self.registry, config=self.config)

        # Recreate observability manager
        self.observability = ObservabilityManager(
            postgres_storage=self.postgres_storage,
            mlflow_manager=self.mlflow_manager,
            runtime=self.runtime,
        )

        # Recreate ToolkitManager (singleton pattern will return same instance in this process)
        self.toolkit_manager = ToolkitManager.get_instance()

    def _configure_dspy_cache(self, cache_config: "CacheConfig") -> None:
        """
        Configure DSPy cache system from ROMA config.

        Args:
            cache_config: CacheConfig instance with cache settings
        """
        import os

        # Expand cache directory (handle ~, env vars)
        cache_dir = os.path.expanduser(cache_config.disk_cache_dir)

        # Ensure directory exists
        os.makedirs(cache_dir, exist_ok=True)

        try:
            dspy.configure_cache(
                enable_disk_cache=cache_config.enable_disk_cache,
                enable_memory_cache=cache_config.enable_memory_cache,
                disk_cache_dir=cache_dir,
                disk_size_limit_bytes=cache_config.disk_size_limit_bytes,
                memory_max_entries=cache_config.memory_max_entries,
            )
            logger.info(
                f"DSPy cache configured: disk={cache_config.enable_disk_cache}, "
                f"memory={cache_config.enable_memory_cache}, dir={cache_dir}"
            )
        except Exception as e:
            logger.warning(f"Failed to configure DSPy cache: {e}")
            # Non-fatal: cache will use defaults

    def _emit_execution_event(
        self,
        event_type: Union[str, ExecutionEventType],
        task_id: Optional[str] = None,
        dag_id: Optional[str] = None,
        event_data: Optional[Dict] = None,
    ) -> None:
        """
        Emit an execution event if event traces are enabled.

        This method checks EventTracesConfig settings before emitting events.
        Events are buffered in ExecutionContext and persisted at execution end.

        Args:
            event_type: Event type (ExecutionEventType enum or string)
            task_id: Optional task identifier
            dag_id: Optional DAG/execution identifier
            event_data: Optional event payload
        """
        # Check if event traces are enabled
        if (
            not self.config
            or not self.config.observability
            or not self.config.observability.event_traces
        ):
            return

        event_config = self.config.observability.event_traces

        if not event_config.enabled:
            return

        # Convert enum to string for filtering
        event_type_str = (
            event_type.value
            if isinstance(event_type, ExecutionEventType)
            else event_type
        )

        # Apply event type filtering
        if (
            event_type_str
            in (
                ExecutionEventType.EXECUTION_START.value,
                ExecutionEventType.EXECUTION_COMPLETE.value,
            )
            and not event_config.track_execution_events
        ):
            return
        if (
            event_type_str
            in (
                ExecutionEventType.ATOMIZE_COMPLETE.value,
                ExecutionEventType.PLAN_COMPLETE.value,
                ExecutionEventType.EXECUTE_COMPLETE.value,
                ExecutionEventType.AGGREGATE_COMPLETE.value,
            )
            and not event_config.track_module_events
        ):
            return
        if (
            event_type_str == ExecutionEventType.EXECUTION_FAILED.value
            and not event_config.track_failures
        ):
            return

        # Apply sampling
        import random

        if (
            event_config.sample_rate < 1.0
            and random.random() > event_config.sample_rate
        ):
            return

        # Get execution context
        ctx = ExecutionContext.get()
        if not ctx:
            return

        # Emit event to context buffer
        ctx.emit_execution_event(
            event_type=event_type_str,
            task_id=task_id,
            dag_id=dag_id,
            event_data=event_data or {},
            priority=0,
        )

    # ==================== Main Entry Points ====================

    def solve(
        self, task: Union[str, TaskNode], dag: Optional[TaskDAG] = None, depth: int = 0
    ) -> TaskNode:
        """
        Synchronously solve a task using recursive decomposition.

        This is a thin synchronous wrapper around async_solve().
        If you're already in an async context, use async_solve() directly.

        Args:
            task: Task goal string or TaskNode
            dag: Optional DAG to track execution
            depth: Current recursion depth

        Returns:
            Completed TaskNode with results
        """
        return asyncio.run(self.async_solve(task, dag, depth))

    async def async_solve(
        self,
        task: Union[str, TaskNode],
        dag: Optional[TaskDAG] = None,
        depth: int = 0,
        approved_plan: Optional[Dict[str, Any]] = None,
    ) -> TaskNode:
        """
        Asynchronously solve a task using recursive decomposition.

        Args:
            task: Task goal string or TaskNode
            dag: Optional DAG to track execution
            depth: Current recursion depth
            approved_plan: Optional user-approved root plan payload

        Returns:
            Completed TaskNode with results
        """
        logger.debug(
            f"Starting async_solve for task: {task if isinstance(task, str) else task.goal}"
        )

        # Initialize task and DAG
        task, dag = self._initialize_task_and_dag(task, dag, depth)

        # Setup observability using ObservabilityManager
        await self.observability.setup_execution(
            task, dag, self.config, depth, execution_mode="recursive"
        )

        # Setup toolkits using ToolkitManager
        await self.toolkit_manager.setup_for_execution(dag, self.config, self.registry)

        if approved_plan:
            task = self._apply_approved_plan(task, dag, approved_plan)

        # Create an early checkpoint so the DAG is visible to the frontend
        # even before warmup (which can take several minutes)
        if self.checkpoint_manager:
            try:
                await self.checkpoint_manager.create_checkpoint(
                    checkpoint_id=None,
                    dag=dag,
                    trigger=CheckpointTrigger.EXECUTION_START,
                    current_depth=depth,
                    max_depth=self.max_depth,
                )
                logger.debug("Created pre-warmup checkpoint for frontend visibility")
            except Exception as e:
                logger.warning(f"Failed to create pre-warmup checkpoint: {e}")

        try:
            # Wrap execution with MLflow tracing
            if self.mlflow_manager and self.mlflow_manager.config.enabled:
                with self.mlflow_manager.trace_execution(
                    execution_id=dag.execution_id,
                    metadata={
                        "max_depth": self.max_depth,
                        "initial_goal": str(task.goal)
                        if isinstance(task, TaskNode)
                        else str(task),
                        "depth": depth,
                    },
                ):
                    # Phase 2: Warmup runs INSIDE MLflow trace so LLM calls are tracked
                    await self._run_warmup_if_enabled(
                        task, approved_plan=approved_plan
                    )

                    result = await self._async_solve_internal(task, dag, depth)

                    # Log final metrics
                    self.mlflow_manager.log_metrics(
                        {
                            "total_tasks": len(dag.get_all_tasks()) if dag else 1,
                            "max_depth_reached": result.depth,
                            "success": 1.0
                            if result.status == TaskStatus.COMPLETED
                            else 0.0,
                        }
                    )

                    # Create final checkpoint before finalization (ensures visualization of completed runs)
                    if self.checkpoint_manager:
                        try:
                            await self.checkpoint_manager.create_checkpoint(
                                checkpoint_id=None,
                                dag=dag,
                                trigger=CheckpointTrigger.EXECUTION_COMPLETE,
                                current_depth=result.depth,
                                max_depth=self.max_depth,
                            )
                            logger.debug("Created final EXECUTION_COMPLETE checkpoint")
                        except Exception as e:
                            logger.warning(f"Failed to create final checkpoint: {e}")

                    # Finalize execution using ObservabilityManager
                    await self.observability.finalize_execution(dag, result)

                    # Auto-save final result to storage (ensures all executions have persisted output)
                    await self._save_final_result(dag, result)

                    return result
            else:
                # Phase 2: Warmup (non-MLflow path)
                await self._run_warmup_if_enabled(task, approved_plan=approved_plan)

                result = await self._async_solve_internal(task, dag, depth)

                # Create final checkpoint before finalization (ensures visualization of completed runs)
                if self.checkpoint_manager:
                    try:
                        await self.checkpoint_manager.create_checkpoint(
                            checkpoint_id=None,
                            dag=dag,
                            trigger=CheckpointTrigger.EXECUTION_COMPLETE,
                            current_depth=result.depth,
                            max_depth=self.max_depth,
                        )
                        logger.debug("Created final EXECUTION_COMPLETE checkpoint")
                    except Exception as e:
                        logger.warning(f"Failed to create final checkpoint: {e}")

                # Finalize execution using ObservabilityManager
                await self.observability.finalize_execution(dag, result)

                # Auto-save final result to storage (ensures all executions have persisted output)
                await self._save_final_result(dag, result)

                return result
        finally:
            # Stop periodic checkpoints if running
            if self.checkpoint_manager:
                await self.checkpoint_manager.stop_periodic_checkpoints()

            # Cleanup toolkits BEFORE persisting metrics
            # (cleanup generates toolkit lifecycle events that need to be persisted)
            await self.toolkit_manager.cleanup_execution(dag.execution_id)

            # Auto-persist metrics (including cleanup events) and reset context
            if hasattr(dag, "_exec_context_token"):
                await ExecutionContext.reset_async(
                    dag._exec_context_token, self.postgres_storage
                )

            logger.debug(f"Cleaned up execution for {dag.execution_id}")

    async def _async_solve_internal(
        self, task: TaskNode, dag: TaskDAG, depth: int
    ) -> TaskNode:
        """Internal async solve implementation (separated for MLflow wrapping)."""
        # Emit execution_start event
        start_time = datetime.now(UTC)
        self._emit_execution_event(
            event_type=ExecutionEventType.EXECUTION_START,
            task_id=task.task_id,
            dag_id=dag.execution_id,
            event_data={
                "goal": task.goal[:200] if len(task.goal) > 200 else task.goal,
                "depth": depth,
                "max_depth": self.max_depth,
            },
        )

        # Create initial checkpoint at execution start (ensures visualization even if interrupted)
        checkpoint_id = None
        if self.checkpoint_manager:
            try:
                checkpoint_id = await self.checkpoint_manager.create_checkpoint(
                    checkpoint_id=None,
                    dag=dag,
                    trigger=CheckpointTrigger.EXECUTION_START,
                    current_depth=depth,
                    max_depth=self.max_depth,
                    solver_config={
                        "max_depth": self.max_depth,
                        "enable_logging": self.enable_logging,
                    },
                )
                logger.debug(f"Created initial checkpoint: {checkpoint_id}")

                # Start periodic checkpoints for long-running executions
                await self.checkpoint_manager.start_periodic_checkpoints(
                    dag, self.max_depth
                )
            except Exception as e:
                logger.warning(f"Failed to create initial checkpoint: {e}")

        try:
            # Execute based on current state
            task = await self._async_execute_state_machine(task, dag, checkpoint_id)

            # Emit execution_complete event
            end_time = datetime.now(UTC)
            duration_ms = (end_time - start_time).total_seconds() * 1000
            self._emit_execution_event(
                event_type=ExecutionEventType.EXECUTION_COMPLETE,
                task_id=task.task_id,
                dag_id=dag.execution_id,
                event_data={
                    "status": task.status.value,
                    "duration_ms": duration_ms,
                    "result_preview": task.result[:200]
                    if task.result and len(task.result) > 200
                    else task.result,
                },
            )

            # Logging is now handled by TreeVisualizer when called by user
            logger.debug(f"Completed async_solve with status: {task.status}")
            return task
        except Exception as e:
            # Emit execution_failed event
            end_time = datetime.now(UTC)
            duration_ms = (end_time - start_time).total_seconds() * 1000
            self._emit_execution_event(
                event_type=ExecutionEventType.EXECUTION_FAILED,
                task_id=task.task_id,
                dag_id=dag.execution_id,
                event_data={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "duration_ms": duration_ms,
                    "depth": task.depth,
                },
            )

            # Enhance error with task hierarchy context
            error_msg = f"Task '{task.task_id}' failed at depth {task.depth}: {str(e)}"
            if task.goal:
                error_msg += f"\nTask goal: {task.goal[:100]}..."

            # Add checkpoint recovery info
            if checkpoint_id and self.checkpoint_manager:
                error_msg += f"\nCheckpoint {checkpoint_id} available for recovery"

            logger.error(error_msg)

            # Re-raise with enhanced context
            # Use RuntimeError instead of trying to reconstruct original exception type
            # (some exception types have custom constructors that don't accept simple string messages)
            enhanced_error = RuntimeError(error_msg)
            enhanced_error.__cause__ = e
            raise enhanced_error from e

    async def async_event_solve(
        self,
        task: Union[str, TaskNode],
        dag: Optional[TaskDAG] = None,
        depth: int = 0,
        priority_fn: Optional[Callable[[TaskNode], int]] = None,
        concurrency: Optional[int] = None,
    ) -> TaskNode:
        """Run the event-driven scheduler to solve the task graph."""
        # Use config's max_concurrency if not explicitly provided
        effective_concurrency = (
            concurrency
            if concurrency is not None
            else self.config.runtime.max_concurrency
        )

        logger.debug(
            "Starting async_event_solve for task: %s (concurrency=%d)",
            task if isinstance(task, str) else task.goal,
            effective_concurrency,
        )

        # Initialize task and DAG
        task, dag = self._initialize_task_and_dag(task, dag, depth)

        # Setup observability using ObservabilityManager
        await self.observability.setup_execution(
            task, dag, self.config, depth, execution_mode="event_driven"
        )

        # Setup toolkits using ToolkitManager
        await self.toolkit_manager.setup_for_execution(dag, self.config, self.registry)

        try:
            # Pass checkpoint manager and postgres_storage to event controller if available
            controller = EventLoopController(
                dag,
                self.runtime,
                priority_fn=priority_fn,
                checkpoint_manager=self.checkpoint_manager,
                postgres_storage=self.postgres_storage,
            )

            # Apply any pending state restorations from previous recovery operations
            if self.checkpoint_manager:
                await controller.apply_pending_restorations()

            # Wrap execution with MLflow tracing
            if self.mlflow_manager and self.mlflow_manager.config.enabled:
                with self.mlflow_manager.trace_execution(
                    execution_id=dag.execution_id,
                    metadata={
                        "max_depth": self.max_depth,
                        "initial_goal": str(task.goal)
                        if isinstance(task, TaskNode)
                        else str(task),
                        "depth": depth,
                        "execution_mode": "event_driven",
                        "concurrency": effective_concurrency,
                    },
                ):
                    await controller.run(max_concurrency=effective_concurrency)

                    updated_task = dag.get_node(task.task_id)

                    # Log final metrics
                    self.mlflow_manager.log_metrics(
                        {
                            "total_tasks": len(dag.get_all_tasks()),
                            "max_depth_reached": updated_task.depth,
                            "success": 1.0
                            if updated_task.status == TaskStatus.COMPLETED
                            else 0.0,
                            "concurrency": effective_concurrency,
                        }
                    )

                    # Create final checkpoint before finalization (ensures visualization of completed runs)
                    if self.checkpoint_manager:
                        try:
                            await self.checkpoint_manager.create_checkpoint(
                                checkpoint_id=None,
                                dag=dag,
                                trigger=CheckpointTrigger.EXECUTION_COMPLETE,
                                current_depth=updated_task.depth,
                                max_depth=self.max_depth,
                            )
                            logger.debug("Created final EXECUTION_COMPLETE checkpoint")
                        except Exception as e:
                            logger.warning(f"Failed to create final checkpoint: {e}")

                    # Finalize execution using ObservabilityManager
                    await self.observability.finalize_execution(dag, updated_task)

                    logger.debug(
                        "Completed async_event_solve with status: %s",
                        updated_task.status,
                    )
                    return updated_task
            else:
                await controller.run(max_concurrency=effective_concurrency)

                updated_task = dag.get_node(task.task_id)

                # Create final checkpoint before finalization (ensures visualization of completed runs)
                if self.checkpoint_manager:
                    try:
                        await self.checkpoint_manager.create_checkpoint(
                            checkpoint_id=None,
                            dag=dag,
                            trigger=CheckpointTrigger.EXECUTION_COMPLETE,
                            current_depth=updated_task.depth,
                            max_depth=self.max_depth,
                        )
                        logger.debug("Created final EXECUTION_COMPLETE checkpoint")
                    except Exception as e:
                        logger.warning(f"Failed to create final checkpoint: {e}")

                # Finalize execution using ObservabilityManager
                await self.observability.finalize_execution(dag, updated_task)

                logger.debug(
                    "Completed async_event_solve with status: %s", updated_task.status
                )
                return updated_task
        finally:
            # Stop periodic checkpoints if running
            if self.checkpoint_manager:
                await self.checkpoint_manager.stop_periodic_checkpoints()

            # Critical cleanup: prevents memory leaks and stale context
            # Cleanup toolkits BEFORE persisting metrics (cleanup generates events)
            await self.toolkit_manager.cleanup_execution(dag.execution_id)

            # Auto-persist metrics and reset execution context
            if hasattr(dag, "_exec_context_token"):
                await ExecutionContext.reset_async(
                    dag._exec_context_token, self.postgres_storage
                )

            logger.debug(f"Cleaned up execution for {dag.execution_id}")

    def event_solve(
        self,
        task: Union[str, TaskNode],
        dag: Optional[TaskDAG] = None,
        depth: int = 0,
        priority_fn: Optional[Callable[[TaskNode], int]] = None,
        concurrency: Optional[int] = None,
    ) -> TaskNode:
        """Synchronous wrapper around the event-driven scheduler.

        Thread-safe: Works correctly when called from DSPy's ParallelExecutor worker threads.
        Ensures proper cleanup of database connections before event loop closes.
        """
        # Use config's max_concurrency if not explicitly provided
        effective_concurrency = (
            concurrency
            if concurrency is not None
            else self.config.runtime.max_concurrency
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            raise RuntimeError(
                "event_solve() cannot be called from a running event loop"
            )

        # Wrap execution with proper cleanup for worker threads
        async def _run_with_cleanup():
            try:
                result = await self.async_event_solve(
                    task=task,
                    dag=dag,
                    depth=depth,
                    priority_fn=priority_fn,
                    concurrency=effective_concurrency,
                )
                return result
            finally:
                # Critical: Shutdown PostgresStorage before event loop closes
                # This prevents "RuntimeError: Event loop is closed" when cleaning up
                # database connections in DSPy's worker threads
                if self.postgres_storage and self.postgres_storage._local.initialized:
                    try:
                        await self.postgres_storage.shutdown()
                        logger.debug(
                            "PostgresStorage shutdown complete before event loop closure"
                        )
                    except Exception as e:
                        # Non-fatal: log but don't fail the task
                        logger.debug(f"PostgresStorage shutdown error (non-fatal): {e}")

        return asyncio.run(_run_with_cleanup())

    # ==================== Phase 2: Warm-up ====================

    async def async_draft_research_plan(
        self, task: Union[str, TaskNode], dag: Optional[TaskDAG] = None, depth: int = 0
    ) -> Dict[str, Any]:
        """Generate a root research plan draft without executing the full pipeline."""
        logger.debug(
            f"Starting async_draft_research_plan for task: "
            f"{task if isinstance(task, str) else task.goal}"
        )

        task, dag = self._initialize_task_and_dag(task, dag, depth)
        await self.toolkit_manager.setup_for_execution(dag, self.config, self.registry)

        try:
            if task.status == TaskStatus.PENDING:
                task = await self.runtime.atomize_async(task, dag)

            if task.status == TaskStatus.ATOMIZING:
                task = self.runtime.transition_from_atomizing(task, dag)

            if task.status == TaskStatus.EXECUTING:
                return {
                    "subtasks": [
                        SubTask(
                            goal=task.goal,
                            task_type=task.task_type,
                            dependencies=[],
                        )
                    ],
                    "dependencies_graph": None,
                    "node_type": task.node_type.value if task.node_type else None,
                }

            task = await self.runtime.plan_async(task, dag)
            planner_result = task.execution_history.get("planner")
            output = planner_result.output if planner_result else {}
            subtasks = [
                SubTask.model_validate(subtask)
                for subtask in output.get("subtasks", [])
            ]

            return {
                "subtasks": subtasks,
                "dependencies_graph": output.get("dependencies"),
                "node_type": task.node_type.value if task.node_type else None,
            }
        finally:
            await self.toolkit_manager.cleanup_execution(dag.execution_id)
            if hasattr(dag, "_exec_context_token"):
                await ExecutionContext.reset_async(
                    dag._exec_context_token, self.postgres_storage
                )

    async def _run_warmup_if_enabled(
        self, task: TaskNode, approved_plan: Optional[Dict[str, Any]] = None
    ) -> None:
        """Run pre-execution warm-up if enabled in config.

        The warm-up phase generates optimized directives and dynamic demos
        that are injected into the runtime before the real execution begins.
        On failure, execution continues normally (zero degradation).
        """
        warmup_config = getattr(self.config, "warmup", None) if self.config else None
        if not warmup_config or not getattr(warmup_config, "enabled", False):
            return

        logger.info("[WARMUP] Starting pre-execution warm-up phase")
        try:
            from roma_dspy.core.engine.warmup import WarmupOrchestrator

            if self.mlflow_manager:
                self.runtime.context_store.set_mlflow_manager(self.mlflow_manager)

            logs_dir = None
            if self.runtime.context_manager and hasattr(self.runtime.context_manager, "file_storage"):
                fs = self.runtime.context_manager.file_storage
                if fs:
                    logs_dir = str(fs.get_logs_path())
            if logs_dir is None:
                logger.warning(
                    "[WARMUP] logs_dir could not be resolved from context_manager "
                    f"(context_manager={self.runtime.context_manager!r}); "
                    "MIPE evolution log will be disabled."
                )
            else:
                logger.info(f"[WARMUP] MIPE evolution log will be written to: {logs_dir}")

            orchestrator = WarmupOrchestrator(
                config=warmup_config,
                roma_config=self.config,
                mlflow_manager=self.mlflow_manager,
                logs_dir=logs_dir,
            )
            warmup_goal = (
                approved_plan.get("refined_goal")
                if approved_plan and approved_plan.get("refined_goal")
                else task.goal
            )
            warmup_result = await orchestrator.run(warmup_goal)

            if warmup_result.demos:
                self.runtime.set_warmup_demos(warmup_result.demos)
                logger.info(
                    f"[WARMUP] Injected dynamic demos: "
                    f"{list(warmup_result.demos.keys())}"
                )

            if getattr(warmup_result, "plan_blueprint", None) is not None:
                blueprint = warmup_result.plan_blueprint
                self.runtime.context_store.store_plan_blueprint_context(
                    blueprint.to_planner_context()
                )
                self.runtime.context_store.store_plan_blueprint(blueprint)
                logger.info(
                    f"[WARMUP] Stored PlanBlueprint in context_store "
                    f"(fitness={blueprint.fitness_score:.1f}, "
                    f"generation={blueprint.generation})"
                )

            logger.info(
                f"[WARMUP] Completed in {warmup_result.iteration_count} iteration(s), "
                f"success={warmup_result.success}"
            )

        except Exception as e:
            logger.warning(f"[WARMUP] Warm-up failed, continuing without: {e}")

    # ==================== Initialization ====================

    def _apply_approved_plan(
        self, task: TaskNode, dag: TaskDAG, approved_plan: Dict[str, Any]
    ) -> TaskNode:
        """Inject a user-approved root plan into the DAG before execution."""
        refined_goal = approved_plan.get("refined_goal")
        if refined_goal and refined_goal != task.goal:
            task = task.model_copy(update={"goal": refined_goal})
            dag.update_node(task)

        return self.runtime.apply_approved_plan(task, dag, approved_plan)

    def _initialize_task_and_dag(
        self, task: Union[str, TaskNode], dag: Optional[TaskDAG], depth: int
    ) -> Tuple[TaskNode, TaskDAG]:
        """Initialize task node and DAG for execution."""
        # Track whether we're creating a new DAG
        newly_created_dag = dag is None

        # Create DAG if not provided
        if dag is None:
            dag = TaskDAG()

        self.last_dag = dag  # Store for visualization

        # Create new ContextManager for each new DAG to ensure execution isolation
        # Each DAG has unique execution_id and needs isolated FileStorage
        if newly_created_dag or self.runtime.context_manager is None:
            # Validate config availability
            if self.config is None:
                raise ValueError(
                    "Config is required for FileStorage creation. "
                    "Provide config when creating RecursiveSolver."
                )

            # Create FileStorage for this execution
            file_storage = FileStorage(
                config=self.config.storage, execution_id=dag.execution_id
            )

            # Set ExecutionContext for toolkit lifecycle management
            # Store token in DAG for later cleanup
            dag._exec_context_token = ExecutionContext.set(
                execution_id=dag.execution_id, file_storage=file_storage
            )

            # Extract overall objective from task
            overall_objective = task if isinstance(task, str) else task.goal

            # Create and inject ContextManager into runtime
            context_manager = ContextManager(file_storage, overall_objective)
            self.runtime.context_manager = context_manager

            logger.debug(
                f"Initialized context system with execution_id: {dag.execution_id}"
            )

        # Convert string to TaskNode if needed
        if isinstance(task, str):
            task = TaskNode(
                goal=task,
                task_type=TaskType.WRITE,
                depth=depth,
                max_depth=self.max_depth,
                execution_id=dag.execution_id,
            )

        # Add to DAG if not already present
        if task.task_id not in dag.graph:
            dag.add_node(task)

        return task, dag

    # ==================== State Machine Execution ====================

    async def _async_execute_state_machine(
        self, task: TaskNode, dag: TaskDAG, checkpoint_id: Optional[str] = None
    ) -> TaskNode:
        """Execute asynchronous state machine for task processing."""
        # Check for forced execution at max depth
        if task.should_force_execute():
            logger.debug(f"Force executing task at max depth: {task.depth}")
            return await self.runtime.force_execute_async(task, dag)

        # Blueprint execution mode: if MIPE produced prebuilt subtasks for
        # this node, skip atomize/plan and load them directly.
        if task.status == TaskStatus.PENDING:
            loaded = await self._try_load_prebuilt_subtasks(task, dag)
            if loaded:
                task = loaded
            elif (task.metadata or {}).get("is_blueprint_leaf"):
                logger.info(
                    f"[BLUEPRINT] {task.task_type.value} leaf "
                    f"{task.task_id[:8]}... — executing directly, "
                    f"skipping atomize/plan"
                )
                task = task.set_node_type(NodeType.EXECUTE)
                task = task.transition_to(TaskStatus.EXECUTING)
                dag.update_node(task)
            else:
                logger.debug(f"Async atomizing task: {task.goal[:50]}...")
                task = await self.runtime.atomize_async(task, dag)

        if task.status == TaskStatus.ATOMIZING:
            task = self.runtime.transition_from_atomizing(task, dag)

        if task.status == TaskStatus.PLANNING:
            logger.debug(f"Async planning task: {task.goal[:50]}...")
            task = await self.runtime.plan_async(task, dag)

            # Create checkpoint after planning (expensive operation completed)
            if self.checkpoint_manager and task.status == TaskStatus.PLAN_DONE:
                try:
                    await self.checkpoint_manager.create_checkpoint(
                        checkpoint_id=f"{checkpoint_id}_after_plan"
                        if checkpoint_id
                        else None,
                        dag=dag,
                        trigger=CheckpointTrigger.AFTER_PLANNING,
                        current_depth=task.depth,
                        max_depth=self.max_depth,
                    )
                except Exception as e:
                    logger.warning(f"Failed to create post-planning checkpoint: {e}")

        if task.status == TaskStatus.EXECUTING:
            logger.debug(f"Async executing task: {task.goal[:50]}...")
            task = await self.runtime.execute_async(task, dag)
        elif task.status == TaskStatus.PLAN_DONE:
            # Create checkpoint before aggregation (preserve completed subtasks)
            if self.checkpoint_manager:
                try:
                    await self.checkpoint_manager.create_checkpoint(
                        checkpoint_id=f"{checkpoint_id}_before_agg"
                        if checkpoint_id
                        else None,
                        dag=dag,
                        trigger=CheckpointTrigger.BEFORE_AGGREGATION,
                        current_depth=task.depth,
                        max_depth=self.max_depth,
                    )
                except Exception as e:
                    logger.warning(f"Failed to create pre-aggregation checkpoint: {e}")

            # Pass _async_solve_internal to avoid nested observability setup
            # (observability is already set up at the top level)
            task = await self.runtime.process_subgraph_async(
                task, dag, self._async_solve_internal
            )

        return task

    # ==================== Blueprint Execution Mode ====================

    async def _try_load_prebuilt_subtasks(
        self, task: TaskNode, dag: TaskDAG
    ) -> Optional[TaskNode]:
        """Check if the MIPE blueprint has pre-planned subtasks for this node.

        Two lookup modes:
        1. Root task (depth=0, no parent_id, no blueprint_node_id):
           Looks for the special ``"__root__"`` key that holds the MIPE-optimised
           depth-0 subtask list, bypassing the root-level Planner call entirely.
        2. Non-root task (has blueprint_node_id in metadata):
           Looks for the parent node's key to load depth-1+ prebuilt subtasks.

        If a match is found, the subtasks are loaded directly into the DAG
        (skipping atomize and plan_async) and the task transitions to PLAN_DONE.

        Returns the updated TaskNode if prebuilt subtasks were loaded,
        or None if no match (caller should fall back to normal flow).
        """
        blueprint = self.runtime.context_store.get_plan_blueprint()
        if blueprint is None or not blueprint.prebuilt_subtasks:
            return None

        bp_node_id = (task.metadata or {}).get("blueprint_node_id")
        is_root = bp_node_id is None and task.depth == 0 and task.parent_id is None

        if is_root:
            prebuilt_dicts = blueprint.prebuilt_subtasks.get("__root__")
            if not prebuilt_dicts:
                return None
            lookup_label = "root"
            # Sync report_policy from blueprint so downstream aggregation works
            if blueprint.report_policy:
                self.runtime.context_store.store_report_policy(blueprint.report_policy)
                logger.debug(
                    "[BLUEPRINT] Stored report_policy from blueprint for root bypass"
                )
        else:
            if bp_node_id is None:
                return None
            prebuilt_dicts = blueprint.prebuilt_subtasks.get(bp_node_id)
            if not prebuilt_dicts:
                return None
            lookup_label = f"node {bp_node_id}"

        logger.info(
            f"[BLUEPRINT] Loading {len(prebuilt_dicts)} prebuilt subtasks "
            f"for {lookup_label} (task={task.task_id[:8]}...) — skipping Planner"
        )

        # Build TaskNode list; carry _flat_index as blueprint_node_id so that
        # non-leaf children can later be matched by a recursive lookup.
        subtask_nodes: List[TaskNode] = []
        local_to_flat: Dict[str, str] = {}  # local_idx → flat_index string
        for idx, st_dict in enumerate(prebuilt_dicts):
            flat_index = st_dict.get("_flat_index", str(idx))
            local_to_flat[str(idx)] = flat_index
            st = SubTask.model_validate(st_dict)
            is_leaf = st.is_leaf or not bool(st.children_ids)
            # Defensive: if this subtask will run at max_depth, force it to be a
            # leaf so the runtime's force_execute_async path does not silently skip
            # subgraph creation for its children (which would never be instantiated).
            if not is_leaf and (task.depth + 1 >= task.max_depth):
                logger.warning(
                    f"[BLUEPRINT] Node {flat_index} "
                    f"({st.task_type.value if hasattr(st.task_type, 'value') else st.task_type}) "
                    f"has children_ids but will run at depth "
                    f"{task.depth + 1} = max_depth ({task.max_depth}). "
                    f"Forcing is_leaf=True to prevent phantom subgraph."
                )
                is_leaf = True
            node_meta: dict = {
                "blueprint_node_id": flat_index,
                "is_blueprint_leaf": is_leaf,
            }
            mandate_checklist = getattr(st, "mandate_checklist", None)
            if mandate_checklist:
                node_meta["mandate_checklist"] = mandate_checklist
            subtask_node = TaskNode(
                goal=st.goal,
                task_type=st.task_type,
                parent_id=task.task_id,
                context_input=st.context_input,
                dynamic_prompt=st.dynamic_prompt,
                depth=task.depth + 1,
                max_depth=task.max_depth,
                execution_id=task.execution_id or dag.execution_id,
                metadata=node_meta,
            )
            subtask_nodes.append(subtask_node)

        # Map local index → task_id for dependency resolution.
        # For root ("__root__") entries the flat and local indices are
        # identical.  For depth-1+ children, SubTask.dependencies may
        # reference *flat* indices (e.g. "21") while the local map only
        # has keys "0"–"N".  We build a flat→local reverse lookup so
        # that both addressing modes resolve correctly.
        index_to_task_id: Dict[str, str] = {
            str(idx): node.task_id for idx, node in enumerate(subtask_nodes)
        }
        flat_to_local: Dict[str, str] = {
            flat_idx: local_idx
            for local_idx, flat_idx in local_to_flat.items()
        }

        # For ROOT prebuilt subtasks, dependencies may reference depth-1
        # child flat indices (e.g. THINK "3" depends on ["9","14","20"]
        # which are children of root tasks "0","1","2").  These children
        # live in *nested* subgraphs, not in the root subgraph.  We map
        # each child's flat index to its depth-0 parent's local index so
        # the dependency becomes "wait for the parent task to complete"
        # (which implicitly waits for all its children).
        child_flat_to_parent_local: Dict[str, str] = {}
        if is_root and blueprint and blueprint.prebuilt_subtasks:
            for local_idx, flat_idx in local_to_flat.items():
                children = blueprint.prebuilt_subtasks.get(flat_idx, [])
                for child_dict in children:
                    child_flat = child_dict.get("_flat_index")
                    if child_flat:
                        child_flat_to_parent_local[child_flat] = local_idx

        task_id_deps: Optional[Dict[str, List[str]]] = None
        has_deps = any(
            SubTask.model_validate(d).dependencies for d in prebuilt_dicts
        )
        if has_deps:
            task_id_deps = {}
            for idx, st_dict in enumerate(prebuilt_dicts):
                st = SubTask.model_validate(st_dict)
                if st.dependencies:
                    own_task_id = index_to_task_id[str(idx)]
                    dep_task_ids = []
                    seen_dep_ids: set = set()
                    for dep_idx in st.dependencies:
                        dep_task_id = index_to_task_id.get(dep_idx)
                        if dep_task_id is None:
                            # dep_idx may be a flat index — translate to local
                            local_idx = flat_to_local.get(dep_idx)
                            if local_idx is not None:
                                dep_task_id = index_to_task_id.get(local_idx)
                        if dep_task_id is None and child_flat_to_parent_local:
                            # dep_idx is a child in a nested subgraph —
                            # resolve to its depth-0 parent task
                            parent_local = child_flat_to_parent_local.get(dep_idx)
                            if parent_local is not None:
                                dep_task_id = index_to_task_id.get(parent_local)
                        if dep_task_id is None:
                            logger.warning(
                                f"[BLUEPRINT] Cannot resolve dependency "
                                f"{dep_idx} for subtask {idx} "
                                f"({lookup_label}), skipping"
                            )
                            continue
                        if dep_task_id == own_task_id:
                            logger.warning(
                                f"[BLUEPRINT] Subtask {idx} has self-dependency "
                                f"(dep_idx={dep_idx}), skipping"
                            )
                            continue
                        if dep_task_id not in seen_dep_ids:
                            dep_task_ids.append(dep_task_id)
                            seen_dep_ids.add(dep_task_id)
                    if dep_task_ids:
                        task_id_deps[own_task_id] = dep_task_ids
                    else:
                        # This subtask declared dependencies but none could be
                        # resolved (e.g. a new node created by T-island evolution
                        # that references a flat-index outside the current subgraph
                        # closure).  Mark with an empty list so that
                        # _repair_empty_deps_for_leaves can distinguish this case
                        # from a node that genuinely has no declared dependencies
                        # and should not have spurious deps inferred.
                        task_id_deps[own_task_id] = []

        # --- Defensive: repair WRITE/THINK leaf tasks with no dependencies ---
        # MIPE evolution can produce WRITE/THINK tasks with empty or
        # self-referencing dependencies (topology split_merge bug).
        # Without this guard they execute immediately, bypassing the
        # RETRIEVE -> THINK -> WRITE ordering.
        if task_id_deps is None:
            task_id_deps = {}
        task_id_deps = self._repair_empty_deps_for_leaves(
            prebuilt_dicts, subtask_nodes, index_to_task_id,
            task_id_deps, lookup_label,
        )

        dag.create_subgraph(task.task_id, subtask_nodes, task_id_deps)
        updated_task = dag.get_node(task.task_id)
        subgraph_id = updated_task.subgraph_id

        if subgraph_id:
            for idx, subtask_node in enumerate(subtask_nodes):
                self.runtime.context_store.register_index_mapping(
                    subgraph_id, idx, subtask_node.task_id
                )

        updated_metrics = task.metrics.model_copy()
        updated_metrics.subtasks_created = len(subtask_nodes)
        task = task.model_copy(
            update={
                "metrics": updated_metrics,
                "subgraph_id": subgraph_id,
                "status": TaskStatus.PLAN_DONE,
            }
        )
        dag.update_node(task)
        return task

    @staticmethod
    def _repair_empty_deps_for_leaves(
        prebuilt_dicts: List[dict],
        subtask_nodes: List["TaskNode"],
        index_to_task_id: Dict[str, str],
        task_id_deps: Dict[str, List[str]],
        lookup_label: str,
    ) -> Dict[str, List[str]]:
        """Infer missing dependencies for WRITE/THINK leaf tasks.

        MIPE evolution (especially ``split_merge``) can produce WRITE or
        THINK leaf tasks whose ``dependencies`` list is empty or contained
        only a self-reference (which gets stripped earlier).  These tasks
        would become immediately ready, executing before the RETRIEVE and
        THINK tasks they logically depend on.

        Heuristic:
          - WRITE leaf with no deps -> depend on all sibling THINK tasks
            (fallback: all sibling RETRIEVE tasks).
          - THINK leaf with no deps -> depend on all sibling RETRIEVE tasks.

        Important: ``task_id_deps`` uses two distinct states for a node:
          - key absent       : node never declared any dependencies
          - key present, []  : node declared deps but all failed to resolve
                               (e.g. cross-subgraph flat-index from T-island
                               evolution).  Do NOT infer deps in this case —
                               the original blueprint intent was non-empty and
                               spurious edges would distort execution order.
        """
        retrieve_tids: List[str] = []
        think_tids: List[str] = []
        for idx, st_dict in enumerate(prebuilt_dicts):
            tt = st_dict.get("task_type", "")
            tt_upper = tt.upper() if isinstance(tt, str) else str(tt).upper()
            tid = index_to_task_id[str(idx)]
            if tt_upper == "RETRIEVE":
                retrieve_tids.append(tid)
            elif tt_upper == "THINK":
                think_tids.append(tid)

        for idx, st_dict in enumerate(prebuilt_dicts):
            is_leaf = st_dict.get("is_leaf", True) or not st_dict.get("children_ids")
            if not is_leaf:
                continue

            tt = st_dict.get("task_type", "")
            tt_upper = tt.upper() if isinstance(tt, str) else str(tt).upper()
            own_tid = index_to_task_id[str(idx)]

            # Distinguish three states:
            #   1. key absent, original deps empty  → genuinely no deps → infer
            #   2. key absent, original deps non-empty → should not happen
            #      (loop above always sets key when deps non-empty)
            #   3. key present with []              → declared but unresolvable → skip
            #   4. key present with non-empty list  → already resolved → skip
            has_resolved_deps = bool(task_id_deps.get(own_tid))
            had_declared_deps = own_tid in task_id_deps  # key=[] sentinel

            if has_resolved_deps or had_declared_deps:
                # Either already wired or blueprint had intent — do not override.
                if had_declared_deps and not has_resolved_deps:
                    logger.warning(
                        f"[BLUEPRINT] {tt_upper} leaf {idx} ({lookup_label}) "
                        f"declared dependencies that could not be resolved "
                        f"(cross-subgraph flat-index). Skipping dep inference "
                        f"to avoid spurious edges."
                    )
                continue

            # Node has genuinely no declared dependencies — apply heuristic.
            if tt_upper == "WRITE":
                inferred = [t for t in think_tids if t != own_tid]
                if not inferred:
                    inferred = [t for t in retrieve_tids if t != own_tid]
                if inferred:
                    task_id_deps[own_tid] = inferred
                    logger.warning(
                        f"[BLUEPRINT] WRITE leaf {idx} ({lookup_label}) has "
                        f"no dependencies — inferred {len(inferred)} deps "
                        f"from sibling THINK/RETRIEVE tasks to prevent "
                        f"premature execution"
                    )

            elif tt_upper == "THINK" and retrieve_tids:
                inferred = [t for t in retrieve_tids if t != own_tid]
                if inferred:
                    task_id_deps[own_tid] = inferred
                    logger.warning(
                        f"[BLUEPRINT] THINK leaf {idx} ({lookup_label}) has "
                        f"no dependencies — inferred {len(inferred)} deps "
                        f"from sibling RETRIEVE tasks to prevent "
                        f"premature execution"
                    )

        return task_id_deps

    # ==================== Unified Checkpoint Coordination ====================

    async def create_unified_checkpoint(
        self,
        trigger: CheckpointTrigger,
        dag: Optional[TaskDAG] = None,
        task_context: Optional[TaskNode] = None,
    ) -> Optional[str]:
        """Create a unified checkpoint capturing all system components."""
        if not self.checkpoint_manager:
            logger.debug(
                "Checkpoint manager not available, skipping unified checkpoint"
            )
            return None

        try:
            logger.info(f"Creating unified system checkpoint for trigger: {trigger}")

            # Use provided DAG or create a minimal one
            target_dag = dag or TaskDAG("unified_checkpoint")
            if task_context and dag is None:
                target_dag.add_node(task_context)

            # Collect comprehensive system state
            solver_config = {
                "max_depth": self.max_depth,
                "enable_logging": self.enable_logging,
                "registry_stats": self.registry.get_stats(),
            }

            # Collect runtime state if available
            module_states = {}
            if hasattr(self, "runtime") and self.runtime:
                module_states["runtime"] = {
                    "total_operations": getattr(self.runtime, "_operation_count", 0),
                    "last_activity": "unified_checkpoint_creation",
                }

            # Create the unified checkpoint
            checkpoint_id = await self.checkpoint_manager.create_checkpoint(
                checkpoint_id=None,  # Let manager generate ID
                dag=target_dag,
                trigger=trigger,
                current_depth=task_context.depth if task_context else 0,
                max_depth=self.max_depth,
                solver_config=solver_config,
                module_states=module_states,
            )

            logger.info(f"Created unified checkpoint: {checkpoint_id}")
            return checkpoint_id

        except Exception as e:
            logger.error(f"Failed to create unified checkpoint: {e}")
            return None

    async def restore_from_unified_checkpoint(
        self, checkpoint_id: str, strategy: Optional[str] = None
    ) -> bool:
        """Restore system state from a unified checkpoint."""
        if not self.checkpoint_manager:
            logger.error("Checkpoint manager not available for restoration")
            return False

        try:
            logger.info(f"Restoring system from unified checkpoint: {checkpoint_id}")

            # Load checkpoint
            checkpoint_data = await self.checkpoint_manager.load_checkpoint(
                checkpoint_id
            )

            # Create recovery plan
            from roma_dspy.types.checkpoint_types import RecoveryStrategy

            recovery_strategy = RecoveryStrategy.PARTIAL
            if strategy == "full":
                recovery_strategy = RecoveryStrategy.FULL
            elif strategy == "selective":
                recovery_strategy = RecoveryStrategy.SELECTIVE

            recovery_plan = await self.checkpoint_manager.create_recovery_plan(
                checkpoint_data, strategy=recovery_strategy
            )

            # Enable module state restoration
            recovery_plan.restore_module_states = True

            # Create a temporary DAG for restoration
            temp_dag = TaskDAG("restoration_target")

            # Apply recovery plan
            restored_dag = await self.checkpoint_manager.apply_recovery_plan(
                recovery_plan, temp_dag
            )

            # Wire restored DAG back into solver for subsequent operations
            self.last_dag = restored_dag

            # Restore solver configuration if available
            if checkpoint_data.solver_config:
                solver_config = checkpoint_data.solver_config
                self.max_depth = solver_config.get("max_depth", self.max_depth)

            logger.info(
                f"Successfully restored from unified checkpoint: {checkpoint_id}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to restore from unified checkpoint {checkpoint_id}: {e}"
            )
            return False

    async def list_unified_checkpoints_async(self) -> list:
        """List all available unified checkpoints (async version)."""
        if not self.checkpoint_manager:
            return []

        try:
            return await self.checkpoint_manager.list_checkpoints()
        except Exception as e:
            logger.error(f"Failed to list checkpoints: {e}")
            return []

    def list_unified_checkpoints(self) -> list:
        """List all available unified checkpoints (sync version)."""
        try:
            import asyncio

            # Try to use existing event loop or create new one
            try:
                loop = asyncio.get_running_loop()
                # We're in an async context, can't use run_until_complete
                logger.warning(
                    "list_unified_checkpoints called from async context. Use list_unified_checkpoints_async instead."
                )
                return []
            except RuntimeError:
                # No running loop, safe to create one
                return asyncio.run(self.list_unified_checkpoints_async())
        except Exception as e:
            logger.error(f"Failed to list checkpoints: {e}")
            return []

    async def auto_recover(self, max_attempts: int = 3) -> bool:
        """Simple recovery mechanism that attempts to restore from the latest checkpoint."""
        if not self.checkpoint_manager:
            logger.error("Cannot auto-recover: checkpoint manager not available")
            return False

        try:
            logger.info("Starting auto-recovery process...")

            # Get list of available checkpoints
            checkpoints = await self.checkpoint_manager.list_checkpoints()
            if not checkpoints:
                logger.warning("No checkpoints available for recovery")
                return False

            # Sort by creation time (most recent first)
            checkpoints.sort(key=lambda x: x["created_at"], reverse=True)

            # Try to recover from checkpoints, starting with the most recent
            for attempt, checkpoint in enumerate(checkpoints[:max_attempts], 1):
                checkpoint_id = checkpoint["checkpoint_id"]
                logger.info(
                    f"Recovery attempt {attempt}/{max_attempts}: trying checkpoint {checkpoint_id}"
                )

                try:
                    # Validate checkpoint first
                    is_valid = await self.checkpoint_manager.validate_checkpoint(
                        checkpoint_id
                    )
                    if not is_valid:
                        logger.warning(
                            f"Checkpoint {checkpoint_id} is invalid, skipping"
                        )
                        continue

                    # Attempt restoration
                    success = await self.restore_from_unified_checkpoint(
                        checkpoint_id, strategy="partial"
                    )

                    if success:
                        logger.info(
                            f"Successfully recovered from checkpoint {checkpoint_id}"
                        )
                        return True
                    else:
                        logger.warning(
                            f"Failed to restore from checkpoint {checkpoint_id}"
                        )

                except Exception as e:
                    logger.warning(f"Error during recovery attempt {attempt}: {e}")
                    continue

            logger.error(f"Auto-recovery failed after {max_attempts} attempts")
            return False

        except Exception as e:
            logger.error(f"Auto-recovery process failed: {e}")
            return False

    def get_system_health(self) -> dict:
        """Get overall system health status for recovery decisions."""
        health_status = {
            "checkpoint_system": {
                "enabled": self.checkpoint_manager is not None,
                "available": self.checkpoint_manager.config.enabled
                if self.checkpoint_manager
                else False,
            },
            "registry": self.registry.get_stats(),
            "configuration": {
                "max_depth": self.max_depth,
                "logging_enabled": self.enable_logging,
            },
        }

        # Add checkpoint storage stats if available (without async issues)
        if self.checkpoint_manager:
            try:
                import asyncio

                # Try to use existing event loop or create new one
                try:
                    loop = asyncio.get_running_loop()
                    # We're in an async context, skip storage stats to avoid issues
                    health_status["checkpoint_storage"] = {
                        "note": "Stats unavailable from async context. Use get_system_health_async()"
                    }
                except RuntimeError:
                    # No running loop, safe to create one
                    storage_stats = asyncio.run(
                        self.checkpoint_manager.get_storage_stats()
                    )
                    health_status["checkpoint_storage"] = storage_stats
            except Exception as e:
                health_status["checkpoint_storage"] = {"error": str(e)}

        return health_status

    async def get_system_health_async(self) -> dict:
        """Get overall system health status for recovery decisions (async version)."""
        health_status = {
            "checkpoint_system": {
                "enabled": self.checkpoint_manager is not None,
                "available": self.checkpoint_manager.config.enabled
                if self.checkpoint_manager
                else False,
            },
            "registry": self.registry.get_stats(),
            "configuration": {
                "max_depth": self.max_depth,
                "logging_enabled": self.enable_logging,
            },
        }

        # Add checkpoint storage stats if available
        if self.checkpoint_manager:
            try:
                storage_stats = await self.checkpoint_manager.get_storage_stats()
                health_status["checkpoint_storage"] = storage_stats
            except Exception as e:
                health_status["checkpoint_storage"] = {"error": str(e)}

        return health_status

    _URL_RE = re.compile(r"(https?://[^\s\]\)<>]+|ragflow://[^\s\]\)<>]+)")
    _INLINE_CITATION_RE = re.compile(
        r"\[Source:\s*(https?://[^\]\s]+|ragflow://[^\]\s]+)\]"
    )
    _NUMBERED_CITATION_RE = re.compile(r"\[(\d+)\]")
    _SOURCES_SECTION_RE = re.compile(
        r"\n---\n\n###\s*Sources\s*\n\n([\s\S]*)$",
        re.IGNORECASE,
    )
    _SOURCE_ENTRY_RE = re.compile(
        r"^\[(\d+)\]\s+(https?://[^\s]+|ragflow://[^\s]+)",
        re.MULTILINE,
    )
    _SECTION_HEADING_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)
    _TEXTLIKE_ARTIFACT_SUFFIXES = {
        ".md",
        ".txt",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
    }
    _MAX_ARTIFACT_SCAN_BYTES = 2 * 1024 * 1024

    def _split_report_body_and_sources(self, report_text: str) -> tuple[str, str]:
        match = self._SOURCES_SECTION_RE.search(report_text)
        if not match:
            return report_text, ""
        return report_text[: match.start()], match.group(1)

    def _extract_cited_urls_from_report(self, report_text: str) -> List[str]:
        body, sources_block = self._split_report_body_and_sources(report_text)
        if sources_block:
            ordered = []
            seen = set()
            for _, url in self._SOURCE_ENTRY_RE.findall(sources_block):
                if url not in seen:
                    seen.add(url)
                    ordered.append(url)
            return ordered

        ordered = []
        seen = set()
        for url in self._INLINE_CITATION_RE.findall(body):
            if url not in seen:
                seen.add(url)
                ordered.append(url)
        return ordered

    def _count_citation_mentions(self, body_text: str) -> int:
        numbered_mentions = self._NUMBERED_CITATION_RE.findall(body_text)
        inline_mentions = self._INLINE_CITATION_RE.findall(body_text)
        return len(numbered_mentions) + len(inline_mentions)

    def _max_uncited_span_chars(self, body_text: str) -> int:
        marker_pattern = re.compile(
            r"\[(?:\d+)\]|\[Source:\s*(?:https?://[^\]\s]+|ragflow://[^\]\s]+)\]"
        )
        last_end = 0
        spans: List[int] = []
        for match in marker_pattern.finditer(body_text):
            spans.append(len(body_text[last_end : match.start()]))
            last_end = match.end()
        spans.append(len(body_text[last_end:]))
        return max(spans) if spans else len(body_text)

    def _build_section_level_source_coverage(
        self, body_text: str, cited_urls: List[str]
    ) -> List[dict[str, Any]]:
        source_map = {str(idx): url for idx, url in enumerate(cited_urls, 1)}
        section_matches = list(self._SECTION_HEADING_RE.finditer(body_text))
        if not section_matches:
            unique_sources = len(set(cited_urls))
            return [
                {
                    "section": "__whole_report__",
                    "unique_sources": unique_sources,
                }
            ]

        coverage: List[dict[str, Any]] = []
        for idx, match in enumerate(section_matches):
            start = match.end()
            end = (
                section_matches[idx + 1].start()
                if idx + 1 < len(section_matches)
                else len(body_text)
            )
            section_text = body_text[start:end]
            numbered_urls = {
                source_map[num]
                for num in self._NUMBERED_CITATION_RE.findall(section_text)
                if num in source_map
            }
            inline_urls = set(self._INLINE_CITATION_RE.findall(section_text))
            unique_sources = len(numbered_urls | inline_urls)
            coverage.append(
                {
                    "section": match.group(1).strip(),
                    "unique_sources": unique_sources,
                }
            )
        return coverage

    async def _extract_retrieved_urls_from_artifacts(self) -> List[str]:
        registry = ExecutionContext.get_artifact_registry()
        if not registry:
            return []

        artifacts = await registry.get_all()
        seen = set()
        ordered: List[str] = []

        def _add_urls_from_text(text: Optional[str]) -> None:
            if not text:
                return
            for url in self._URL_RE.findall(text):
                if url not in seen:
                    seen.add(url)
                    ordered.append(url)

        for artifact in artifacts:
            _add_urls_from_text(getattr(artifact.metadata, "preview", None))
            _add_urls_from_text(getattr(artifact.metadata, "description", None))

            try:
                path = Path(artifact.storage_path)
                if (
                    not path.exists()
                    or not path.is_file()
                    or path.suffix.lower() not in self._TEXTLIKE_ARTIFACT_SUFFIXES
                    or path.stat().st_size > self._MAX_ARTIFACT_SCAN_BYTES
                ):
                    continue
                _add_urls_from_text(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue

        return ordered

    async def _build_citation_telemetry(self, report_text: str) -> dict[str, Any]:
        body_text, _ = self._split_report_body_and_sources(report_text)
        cited_urls = self._extract_cited_urls_from_report(report_text)
        retrieved_urls = await self._extract_retrieved_urls_from_artifacts()

        body_chars = len(body_text.strip())
        citation_mentions = self._count_citation_mentions(body_text)
        overlap_urls = sorted(set(cited_urls) & set(retrieved_urls))
        overlap_ratio = (
            round(len(overlap_urls) / len(cited_urls), 4) if cited_urls else 0.0
        )

        return {
            "retrieved_unique_urls": len(retrieved_urls),
            "cited_unique_urls": len(cited_urls),
            "artifact_to_citation_overlap": {
                "count": len(overlap_urls),
                "ratio": overlap_ratio,
            },
            "citation_density_per_1k_chars": (
                round(citation_mentions / body_chars * 1000, 2) if body_chars else 0.0
            ),
            "max_uncited_span_chars": self._max_uncited_span_chars(body_text),
            "section_level_source_coverage": self._build_section_level_source_coverage(
                body_text, cited_urls
            ),
        }

    async def _save_final_result(self, dag: TaskDAG, result: TaskNode) -> None:
        """
        Auto-save final execution result to storage.
        
        Ensures every execution has a persisted output file at:
        storage/executions/<execution_id>/results/reports/final_result.md
        
        Args:
            dag: Task DAG with execution context
            result: Final TaskNode with synthesized result
        """
        if not dag or not result:
            return
            
        try:
            # Use existing FileStorage from DAG/context if available
            file_storage = getattr(dag, "_file_storage", None)
            
            # Fallback: create new FileStorage if needed
            if not file_storage and self.config and self.config.storage:
                from roma_dspy.core.storage.file_storage import FileStorage
                file_storage = FileStorage(
                    config=self.config.storage,
                    execution_id=dag.execution_id
                )
            
            if not file_storage:
                logger.debug("No FileStorage available, skipping final result save")
                return
            
            # Extract result content (handle both string and JSON formats)
            result_content = result.result if hasattr(result, "result") and result.result else str(result)
            
            # If result is JSON string with synthesized_result, extract it
            if isinstance(result_content, str) and result_content.strip().startswith("{"):
                try:
                    import json
                    parsed = json.loads(result_content)
                    if "synthesized_result" in parsed:
                        result_content = parsed["synthesized_result"]
                except (json.JSONDecodeError, KeyError):
                    pass  # Keep original if parsing fails
            
            # Build final report with metadata
            from datetime import datetime
            timestamp = datetime.now().isoformat(timespec="seconds")
            
            report_md = (
                f"# ROMA Execution Result\n\n"
                f"**Execution ID:** `{dag.execution_id}`  \n"
                f"**Task:** {result.goal}  \n"
                f"**Status:** {result.status.value if hasattr(result, 'status') else 'completed'}  \n"
                f"**Timestamp:** {timestamp}  \n"
                f"**Depth:** {result.depth}  \n\n"
                f"---\n\n"
                f"{result_content}\n"
            )

            citation_telemetry = await self._build_citation_telemetry(result_content)
            
            # Save to results/reports/final_result.md
            await file_storage.put_text(
                key="results/reports/final_result.md",
                text=report_md,
                metadata={
                    "execution_id": dag.execution_id,
                    "task": result.goal[:200],
                    "status": result.status.value if hasattr(result, "status") else "completed",
                    "timestamp": timestamp,
                    "depth": str(result.depth),
                    "citation_telemetry": citation_telemetry,
                }
            )
            
            logger.info(f"Saved final result to storage: {dag.execution_id}/results/reports/final_result.md")
            
        except Exception as e:
            # Non-fatal: execution still succeeds even if save fails
            logger.warning(f"Failed to auto-save final result for {dag.execution_id}: {e}")


# ==================== Convenience Functions ====================


def solve(
    task: Union[str, TaskNode],
    max_depth: int = 2,
    config: Optional[ROMAConfig] = None,
    **kwargs,
) -> TaskNode:
    """
    Solve a task using recursive decomposition.

    Args:
        task: Task goal string or TaskNode
        max_depth: Maximum recursion depth
        config: Optional ROMAConfig (creates default if None)
        **kwargs: Additional arguments for RecursiveSolver

    Returns:
        Completed TaskNode with results
    """
    if config is None:
        config = ROMAConfig()  # Uses Pydantic defaults
    solver = RecursiveSolver(config=config, max_depth=max_depth, **kwargs)
    return solver.solve(task)


async def async_solve(
    task: Union[str, TaskNode],
    max_depth: int = 2,
    config: Optional[ROMAConfig] = None,
    **kwargs,
) -> TaskNode:
    """
    Asynchronously solve a task using recursive decomposition.

    Args:
        task: Task goal string or TaskNode
        max_depth: Maximum recursion depth
        config: Optional ROMAConfig (creates default if None)
        **kwargs: Additional arguments for RecursiveSolver

    Returns:
        Completed TaskNode with results
    """
    if config is None:
        config = ROMAConfig()  # Uses Pydantic defaults
    solver = RecursiveSolver(config=config, max_depth=max_depth, **kwargs)
    return await solver.async_solve(task)


def event_solve(
    task: Union[str, TaskNode],
    max_depth: int = 2,
    config: Optional[ROMAConfig] = None,
    priority_fn: Optional[Callable[[TaskNode], int]] = None,
    concurrency: Optional[int] = None,
    **kwargs,
) -> TaskNode:
    """Synchronously solve using the event-driven scheduler.

    Args:
        task: The task to solve
        max_depth: Maximum recursion depth
        config: ROMAConfig instance (defaults to ROMAConfig())
        priority_fn: Optional priority function for task ordering
        concurrency: Number of concurrent tasks (defaults to config.runtime.max_concurrency)
        **kwargs: Additional arguments passed to RecursiveSolver
    """
    if config is None:
        config = ROMAConfig()  # Uses Pydantic defaults

    # Use config's max_concurrency if not explicitly provided
    effective_concurrency = (
        concurrency if concurrency is not None else config.runtime.max_concurrency
    )

    solver = RecursiveSolver(config=config, max_depth=max_depth, **kwargs)
    return solver.event_solve(task, priority_fn=priority_fn, concurrency=effective_concurrency)


async def async_event_solve(
    task: Union[str, TaskNode],
    max_depth: int = 2,
    config: Optional[ROMAConfig] = None,
    priority_fn: Optional[Callable[[TaskNode], int]] = None,
    concurrency: Optional[int] = None,
    **kwargs,
) -> TaskNode:
    """Asynchronously solve using the event-driven scheduler.

    Args:
        task: The task to solve
        max_depth: Maximum recursion depth
        config: ROMAConfig instance (defaults to ROMAConfig())
        priority_fn: Optional priority function for task ordering
        concurrency: Number of concurrent tasks (defaults to config.runtime.max_concurrency)
        **kwargs: Additional arguments passed to RecursiveSolver
    """
    if config is None:
        config = ROMAConfig()  # Uses Pydantic defaults

    # Use config's max_concurrency if not explicitly provided
    effective_concurrency = (
        concurrency if concurrency is not None else config.runtime.max_concurrency
    )

    solver = RecursiveSolver(config=config, max_depth=max_depth, **kwargs)
    return await solver.async_event_solve(
        task,
        priority_fn=priority_fn,
        concurrency=effective_concurrency,
    )
