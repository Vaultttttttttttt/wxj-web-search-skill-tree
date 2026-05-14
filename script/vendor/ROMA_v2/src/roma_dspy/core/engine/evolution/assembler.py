"""Assembler — LLM-driven semantic cross-island merge.

Architecture:
  A single LLM call (CrossIslandMergeSignature) performs both:
    a) Semantic prompt remapping: map DP-island prompt improvements onto
       T_best's (possibly different) topology via goal/task_type matching,
       NOT by index.
    b) Consistency checking: detect cross-node issues and fix them.

  Programmatic (index-based) merge has been removed.  It was unreliable
  because T-island topology mutations can change node indices even when
  the total node count stays the same (e.g. -1 merge + 1 add), causing
  DP prompts to be silently applied to semantically unrelated nodes.
"""

from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from roma_dspy.core.signatures.base_models.evolutionary_solution import (
    EvolutionarySolution,
)
from roma_dspy.core.signatures.base_models.plan_blueprint import (
    DAGSkeleton,
    DirectiveTemplate,
    NodeRigidity,
    PlanBlueprint,
    SkeletonNode,
)
from roma_dspy.core.engine.evolution.tournament import TournamentResult
from roma_dspy.types.task_type import TaskType

if TYPE_CHECKING:
    from roma_dspy.core.modules.base_module import BaseModule


