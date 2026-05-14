"""Runtime orchestration: agent routing, context building, and subgraph execution.

Structural overview
-------------------
ModuleRuntime inherits from six focused Mixins (each in its own module):

  DirectivesMixin      – dynamic_prompt / directive block construction
  AggregationMixin     – WRITE concat / pass-through / citation helpers
  DagBuilderMixin      – subtask graph construction + dependency resolution
  LmTracingMixin       – token extraction + Postgres LM trace persistence
  SentinelMixin        – phase-boundary Sentinel checkpoint integration
  ExecutionHelpersMixin– artifact scanning, parallel scheduling, error enrichment

This file keeps only the public agent-execution methods and the shared
``_execute_agent_with_tracing`` template.
"""

from __future__ import annotations

import copy
import re
import time
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    TYPE_CHECKING,
)

import dspy
from loguru import logger

from roma_dspy.core.context import ExecutionContext
from roma_dspy.core.engine.aggregation import AggregationMixin
from roma_dspy.core.engine.context_store import ContextStore, _sanitize_directive
from roma_dspy.core.engine.dag import TaskDAG
from roma_dspy.core.engine.dag_builder import DagBuilderMixin
from roma_dspy.core.engine.directives import DirectivesMixin
from roma_dspy.core.engine.execution_helpers import ExecutionHelpersMixin
from roma_dspy.core.engine.lm_tracing import LmTracingMixin
from roma_dspy.core.engine.sentinel_mixin import SentinelMixin
from roma_dspy.core.observability import get_span_manager
from roma_dspy.core.registry import AgentRegistry
from roma_dspy.core.signatures import TaskNode
from roma_dspy.resilience import with_module_resilience, measure_execution_time
from roma_dspy.tools.base.manager import ToolkitManager
from roma_dspy.types import (
    AgentType,
    NodeType,
    TaskStatus,
    TaskType,
)
from roma_dspy.types.artifact_injection import ArtifactInjectionMode

if TYPE_CHECKING:
    from ..context import ContextManager
    from roma_dspy.core.modules.base_module import BaseModule


SolveFn = Callable[[TaskNode, TaskDAG, int], TaskNode]
AsyncSolveFn = Callable[[TaskNode, TaskDAG, int], Awaitable[TaskNode]]


