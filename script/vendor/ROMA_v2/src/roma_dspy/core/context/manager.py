"""
Context Manager for building execution context for ROMA-DSPy agents.

The ContextManager is responsible for:
1. Building Pydantic context models from runtime state
2. Composing fundamental + agent-specific context
3. Serializing to XML strings for DSPy signatures
4. Injecting artifacts into context based on injection mode

It follows the Single Responsibility Principle: one job is context orchestration.
"""

from datetime import datetime, UTC
from pathlib import Path
from typing import List, Optional, TYPE_CHECKING, Set
from roma_dspy.core.context.models import (
    TemporalContext,
    FileSystemContext,
    RecursionContext,
    ToolsContext,
    ToolInfo,
    FundamentalContext,
    ExecutorSpecificContext,
    PlannerSpecificContext,
    AggregatorSpecificContext,
    DependencyResult,
    ParentResult,
    SiblingResult,
)
from roma_dspy.types.artifact_models import ArtifactReference
from roma_dspy.core.artifacts.query_service import ArtifactQueryService
from roma_dspy.core.context.execution_context import ExecutionContext
from roma_dspy.types import TaskStatus, TaskType
from roma_dspy.types.artifact_injection import ArtifactInjectionMode

if TYPE_CHECKING:
    from ..engine.runtime import ModuleRuntime
    from ..engine.dag import TaskDAG
    from ..signatures.base_models.task_node import TaskNode
    from ..storage import FileStorage