class Assembler:
    """Cross-island merge via LLM semantic merge (CrossIslandMergeSignature)."""

    def __init__(
        self,
        merge_agent: "BaseModule",
    ) -> None:
        self.merge_agent = merge_agent

    # ------------------------------------------------------------------
    # Public entry point (called by MIPEOrchestrator)
    # ------------------------------------------------------------------

    async def assemble(
        self,
        goal: str,
        t_result: TournamentResult,
        dp_result: TournamentResult,
        p0: Optional[EvolutionarySolution] = None,
        migration_insights: Optional[str] = None,
    ) -> AssemblyOutput:
        """Merge T_best topology with DP_best prompts via LLM semantic merge.

        Routing:
          - Same solution on both islands → no merge needed (return T_best).
          - Otherwise → always LLM semantic merge via CrossIslandMergeSignature,
            regardless of node count.  Index-based programmatic merge has been
            removed because it cannot detect topology changes that preserve
            the total node count (e.g. -1 delete + 1 add).
        """
        t_best = t_result.best
        dp_best = dp_result.best

        if t_best.solution_id == dp_best.solution_id:
            logger.info(
                "[ASSEMBLER] Both island winners are the same solution — "
                "skipping merge"
            )
            return AssemblyOutput(
                assembled=t_best,
                assembly_rationale="Seed P0 won both islands; no merge needed.",
                known_risks=[],
                t_runner_up=t_result.runner_up,
            )

        # Always use LLM semantic merge regardless of node count.
        p_assembled, rationale, risks = await self._llm_merge(
            goal, t_best, dp_best, p0, migration_insights,
        )

        return AssemblyOutput(
            assembled=p_assembled,
            assembly_rationale=rationale,
            known_risks=risks,
            t_runner_up=t_result.runner_up,
        )

    async def build_blueprint(
        self,
        output: AssemblyOutput,
    ) -> RefinementOutput:
        """Package the assembled solution into a PlanBlueprint."""
        p_final = output.assembled
        blueprint = _solution_to_blueprint(
            p_final,
            rationale=output.assembly_rationale,
            known_risks=output.known_risks,
            runner_up=output.t_runner_up,
        )
        return RefinementOutput(
            p_final=p_final,
            blueprint=blueprint,
            refinement_log=output.assembly_rationale,
        )

    # ------------------------------------------------------------------
    # LLM semantic merge (cross-topology)
    # ------------------------------------------------------------------

    async def _llm_merge(
        self,
        goal: str,
        t_best: EvolutionarySolution,
        dp_best: EvolutionarySolution,
        p0: Optional[EvolutionarySolution],
        migration_insights: Optional[str],
    ) -> tuple:
        """Use LLM to semantically map DP prompt improvements onto T topology.

        The LLM receives T_best's full plan (topology + prompts) and an
        annotated diff of DP_best's prompt changes vs P0.  It outputs a
        ``List[PromptEdit]`` targeting T_best node indices, which are
        applied deterministically — preserving T_best's topology exactly.
        """
        t_plan = t_best.to_skeleton_json(include_prompts_for=["all"])
        dp_diff = (
            dp_best.diff_prompts_from(p0)
            if p0 is not None
            else "(no P0 baseline; DP prompts unavailable for diff)"
        )

        logger.info(
            f"[ASSEMBLER] LLM merge: T_best={t_best.solution_id} "
            f"({len(t_best.subtasks)} nodes), "
            f"DP_best={dp_best.solution_id} "
            f"({len(dp_best.subtasks)} nodes)"
        )

        merge_log = ""
        merge_fallback_level = "none"

        try:
            result = await self.merge_agent.aforward(
                goal=goal,
                t_best_plan=t_plan,
                dp_prompt_improvements=dp_diff,
                island_feedback=migration_insights,
            )

            edits = getattr(result, "prompt_edits", None) or []
            merge_log = getattr(result, "merge_log", "")
            received_count = len(edits)

            if not edits:
                logger.info(
                    "[ASSEMBLER] LLM merge returned no edits — "
                    "T_best prompts are adequate"
                )
                p_merged = t_best
                merge_fallback_level = "no_edits"
                applied_count = 0
            else:
                logger.info(
                    f"[ASSEMBLER] LLM merge produced {received_count} prompt edits"
                )

                # ── Level-1: Try full merge ────────────────────────────────
                p_candidate = t_best.apply_prompt_edits(
                    edits,
                    island_id="cross_island_merge",
                    mutation_log=merge_log,
                )

                if len(p_candidate.subtasks) == len(t_best.subtasks):
                    p_merged = p_candidate
                    applied_count = received_count
                    merge_fallback_level = "full_merge"
                else:
                    logger.error(
                        "[ASSEMBLER] Topology invariant violated after full LLM merge — "
                        "attempting partial merge."
                    )
                    # ── Level-2: Partial merge — only in-bounds edits ─────
                    valid_edits = [
                        e for e in edits
                        if hasattr(e, "node_index")
                        and str(e.node_index).lstrip("-").isdigit()
                        and 0 <= int(e.node_index) < len(t_best.subtasks)
                    ]
                    if valid_edits:
                        p_partial = t_best.apply_prompt_edits(
                            valid_edits,
                            island_id="cross_island_merge_partial",
                            mutation_log=merge_log,
                        )
                        if len(p_partial.subtasks) == len(t_best.subtasks):
                            p_merged = p_partial
                            applied_count = len(valid_edits)
                            merge_fallback_level = "partial_merge"
                            logger.warning(
                                f"[ASSEMBLER] Partial merge applied "
                                f"{applied_count}/{received_count} edits."
                            )
                        else:
                            # ── Level-3: T_best fallback ──────────────────
                            p_merged = t_best
                            applied_count = 0
                            merge_fallback_level = "t_best_fallback"
                            logger.error(
                                "[ASSEMBLER] Partial merge also violated invariant — "
                                "falling back to T_best."
                            )
                    else:
                        p_merged = t_best
                        applied_count = 0
                        merge_fallback_level = "t_best_fallback"
                        logger.error(
                            "[ASSEMBLER] No in-bounds edits in partial set — "
                            "falling back to T_best."
                        )

                # Warn if applied ratio is low (DP improvements largely lost).
                if received_count > 0:
                    ratio = applied_count / received_count
                    if ratio < 0.5:
                        logger.warning(
                            f"[ASSEMBLER] Low DP edit application ratio: "
                            f"{applied_count}/{received_count} ({ratio:.0%}). "
                            f"fallback_level={merge_fallback_level}. "
                            "Most DP prompt improvements were not applied — "
                            "consider inspecting CrossIslandMergeSignature output."
                        )

        except Exception as e:
            logger.error(f"[ASSEMBLER] LLM merge failed: {e} — using T_best as-is")
            p_merged = t_best
            merge_log = f"LLM merge error: {e}"
            received_count = 0
            applied_count = 0
            merge_fallback_level = "t_best_fallback"

        logger.info(
            f"[ASSEMBLER] Merge complete: fallback_level={merge_fallback_level}"
        )

        p_merged.fitness_score = max(
            t_best.fitness_score, dp_best.fitness_score
        )

        rationale = (
            f"LLM semantic merge: topology from T-island winner "
            f"({t_best.solution_id}), DP prompt improvements mapped via "
            f"CrossIslandMergeSignature onto {len(t_best.subtasks)} nodes. "
            f"[merge_fallback_level={merge_fallback_level}] "
            f"{merge_log[:200]}"
        )
        return p_merged, rationale, []

    # ------------------------------------------------------------------
    # Legacy: kept for backward compatibility (refine → build_blueprint)
    # ------------------------------------------------------------------

    async def refine(
        self,
        goal: str,
        output: AssemblyOutput,
    ) -> RefinementOutput:
        """Build blueprint from assembled solution.

        In the new architecture, merge + consistency check happen in
        ``assemble()`` already, so this just packages the blueprint.
        """
        return await self.build_blueprint(output)


