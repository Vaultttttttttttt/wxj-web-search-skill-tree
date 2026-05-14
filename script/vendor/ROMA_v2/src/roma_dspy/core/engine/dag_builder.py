"""DAG / subtask-graph construction logic extracted from ModuleRuntime."""

from __future__ import annotations

import json as _json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from loguru import logger

from roma_dspy.core.signatures import SubTask, TaskNode
from roma_dspy.types import TaskStatus, TaskType


class DagBuilderMixin:
    """Mixin that provides subtask graph construction helpers.

    Requires subclass to expose:
    - ``context_store`` (ContextStore)
    - ``_record_module_result(...)``
    """

    # Defines which child task_types are permitted for each parent task_type.
    # None key = root node (no parent type), all children allowed.
    _ALLOWED_CHILD_TYPES: Dict[TaskType, set] = {
        TaskType.RETRIEVE: {TaskType.RETRIEVE, TaskType.THINK},
        TaskType.THINK: {TaskType.THINK, TaskType.RETRIEVE},
        TaskType.WRITE: {TaskType.THINK, TaskType.WRITE},
    }

    # ------------------------------------------------------------------
    # Public: build subgraph from planner output
    # ------------------------------------------------------------------

    def _create_subtask_graph(
        self, task: TaskNode, dag: Any, planner_result: Any
    ) -> TaskNode:
        """Build a subgraph from planner output and attach it to the DAG.

        Supports multi-level topology: when the planner outputs subtasks with
        ``depth`` / ``parent_node_id`` / ``is_leaf`` metadata, only leaf
        subtasks are materialised as TaskNodes.  Non-leaf container nodes are
        skipped and their cross-level dependencies are preserved through the
        original-index-based mapping.
        """
        planner_result = self._normalize_planner_output(planner_result)

        all_subtasks = planner_result.subtasks
        blueprint = self.context_store.get_plan_blueprint()
        is_root_planner = task.depth == 0 and task.parent_id is None
        # Root tasks may freely mix RETRIEVE/THINK/WRITE children; the
        # _ALLOWED_CHILD_TYPES constraint only applies to non-root sub-planners.
        allowed_child_types = (
            None if is_root_planner else self._ALLOWED_CHILD_TYPES.get(task.task_type)
        )

        leaf_mask = self._compute_leaf_mask(all_subtasks)
        has_multi_level = not all(leaf_mask)
        n_original = len(all_subtasks)

        if has_multi_level:
            leaf_count = sum(leaf_mask)
            dropped_summary = [
                f"#{i}({getattr(st, 'task_type', '?')})"
                for i, (st, leaf) in enumerate(zip(all_subtasks, leaf_mask))
                if not leaf
            ]
            logger.info(
                f"[PLANNER] Multi-level topology detected: "
                f"{leaf_count} leaf / {n_original} total subtasks. "
                f"Flattening to leaf-only execution. "
                f"Non-leaf containers skipped: {dropped_summary}"
            )

        subtask_nodes: List[TaskNode] = []
        index_to_task_id: Dict[str, str] = {}

        for idx, subtask in enumerate(all_subtasks):
            if has_multi_level and not leaf_mask[idx]:
                continue

            metadata: Dict[str, Any] = {}

            # Store the planner's output sequence index so that Kahn's topological
            # sort can use it as a stable tiebreaker, preserving the planner's
            # intended chapter order even among parallel (sibling) WRITE nodes.
            metadata["planner_seq_idx"] = idx

            if is_root_planner and blueprint:
                skeleton_nodes = blueprint.topology_skeleton.nodes
                if idx < len(skeleton_nodes):
                    metadata["blueprint_node_id"] = skeleton_nodes[idx].node_id

            # Propagate mandate_checklist from SubTask to TaskNode metadata so
            # the context manager can inject it as a structured XML block into
            # the WRITE executor's context without altering TaskNode's schema.
            mandate_checklist = getattr(subtask, "mandate_checklist", None)
            if mandate_checklist:
                metadata["mandate_checklist"] = mandate_checklist

            effective_task_type = subtask.task_type
            if allowed_child_types and effective_task_type not in allowed_child_types:
                allowed_names = {t.value for t in allowed_child_types}
                logger.warning(
                    f"[PLANNER] Subtask {idx} has illegal task_type={effective_task_type.value} "
                    f"under parent task_type={task.task_type.value}. "
                    f"Allowed: {allowed_names}. "
                    f"Coercing to RETRIEVE."
                )
                effective_task_type = TaskType.RETRIEVE

            subtask_node = TaskNode(
                goal=subtask.goal,
                task_type=effective_task_type,
                parent_id=task.task_id,
                context_input=subtask.context_input,
                dynamic_prompt=getattr(subtask, "dynamic_prompt", None),
                depth=task.depth + 1,
                max_depth=task.max_depth,
                execution_id=task.execution_id or dag.execution_id,
                metadata=metadata,
            )
            subtask_nodes.append(subtask_node)
            index_to_task_id[str(idx)] = subtask_node.task_id

        # Fix A — root planner write-count lock:
        # After all subtask nodes are created, count the committed WRITE tasks
        # and inject that count as a hard constraint into every THINK node's
        # dynamic_prompt so the outline executor cannot produce a Heading
        # Skeleton with more (or fewer) write groups than there are executors.
        if is_root_planner:
            n_write = sum(
                1 for n in subtask_nodes if n.task_type == TaskType.WRITE
            )
            if n_write > 0:
                write_group_labels = ", ".join(f"W{i}" for i in range(1, n_write + 1))
                write_count_constraint = (
                    f"\n\n[SYSTEM CONSTRAINT — WRITE GROUP COUNT LOCK]\n"
                    f"The Heading Skeleton you produce MUST use EXACTLY {n_write} "
                    f"write group(s): {write_group_labels}. "
                    f"The root planner has already committed to {n_write} WRITE "
                    f"executor(s) in the DAG. "
                    f"Using FEWER groups leaves WRITE tasks with nothing to output. "
                    f"Using MORE groups (e.g., W{n_write + 1}) means those sections "
                    f"have NO executor and their content is silently dropped from "
                    f"the final report. Verify your skeleton's distinct "
                    f"assigned_to_write_idx values equal {n_write} before finishing."
                )
                updated_nodes: List[TaskNode] = []
                think_count = 0
                for node in subtask_nodes:
                    if node.task_type == TaskType.THINK:
                        existing_dp = node.dynamic_prompt or ""
                        updated_nodes.append(
                            node.model_copy(
                                update={
                                    "dynamic_prompt": existing_dp + write_count_constraint
                                }
                            )
                        )
                        think_count += 1
                    else:
                        updated_nodes.append(node)
                subtask_nodes = updated_nodes
                if think_count > 0:
                    logger.info(
                        f"[DAG_BUILDER] Injected write-count constraint "
                        f"(n_write={n_write}) into {think_count} THINK node(s) "
                        f"dynamic_prompt for root planner task {task.task_id[:8]}..."
                    )

        task_id_dependencies = self._resolve_dependencies(
            planner_result, subtask_nodes, index_to_task_id,
            parent_task_type=task.task_type,
            n_original=n_original if has_multi_level else None,
        )

        if has_multi_level:
            task_id_dependencies = self._propagate_container_dependencies(
                all_subtasks=all_subtasks,
                leaf_mask=leaf_mask,
                index_to_task_id=index_to_task_id,
                raw_deps=task_id_dependencies or {},
            )

        dag.create_subgraph(task.task_id, subtask_nodes, task_id_dependencies)

        updated_task = dag.get_node(task.task_id)
        subgraph_id = updated_task.subgraph_id

        if subgraph_id:
            for seq_idx, subtask_node in enumerate(subtask_nodes):
                self.context_store.register_index_mapping(
                    subgraph_id, seq_idx, subtask_node.task_id
                )

        self._assert_no_silent_drops(
            parent_task=task,
            all_subtasks=all_subtasks,
            materialized_count=len(subtask_nodes),
            leaf_mask=leaf_mask,
        )

        updated_metrics = task.metrics.model_copy()
        updated_metrics.subtasks_created = len(subtask_nodes)
        return task.model_copy(
            update={"metrics": updated_metrics, "subgraph_id": subgraph_id}
        )

    @staticmethod
    def _assert_no_silent_drops(
        parent_task: TaskNode,
        all_subtasks: List[Any],
        materialized_count: int,
        leaf_mask: List[bool],
    ) -> None:
        """Log an ERROR when planner-declared subtasks silently disappeared.

        The only legitimate way a declared subtask fails to become a TaskNode
        is by being a non-leaf container (``leaf_mask[idx] is False``).  Any
        other shortfall indicates a defect — typically a hierarchy-field
        hallucination that made ``_compute_leaf_mask`` drop a real executable.

        This is a diagnostic log rather than a hard exception so that the
        execution can still proceed with whatever was materialised; the error
        log makes the regression visible immediately in subsequent runs.
        """
        declared = len(all_subtasks)
        legitimate_skips = sum(1 for leaf in leaf_mask if not leaf)
        expected = declared - legitimate_skips
        if materialized_count == expected:
            return

        type_distribution_declared: Dict[str, int] = {}
        type_distribution_materialized: Dict[str, int] = {}
        for idx, (st, leaf) in enumerate(zip(all_subtasks, leaf_mask)):
            tt = getattr(st, "task_type", "?")
            tt_key = getattr(tt, "value", str(tt))
            type_distribution_declared[tt_key] = (
                type_distribution_declared.get(tt_key, 0) + 1
            )
            if leaf:
                type_distribution_materialized[tt_key] = (
                    type_distribution_materialized.get(tt_key, 0) + 1
                )

        logger.error(
            "[_create_subtask_graph] Silent subtask drop detected for parent "
            "%s (task_type=%s, depth=%d): declared=%d, expected_after_container_skip=%d, "
            "materialized=%d, container_skips=%d. "
            "Declared type distribution=%s; materialized type distribution=%s. "
            "This almost always indicates a hierarchy-field hallucination that "
            "escaped normalisation — inspect planner output and _compute_leaf_mask.",
            parent_task.task_id[:8],
            parent_task.task_type.value if parent_task.task_type else "?",
            parent_task.depth,
            declared,
            expected,
            materialized_count,
            legitimate_skips,
            type_distribution_declared,
            type_distribution_materialized,
        )

    # ------------------------------------------------------------------
    # Public: apply a user-approved plan without re-running the planner
    # ------------------------------------------------------------------

    def apply_approved_plan(
        self, task: TaskNode, dag: Any, approved_plan: Dict[str, Any]
    ) -> TaskNode:
        """Apply a user-approved root plan without re-running the root planner."""
        report_policy = (approved_plan.get("directives") or {}).get("report_policy")
        if report_policy:
            self.context_store.store_report_policy(report_policy)
        filtered_directives = {"report_policy": report_policy}

        task = self._record_module_result(
            task,
            "approved_plan",
            task.goal,
            {
                "plan_id": approved_plan.get("plan_id"),
                "subtasks": [
                    subtask.model_dump()
                    if hasattr(subtask, "model_dump")
                    else subtask
                    for subtask in approved_plan.get("subtasks", [])
                ],
                "dependencies": approved_plan.get("dependencies_graph"),
                "directives": filtered_directives,
                "user_notes": approved_plan.get("user_notes"),
                "clarifications": approved_plan.get("clarifications", []),
            },
            duration=0.0,
            metadata={"source": "user_approved_plan"},
        )

        raw_subtasks = approved_plan.get("subtasks", [])
        subtask_objects = [
            SubTask.model_validate(s) if isinstance(s, dict) else s
            for s in raw_subtasks
        ]
        planner_like_result = SimpleNamespace(
            subtasks=subtask_objects,
            dependencies_graph=approved_plan.get("dependencies_graph"),
        )
        task = self._create_subtask_graph(task, dag, planner_like_result)
        if task.status == TaskStatus.PENDING:
            task = task.transition_to(TaskStatus.ATOMIZING)
        if task.status == TaskStatus.ATOMIZING:
            task = task.transition_to(TaskStatus.PLANNING)
        task = task.transition_to(TaskStatus.PLAN_DONE)
        dag.update_node(task)
        return task

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    # Hierarchy fields that the planner LLM must NEVER author.
    # These are declared as ``SkipJsonSchema`` on ``SubTask`` so DSPy does not
    # advertise them to the LLM, but the LLM can still hallucinate them from
    # in-context demos or training data.  On the planner path (``plan_async``
    # and ``apply_approved_plan``) hierarchy is the sole responsibility of
    # the system — any LLM-authored value is a defect.
    # MIPE / blueprint path bypasses ``_normalize_planner_output`` entirely
    # (it goes through ``_try_load_prebuilt_subtasks`` / the assembler),
    # so legitimate hierarchy set by ``_ensure_hierarchy_before_blueprint``
    # is never touched by this stripping.
    _HIERARCHY_FIELDS = ("depth", "parent_node_id", "children_ids", "is_leaf")
    _HIERARCHY_DEFAULTS = {
        "depth": 0,
        "parent_node_id": None,
        "children_ids": [],
        "is_leaf": False,
    }

    @classmethod
    def _normalize_planner_output(cls, planner_result: Any) -> Any:
        """Fix common ChatAdapter parsing issues in planner output.

        DSPy ChatAdapter may:
        - Return int values instead of str in dependencies_graph / SubTask.dependencies
        - Return a JSON string instead of a parsed dict for dependencies_graph
        - Return None for dependencies_graph while SubTask.dependencies has data

        Also strips any hierarchy fields (``depth`` / ``parent_node_id`` /
        ``children_ids`` / ``is_leaf``) that the planner LLM may have hallucinated
        despite those fields being declared as ``SkipJsonSchema``.  LLM-authored
        hierarchy has no legitimate semantics on this code path and, if retained,
        causes ``_compute_leaf_mask`` to silently drop real executable subtasks
        (e.g. a local-outline THINK labelled ``is_leaf=False`` with fabricated
        ``children_ids`` pointing at sibling WRITE chapters).
        """
        dg = getattr(planner_result, "dependencies_graph", None)

        if isinstance(dg, str):
            try:
                dg = _json.loads(dg)
                planner_result.dependencies_graph = dg
                logger.debug("[normalize] Parsed dependencies_graph from JSON string")
            except (ValueError, TypeError):
                planner_result.dependencies_graph = None
                dg = None

        if isinstance(dg, dict):
            normalized: Dict[str, List[str]] = {}
            for k, v in dg.items():
                str_key = str(k)
                if isinstance(v, (list, tuple)):
                    normalized[str_key] = [str(x) for x in v]
                elif isinstance(v, str):
                    try:
                        parsed = _json.loads(v)
                        if isinstance(parsed, list):
                            normalized[str_key] = [str(x) for x in parsed]
                        else:
                            normalized[str_key] = [str(parsed)]
                    except (ValueError, TypeError):
                        normalized[str_key] = [v]
                else:
                    normalized[str_key] = []
            planner_result.dependencies_graph = normalized

        subtasks = getattr(planner_result, "subtasks", [])
        hallucinated_counts = {f: 0 for f in cls._HIERARCHY_FIELDS}
        for subtask in subtasks:
            deps = getattr(subtask, "dependencies", None)
            if deps is not None:
                if isinstance(deps, str):
                    try:
                        deps = _json.loads(deps)
                    except (ValueError, TypeError):
                        deps = []
                if isinstance(deps, (list, tuple)):
                    subtask.dependencies = [str(x) for x in deps]

            for field_name in cls._HIERARCHY_FIELDS:
                current = getattr(subtask, field_name, None)
                default = cls._HIERARCHY_DEFAULTS[field_name]
                if current != default and current not in (None, [], 0, False):
                    hallucinated_counts[field_name] += 1
                try:
                    # Always construct a fresh list for mutable defaults so
                    # each subtask gets its own independent instance.
                    value = list(default) if isinstance(default, list) else default
                    setattr(subtask, field_name, value)
                except (AttributeError, TypeError, ValueError):
                    pass

        total_hallucinated = sum(hallucinated_counts.values())
        if total_hallucinated:
            logger.warning(
                "[normalize] Stripped %d hallucinated hierarchy field value(s) "
                "from planner output (per-field counts: %s). The planner LLM is "
                "not permitted to author hierarchy on this path; these values "
                "would otherwise cause _compute_leaf_mask to drop real subtasks.",
                total_hallucinated,
                hallucinated_counts,
            )

        return planner_result

    # ------------------------------------------------------------------
    # Multi-level topology helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_leaf_mask(subtasks: Any) -> List[bool]:
        """Determine which subtasks are execution leaves in a multi-level plan.

        A subtask is a non-leaf container only when its declared ``children_ids``
        are **cross-validated** by those children's ``parent_node_id`` pointing
        back at it.  Relying on ``children_ids`` alone is unreliable: the LLM
        can hallucinate a non-empty ``children_ids`` list (especially for a
        local-outline THINK node whose sibling WRITE chapters merely *depend*
        on it) and that would silently drop the THINK from execution.
        Relying on ``parent_node_id`` alone is equally unreliable because
        planners sometimes use it to express a *dependency* rather than a
        real hierarchical parent.

        Decision logic per subtask:
          1. ``is_leaf == True``                → executable leaf (materialized)
          2. ``children_ids`` non-empty AND at least one declared child's
             ``parent_node_id`` points back at self  → non-leaf container (skipped)
          3. Everything else                    → executable leaf (materialized)

        When no multi-level metadata is detected the entire list is treated as
        leaves (backward-compatible).  When ``children_ids`` fails the
        cross-check a warning is emitted so the discrepancy is visible in logs.
        """
        n = len(subtasks)

        has_hierarchy = any(
            getattr(s, "depth", 0) > 0
            or getattr(s, "parent_node_id", None) is not None
            or bool(getattr(s, "children_ids", []))
            for s in subtasks
        )
        if not has_hierarchy:
            return [True] * n

        # Build index → set-of-children corroborated by parent_node_id pointers.
        # A single pass is enough: iterate children, accumulate under their parent.
        inferred_children: Dict[int, set] = {i: set() for i in range(n)}
        for child_idx, s in enumerate(subtasks):
            pid_raw = getattr(s, "parent_node_id", None)
            if pid_raw is None:
                continue
            try:
                pid_int = int(pid_raw)
            except (ValueError, TypeError):
                continue
            if 0 <= pid_int < n and pid_int != child_idx:
                inferred_children[pid_int].add(child_idx)

        mask: List[bool] = []
        for idx, s in enumerate(subtasks):
            is_leaf_flag = getattr(s, "is_leaf", False)
            declared_children_raw = getattr(s, "children_ids", []) or []

            if is_leaf_flag:
                mask.append(True)
                continue

            declared_children: set = set()
            for cid in declared_children_raw:
                try:
                    declared_children.add(int(cid))
                except (ValueError, TypeError):
                    continue

            if not declared_children:
                # No declared children at all — treat as leaf.
                # Warn when other subtasks point at this node via parent_node_id
                # (the planner probably conflated dependency with hierarchy).
                if inferred_children[idx]:
                    logger.warning(
                        f"[_compute_leaf_mask] Subtask {idx} "
                        f"(task_type={getattr(s, 'task_type', '?')}) "
                        f"is referenced as parent by other subtasks via "
                        f"parent_node_id but its own children_ids is empty. "
                        f"Treating as an executable leaf. "
                        f"Check whether the planner used parent_node_id to "
                        f"express a dependency instead of hierarchy."
                    )
                mask.append(True)
                continue

            # Cross-validate: at least one declared child must point back at
            # this node via parent_node_id.  Otherwise the ``children_ids``
            # list is almost certainly a planner hallucination (e.g. a local
            # outline THINK listing its sibling WRITE chapters because they
            # depend on it, not because they are its hierarchical children).
            confirmed_children = declared_children & inferred_children[idx]
            if confirmed_children:
                mask.append(False)
            else:
                logger.warning(
                    f"[_compute_leaf_mask] Subtask {idx} "
                    f"(task_type={getattr(s, 'task_type', '?')}) "
                    f"declared children_ids={sorted(declared_children)} but "
                    f"none of those nodes list {idx} as their parent_node_id. "
                    f"Treating children_ids as hallucinated and materialising "
                    f"this node as an executable leaf to avoid silently "
                    f"dropping it from execution."
                )
                mask.append(True)

        if not any(mask):
            logger.warning(
                "[_compute_leaf_mask] No leaf subtasks detected — "
                "falling back to treating all subtasks as leaves."
            )
            return [True] * n

        return mask

    @staticmethod
    def _propagate_container_dependencies(
        all_subtasks: List[Any],
        leaf_mask: List[bool],
        index_to_task_id: Dict[str, str],
        raw_deps: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        """Propagate container-level dependencies to leaf children.

        When a non-leaf container C depends on containers [D1, D2, ...], all
        leaf children of C must complete after all leaves of D1, D2, ...

        Sibling-skip optimisation: if a leaf already has a dep on a sibling
        (another leaf with the same parent container) in raw_deps, it will
        reach the container deps transitively through that sibling.  We skip
        adding them explicitly, eliminating redundant edges.

        The check is always against raw_deps (pre-propagation state) so the
        result is independent of iteration order.
        """
        # Step 1: build two auxiliary maps in a single pass
        container_to_leaves: Dict[int, List[str]] = {}  # container_idx → leaf task_ids
        tid_to_parent: Dict[str, int] = {}              # leaf task_id → parent container_idx

        for idx, (st, is_leaf) in enumerate(zip(all_subtasks, leaf_mask)):
            if not is_leaf:
                continue
            parent_str = getattr(st, "parent_node_id", None)
            if parent_str is None:
                continue  # depth-0 leaf (e.g. task4): no container to register under
            try:
                parent_idx = int(parent_str)
            except (ValueError, TypeError):
                continue
            task_id = index_to_task_id.get(str(idx))
            if not task_id:
                continue
            container_to_leaves.setdefault(parent_idx, []).append(task_id)
            tid_to_parent[task_id] = parent_idx

        # Step 2: propagate container deps to each leaf that needs them
        result: Dict[str, List[str]] = dict(raw_deps)

        for idx, (st, is_leaf) in enumerate(zip(all_subtasks, leaf_mask)):
            if not is_leaf:
                continue
            parent_str = getattr(st, "parent_node_id", None)
            if parent_str is None:
                continue  # depth-0 leaf: nothing to inherit

            leaf_tid = index_to_task_id.get(str(idx))
            if not leaf_tid:
                continue

            try:
                parent_idx = int(parent_str)
            except (ValueError, TypeError):
                continue

            parent_deps = getattr(all_subtasks[parent_idx], "dependencies", [])
            if not parent_deps:
                continue  # container has no upstream deps → nothing to inherit

            # Sibling-skip optimisation: if this leaf already has a dep on any
            # sibling (same parent container) in the *original* raw_deps, the
            # container deps will reach it transitively through that sibling.
            # Always check raw_deps (not result) to stay order-independent.
            original_deps = raw_deps.get(leaf_tid, [])
            if any(tid_to_parent.get(d) == parent_idx for d in original_deps):
                logger.debug(
                    f"[propagate_container_deps] Leaf idx={idx} skipped: "
                    f"sibling dep found → container deps inherited transitively"
                )
                continue

            # Collect all leaf descendants of each upstream container
            inherited: List[str] = []
            for dep_str in parent_deps:
                try:
                    dep_idx = int(dep_str)
                except (ValueError, TypeError):
                    continue
                if dep_idx < 0 or dep_idx >= len(all_subtasks):
                    continue

                if leaf_mask[dep_idx]:
                    # Upstream is itself a leaf (depth-0 leaf like task4)
                    dep_tid = index_to_task_id.get(str(dep_idx))
                    if dep_tid and dep_tid != leaf_tid:
                        inherited.append(dep_tid)
                else:
                    # Upstream is a container → expand to all its leaf descendants
                    for leaf_id in container_to_leaves.get(dep_idx, []):
                        if leaf_id != leaf_tid:
                            inherited.append(leaf_id)

            if inherited:
                existing = set(result.get(leaf_tid, []))
                new_deps = set(inherited) - existing - {leaf_tid}
                if new_deps:
                    result[leaf_tid] = list(existing | new_deps)
                    logger.debug(
                        f"[propagate_container_deps] Leaf idx={idx} "
                        f"(parent_container={parent_idx}) inherited "
                        f"{len(new_deps)} deps"
                    )

        return result

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_index_deps(
        raw_graph: Dict,
        num_subtasks: int,
        index_to_task_id: Dict[str, str],
    ) -> Dict[str, List[str]]:
        """Parse a dependencies_graph dict into {task_id: [dep_task_ids]}.

        Handles int/str key/value mismatches robustly.
        """
        result: Dict[str, List[str]] = {}
        for subtask_idx_raw, dep_indices in raw_graph.items():
            try:
                subtask_idx = int(subtask_idx_raw)
                if subtask_idx < 0 or subtask_idx >= num_subtasks:
                    continue
            except (ValueError, TypeError):
                continue

            subtask_key = str(subtask_idx)
            if subtask_key not in index_to_task_id:
                continue
            subtask_task_id = index_to_task_id[subtask_key]

            if not isinstance(dep_indices, (list, tuple)):
                continue

            dep_task_ids: List[str] = []
            for dep_idx in dep_indices:
                try:
                    dep_idx_int = int(dep_idx)
                    if dep_idx_int == subtask_idx:
                        continue
                    if dep_idx_int < 0 or dep_idx_int >= num_subtasks:
                        continue
                    dep_key = str(dep_idx_int)
                    if dep_key in index_to_task_id:
                        dep_task_ids.append(index_to_task_id[dep_key])
                except (ValueError, TypeError):
                    continue
            if dep_task_ids:
                result[subtask_task_id] = dep_task_ids
        return result

    @staticmethod
    def _deps_from_subtask_fields(
        subtasks: Any,
        num_subtasks: int,
        index_to_task_id: Dict[str, str],
    ) -> Dict[str, List[str]]:
        """Build {task_id: [dep_task_ids]} from each SubTask.dependencies field."""
        result: Dict[str, List[str]] = {}
        for idx, subtask in enumerate(subtasks):
            deps = getattr(subtask, "dependencies", None)
            if not deps:
                continue
            subtask_key = str(idx)
            if subtask_key not in index_to_task_id:
                continue
            subtask_task_id = index_to_task_id[subtask_key]
            dep_task_ids: List[str] = []
            for dep in deps:
                try:
                    dep_int = int(dep)
                    if dep_int == idx or dep_int < 0 or dep_int >= num_subtasks:
                        continue
                    dep_key = str(dep_int)
                    if dep_key in index_to_task_id:
                        dep_task_ids.append(index_to_task_id[dep_key])
                except (ValueError, TypeError):
                    continue
            if dep_task_ids:
                result[subtask_task_id] = dep_task_ids
        return result

    def _resolve_dependencies(
        self,
        planner_result: Any,
        subtask_nodes: List[TaskNode],
        index_to_task_id: Dict[str, str],
        parent_task_type: Optional[TaskType] = None,
        n_original: Optional[int] = None,
    ) -> Optional[Dict[str, List[str]]]:
        """Merge dependencies from both sources with cross-validation.

        Priority: dependencies_graph is primary, SubTask.dependencies fills gaps.
        If neither source has data, falls back to implicit type-based inference.

        Args:
            n_original: When set, the original subtask count (before leaf
                filtering) is used for index-range validation so that
                cross-level references in multi-level plans are not
                silently dropped.
        """
        n = n_original or len(subtask_nodes)

        parallel_wc = True
        cfg = getattr(self, "config", None)
        if cfg and hasattr(cfg, "runtime"):
            parallel_wc = getattr(cfg.runtime, "parallel_write_chapters", True)

        from_graph = self._parse_index_deps(
            planner_result.dependencies_graph or {}, n, index_to_task_id
        )
        from_fields = self._deps_from_subtask_fields(
            planner_result.subtasks, n, index_to_task_id
        )

        has_graph = bool(from_graph)
        has_fields = bool(from_fields)

        if not has_graph and not has_fields:
            implicit = self._infer_implicit_dependencies(
                subtask_nodes, parent_task_type,
                parallel_write_chapters=parallel_wc,
            )
            if implicit:
                logger.info(
                    f"[_resolve_deps] Inferred {len(implicit)} implicit dependencies "
                    f"from task_type ordering: "
                    f"{ {k[:8]: [v[:8] for v in vs] for k, vs in implicit.items()} }"
                )
                return implicit

            # Check whether all subtasks share the same type — if so, parallel
            # execution is the correct and expected behaviour (e.g. N×RETRIEVE
            # sub-tasks fan out independently).  Only warn when mixed types have
            # no ordering, which is the genuinely suspicious case.
            unique_types = {n.task_type for n in subtask_nodes}
            if len(unique_types) <= 1:
                logger.info(
                    f"[_resolve_deps] No dependency data provided; all {len(subtask_nodes)} "
                    f"subtask(s) are type={next(iter(unique_types), '?')} — "
                    f"parallel execution is correct, no ordering needed."
                )
            else:
                logger.warning(
                    "[_resolve_deps] Neither dependencies_graph nor SubTask.dependencies "
                    "contain any dependency and implicit inference yielded nothing — "
                    f"mixed-type subtasks ({', '.join(t.value for t in unique_types)}) "
                    "will execute in parallel! Check planner output."
                )
            return None

        merged: Dict[str, List[str]] = {}
        all_task_ids = set(from_graph.keys()) | set(from_fields.keys())
        for tid in all_task_ids:
            g_deps = set(from_graph.get(tid, []))
            f_deps = set(from_fields.get(tid, []))
            merged[tid] = list(g_deps | f_deps)

        if has_graph and has_fields:
            only_in_graph = {tid for tid in from_graph if tid not in from_fields}
            only_in_fields = {tid for tid in from_fields if tid not in from_graph}
            if only_in_graph or only_in_fields:
                logger.warning(
                    f"[_resolve_deps] Mismatch between sources — "
                    f"only in dependencies_graph: {len(only_in_graph)}, "
                    f"only in SubTask.dependencies: {len(only_in_fields)}. "
                    f"Using union of both."
                )
            else:
                logger.info("[_resolve_deps] Both sources consistent.")
        elif has_graph:
            logger.info(
                f"[_resolve_deps] Using dependencies_graph ({len(from_graph)} entries)"
            )
        else:
            logger.info(
                f"[_resolve_deps] dependencies_graph empty, using "
                f"SubTask.dependencies ({len(from_fields)} entries)"
            )

        # Supplement with implicit dependencies for nodes that have none
        implicit = self._infer_implicit_dependencies(
            subtask_nodes, parent_task_type,
            parallel_write_chapters=parallel_wc,
        )
        if implicit:
            supplemented = 0
            for tid, dep_ids in implicit.items():
                if tid not in merged:
                    merged[tid] = dep_ids
                    supplemented += 1
            if supplemented:
                logger.info(
                    f"[_resolve_deps] Supplemented {supplemented} nodes with "
                    f"implicit type-based dependencies"
                )

        logger.info(
            f"[_resolve_deps] Final merged dependencies: "
            f"{ {k[:8]: [v[:8] for v in vs] for k, vs in merged.items()} }"
        )
        return merged

    @staticmethod
    def _infer_implicit_dependencies(
        subtask_nodes: List[TaskNode],
        parent_task_type: Optional["TaskType"] = None,
        parallel_write_chapters: bool = True,
    ) -> Dict[str, List[str]]:
        """Infer dependencies from task_type ordering when Planner omits them.

        Rules:
        - THINK nodes should depend on all RETRIEVE nodes (need evidence to analyze)
        - WRITE nodes should depend on all THINK nodes (need analysis to write)
        - If no THINK exists, WRITE depends on all RETRIEVE nodes directly
        - When ``parallel_write_chapters`` is True (default), WRITE children
          under a WRITE parent all depend only on upstream THINK nodes and
          execute in parallel.  The local outline THINK guarantees coherence.
        - When ``parallel_write_chapters`` is False, child WRITE nodes under
          a WRITE parent are chained sequentially (legacy behavior).
        """
        retrieve_ids: List[str] = []
        think_ids: List[str] = []
        write_ids: List[str] = []

        for node in subtask_nodes:
            if node.task_type == TaskType.RETRIEVE:
                retrieve_ids.append(node.task_id)
            elif node.task_type == TaskType.THINK:
                think_ids.append(node.task_id)
            elif node.task_type == TaskType.WRITE:
                write_ids.append(node.task_id)

        implicit: Dict[str, List[str]] = {}

        if retrieve_ids and think_ids:
            for tid in think_ids:
                implicit[tid] = list(retrieve_ids)

        chain_writes = (
            parent_task_type == TaskType.WRITE
            and len(write_ids) >= 2
            and not parallel_write_chapters
        )

        if chain_writes:
            base_deps = list(think_ids) if think_ids else list(retrieve_ids)
            for i, wid in enumerate(write_ids):
                if i == 0:
                    implicit[wid] = list(base_deps) if base_deps else []
                else:
                    deps = list(base_deps) + [write_ids[i - 1]]
                    implicit[wid] = deps
            logger.info(
                f"[_infer_implicit_deps] Chained {len(write_ids)} WRITE nodes "
                f"sequentially (parent_task_type=WRITE, parallel_write_chapters=False)"
            )
        else:
            if think_ids and write_ids:
                for wid in write_ids:
                    implicit[wid] = list(think_ids)
            elif retrieve_ids and write_ids:
                for wid in write_ids:
                    implicit[wid] = list(retrieve_ids)

        return implicit