class ContextManager:
    """
    Central context manager that orchestrates context building for all agents.

    Design principles:
    - Uses Pydantic models for type safety and validation
    - Separates data (models) from building logic (this class)
    - Returns XML strings ready for DSPy signatures
    - Follows DRY: shared components built once, composed differently per agent

    Usage:
        manager = ContextManager(file_storage, overall_objective)
        context_xml = manager.build_executor_context(task, tools_data, runtime, dag)
        # Pass context_xml to executor signature
    """

    def __init__(self, file_storage: "FileStorage", overall_objective: str):
        """
        Initialize context manager.

        Args:
            file_storage: FileStorage instance for this execution (provides paths and execution_id)
            overall_objective: Root goal of execution (helps agents align with bigger picture)
        """
        self.file_storage = file_storage
        self.overall_objective = overall_objective
        self._artifact_query_service = ArtifactQueryService()

    # ==================== Component Builders (Private) ====================

    def _build_temporal(self) -> TemporalContext:
        """Build temporal context with current date/time."""
        now = datetime.now(UTC)
        return TemporalContext(
            current_date=now.strftime("%Y-%m-%d"),
            current_year=now.year,
            current_timestamp=now.isoformat(),
        )

    def _build_file_system(self) -> FileSystemContext:
        """Build file system context from FileStorage instance."""
        return FileSystemContext.from_file_storage(self.file_storage)

    def _build_recursion(self, task: "TaskNode") -> RecursionContext:
        """Build recursion context from task's depth information."""
        return RecursionContext(
            current_depth=task.depth,
            max_depth=task.max_depth,
            at_limit=task.depth >= task.max_depth,
        )

    def _build_tools(self, tools_data: List[dict]) -> ToolsContext:
        """Build tools context from tools data."""
        tools = [
            ToolInfo(name=t["name"], description=t["description"]) for t in tools_data
        ]
        return ToolsContext(tools=tools)

    @staticmethod
    def _detect_language(text: str) -> str:
        """Script-based language detection.

        Returns 'zh' if more than 15% of characters are CJK (Chinese/Japanese/Korean),
        otherwise returns 'en'. The 15% threshold handles queries that are primarily
        English but mention a few Chinese proper nouns (e.g. regulatory body names).
        """
        if not text:
            return "en"
        cjk_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        return "zh" if cjk_count / len(text) > 0.15 else "en"

    def _build_fundamental(
        self,
        task: "TaskNode",
        tools_data: List[dict],
        include_file_system: bool = False,
    ) -> FundamentalContext:
        """Build fundamental context available to all agents."""
        return FundamentalContext(
            overall_objective=self.overall_objective,
            output_language=self._detect_language(self.overall_objective),
            temporal=self._build_temporal(),
            recursion=self._build_recursion(task),
            tools=self._build_tools(tools_data),
            file_system=self._build_file_system() if include_file_system else None,
        )

    async def _query_artifacts_for_context(
        self,
        task_ids: List[str],
        injection_mode: ArtifactInjectionMode,
        current_task_id: Optional[str] = None,
        dag: Optional["TaskDAG"] = None,
    ) -> List:
        """
        Query artifacts based on injection mode.

        Centralized artifact querying logic usable by any agent type.
        This method is DRY - single source of truth for artifact queries.

        Args:
            task_ids: Task IDs to query artifacts from (dependencies, parent, siblings, etc.)
            injection_mode: Controls which artifacts to retrieve
            current_task_id: Current task ID (needed for SUBTASK mode)
            dag: Task DAG (needed for SUBTASK mode to navigate hierarchy)

        Returns:
            List of ArtifactReference objects for context injection
        """
        if injection_mode == ArtifactInjectionMode.NONE:
            return []

        registry = ExecutionContext.get_artifact_registry()
        if not registry:
            return []

        if injection_mode == ArtifactInjectionMode.DEPENDENCIES:
            if not task_ids:
                return []
            return await self._artifact_query_service.get_artifacts_for_dependencies(
                registry=registry, dependency_task_ids=task_ids, mode=injection_mode
            )
        elif injection_mode == ArtifactInjectionMode.FULL:
            return await self._artifact_query_service.get_all_artifacts(
                registry=registry, mode=injection_mode
            )
        elif injection_mode == ArtifactInjectionMode.SUBTASK:
            if not current_task_id or not dag:
                from loguru import logger

                logger.warning(
                    "SUBTASK mode requires current_task_id and dag parameters, "
                    "falling back to empty list"
                )
                return []
            return await self._artifact_query_service.get_artifacts_for_subtask(
                registry=registry,
                dag=dag,
                current_task_id=current_task_id,
                mode=injection_mode,
            )

        return []

    async def _build_executor_specific(
        self,
        task: "TaskNode",
        runtime: "ModuleRuntime",
        dag: "TaskDAG",
        injection_mode: ArtifactInjectionMode = ArtifactInjectionMode.DEPENDENCIES,
    ) -> ExecutorSpecificContext:
        """
        Build executor-specific context with dependency results and artifacts.

        When a nested-subgraph task has no direct dependencies, results from
        ancestor tasks are propagated so that evidence gathered by earlier
        phases (e.g. RETRIEVE) is visible to downstream THINK / WRITE
        executors regardless of DAG nesting depth.

        Args:
            task: Current task node
            runtime: Module runtime for accessing context store
            dag: Task DAG for finding dependency tasks
            injection_mode: Controls which artifacts are injected

        Returns:
            ExecutorSpecificContext with dependency results and artifact references
        """
        dependency_results = []

        root_dag = dag.root
        if task.dependencies:
            for dep_id in task.dependencies:
                result_str = runtime.context_store.get_result(dep_id)
                if result_str:
                    try:
                        dep_task, _ = root_dag.find_node(dep_id)
                        dependency_results.append(
                            DependencyResult(goal=dep_task.goal, output=result_str)
                        )
                    except ValueError:
                        pass

        if not dependency_results and task.parent_id:
            dependency_results = self._collect_ancestor_dependency_results(
                task, runtime, root_dag
            )

        artifact_task_ids = (
            self._get_executor_artifact_task_ids(task, dag)
            if injection_mode == ArtifactInjectionMode.DEPENDENCIES
            else list(task.dependencies)
        )

        available_artifacts = await self._query_artifacts_for_context(
            task_ids=artifact_task_ids,
            injection_mode=injection_mode,
            current_task_id=task.task_id,
            dag=dag,
        )
        available_artifacts = self._deduplicate_artifacts(available_artifacts)

        parent_write_scope, report_policy = self._resolve_executor_scope_context(
            task, runtime, dag
        )

        evidence_inventory = None
        if task.task_type in (TaskType.THINK, TaskType.WRITE):
            evidence_inventory = await self._build_evidence_inventory_from_registry()

        # Read mandate_checklist from task metadata (set by dag_builder from SubTask field).
        mandate_checklist: Optional[str] = None
        if task.task_type == TaskType.WRITE and task.metadata:
            mandate_checklist = task.metadata.get("mandate_checklist")

        return ExecutorSpecificContext(
            dependency_results=dependency_results,
            task_context_input=getattr(task, "context_input", None),
            available_artifacts=available_artifacts,
            parent_write_scope=parent_write_scope,
            report_policy=report_policy,
            evidence_inventory=evidence_inventory,
            mandate_checklist=mandate_checklist,
        )

    @staticmethod
    async def _build_evidence_inventory_from_registry() -> Optional[str]:
        """Build evidence inventory from the FULL artifact registry.

        Queries ALL registered artifacts in the execution, not just those
        matching the dependency-scoped task_ids. This is critical because
        nested THINK tasks (depth >= 2) often cannot reach RETRIEVE task
        artifacts through the dependency chain alone — RETRIEVE tasks are
        typically dependencies of an ancestor THINK, not of the current
        task's direct ancestors.

        Returns compact inventory string or None if no .md evidence files.
        """
        from roma_dspy.types.artifact_models import ArtifactReference

        ctx = ExecutionContext.get()
        if not ctx:
            return None
        registry = ctx.get_artifact_registry()
        if not registry:
            return None

        all_artifacts = await registry.get_all()
        if not all_artifacts:
            return None

        entries = []
        for art in all_artifacts:
            path = art.storage_path.replace("\\", "/")
            
            # Try to get path relative to execution root
            try:
                from pathlib import Path
                root_path = Path(ctx.file_storage.root).resolve()
                art_path = Path(art.storage_path).resolve()
                rel_path = art_path.relative_to(root_path).as_posix()
            except Exception:
                # Fallback to just the filename if relative_to fails
                rel_path = path.rsplit("/", 1)[-1] if "/" in path else path

            # Only surface evidence files that currently exist on disk.
            # This prevents stale registry entries (e.g. missing report_outline.md)
            # from misleading downstream THINK/WRITE tasks into repeated read_file errors.
            try:
                from pathlib import Path

                candidate = Path(art.storage_path).resolve()
                if not candidate.exists() or not candidate.is_file():
                    continue
            except Exception:
                # If path validation fails, skip this artifact entry defensively.
                continue

            if not rel_path.endswith(".md"):
                continue
            desc = art.metadata.description
            if len(desc) > 120:
                desc = desc[:120] + "..."
            entries.append((rel_path, desc))

        if not entries:
            return None

        lines = [f"Total evidence files: {len(entries)}"]
        for i, (rel_path, desc) in enumerate(entries, 1):
            lines.append(f"[{i}] {rel_path} — {desc}")

        return "\n".join(lines)

    @staticmethod
    def _resolve_executor_scope_context(
        task: "TaskNode",
        runtime: "ModuleRuntime",
        dag: "TaskDAG",
    ) -> tuple:
        """Resolve scope constraint and report_policy for executor context.

        When a THINK task is a child of a WRITE parent, the parent's goal
        carries critical scope information that the THINK executor must
        respect when generating local outlines.  Injecting this directly
        into the XML context prevents the THINK executor from reorganising
        chapters or pulling content from other groups' scope.

        Returns:
            (parent_write_scope, report_policy) — both Optional[str].
        """
        from loguru import logger

        parent_write_scope: Optional[str] = None
        report_policy: Optional[str] = None

        if task.task_type == TaskType.THINK and task.parent_id:
            try:
                parent_task, _ = dag.root.find_node(task.parent_id)
                if parent_task.task_type == TaskType.WRITE:
                    parent_write_scope = (
                        f"[Parent WRITE Scope]\n"
                        f"{parent_task.goal}\n\n"
                        f"The chapter-level structure has already been decided. "
                        f"You are detailing the chapters within this scope, "
                        f"not redesigning the structure."
                    )
                    logger.debug(
                        f"[SCOPE] Injected parent WRITE scope for "
                        f"THINK task {task.task_id[:8]}..."
                    )
            except ValueError:
                pass

        if task.task_type in (TaskType.THINK, TaskType.WRITE):
            report_policy = runtime.context_store.get_report_policy()
            if report_policy and task.task_type == TaskType.WRITE:
                logger.debug(
                    f"[SCOPE] Injected report_policy for "
                    f"WRITE task {task.task_id[:8]}..."
                )

        return parent_write_scope, report_policy

    def _get_executor_artifact_task_ids(
        self, task: "TaskNode", dag: "TaskDAG"
    ) -> List[str]:
        """Expand executor evidence beyond direct dependencies.

        WRITE and THINK tasks in nested subgraphs often need evidence
        artifacts that were collected by ancestor-level RETRIEVE tasks.
        We widen the artifact query scope by walking up the parent chain
        and collecting each ancestor's own dependencies.
        """
        task_ids: Set[str] = set(task.dependencies or [])
        if task.task_type not in (TaskType.WRITE, TaskType.THINK):
            return list(task_ids)

        root_dag = dag.root
        parent_id = task.parent_id
        visited: Set[str] = set()
        while parent_id and parent_id not in visited:
            visited.add(parent_id)
            try:
                parent_task, _ = root_dag.find_node(parent_id)
            except ValueError:
                break

            task_ids.add(parent_task.task_id)
            task_ids.update(parent_task.dependencies or [])
            parent_id = parent_task.parent_id

        # Safety-net: always include all RETRIEVE nodes from the root DAG.
        # Prevents evidence injection failure when RETRIEVE nodes are
        # structurally disconnected from the WRITE/THINK tree (e.g. after
        # topo_evolver mutations that left RETRIEVE nodes as isolated roots).
        for node in root_dag.get_all_tasks(include_subgraphs=False):
            if getattr(node, "task_type", None) == TaskType.RETRIEVE:
                task_ids.add(node.task_id)

        return list(task_ids)

    @staticmethod
    def _collect_ancestor_dependency_results(
        task: "TaskNode",
        runtime: "ModuleRuntime",
        root_dag: "TaskDAG",
    ) -> List[DependencyResult]:
        """Walk up the parent chain and collect completed dependency results.

        Called when a nested-subgraph task has no direct ``task.dependencies``
        so that RETRIEVE / THINK evidence gathered by ancestor phases is
        available to the executor's context.
        """
        from loguru import logger

        results: List[DependencyResult] = []
        seen_ids: Set[str] = set()
        parent_id = task.parent_id
        visited: Set[str] = set()

        while parent_id and parent_id not in visited:
            visited.add(parent_id)
            try:
                parent_task, _ = root_dag.find_node(parent_id)
            except ValueError:
                break

            for dep_id in parent_task.dependencies or []:
                if dep_id in seen_ids:
                    continue
                dep_result = runtime.context_store.get_result(dep_id)
                if dep_result:
                    try:
                        dep_node, _ = root_dag.find_node(dep_id)
                        results.append(
                            DependencyResult(
                                goal=dep_node.goal, output=dep_result
                            )
                        )
                        seen_ids.add(dep_id)
                    except ValueError:
                        pass

            parent_id = parent_task.parent_id

        if results:
            logger.info(
                f"[ANCESTOR] Propagated {len(results)} ancestor dependency "
                f"result(s) to task {task.task_id[:8]}..."
            )
        return results

    @staticmethod
    def _deduplicate_artifacts(artifacts: List) -> List:
        """Deduplicate artifacts by storage path, keeping the first occurrence."""
        seen: Set[str] = set()
        result: List = []
        for artifact in artifacts:
            if artifact.storage_path not in seen:
                seen.add(artifact.storage_path)
                result.append(artifact)
        return result

    async def _build_planner_specific(
        self,
        task: "TaskNode",
        runtime: "ModuleRuntime",
        dag: "TaskDAG",
        injection_mode: ArtifactInjectionMode = ArtifactInjectionMode.DEPENDENCIES,
    ) -> PlannerSpecificContext:
        """
        Build planner-specific context with parent/sibling results and artifacts.

        Args:
            task: Current task node
            runtime: Module runtime for accessing context store
            dag: Task DAG for finding parent/sibling tasks
            injection_mode: Controls which artifacts are injected

        Returns:
            PlannerSpecificContext with parent/sibling results and artifact references
        """
        parent_results = []
        sibling_results = []

        # Use root DAG for hierarchical lookups: the current `dag` may be a
        # subgraph that doesn't contain the parent task.  Searching from root
        # guarantees we can reach any node in the full hierarchy.
        root_dag = dag.root

        # Get parent result
        if task.parent_id:
            parent_result = runtime.context_store.get_result(task.parent_id)
            if parent_result:
                try:
                    parent_task, _ = root_dag.find_node(task.parent_id)
                    parent_results.append(
                        ParentResult(
                            goal=parent_task.goal,
                            result=parent_result,
                            task_type=parent_task.task_type.value if parent_task.task_type else None,
                        )
                    )
                except ValueError:
                    from loguru import logger

                    logger.warning(
                        f"[build_planner_context] Parent task {task.parent_id[:8]}... not found in DAG hierarchy"
                    )

        # Get sibling results
        if task.parent_id:
            try:
                parent, owning_dag = root_dag.find_node(task.parent_id)
            except ValueError:
                from loguru import logger

                logger.warning(
                    f"[build_planner_context] Parent task {task.parent_id[:8]}... not found for sibling lookup"
                )
                parent = None
                owning_dag = None
            if parent and parent.subgraph_id and owning_dag:
                subgraph = owning_dag.get_subgraph(parent.subgraph_id)
                if subgraph:
                    for sibling in subgraph.get_all_tasks(include_subgraphs=False):
                        if (
                            sibling.task_id != task.task_id
                            and sibling.status == TaskStatus.COMPLETED
                        ):
                            sib_result = runtime.context_store.get_result(sibling.task_id)
                            if sib_result:
                                sibling_results.append(
                                    SiblingResult(goal=sibling.goal, result=sib_result)
                                )

        # Query artifacts from parent using centralized method
        # Note: Siblings don't have task_id in SiblingResult model, so we only query parent
        task_ids = [task.parent_id] if task.parent_id else []
        available_artifacts = await self._query_artifacts_for_context(
            task_ids=task_ids,
            injection_mode=injection_mode,
            current_task_id=task.task_id,
            dag=dag,
        )

        # For WRITE sub-planners: find report_outline.md from the execution artifact registry
        # and surface it as a high-priority global_outline field so it appears before
        # available_artifacts in the XML context (which may contain 30+ retrieve previews).
        # NOTE: report_outline.md is registered by the LEAF executor node inside the THINK
        # subgraph (not by the THINK parent task itself), so scanning get_by_task(think_id)
        # returns empty. We must scan registry.get_all() and filter by path instead.
        global_outline: Optional[ArtifactReference] = None
        global_outline_content: Optional[str] = None
        if task.task_type == TaskType.WRITE and task.parent_id:
            try:
                registry = ExecutionContext.get_artifact_registry()
                if registry:
                    all_artifacts = await registry.get_all()
                    outline_candidates = [
                        art
                        for art in all_artifacts
                        if art.storage_path and "report_outline" in art.storage_path
                    ]
                    if outline_candidates:
                        outline_candidates.sort(
                            key=lambda a: getattr(a, "created_at", datetime.min.replace(tzinfo=UTC)),
                            reverse=True,
                        )
                        selected_outline = outline_candidates[0]
                        global_outline = ArtifactReference.from_artifact(selected_outline)
                        global_outline_content = self._load_text_artifact_content(
                            selected_outline.storage_path
                        )
            except Exception:
                pass

        # Two-layer Blueprint injection:
        #   depth==0 (Root Planner)       → full blueprint context (~1700 tokens)
        #   depth>=1 with blueprint_node_id → node-scoped context (~200 tokens)
        #   otherwise                     → no blueprint injection
        blueprint_context: Optional[str] = None
        if task.depth == 0 and task.parent_id is None:
            blueprint_context = runtime.context_store.get_plan_blueprint_context()
        elif task.metadata and task.metadata.get("blueprint_node_id"):
            blueprint = runtime.context_store.get_plan_blueprint()
            if blueprint:
                node_id = task.metadata["blueprint_node_id"]
                blueprint_context = blueprint.to_node_scoped_context(node_id)

        # report_policy injection: only for sub-Planners (depth >= 1).
        # Root Planner generates report_policy itself; sub-Planners need it as
        # a constraint so they inherit audience/tone/length targets consistently.
        report_policy: Optional[str] = None
        if task.depth > 0 or task.parent_id is not None:
            report_policy = runtime.context_store.get_report_policy()

        return PlannerSpecificContext(
            parent_results=parent_results,
            sibling_results=sibling_results,
            available_artifacts=available_artifacts,
            blueprint_context=blueprint_context,
            report_policy=report_policy,
            global_outline=global_outline,
            global_outline_content=global_outline_content,
        )

    @staticmethod
    def _load_text_artifact_content(storage_path: Optional[str]) -> Optional[str]:
        """Load full text content from artifact path for high-priority injections."""
        if not storage_path:
            return None
        try:
            path = Path(storage_path)
            if not path.exists() or not path.is_file():
                return None
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

    async def _build_aggregator_specific(
        self,
        task: "TaskNode",
        runtime: "ModuleRuntime",
        dag: "TaskDAG",
        injection_mode: ArtifactInjectionMode = ArtifactInjectionMode.DEPENDENCIES,
    ) -> AggregatorSpecificContext:
        """
        Build aggregator-specific context with artifacts from subtasks.

        For RETRIEVE aggregators, also injects an evidence_inventory so that
        the aggregator can reference real retrieve_*.md filenames when building
        the evidence catalog (without it the LLM would hallucinate filenames).

        Args:
            task: Current task node (should have subgraph_id for subtasks)
            runtime: Module runtime (not used for aggregator, but kept for signature consistency)
            dag: Task DAG for accessing subgraph
            injection_mode: Controls which artifacts are injected (DEPENDENCIES mode queries all subtasks)

        Returns:
            AggregatorSpecificContext with artifact references and (for RETRIEVE) evidence inventory
        """
        subtask_ids = []

        # Get all subtask IDs from the subgraph
        if task.subgraph_id:
            subgraph = dag.get_subgraph(task.subgraph_id)
            if subgraph:
                subtask_ids = [
                    t.task_id for t in subgraph.get_all_tasks(include_subgraphs=False)
                ]

        # Query artifacts from all subtasks using centralized method
        available_artifacts = await self._query_artifacts_for_context(
            task_ids=subtask_ids,
            injection_mode=injection_mode,
            current_task_id=task.task_id,
            dag=dag,
        )

        # Inject evidence_inventory for RETRIEVE aggregators so the LLM can
        # reference real retrieve_*.md filenames rather than hallucinating them.
        evidence_inventory = None
        if task.task_type == TaskType.RETRIEVE:
            evidence_inventory = await self._build_evidence_inventory_from_registry()

        return AggregatorSpecificContext(
            available_artifacts=available_artifacts,
            evidence_inventory=evidence_inventory,
        )

    # ==================== Generic Builder (DRY) ====================

    def _build_context(
        self,
        task: "TaskNode",
        tools_data: List[dict],
        include_file_system: bool = False,
        specific_context: Optional[str] = None,
    ) -> str:
        """
        Generic context builder - composes fundamental + agent-specific context.

        Args:
            task: Current task node
            tools_data: Available tools information
            include_file_system: Whether to include file system in fundamental context
            specific_context: Optional agent-specific context XML (or None for agents with no specific context)

        Returns:
            Complete XML context string
        """
        fundamental = self._build_fundamental(task, tools_data, include_file_system)

        parts = ["<context>", fundamental.to_xml()]
        if specific_context:
            parts.append(specific_context)
        parts.append("</context>")

        return "\n".join(parts)

    # ==================== Public API: Agent-Specific Builders ====================
    # Executor, Planner, and Aggregator have specialized async builders (they need artifacts)
    # Other agents (Atomizer, Verifier) use build_basic_context (no artifacts needed)

    async def build_planner_context(
        self,
        task: "TaskNode",
        tools_data: List[dict],
        runtime: "ModuleRuntime",
        dag: "TaskDAG",
        injection_mode: ArtifactInjectionMode = ArtifactInjectionMode.DEPENDENCIES,
    ) -> str:
        """
        Build complete context for Planner agent (fundamental + parent/siblings + artifacts).

        Args:
            task: Current task node
            tools_data: Available tools information
            runtime: Module runtime for context access
            dag: Task DAG for parent/sibling lookup
            injection_mode: Controls which artifacts are injected (default: DEPENDENCIES)

        Returns:
            Complete XML context string
        """
        specific = await self._build_planner_specific(
            task, runtime, dag, injection_mode
        )
        return self._build_context(
            task,
            tools_data,
            include_file_system=False,
            specific_context=specific.to_xml(),
        )

    async def build_executor_context(
        self,
        task: "TaskNode",
        tools_data: List[dict],
        runtime: "ModuleRuntime",
        dag: "TaskDAG",
        injection_mode: ArtifactInjectionMode = ArtifactInjectionMode.DEPENDENCIES,
    ) -> str:
        """
        Build complete context for Executor agent (fundamental + file_system + dependencies + artifacts).

        Args:
            task: Current task node
            tools_data: Available tools information
            runtime: Module runtime for context access
            dag: Task DAG for dependency lookup
            injection_mode: Controls which artifacts are injected (default: DEPENDENCIES)

        Returns:
            Complete XML context string
        """
        specific = await self._build_executor_specific(
            task, runtime, dag, injection_mode
        )
        return self._build_context(
            task,
            tools_data,
            include_file_system=True,
            specific_context=specific.to_xml(),
        )

    async def build_aggregator_context(
        self,
        task: "TaskNode",
        tools_data: List[dict],
        runtime: "ModuleRuntime",
        dag: "TaskDAG",
        injection_mode: ArtifactInjectionMode = ArtifactInjectionMode.DEPENDENCIES,
    ) -> str:
        """
        Build complete context for Aggregator agent (fundamental + subtask artifacts).

        Args:
            task: Current task node (should have subgraph_id)
            tools_data: Available tools information
            runtime: Module runtime for context access
            dag: Task DAG for subgraph access
            injection_mode: Controls which artifacts are injected (default: DEPENDENCIES)

        Returns:
            Complete XML context string
        """
        specific = await self._build_aggregator_specific(
            task, runtime, dag, injection_mode
        )
        return self._build_context(
            task,
            tools_data,
            include_file_system=False,
            specific_context=specific.to_xml(),
        )

    def build_basic_context(
        self,
        task: "TaskNode",
        tools_data: List[dict],
    ) -> str:
        """
        Build fundamental context for agents without specific context needs (Atomizer, Verifier).

        Args:
            task: Current task node
            tools_data: Available tools information

        Returns:
            Complete XML context string with only fundamental context
        """
        return self._build_context(
            task, tools_data, include_file_system=False, specific_context=None
        )
