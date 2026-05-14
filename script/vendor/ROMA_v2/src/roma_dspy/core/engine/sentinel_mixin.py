"""Sentinel phase-boundary checkpoint integration extracted from ModuleRuntime."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from loguru import logger

from roma_dspy.types import AgentType, TaskType

if TYPE_CHECKING:
    from roma_dspy.core.engine.dag import TaskDAG
    from roma_dspy.core.signatures import TaskNode

_PH_RE = re.compile(r"\{(\w+)\}")


class SentinelMixin:
    """Mixin that provides Sentinel checkpoint integration.

    Requires subclass to expose:
    - ``context_store`` (ContextStore)
    - ``config`` (optional Roma config)
    - ``registry`` (AgentRegistry, for subtree_replan planner access)
    """

    # ------------------------------------------------------------------
    # Phase-boundary Sentinel dispatch
    # ------------------------------------------------------------------

    async def _maybe_run_sentinel(
        self,
        subgraph: "TaskDAG",
        completed: Set[str],
        pending: Set[str],
        checkpoint_type: str,
    ) -> bool:
        """Run Sentinel checkpoint if the relevant phase is fully completed.

        Returns True if the checkpoint was triggered (regardless of outcome).
        When ``subtree_replan`` is triggered, *pending* is mutated in place
        to reflect any newly added tasks.
        """
        if not self._is_sentinel_enabled():
            return True

        blueprint = self.context_store.get_plan_blueprint()
        if not blueprint:
            return False

        all_tasks = {
            tid: subgraph.get_node(tid)
            for tid in list(completed) + list(pending)
        }

        if checkpoint_type == "post_retrieve":
            target_type = TaskType.RETRIEVE
        elif checkpoint_type == "post_think":
            target_type = TaskType.THINK
        else:
            return False

        phase_tasks = [t for t in all_tasks.values() if t.task_type == target_type]
        if not phase_tasks:
            return False

        all_phase_done = all(t.task_id in completed for t in phase_tasks)
        if not all_phase_done:
            return False

        has_remaining_of_type = any(
            t.task_id in pending and t.task_type == target_type
            for t in all_tasks.values()
        )
        if has_remaining_of_type:
            return False

        try:
            from roma_dspy.core.engine.sentinel import SentinelCheckpoint, NodeResult

            sentinel_config = self._get_sentinel_config()
            sentinel = SentinelCheckpoint(
                blueprint=blueprint,
                context_store=self.context_store,
                sentinel_config=sentinel_config,
                roma_config=self.config,
                mlflow_manager=self.context_store.get_mlflow_manager(),
            )

            node_results = []
            for task in phase_tasks:
                result_text = self.context_store.get_result(task.task_id) or ""
                quality_signals = SentinelCheckpoint.collect_quality_signals(
                    result_text, task.task_type.value
                )
                bp_node_id = (task.metadata or {}).get(
                    "blueprint_node_id", task.task_id[:8]
                )
                node_results.append(
                    NodeResult(
                        node_id=bp_node_id,
                        task_id=task.task_id,
                        goal=task.goal,
                        result_summary=result_text[:500] if result_text else "",
                        quality_signals=quality_signals,
                    )
                )

            root_goal = self._get_root_goal(subgraph, all_tasks)

            if checkpoint_type == "post_retrieve":
                sentinel_result = await sentinel.check_post_retrieve(
                    node_results, root_goal
                )
            else:
                sentinel_result = await sentinel.check_post_think(
                    node_results, root_goal
                )

            if not sentinel_result.adjustment_needed:
                return True

            if sentinel_result.adjustment_type == "directive_only":
                self._apply_sentinel_prompt_adjustments(
                    subgraph, pending, sentinel_result, blueprint
                )
            elif sentinel_result.adjustment_type == "subtree_replan":
                await self._execute_subtree_replan(
                    subgraph, completed, pending,
                    sentinel_result, sentinel, root_goal,
                )
            elif sentinel_result.adjustment_type == "topology_change":
                logger.info(
                    f"[SENTINEL] topology_change requested "
                    f"for nodes: {sentinel_result.affected_nodes}. "
                    f"Guidance: {(sentinel_result.replan_guidance or '')[:200]}"
                )

            return True

        except Exception as e:
            logger.warning(f"[SENTINEL] {checkpoint_type} integration failed: {e}")
            return True

    # ------------------------------------------------------------------
    # directive_only: prompt overrides + placeholder filling
    # ------------------------------------------------------------------

    def _apply_sentinel_prompt_adjustments(
        self,
        subgraph: "TaskDAG",
        pending: Set[str],
        sentinel_result: Any,
        blueprint: Any,
    ) -> None:
        """Apply Sentinel's prompt adjustments to pending tasks.

        Two modes (checked in priority order):
        1. ``prompt_overrides``: full replacement of dynamic_prompt per node_id
        2. ``directive_fill_hints``: legacy placeholder filling via TemplateFiller
        """
        overrides = sentinel_result.prompt_overrides
        fill_hints = sentinel_result.directive_fill_hints
        affected = set(sentinel_result.affected_nodes or [])

        override_count = 0
        fill_count = 0

        # --- Mode 1: Full prompt overrides (preferred) ---
        if overrides:
            override_count = self._apply_prompt_overrides(
                subgraph, pending, overrides, affected
            )

        # --- Mode 2: Placeholder filling (legacy / fallback) ---
        if fill_hints:
            fill_count = self._apply_placeholder_fills(
                subgraph, pending, fill_hints, blueprint
            )

        total = override_count + fill_count
        if total:
            logger.info(
                f"[SENTINEL] directive_only: {override_count} prompt overrides, "
                f"{fill_count} placeholder fills applied"
            )
        else:
            logger.info(
                "[SENTINEL] directive_only: no prompt changes applied "
                "(no matching pending tasks or empty hints)"
            )

    def _apply_prompt_overrides(
        self,
        subgraph: "TaskDAG",
        pending: Set[str],
        overrides: Dict[str, str],
        affected_nodes: Set[str],
    ) -> int:
        """Directly replace dynamic_prompt on pending tasks by node_id.

        ``overrides`` maps blueprint node_id (e.g. "3") → new prompt text.
        We match pending tasks via their ``blueprint_node_id`` metadata.
        """
        node_id_to_task: Dict[str, "TaskNode"] = {}
        for tid in pending:
            task = subgraph.get_node(tid)
            bp_id = (task.metadata or {}).get("blueprint_node_id")
            if bp_id is not None:
                node_id_to_task[str(bp_id)] = task

        count = 0
        for node_id, new_prompt in overrides.items():
            if not new_prompt or not new_prompt.strip():
                continue
            task = node_id_to_task.get(str(node_id))
            if task is None:
                logger.debug(
                    f"[SENTINEL] prompt_override for node {node_id} — "
                    f"no matching pending task found, skipping"
                )
                continue

            old_prompt = task.dynamic_prompt or ""
            if new_prompt.strip() == old_prompt.strip():
                continue

            updated = task.model_copy(update={"dynamic_prompt": new_prompt.strip()})
            subgraph.update_node(updated)
            count += 1
            logger.debug(
                f"[SENTINEL] Overwrote dynamic_prompt for node {node_id} "
                f"(task {task.task_id[:8]}): "
                f"{len(old_prompt)}→{len(new_prompt)} chars"
            )

        return count

    def _apply_placeholder_fills(
        self,
        subgraph: "TaskDAG",
        pending: Set[str],
        fill_hints: Dict[str, str],
        blueprint: Any,
    ) -> int:
        """Fill {placeholder} tokens in pending tasks' dynamic_prompts."""
        from roma_dspy.core.engine.template_filler import TemplateFiller

        filler = TemplateFiller()

        retrieve_results: Dict[str, str] = {}
        for tid in list(subgraph.graph.nodes()):
            task = subgraph.get_node(tid)
            if task.task_type == TaskType.RETRIEVE and tid not in pending:
                result = self.context_store.get_result(tid)
                if result:
                    retrieve_results[tid] = result

        fill_values = filler.fill_from_retrieve_results(
            retrieve_results, blueprint, fill_hints
        )

        if not fill_values:
            return 0

        filled_count = 0
        for tid in pending:
            task = subgraph.get_node(tid)
            if not task.dynamic_prompt:
                continue

            placeholders = set(_PH_RE.findall(task.dynamic_prompt))
            if not placeholders:
                continue

            new_prompt = task.dynamic_prompt
            for ph in placeholders:
                if ph in fill_values:
                    new_prompt = new_prompt.replace(f"{{{ph}}}", fill_values[ph])

            if new_prompt != task.dynamic_prompt:
                updated = task.model_copy(update={"dynamic_prompt": new_prompt})
                subgraph.update_node(updated)
                filled_count += 1

        return filled_count

    # ------------------------------------------------------------------
    # subtree_replan: targeted Planner call on affected pending tasks
    # ------------------------------------------------------------------

    async def _execute_subtree_replan(
        self,
        subgraph: "TaskDAG",
        completed: Set[str],
        pending: Set[str],
        sentinel_result: Any,
        sentinel: Any,
        root_goal: str,
    ) -> None:
        """Execute a targeted replan for nodes flagged by the Sentinel.

        Flow:
        1. Obtain a Planner agent from the registry
        2. Call micro_replan with Sentinel's guidance
        3. Map returned subtasks to affected pending nodes
        4. Update goals, dynamic_prompts, and optionally task_types
        """
        affected_ids = sentinel_result.affected_nodes or []
        guidance = sentinel_result.replan_guidance or ""

        if not affected_ids:
            logger.info("[SENTINEL] subtree_replan: no affected_nodes specified, skipping")
            return

        # Resolve affected task_ids from blueprint node_ids
        affected_tasks = self._resolve_affected_tasks(subgraph, pending, affected_ids)
        if not affected_tasks:
            logger.warning(
                "[SENTINEL] subtree_replan: could not match any affected_nodes "
                f"{affected_ids} to pending tasks, falling back to directive_only"
            )
            if sentinel_result.prompt_overrides or sentinel_result.directive_fill_hints:
                blueprint = self.context_store.get_plan_blueprint()
                self._apply_sentinel_prompt_adjustments(
                    subgraph, pending, sentinel_result, blueprint
                )
            return

        planner_agent = self._get_planner_for_replan()
        if planner_agent is None:
            logger.warning(
                "[SENTINEL] subtree_replan: no Planner agent available, "
                "falling back to prompt overrides"
            )
            if sentinel_result.prompt_overrides:
                blueprint = self.context_store.get_plan_blueprint()
                self._apply_sentinel_prompt_adjustments(
                    subgraph, pending, sentinel_result, blueprint
                )
            return

        replan_result = await sentinel.micro_replan(
            affected_task_ids=[t.task_id for t in affected_tasks],
            replan_guidance=guidance,
            goal=root_goal,
            planner_agent=planner_agent,
        )

        if replan_result is None:
            logger.warning("[SENTINEL] micro_replan returned None, no changes applied")
            return

        new_subtasks = getattr(replan_result, "subtasks", None) or []
        if not new_subtasks:
            logger.warning("[SENTINEL] micro_replan returned empty subtasks")
            return

        self._apply_replan_to_pending(
            subgraph, pending, affected_tasks, new_subtasks
        )

    def _resolve_affected_tasks(
        self,
        subgraph: "TaskDAG",
        pending: Set[str],
        affected_node_ids: List[str],
    ) -> List["TaskNode"]:
        """Map blueprint node_ids from Sentinel to actual pending TaskNodes."""
        affected_set = {str(nid) for nid in affected_node_ids}
        matched: List["TaskNode"] = []

        for tid in pending:
            task = subgraph.get_node(tid)
            bp_id = (task.metadata or {}).get("blueprint_node_id")
            if bp_id is not None and str(bp_id) in affected_set:
                matched.append(task)

        return matched

    def _get_planner_for_replan(self) -> Optional[Any]:
        """Obtain a Planner agent from the registry for micro-replan."""
        if not hasattr(self, "registry") or self.registry is None:
            return None
        try:
            return self.registry.get_agent(AgentType.PLANNER, None)
        except (KeyError, Exception) as e:
            logger.debug(f"[SENTINEL] Could not get Planner agent: {e}")
            return None

    def _apply_replan_to_pending(
        self,
        subgraph: "TaskDAG",
        pending: Set[str],
        affected_tasks: List["TaskNode"],
        new_subtasks: List[Any],
    ) -> None:
        """Map replanned subtasks onto existing affected pending nodes.

        For each position ``i``:
        - If ``i < len(affected_tasks)``: update the existing task in place
        - If ``i >= len(affected_tasks)``: add a new node to the subgraph
          and insert it into ``pending``

        This preserves the existing DAG topology for matched tasks while
        allowing the planner to inject additional tasks when needed.
        """
        updated_count = 0
        added_count = 0

        for i, new_st in enumerate(new_subtasks):
            new_goal = getattr(new_st, "goal", None) or ""
            new_dp = getattr(new_st, "dynamic_prompt", None)
            new_type = getattr(new_st, "task_type", None)

            if i < len(affected_tasks):
                # Update existing task
                task = affected_tasks[i]
                update_fields: Dict[str, Any] = {}

                if new_goal and new_goal.strip():
                    update_fields["goal"] = new_goal.strip()
                if new_dp is not None:
                    update_fields["dynamic_prompt"] = new_dp.strip() if new_dp else None
                if new_type is not None and new_type != task.task_type:
                    update_fields["task_type"] = new_type

                if update_fields:
                    updated = task.model_copy(update=update_fields)
                    subgraph.update_node(updated)
                    updated_count += 1
                    logger.debug(
                        f"[SENTINEL] Replanned task {task.task_id[:8]}: "
                        f"fields={list(update_fields.keys())}"
                    )
            else:
                # Add new task — use first affected task as reference for
                # parent_id, depth, execution_id
                ref = affected_tasks[0]
                from roma_dspy.core.signatures import TaskNode

                new_task = TaskNode(
                    goal=new_goal.strip() if new_goal else "replanned subtask",
                    task_type=new_type or TaskType.THINK,
                    parent_id=ref.parent_id,
                    dynamic_prompt=new_dp.strip() if new_dp else None,
                    depth=ref.depth,
                    max_depth=ref.max_depth,
                    execution_id=ref.execution_id,
                    metadata={"source": "sentinel_replan"},
                )
                subgraph.add_node(new_task)

                # Wire dependency: new task depends on last affected task
                last_affected = affected_tasks[-1]
                try:
                    subgraph.add_edge(
                        last_affected.task_id, new_task.task_id,
                        edge_type="dependency"
                    )
                except Exception as e:
                    logger.debug(
                        f"[SENTINEL] Could not add dependency edge for "
                        f"new task {new_task.task_id[:8]}: {e}"
                    )

                pending.add(new_task.task_id)
                added_count += 1
                logger.debug(
                    f"[SENTINEL] Added new replanned task {new_task.task_id[:8]} "
                    f"({new_task.task_type.value}): {new_goal[:60]}"
                )

        logger.info(
            f"[SENTINEL] subtree_replan complete: "
            f"{updated_count} tasks updated, {added_count} tasks added"
        )

    # ------------------------------------------------------------------
    # Config / context helpers
    # ------------------------------------------------------------------

    def _get_sentinel_config(self) -> Optional[Any]:
        """Extract Sentinel config from warmup.evolution.sentinel."""
        if not self.config:
            return None
        warmup = getattr(self.config, "warmup", None)
        if not warmup:
            return None
        evolution = getattr(warmup, "evolution", None)
        if not evolution:
            return None
        return getattr(evolution, "sentinel", None)

    def _is_sentinel_enabled(self) -> bool:
        """Check whether Sentinel checkpoint is enabled."""
        if not self.config:
            return True
        warmup = getattr(self.config, "warmup", None)
        if not warmup:
            return True
        evolution = getattr(warmup, "evolution", None)
        if not evolution:
            return True
        return bool(getattr(evolution, "sentinel_enabled", True))

    @staticmethod
    def _get_root_goal(subgraph: "TaskDAG", all_tasks: Dict[str, "TaskNode"]) -> str:
        """Best-effort extraction of the root goal for Sentinel context."""
        for task in all_tasks.values():
            if task.depth == 0 or task.parent_id is None:
                return task.goal
        if all_tasks:
            return next(iter(all_tasks.values())).goal
        return ""