# =====================================================================
# Output data classes
# =====================================================================


class AssemblyOutput:
    """Result after cross-island LLM semantic merge."""

    __slots__ = ("assembled", "assembly_rationale", "known_risks", "t_runner_up")

    def __init__(
        self,
        assembled: EvolutionarySolution,
        assembly_rationale: str,
        known_risks: List[str],
        t_runner_up: Optional[EvolutionarySolution],
    ) -> None:
        self.assembled = assembled
        self.assembly_rationale = assembly_rationale
        self.known_risks = known_risks
        self.t_runner_up = t_runner_up


class RefinementOutput:
    """Final result with blueprint packaging."""

    __slots__ = ("p_final", "blueprint", "refinement_log")

    def __init__(
        self,
        p_final: EvolutionarySolution,
        blueprint: PlanBlueprint,
        refinement_log: str,
    ) -> None:
        self.p_final = p_final
        self.blueprint = blueprint
        self.refinement_log = refinement_log


# =====================================================================
# Blueprint packaging
# =====================================================================


def _solution_to_blueprint(
    solution: EvolutionarySolution,
    rationale: str,
    known_risks: List[str],
    runner_up: Optional[EvolutionarySolution] = None,
    blueprint_validation_policy: str = "strict",
) -> PlanBlueprint:
    """Build a PlanBlueprint from a final EvolutionarySolution.

    Args:
        blueprint_validation_policy: ``"strict"`` (default) raises a
            ValueError for fatal consistency issues, preventing a
            corrupted blueprint from being stored.  ``"lenient"`` merely
            logs them and continues.
    """
    nodes: List[SkeletonNode] = []
    directive_templates: List[DirectiveTemplate] = []
    flexible_dep_map: Dict[str, List[str]] = {}

    for i, st in enumerate(solution.subtasks):
        node_id = str(i)
        # Heuristic: depth-0 nodes are original P0 skeleton → RIGID.
        # depth>0 nodes were created by topology evolution → FLEXIBLE.
        rigidity = NodeRigidity.RIGID if st.depth == 0 else NodeRigidity.FLEXIBLE

        nodes.append(SkeletonNode(
            node_id=node_id,
            task_type=st.task_type,
            title=st.goal[:80],
            rigidity=rigidity,
            scope_summary=st.goal,
        ))
        if st.dynamic_prompt:
            directive_templates.append(DirectiveTemplate(
                template=st.dynamic_prompt,
                node_id=node_id,
            ))

        # Build flexible_dependencies for depth-1 WRITE chains:
        # chains connecting a THINK outline node to its WRITE children are
        # execution-order hints that the runtime MAY relax if children are
        # truly independent.
        if (
            st.depth == 1
            and st.task_type == TaskType.WRITE
            and st.parent_node_id is not None
        ):
            parent_st = None
            try:
                parent_st = solution.subtasks[int(st.parent_node_id)]
            except (ValueError, IndexError):
                pass
            if parent_st is not None and parent_st.task_type == TaskType.THINK:
                # This WRITE node's dependency on its THINK parent is flexible.
                flexible_dep_map.setdefault(node_id, []).extend(
                    [d for d in st.dependencies if d == st.parent_node_id]
                )

    fallback = None
    if runner_up and runner_up.solution_id != solution.solution_id:
        fallback_nodes = [
            SkeletonNode(
                node_id=str(i),
                task_type=st.task_type,
                title=st.goal[:80],
                rigidity=NodeRigidity.FLEXIBLE,
            )
            for i, st in enumerate(runner_up.subtasks)
        ]
        fallback = DAGSkeleton(
            nodes=fallback_nodes,
            min_nodes=max(1, len(fallback_nodes) - 2),
            max_nodes=len(fallback_nodes) + 2,
        )

    skeleton = DAGSkeleton(
        nodes=nodes,
        rigid_dependencies=dict(solution.dependencies_graph),
        flexible_dependencies=flexible_dep_map,
        min_nodes=max(1, len(nodes) - 2),
        max_nodes=len(nodes) + 2,
    )

    _ensure_hierarchy_before_blueprint(solution)
    _repair_dependencies_before_blueprint(solution)
    _repair_goal_scope_errors(solution)
    prebuilt = _build_prebuilt_subtasks(solution)

    blueprint = PlanBlueprint(
        topology_skeleton=skeleton,
        report_policy=solution.report_policy,
        directive_templates=directive_templates,
        optimization_rationale=rationale,
        known_risks=known_risks,
        fallback_topology=fallback,
        generation=solution.generation,
        fitness_score=solution.fitness_score,
        prebuilt_subtasks=prebuilt,
    )

    # ── P1-1: Classify consistency issues as fatal vs warn ────────────────
    issues = blueprint.validate_internal_consistency()
    if issues:
        fatal_issues = [
            iss for iss in issues
            if any(
                kw in iss
                for kw in (
                    "not in skeleton",
                    "unknown node_id",
                    "below min_nodes",
                    "above max_nodes",
                )
            )
        ]
        warn_issues = [iss for iss in issues if iss not in fatal_issues]

        if warn_issues:
            logger.warning(
                f"[BLUEPRINT] {len(warn_issues)} non-critical consistency warning(s): "
                f"{warn_issues}"
            )

        if fatal_issues:
            msg = (
                f"[BLUEPRINT] {len(fatal_issues)} FATAL consistency issue(s) detected: "
                f"{fatal_issues}"
            )
            if blueprint_validation_policy == "strict":
                logger.error(msg + " — strict policy: raising to prevent corrupt storage.")
                raise ValueError(
                    f"Blueprint has fatal consistency issues "
                    f"(policy=strict): {fatal_issues}"
                )
            else:
                logger.error(msg + " — lenient policy: blueprint stored despite fatal issues.")
                blueprint.known_risks.extend(
                    [f"[FATAL] {iss}" for iss in fatal_issues]
                )
    else:
        logger.debug("[BLUEPRINT] Internal consistency check passed.")

    flexible_count = sum(1 for n in nodes if n.rigidity == NodeRigidity.FLEXIBLE)
    logger.info(
        f"[BLUEPRINT] Packaged: {len(nodes)} nodes "
        f"({flexible_count} FLEXIBLE / {len(nodes) - flexible_count} RIGID), "
        f"{len(directive_templates)} directive templates, "
        f"{len(flexible_dep_map)} flexible dependency chains."
    )

    return blueprint