class ModuleRuntime(
    DirectivesMixin,
    AggregationMixin,
    DagBuilderMixin,
    LmTracingMixin,
    SentinelMixin,
    ExecutionHelpersMixin,
):
    """Module orchestration using AgentRegistry for task-aware agent selection.

    All heavy logic lives in the Mixin base classes listed above.
    This class owns only initialisation, the agent-execution template, and the
    four public async entry-points (atomize / plan / execute / aggregate).
    """

    _WRITE_INLINE_CITATION_RE = re.compile(
        r"\[Source:\s*(https?://[^\]\s]+|ragflow://[^\]\s]+)\]"
    )
    _WRITE_SOURCES_SPLIT_RE = re.compile(
        r"\n---\n\n###\s*(?:Sources|参考文献|References)\s*\n\n",
        re.IGNORECASE,
    )
    _WRITE_NUMBERED_CITATION_RE = re.compile(r"\[(\d+)\]")

    def __init__(
        self,
        registry: AgentRegistry,
        context_manager: Optional["ContextManager"] = None,
        config: Optional[Any] = None,
    ) -> None:
        self.registry = registry
        self.context_store = ContextStore()
        self.context_manager = context_manager
        self.config = config
        self._warmup_demos: Dict[str, list] = {}

    # ------------------------------------------------------------------
    # Warmup demo injection
    # ------------------------------------------------------------------

    def set_warmup_demos(self, demos: Dict[str, list]) -> None:
        """Store dynamic demos extracted from the warm-up phase."""
        self._warmup_demos = demos

    def _resolve_demos_for_agent(
        self, agent_type: AgentType, task: TaskNode
    ) -> Optional[list]:
        """Resolve demos for an agent call.

        Priority order:
        1. MIPE warmup demos — produced by the evolution engine after warm-up
           and injected via ``set_warmup_demos``.  Highest fidelity; take
           precedence over seed demos when available.
        2. Seed demos selected by ``parent_task_type`` — always active as a
           fallback (and as the primary mechanism before MIPE has run).
           Ensures each Planner call only sees demos that match the current
           decomposition context, eliminating cross-type confusion.
        """
        # --- Priority 1: MIPE warmup demos (executor / specific planner) ---
        if self._warmup_demos:
            mipe_key_map = {
                (AgentType.EXECUTOR, TaskType.WRITE): "executor_write",
                (AgentType.EXECUTOR, TaskType.THINK): "executor_think",
                (AgentType.PLANNER,  TaskType.WRITE): "planner_write",
            }
            key = mipe_key_map.get((agent_type, task.task_type))
            if key and key in self._warmup_demos:
                return self._warmup_demos[key]

        # --- Priority 2: Seed demo routing for Planner ---
        if agent_type == AgentType.PLANNER:
            from prompt_optimization.prompts.seed_prompts.dr_planner_seed import (
                PLANNER_DR_DEMOS_ROOT,
                PLANNER_DR_DEMOS_WRITE,
                PLANNER_DR_DEMOS_THINK,
                PLANNER_DR_DEMOS_RETRIEVE,
            )
            is_root = task.depth == 0 and task.parent_id is None
            if is_root:
                return PLANNER_DR_DEMOS_ROOT
            seed_map = {
                TaskType.WRITE:    PLANNER_DR_DEMOS_WRITE,
                TaskType.THINK:    PLANNER_DR_DEMOS_THINK,
                TaskType.RETRIEVE: PLANNER_DR_DEMOS_RETRIEVE,
            }
            return seed_map.get(task.task_type, PLANNER_DR_DEMOS_ROOT)

        return None

    def _replace_demos(
        self, agent: "BaseModule", warmup_demos: list
    ) -> "BaseModule":
        """Replace agent's static demos with dynamic warm-up demos.

        Warm-up demos REPLACE (not merge with) the static config demos so that
        a single, consistent style signal is given to the LM.
        """
        agent_copy = copy.copy(agent)
        agent_copy._config_demos = list(warmup_demos)
        return agent_copy

    # ------------------------------------------------------------------
    # Tools data extraction
    # ------------------------------------------------------------------

    async def _get_tools_data_async(self, agent: "BaseModule") -> list[dict]:
        """Extract tool information from agent for context building."""
        if not (hasattr(agent, "_toolkit_configs") and agent._toolkit_configs):
            return []

        ctx = ExecutionContext.get()
        if not (ctx and ctx.file_storage):
            return []

        try:
            manager = ToolkitManager.get_instance()
            tools_dict = await manager.get_tools_for_execution(
                execution_id=ctx.execution_id,
                file_storage=ctx.file_storage,
                toolkit_configs=agent._toolkit_configs,
            )
            return [
                {
                    "name": name,
                    "description": getattr(tool, "__doc__", "No description available"),
                }
                for name, tool in tools_dict.items()
            ]
        except Exception as e:
            logger.warning(f"Failed to load toolkit tools: {e}")
            return []

    @classmethod
    def _write_output_has_citations(cls, output_text: str) -> bool:
        """Check whether WRITE output contains inline citation markers."""
        if not output_text or not output_text.strip():
            return False

        if cls._WRITE_INLINE_CITATION_RE.search(output_text):
            return True

        body_text = cls._WRITE_SOURCES_SPLIT_RE.split(output_text, maxsplit=1)[0]
        return bool(cls._WRITE_NUMBERED_CITATION_RE.search(body_text))

    @staticmethod
    def _build_write_hard_constraint_block() -> str:
        """Build non-overridable WRITE citation constraints."""
        return (
            "<non_overridable_constraints>\n"
            "- For WRITE tasks, citation compliance is mandatory.\n"
            "- Every factual claim must include inline citation markers.\n"
            "- Use [Source: URL] where URL is https://... or ragflow://...\n"
            "- If numbered citations are used, they must be traceable to concrete URLs.\n"
            "- Do not finish without citations in the chapter body.\n"
            "</non_overridable_constraints>"
        )

    # ------------------------------------------------------------------
    # Core execution template
    # ------------------------------------------------------------------

    async def _execute_agent_with_tracing(
        self,
        agent_type: AgentType,
        task: TaskNode,
        dag: TaskDAG,
        *,
        prepare_module_kwargs: Callable[[TaskNode, Optional[str]], dict],
        process_result: Callable[[TaskNode, Any, float, Any, Any, TaskDAG], TaskNode],
    ) -> TaskNode:
        """Execute an agent with MLflow tracing and LM trace persistence.

        Centralises the common execution pattern: agent routing, directive
        injection, context building, span creation, and result processing.
        """
        # Root-level orchestration calls: force default agent (task_type=None)
        # to avoid accidentally routing into task-specific variants.
        routed_task_type: Optional[TaskType] = task.task_type
        if (
            agent_type in (AgentType.PLANNER, AgentType.AGGREGATOR)
            and task.depth == 0
            and task.parent_id is None
        ):
            routed_task_type = None

        agent = self.registry.get_agent(agent_type, routed_task_type)
        effective_agent_config = self._get_effective_agent_config(
            agent_type, routed_task_type
        )

        warmup_demos = self._resolve_demos_for_agent(agent_type, task)
        if warmup_demos:
            agent = self._replace_demos(agent, warmup_demos)

        context = None
        if self.context_manager:
            tools_data = await self._get_tools_data_async(agent)

            injection_mode_str = "full"
            if effective_agent_config and hasattr(
                effective_agent_config, "artifact_injection_mode"
            ):
                injection_mode_str = effective_agent_config.artifact_injection_mode
            injection_mode = ArtifactInjectionMode.from_string(injection_mode_str)

            if agent_type == AgentType.EXECUTOR:
                context = await self.context_manager.build_executor_context(
                    task, tools_data, self, dag, injection_mode
                )
            elif agent_type == AgentType.PLANNER:
                context = await self.context_manager.build_planner_context(
                    task, tools_data, self, dag, injection_mode
                )
            elif agent_type == AgentType.AGGREGATOR:
                context = await self.context_manager.build_aggregator_context(
                    task, tools_data, self, dag, injection_mode
                )
            else:
                context = self.context_manager.build_basic_context(task, tools_data)

        directive_block = self._build_dynamic_prompt_block(task.dynamic_prompt)
        if agent_type == AgentType.EXECUTOR and task.task_type == TaskType.WRITE:
            write_hard_constraints = self._build_write_hard_constraint_block()
            directive_block = (
                f"{directive_block}\n{write_hard_constraints}"
                if directive_block
                else write_hard_constraints
            )
        if directive_block:
            logger.debug(
                f"[DIRECTIVE] Injecting dynamic_prompt for {agent_type.value} "
                f"task {task.task_id[:8]}..."
            )

        task_context_token = ExecutionContext.set_current_task(task.task_id)
        try:
            start_time = time.time()
            module_kwargs = prepare_module_kwargs(task, context)
            if directive_block:
                module_kwargs["directive"] = directive_block
                logger.debug(
                    f"[DIRECTIVE] Injected directive ({len(directive_block)} chars) "
                    f"into {agent_type.value} for task {task.task_id[:8]}..."
                )
            else:
                logger.debug(
                    f"[DIRECTIVE] No directive for {agent_type.value} "
                    f"task {task.task_id[:8]}... "
                    f"(metadata keys="
                    f"{list(task.metadata.keys()) if task.metadata else 'None'})"
                )

            existing_callbacks = (
                list(dspy.settings.callbacks)
                if hasattr(dspy.settings, "callbacks")
                else []
            )
            module_kwargs["dspy_context"] = {"callbacks": existing_callbacks}

            span_manager = get_span_manager()
            with span_manager.create_span(agent_type, task, agent.__class__.__name__):
                (
                    result,
                    duration,
                    token_metrics,
                    messages,
                ) = await self._async_execute_module(agent, **module_kwargs)

            execution_id, postgres = self.context_store.get_execution_context()
            if postgres and execution_id:
                await self._persist_lm_trace(
                    execution_id, postgres, agent, result, start_time, task.task_id
                )

            # Artifact detection (multi-layer):
            # 1. DataStorage.store_parquet (automatic)
            # 2. Tool output detection via track_tool_invocation (automatic)
            # 3. Text parser: explicit artifact declarations in LLM output
            # 4. Filesystem scanner: fallback for uncaught files
            await self._run_text_parser(task, result)
            await self._run_filesystem_scanner(task, start_time)

            return process_result(task, result, duration, token_metrics, messages, dag)

        except Exception as e:
            self._enhance_error_context(e, agent_type, task)
            raise
        finally:
            ExecutionContext.reset_current_task(task_context_token)

    # ------------------------------------------------------------------
    # Core execution entry-points
    # ------------------------------------------------------------------

    async def atomize_async(self, task: TaskNode, dag: TaskDAG) -> TaskNode:
        task = task.transition_to(TaskStatus.ATOMIZING)

        def prepare_kwargs(t, context):
            return {"goal": t.goal, "context": context}

        def process_result(t, result, duration, token_metrics, messages, dag):
            t = self._record_module_result(
                t,
                "atomizer",
                t.goal,
                {"is_atomic": result.is_atomic, "node_type": result.node_type.value},
                duration,
                token_metrics=token_metrics,
                messages=messages,
            )
            t = t.set_node_type(result.node_type)
            dag.update_node(t)
            return t

        return await self._execute_agent_with_tracing(
            AgentType.ATOMIZER,
            task,
            dag,
            prepare_module_kwargs=prepare_kwargs,
            process_result=process_result,
        )

    def transition_from_atomizing(self, task: TaskNode, dag: TaskDAG) -> TaskNode:
        if task.node_type == NodeType.EXECUTE:
            task = task.transition_to(TaskStatus.EXECUTING)
        else:
            task = task.transition_to(TaskStatus.PLANNING)
        dag.update_node(task)
        return task

    async def plan_async(self, task: TaskNode, dag: TaskDAG) -> TaskNode:
        def prepare_kwargs(t, context):
            is_root = t.depth == 0 and t.parent_id is None
            return {
                "goal": t.goal,
                "context": context,
                "parent_task_type": None if is_root else (t.task_type.value if t.task_type else None),
            }

        def process_result(t, result, duration, token_metrics, messages, dag):
            report_policy = _sanitize_directive(
                getattr(result, "report_policy", None)
            )

            if report_policy and t.parent_id is None:
                self.context_store.store_report_policy(report_policy)
                logger.info(
                    f"[PLANNER] Stored report_policy for root task {t.task_id[:8]}..."
                )

            t = self._record_module_result(
                t,
                "planner",
                t.goal,
                {
                    "subtasks": [s.model_dump() for s in result.subtasks],
                    "dependencies": result.dependencies_graph,
                    "report_policy": report_policy,
                },
                duration,
                token_metrics=token_metrics,
                messages=messages,
            )
            t = self._create_subtask_graph(t, dag, result)
            t = t.transition_to(TaskStatus.PLAN_DONE)
            dag.update_node(t)
            return t

        return await self._execute_agent_with_tracing(
            AgentType.PLANNER,
            task,
            dag,
            prepare_module_kwargs=prepare_kwargs,
            process_result=process_result,
        )

    async def execute_async(
        self, task: TaskNode, dag: TaskDAG, *, forced: bool = False
    ) -> TaskNode:
        """Execute a task using the appropriate Executor agent.

        Args:
            task: Task to execute.
            dag: The owning DAG.
            forced: If True, force the task into EXECUTE mode regardless of its
                    current node type (used by ``force_execute_async``).
        """
        if forced:
            task = task.set_node_type(NodeType.EXECUTE)
            task = task.transition_to(TaskStatus.EXECUTING)
            dag.update_node(task)

        context_captured = None

        def prepare_kwargs(t: TaskNode, context: Optional[str]) -> dict:
            nonlocal context_captured
            context_captured = context
            return {"goal": t.goal, "context": context}

        def process_result(
            t: TaskNode,
            result: Any,
            duration: float,
            token_metrics: Any,
            messages: Any,
            dag: TaskDAG,
        ) -> TaskNode:
            output_text = (
                str(result.output) if hasattr(result, "output") and result.output else ""
            )
            if t.task_type == TaskType.WRITE and not self._write_output_has_citations(
                output_text
            ):
                raise ValueError(
                    "WRITE output is missing inline citation markers. "
                    "Executor response rejected to enforce evidence-grounded writing."
                )

            metadata: dict = {"forced": True, "depth": t.depth} if forced else {}
            if context_captured and isinstance(context_captured, str):
                metadata["context_received"] = (
                    context_captured[:200] + "..."
                    if len(context_captured) > 200
                    else context_captured
                )
                if t.dependencies:
                    metadata["dependency_ids"] = list(t.dependencies)

            # Merge LLM structured output with tool-collected URLs
            llm_sources = (
                list(result.sources)
                if hasattr(result, "sources") and result.sources
                else []
            )
            tool_sources = ExecutionContext.get_and_clear_sources()
            if llm_sources or tool_sources:
                seen: set = set()
                merged: List[str] = []
                for url in llm_sources + tool_sources:
                    if url not in seen:
                        seen.add(url)
                        merged.append(url)
                metadata["sources"] = merged

            if hasattr(result, "trajectory"):
                metadata["trajectory"] = [str(step) for step in result.trajectory]

            t = self._record_module_result(
                t,
                "executor",
                t.goal,
                output_text,
                duration,
                metadata=metadata,
                token_metrics=token_metrics,
                messages=messages,
            )
            t = t.with_result(output_text)
            dag.update_node(t)
            return t

        task = await self._execute_agent_with_tracing(
            AgentType.EXECUTOR,
            task,
            dag,
            prepare_module_kwargs=prepare_kwargs,
            process_result=process_result,
        )

        await self.context_store.store_result(task.task_id, task.result)
        return task

    async def force_execute_async(self, task: TaskNode, dag: TaskDAG) -> TaskNode:
        """Force-execute a task, bypassing atomization."""
        return await self.execute_async(task, dag, forced=True)

    async def aggregate_async(
        self,
        task: TaskNode,
        subgraph: Optional[TaskDAG],
        dag: TaskDAG,
    ) -> TaskNode:
        if task.status != TaskStatus.PLAN_DONE:
            return task
        task = task.transition_to(TaskStatus.AGGREGATING)

        # Path 1: If the subtree contains leaf WRITE nodes, concatenate them
        # directly (no LLM). This handles ALL report-generation scenarios
        # regardless of DAG depth or intermediate node types.
        if subgraph:
            leaf_writes = self._collect_ordered_leaf_writes(subgraph)
            if leaf_writes:
                logger.info(
                    f"[WRITE-CONCAT] Found {len(leaf_writes)} leaf WRITE nodes "
                    f"in subtree, concatenating for: {task.goal[:80]}..."
                )
                return self._concatenate_leaf_writes(task, leaf_writes, dag)

        # Path 2: No WRITE leaves — use LLM aggregator (for RETRIEVE/THINK)
        subtask_results = self._collect_subtask_results(subgraph)
        if not subtask_results:
            logger.warning(
                f"[AGG-FALLBACK] No aggregatable subtask results for {task.task_id[:8]} "
                f"(possibly all skipped by content filter); using deterministic fallback."
            )
            fallback_text = (
                "No aggregatable subtask outputs are available because some "
                "subtasks were skipped after a server-side content-filter "
                "BadRequest (DataInspectionFailed)."
            )
            task = self._record_module_result(
                task,
                "aggregator",
                {"original_goal": task.goal, "subtask_count": 0, "mode": "empty_fallback"},
                fallback_text,
                0.0,
            )
            task = task.with_result(fallback_text)
            dag.update_node(task)
            return task

        all_subtask_sources: List[str] = []
        for st in subtask_results:
            if st.sources:
                all_subtask_sources.extend(st.sources)

        def prepare_kwargs(t: TaskNode, context: Optional[str]) -> dict:
            return {
                "original_goal": t.goal,
                "subtasks_results": subtask_results,
                "context": context,
            }

        def process_result(
            t: TaskNode,
            result: Any,
            duration: float,
            token_metrics: Any,
            messages: Any,
            dag: TaskDAG,
        ) -> TaskNode:
            final_text = result.synthesized_result
            if final_text and "[Source:" in final_text:
                final_text, unique_sources = self._replace_inline_citations(
                    final_text, all_subtask_sources
                )
                if unique_sources:
                    sources_section = "\n\n---\n\n### Sources\n\n"
                    for i, src in enumerate(unique_sources, 1):
                        sources_section += f"[{i}] {src}\n"
                    final_text += sources_section

            t = self._record_module_result(
                t,
                "aggregator",
                {"original_goal": t.goal, "subtask_count": len(subtask_results)},
                final_text,
                duration,
                token_metrics=token_metrics,
                messages=messages,
            )
            t = t.with_result(final_text)
            dag.update_node(t)
            return t

        return await self._execute_agent_with_tracing(
            AgentType.AGGREGATOR,
            task,
            dag,
            prepare_module_kwargs=prepare_kwargs,
            process_result=process_result,
        )

    # ------------------------------------------------------------------
    # Subgraph orchestration
    # ------------------------------------------------------------------

    async def process_subgraph_async(
        self,
        task: TaskNode,
        dag: TaskDAG,
        solve_fn: AsyncSolveFn,
    ) -> TaskNode:
        subgraph = dag.get_subgraph(task.subgraph_id) if task.subgraph_id else None
        if subgraph:
            await self.solve_subgraph_async(subgraph, solve_fn)
            task = await self.aggregate_async(task, subgraph, dag)
        return task

    async def solve_subgraph_async(
        self,
        subgraph: TaskDAG,
        solve_fn: AsyncSolveFn,
    ) -> None:
        pending = set(subgraph.graph.nodes())
        completed: set[str] = set()

        sentinel_ran_post_retrieve = False
        sentinel_ran_post_think = False

        node_info = []
        for nid in subgraph.graph.nodes():
            n = subgraph.get_node(nid)
            deps = [d[:8] for d in subgraph.graph.predecessors(nid)]
            node_info.append(f"{nid[:8]}({n.task_type.value}) deps={deps}")
        logger.info(
            f"[solve_subgraph] Starting with {len(pending)} tasks, "
            f"edges: {list(subgraph.graph.edges())}\n"
            f"  Node details: {node_info}"
        )

        wave = 0
        while pending:
            ready = self._get_ready_tasks(subgraph, pending, completed)
            if not ready:
                if pending:
                    logger.error(
                        f"[solve_subgraph] Deadlock: {len(pending)} tasks remain "
                        f"but none are ready. Pending: {pending}, "
                        f"Completed: {completed}"
                    )
                break

            wave += 1
            ready_info = [
                f"{t.task_id[:8]}({t.task_type.value})" for t in ready
            ]
            logger.info(
                f"[solve_subgraph] Wave {wave}: executing "
                f"{len(ready)} tasks: {ready_info}"
            )

            solved_tasks = await self._execute_tasks_parallel(
                ready, subgraph, solve_fn
            )
            for solved_task in solved_tasks:
                subgraph.update_node(solved_task)
                pending.remove(solved_task.task_id)
                completed.add(solved_task.task_id)

            # Phase-boundary Sentinel checks (only when MIPE blueprint exists)
            if not sentinel_ran_post_retrieve:
                sentinel_ran_post_retrieve = await self._maybe_run_sentinel(
                    subgraph, completed, pending, "post_retrieve"
                )
            if sentinel_ran_post_retrieve and not sentinel_ran_post_think:
                sentinel_ran_post_think = await self._maybe_run_sentinel(
                    subgraph, completed, pending, "post_think"
                )

    # ------------------------------------------------------------------
    # Low-level module execution (resilience + timing wrapper)
    # ------------------------------------------------------------------

    @measure_execution_time
    @with_module_resilience(module_name="module_execution")
    async def _async_execute_module(self, module, *args, **kwargs):
        return await module.aforward(*args, **kwargs)