def _ensure_hierarchy_before_blueprint(
    solution: EvolutionarySolution,
) -> None:
    """Defensive hierarchy repair before building prebuilt_subtasks."""
    subtasks = solution.subtasks
    if not subtasks or not any(st.depth > 0 for st in subtasks):
        return

    idx_children: Dict[str, List[str]] = {}
    for i, st in enumerate(subtasks):
        if st.parent_node_id is not None:
            idx_children.setdefault(st.parent_node_id, []).append(str(i))

    repaired = 0
    for i, st in enumerate(subtasks):
        expected_children = idx_children.get(str(i), [])
        if expected_children and (not st.children_ids or set(st.children_ids) != set(expected_children)):
            st.children_ids = expected_children
            st.is_leaf = False
            repaired += 1
        elif not expected_children and not st.children_ids:
            st.is_leaf = True

    if repaired:
        logger.info(
            f"[BLUEPRINT] Repaired children_ids for {repaired} nodes "
            f"before building prebuilt_subtasks"
        )


def _repair_dependencies_before_blueprint(
    solution: EvolutionarySolution,
) -> None:
    """Fix broken dependencies before blueprint packaging.

    MIPE evolution (especially ``split_merge``) can produce subtasks with:
      - Self-referencing dependencies (Planner hallucination)
      - WRITE/THINK leaf tasks with empty dependencies (split without
        dependency inheritance)
      - THINK/WRITE nodes that depend on only the first fragment of a SPLIT
        pair, missing the second fragment (confirmed bug: THINK-21 missing
        dep on RETRIEVE-20 after Thailand node was split into two parts)

    This repairs all issues in-place on ``solution.subtasks`` and
    ``solution.dependencies_graph``.
    """
    from roma_dspy.types.task_type import TaskType

    subtasks = solution.subtasks
    if not subtasks:
        return

    repaired_self = 0
    repaired_empty = 0

    for i, st in enumerate(subtasks):
        i_str = str(i)
        if i_str in st.dependencies:
            st.dependencies = [d for d in st.dependencies if d != i_str]
            repaired_self += 1
        if i_str in solution.dependencies_graph:
            deps = solution.dependencies_graph[i_str]
            if i_str in deps:
                solution.dependencies_graph[i_str] = [
                    d for d in deps if d != i_str
                ]

    depth_parent_groups: Dict[tuple, List[int]] = {}
    for i, st in enumerate(subtasks):
        key = (st.depth, st.parent_node_id)
        depth_parent_groups.setdefault(key, []).append(i)

    for group_key, indices in depth_parent_groups.items():
        retrieve_indices = [
            i for i in indices
            if subtasks[i].task_type == TaskType.RETRIEVE
        ]
        think_indices = [
            i for i in indices
            if subtasks[i].task_type == TaskType.THINK
        ]

        for i in indices:
            st = subtasks[i]
            is_leaf = st.is_leaf or not bool(st.children_ids)
            if not is_leaf:
                continue
            has_deps = bool(st.dependencies)

            if st.task_type == TaskType.WRITE and not has_deps:
                inferred = [str(t) for t in think_indices if t != i]
                if not inferred:
                    inferred = [str(t) for t in retrieve_indices if t != i]
                if inferred:
                    st.dependencies = inferred
                    solution.dependencies_graph[str(i)] = inferred
                    repaired_empty += 1

            elif st.task_type == TaskType.THINK and not has_deps and retrieve_indices:
                inferred = [str(t) for t in retrieve_indices if t != i]
                if inferred:
                    st.dependencies = inferred
                    solution.dependencies_graph[str(i)] = inferred
                    repaired_empty += 1

    if repaired_self or repaired_empty:
        logger.info(
            f"[BLUEPRINT] Dependency repair: removed {repaired_self} "
            f"self-deps, inferred deps for {repaired_empty} "
            f"empty WRITE/THINK leaves"
        )

    # --- SPLIT-tail propagation pass ---
    # When a RETRIEVE node R_tail (depth-0) depends on another RETRIEVE node
    # R_head (depth-0), this is the fingerprint of a SPLIT operation where the
    # original node was split into R_head and R_tail.  Any THINK/WRITE node D
    # that depends on R_head but NOT R_tail will miss the second research branch.
    # This pass detects all such (R_head, R_tail) pairs and ensures D depends on
    # both.  This is a safety net for cases where _topo_split's in-situ
    # propagation was bypassed (e.g. pre-existing blueprints, LLM-generated plans
    # that manually express a split pattern without using _topo_split).
    repaired_split = _repair_split_tail_deps(solution)
    if repaired_split:
        logger.info(
            f"[BLUEPRINT] Split-tail repair: added missing tail deps to "
            f"{repaired_split} THINK/WRITE node(s)"
        )


def _repair_split_tail_deps(solution: "EvolutionarySolution") -> int:
    """Ensure THINK/WRITE nodes depending on a SPLIT head also depend on the tail.

    A 'split tail' is a depth-0 RETRIEVE node that depends on another depth-0
    RETRIEVE node (R_head).  Any downstream THINK or WRITE node that depends on
    R_head but not on the tail will race ahead before the tail branch completes.

    Returns the number of nodes that were updated.
    """
    from roma_dspy.types.task_type import TaskType

    subtasks = solution.subtasks
    if not subtasks:
        return 0

    depth0_retrieve_set = {
        str(i) for i, st in enumerate(subtasks)
        if st.depth == 0 and st.task_type == TaskType.RETRIEVE
    }

    # Identify split-tail RETRIEVE nodes: depth-0 RETs that depend on another
    # depth-0 RET.  Map tail_idx_str -> set of head_idx_str they depend on.
    tail_to_heads: Dict[str, set] = {}
    for i, st in enumerate(subtasks):
        if str(i) not in depth0_retrieve_set:
            continue
        heads = {d for d in st.dependencies if d in depth0_retrieve_set}
        if heads:
            tail_to_heads[str(i)] = heads

    if not tail_to_heads:
        return 0

    updated = 0
    for i, st in enumerate(subtasks):
        if st.depth != 0:
            continue
        if st.task_type not in (TaskType.THINK, TaskType.WRITE):
            continue
        i_str = str(i)
        current_deps = set(st.dependencies)
        extra: List[str] = []
        for tail_str, heads in tail_to_heads.items():
            # D already depends on the tail — nothing to do.
            if tail_str in current_deps:
                continue
            # D depends on at least one head of this tail — add the tail.
            if heads & current_deps:
                extra.append(tail_str)
        if extra:
            st.dependencies.extend(extra)
            existing_graph = set(solution.dependencies_graph.get(i_str, []))
            solution.dependencies_graph[i_str] = list(existing_graph | set(extra))
            updated += 1
            logger.info(
                f"[BLUEPRINT] Split-tail repair: node {i} "
                f"({st.task_type.value}) gained deps {extra}"
            )

    return updated


def _repair_goal_scope_errors(
    solution: "EvolutionarySolution",
) -> None:
    """Detect and fix goal/dynamic_prompt scope contradictions before packaging.

    When the Planner produces a low-quality seed and only the ``dynamic_prompt``
    is later corrected by the DP evolver (PromptEdit), the ``goal`` field can
    remain incorrect (e.g., pointing to the wrong countries).  This creates a
    contradiction where the executor receives conflicting instructions.

    Heuristic (no LLM call):
    - For each subtask, compare the first ~120 chars of ``goal`` with the first
      ~120 chars of ``dynamic_prompt`` for the presence of a generic placeholder
      or an obvious entity mismatch.
    - Placeholder detection: ``goal`` contains "Country N" (N is a digit) while
      ``dynamic_prompt`` starts with a specific country/entity name.
    - If a mismatch is found, replace ``goal`` with the first non-empty sentence
      from ``dynamic_prompt`` (capped at 200 chars) and log the repair.

    This is intentionally conservative — it only triggers on clear structural
    mismatches, not on subtle semantic differences.
    """
    import re

    # Pattern: "Country 1", "Country 2", ... in goal text (Planner placeholder)
    _placeholder_re = re.compile(r"\bCountry\s+\d+\b", re.IGNORECASE)
    # First sentence of a text block (split on . or \n)
    _first_sentence_re = re.compile(r"^([^\n.]{10,200})")

    repaired = 0
    for i, st in enumerate(solution.subtasks):
        if not st.goal or not st.dynamic_prompt:
            continue

        goal_head = st.goal[:200]
        prompt_head = st.dynamic_prompt[:200]

        should_repair = False

        # Case 1: goal has a generic placeholder ("Country 3") but prompt is specific
        if _placeholder_re.search(goal_head) and not _placeholder_re.search(prompt_head):
            should_repair = True

        if not should_repair:
            continue

        # Extract first usable sentence from dynamic_prompt as replacement goal
        m = _first_sentence_re.match(st.dynamic_prompt.lstrip())
        new_goal = m.group(1).strip() if m else st.dynamic_prompt[:200].strip()
        # Avoid using prompt directive keywords as the goal
        for skip_prefix in ("WHAT TO FIND", "WHO &", "STRUCTURE:", "STRICTLY"):
            if new_goal.upper().startswith(skip_prefix):
                new_goal = st.goal  # keep original if prompt starts with a directive
                should_repair = False
                break

        if should_repair and new_goal != st.goal:
            old_goal_preview = st.goal[:80]
            st.goal = new_goal
            repaired += 1
            logger.info(
                f"[BLUEPRINT] Goal scope repair: node {i} — "
                f"replaced placeholder goal '{old_goal_preview}...' "
                f"with prompt-derived goal '{new_goal[:80]}'"
            )

    if repaired:
        logger.info(
            f"[BLUEPRINT] Goal scope repair: fixed {repaired} node(s) "
            f"with placeholder/contradictory goal text"
        )


def _build_prebuilt_subtasks(
    solution: EvolutionarySolution,
) -> Dict[str, List[Dict]]:
    """Extract parent->children mapping for PlanBlueprint.prebuilt_subtasks."""
    result: Dict[str, List[Dict]] = {}

    root_subtasks: List[Dict] = []
    for i, st in enumerate(solution.subtasks):
        if st.depth == 0:
            st_dict = st.model_dump()
            st_dict["_flat_index"] = str(i)
            root_subtasks.append(st_dict)
    if root_subtasks:
        result["__root__"] = root_subtasks

    for i, st in enumerate(solution.subtasks):
        if not st.children_ids:
            continue
        children: List[Dict] = []
        for cid_str in st.children_ids:
            try:
                cid = int(cid_str)
                if 0 <= cid < len(solution.subtasks):
                    child_dict = solution.subtasks[cid].model_dump()
                    child_dict["_flat_index"] = str(cid)
                    children.append(child_dict)
            except (ValueError, TypeError):
                continue
        if children:
            result[str(i)] = children
    return result
