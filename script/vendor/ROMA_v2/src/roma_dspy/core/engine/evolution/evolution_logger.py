"""MIPEEvolutionLogger — human-readable evolution trace for MIPE pipeline.

Writes an incrementally-updated Markdown file that captures every phase
of the MIPE optimisation process: seed generation, bootstrap diagnosis,
island evolution (edit-script variants + tournament rounds), programmatic
assembly, consistency check, and validity check.

Edit-script architecture: variants are logged with their edit logs
(what was changed) rather than full plan JSON dumps.

Output lands in ``{logs_dir}/mipe_evolution_{timestamp}.md``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from roma_dspy.core.signatures.base_models.evolutionary_solution import (
        EvolutionarySolution,
    )
    from roma_dspy.core.engine.evolution.judge import JudgeResult


_TYPE_ABBR: Dict[str, str] = {"RETRIEVE": "RET", "THINK": "THK", "WRITE": "WRT"}


def _type_abbr(st: Any) -> str:
    tt = st.task_type.value if hasattr(st.task_type, "value") else str(st.task_type)
    return _TYPE_ABBR.get(tt, tt[:3])


def _type_str(st: Any) -> str:
    return st.task_type.value if hasattr(st.task_type, "value") else str(st.task_type)


def _dep_label(idx_str: str, subtasks: List[Any]) -> str:
    """Resolve a dependency index string to [idx:TYPE goal_snippet]."""
    try:
        d = int(idx_str)
        if 0 <= d < len(subtasks):
            st = subtasks[d]
            goal_snip = st.goal[:22].replace("|", "/").replace("\n", " ")
            if len(st.goal) > 22:
                goal_snip += ".."
            return f"[{d}:{_type_abbr(st)} {goal_snip}]"
    except (ValueError, TypeError):
        pass
    return f"[{idx_str}:?]"


def _detect_split_tails(subtasks: List[Any]) -> Dict[str, set]:
    """Return mapping tail_idx_str -> set of head_idx_str for split patterns.

    A 'split tail' is a depth-0 RETRIEVE node that depends on another
    depth-0 RETRIEVE node — the fingerprint left by _topo_split.
    """
    depth0_ret = {
        str(i) for i, st in enumerate(subtasks)
        if st.depth == 0 and _type_str(st) == "RETRIEVE"
    }
    tail_to_heads: Dict[str, set] = {}
    for i, st in enumerate(subtasks):
        if str(i) not in depth0_ret:
            continue
        heads = {d for d in st.dependencies if d in depth0_ret}
        if heads:
            tail_to_heads[str(i)] = heads
    return tail_to_heads


def _node_warnings(i: int, st: Any, subtasks: List[Any],
                   tail_to_heads: Dict[str, set]) -> List[str]:
    """Return a list of warning strings for structural dependency issues."""
    warnings: List[str] = []
    tt = _type_str(st)
    is_leaf = st.is_leaf or not bool(st.children_ids)

    if tt in ("THINK", "WRITE") and is_leaf and not st.dependencies:
        parent_id = getattr(st, "parent_node_id", None)
        if parent_id is None:  # 仅对真正孤立的根级节点发出警告；depth>0 子节点通过父子层级继承上下文
            warnings.append("⚠ no deps")

    if tt in ("THINK", "WRITE") and st.depth == 0:
        current_deps = set(st.dependencies)
        for tail_str, heads in tail_to_heads.items():
            if tail_str not in current_deps and (heads & current_deps):
                warnings.append(f"⚠ missing {_dep_label(tail_str, subtasks)}")

    return warnings


def _plan_tree_view(solution: "EvolutionarySolution") -> str:
    """Build a hierarchical tree view of a plan's subtask structure.

    Format (two lines per node when deps are present):
      idx D  TYPE   [tree-prefix] Goal (truncated to ~70 chars)     [P]
                    ↳ [n:T goal..] [n:T goal..]  (or ⚠ warnings)

    Features vs the old flat table:
    - Tree-branch markers (├─ / └─) show parent-child nesting visually.
    - ALL dependencies shown (no +N truncation) — each as [idx:T short_goal].
    - Dep line only shown when there are deps or warnings (no clutter for
      pure RETRIEVE leaves that naturally have no deps).
    - Structural warnings on the dep line:
        ⚠ no deps       — WRITE/THINK leaf with empty dependency list
        ⚠ missing [N:T] — THINK/WRITE missing dep on a SPLIT-tail RETRIEVE
    - Stats header and warning summary at the top for quick scanning.
    """
    subtasks = solution.subtasks
    if not subtasks:
        return "(empty plan)"

    tail_to_heads = _detect_split_tails(subtasks)

    # Build parent→children index for depth-1 nodes.
    children_map: Dict[str, List[int]] = {}
    orphan_depth1: List[int] = []
    for i, st in enumerate(subtasks):
        if st.depth > 0:
            parent = st.parent_node_id
            if parent is not None:
                children_map.setdefault(parent, []).append(i)
            else:
                orphan_depth1.append(i)

    # Count by type for the stats header.
    type_counts: Dict[str, int] = {}
    for st in subtasks:
        k = _type_abbr(st)
        type_counts[k] = type_counts.get(k, 0) + 1

    def short_goal(goal: str, w: int) -> str:
        g = goal.replace("|", "/").replace("\n", " ").strip()
        return (g[:w] + "..") if len(g) > w else g

    def dep_line(deps: List[str], warns: List[str], indent: str) -> Optional[str]:
        """Return the continuation line for deps/warnings, or None if nothing to show."""
        if not deps and not warns:
            return None
        parts: List[str] = []
        if deps:
            parts = [_dep_label(d, subtasks) for d in deps]
        warn_parts = ["  " + w for w in warns]
        content = " ".join(parts) + "".join(warn_parts)
        return f"{indent}↳ {content}"

    lines: List[str] = []

    # ── Stats header ──────────────────────────────────────────────────────────
    total = len(subtasks)
    leaf_count = sum(1 for st in subtasks if st.is_leaf or not st.children_ids)
    depth1_count = sum(1 for st in subtasks if st.depth > 0)
    stats_parts = [f"{v} {k}" for k, v in sorted(type_counts.items())]
    lines.append(f"```")
    lines.append(
        f"  nodes={total}  leaves={leaf_count}  depth-1={depth1_count}"
        f"  ({', '.join(stats_parts)})"
    )

    # ── Collect all warnings for summary ──────────────────────────────────────
    all_warnings: List[str] = []
    for i, st in enumerate(subtasks):
        ws = _node_warnings(i, st, subtasks, tail_to_heads)
        for w in ws:
            all_warnings.append(f"  node {i} ({_type_str(st)}): {w}")
    if all_warnings:
        lines.append(f"  ⚠ WARNINGS ({len(all_warnings)}):")
        for w in all_warnings:
            lines.append(w)
    else:
        lines.append("  ✓ no structural dependency warnings")

    lines.append("─" * 92)
    lines.append(f"  {'#':>3}  D  {'Type':<8}  Goal")
    lines.append("─" * 92)

    def render_node(i: int, tree_prefix: str = "", indent_for_deps: str = "         ") -> None:
        st = subtasks[i]
        tt = _type_str(st)
        prompt_mark = "[P]" if st.dynamic_prompt else "   "
        child_indicator = f"({len(children_map.get(str(i), []))} ch)" if children_map.get(str(i)) else "     "
        goal_w = max(20, 68 - len(tree_prefix))
        g = short_goal(st.goal, goal_w)
        # Node line
        lines.append(f"  {i:>3}  {st.depth}  {tt:<8}  {tree_prefix}{g} {prompt_mark} {child_indicator}")
        # Deps / warnings continuation line
        warns = _node_warnings(i, st, subtasks, tail_to_heads)
        dl = dep_line(st.dependencies, warns, indent_for_deps)
        if dl:
            lines.append(dl)

    def render_children(parent_idx: int, depth_indent: str) -> None:
        kids = children_map.get(str(parent_idx), [])
        dep_indent = depth_indent + "   "
        for ci, kid in enumerate(kids):
            is_last = ci == len(kids) - 1
            connector = "└─ " if is_last else "├─ "
            subtree_indent = depth_indent + ("   " if is_last else "│  ")
            render_node(kid, tree_prefix=depth_indent + connector,
                        indent_for_deps=subtree_indent + "   ")
            # Recurse for depth-2+ nodes if present.
            render_children(kid, subtree_indent)

    for i, st in enumerate(subtasks):
        if st.depth != 0:
            continue
        render_node(i, tree_prefix="", indent_for_deps="         ")
        render_children(i, depth_indent="     ")

    # Orphaned depth-1 nodes (no declared parent_node_id).
    if orphan_depth1:
        lines.append("─" * 92)
        lines.append("  (orphaned depth-1 nodes — parent_node_id is None)")
        for i in orphan_depth1:
            render_node(i, tree_prefix="? ", indent_for_deps="         ")

    lines.append("─" * 92)
    lines.append("```")
    return "\n".join(lines)


def _plan_summary_table(solution: "EvolutionarySolution") -> str:
    """Wrapper retained for backward compatibility — delegates to _plan_tree_view."""
    try:
        return _plan_tree_view(solution)
    except Exception as exc:
        # Fallback to a minimal flat table if the tree renderer fails.
        rows = [
            "| # | Depth | Type | Goal (truncated) | Deps | Has Prompt |",
            "|---|-------|------|-------------------|------|------------|",
        ]
        for i, st in enumerate(solution.subtasks):
            tt = st.task_type.value if hasattr(st.task_type, "value") else str(st.task_type)
            goal = st.goal[:60].replace("|", "/").replace("\n", " ")
            if len(st.goal) > 60:
                goal += "..."
            deps = ", ".join(str(d) for d in st.dependencies) if st.dependencies else "--"
            rows.append(f"| {i} | {st.depth} | {tt} | {goal} | {deps} | {'Y' if st.dynamic_prompt else '--'} |")
        return "\n".join(rows) + f"\n\n*(tree view failed: {exc})*"


def _plan_skeleton_block(solution: "EvolutionarySolution", *, include_prompts: bool = False) -> str:
    """Render plan skeleton as a collapsible JSON block."""
    try:
        include_for = ["all"] if include_prompts else None
        content = solution.to_skeleton_json(include_prompts_for=include_for)
        payload = json.loads(content)
        content = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        content = "(serialization failed)"
    return f"<details>\n<summary>Plan Skeleton (click to expand)</summary>\n\n```json\n{content}\n```\n</details>"


def _edit_log_block(solution: "EvolutionarySolution") -> str:
    """Render the latest edits applied to a solution."""
    from roma_dspy.core.signatures.base_models.evolutionary_solution import (
        EVOLUTION_LOG_PROMPT_PREVIEW_CHARS,
        format_edit_log,
    )
    edits = getattr(solution, "_latest_edits", None)
    if not edits:
        return ""
    log = format_edit_log(
        edits,
        prompt_preview_chars=EVOLUTION_LOG_PROMPT_PREVIEW_CHARS,
        include_input_mode=False,
    )
    return f"\n**Edit Log ({len(edits)} edits):**\n\n{log}\n"


def _edit_history_summary(solution: "EvolutionarySolution") -> str:
    """Summarize the cumulative edit history."""
    history = getattr(solution, "_edit_history", [])
    if not history:
        return ""
    # Detection priority mirrors format_edit_log:
    #   1. hasattr "section_content"  → new PromptEdit (anchor-section op)
    #   2. hasattr "new_dynamic_prompt" → legacy PromptEdit (full rewrite)
    #   3. hasattr "op" AND "target_node" → TopoEdit
    # New PromptEdits also carry an "op" attribute (PromptEditOp enum), so
    # checking only hasattr(e, "op") would incorrectly count them as topo edits.
    prompt_edits = sum(
        1 for e in history
        if hasattr(e, "section_content") or hasattr(e, "new_dynamic_prompt")
    )
    topo_edits = sum(
        1 for e in history
        if hasattr(e, "op") and hasattr(e, "target_node")
    )
    return (
        f"- **Cumulative edits from seed:** {len(history)} "
        f"(prompt: {prompt_edits}, topo: {topo_edits})\n"
    )


class MIPEEvolutionLogger:
    """Incrementally writes a Markdown evolution trace to disk.

    If ``logs_dir`` is None the logger becomes a silent no-op so that
    callers need no conditional guards.
    """

    def __init__(self, logs_dir: Optional[str] = None) -> None:
        self._enabled = logs_dir is not None
        self._path: Optional[Path] = None
        if self._enabled:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            d = Path(logs_dir)
            d.mkdir(parents=True, exist_ok=True)
            self._path = d / f"mipe_evolution_{ts}.md"
            logger.info(f"[MIPE-LOG] Evolution log will be written to: {self._path}")
        else:
            logger.warning("[MIPE-LOG] logs_dir is None -- evolution log disabled")

    @property
    def filepath(self) -> Optional[str]:
        return str(self._path) if self._path else None

    def _write(self, text: str) -> None:
        if not self._enabled or self._path is None:
            return
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception as e:
            logger.warning(f"[MIPE-LOG] Failed to write evolution log: {e}")

    # ------------------------------------------------------------------
    # Phase loggers
    # ------------------------------------------------------------------

    def log_start(
        self,
        goal: str,
        pop_size: int,
        max_generations: int,
        num_islands: int,
    ) -> None:
        self._write(
            f"# MIPE Evolution Log (Edit-Script Architecture)\n\n"
            f"**Goal:** {goal}\n\n"
            f"**Start Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"**Configuration:**\n"
            f"- Population Size: {pop_size}\n"
            f"- Max Generations: {max_generations}\n"
            f"- Number of Islands: {num_islands}\n"
            f"- Mode: Edit-Script (incremental patches)\n\n"
            f"---\n"
        )

    def log_p0(self, p0: "EvolutionarySolution") -> None:
        leaf_count = sum(1 for st in p0.subtasks if st.is_leaf)
        self._write(
            f"\n## Phase 0: Seed Generation (P0)\n\n"
            f"- **Solution ID:** `{p0.solution_id}`\n"
            f"- **Total Subtasks:** {len(p0.subtasks)}\n"
            f"- **Leaf Nodes:** {leaf_count}\n"
            f"- **Max Depth:** {p0.max_depth}\n\n"
            f"### P0 Plan Structure\n\n"
            f"{_plan_summary_table(p0)}\n"
        )

    def log_init_evaluation(
        self,
        t_feedback: Optional[str],
    ) -> None:
        """Log the initial topology evaluation of P0 (replaces the old dual-dim bootstrap)."""
        self._write(
            f"\n## Phase 1 Init: Topology Evaluation of P0\n\n"
            f"{'> ' + t_feedback.replace(chr(10), chr(10) + '> ') if t_feedback else '*No feedback generated*'}\n\n"
            f"---\n"
        )

    def log_bootstrap(
        self,
        dp_feedback: Optional[str],
        t_feedback: Optional[str],
    ) -> None:
        """Deprecated — kept for backward compatibility with non-SAE paths."""
        self._write(
            f"\n## Bootstrap: P0 Self-Diagnosis (PlanJudgeSignature)\n\n"
            f"### Dynamic Prompt Dimension\n\n"
            f"{'> ' + dp_feedback.replace(chr(10), chr(10) + '> ') if dp_feedback else '*No feedback generated*'}\n\n"
            f"### Topology Dimension\n\n"
            f"{'> ' + t_feedback.replace(chr(10), chr(10) + '> ') if t_feedback else '*No feedback generated*'}\n\n"
            f"---\n"
        )

    def log_island_start(self, dimension: str, max_generations: int, pop_size: int) -> None:
        label = "Dynamic Prompt (DP)" if dimension == "dynamic_prompt" else "Topology (T)"
        edit_type = "PromptEdit" if dimension == "dynamic_prompt" else "TopoEdit"
        self._write(
            f"\n## Phase 1 -- Island: {label}\n\n"
            f"- Generations: {max_generations}\n"
            f"- Population Size: {pop_size}\n"
            f"- Edit Type: `{edit_type}`\n"
        )

    def log_sae_step_start(
        self,
        dimension: str,
        gen: int,
        total_gens: int,
        pop_size: int,
        seed_id: str,
    ) -> None:
        """Log the start of a single SAE step (replaces log_island_start for SAE mode)."""
        label = "DP" if dimension == "dynamic_prompt" else "T"
        edit_type = "PromptEdit" if dimension == "dynamic_prompt" else "TopoEdit"
        step_num = gen * 2 + (2 if dimension == "dynamic_prompt" else 1)
        total_steps = total_gens * 2
        self._write(
            f"\n## Phase 1 -- SAE Step {step_num}/{total_steps} ({label})\n\n"
            f"- **Gen:** {gen + 1}/{total_gens}\n"
            f"- **Edit Type:** `{edit_type}`\n"
            f"- **Population Size:** {pop_size}\n"
            f"- **Seed:** `{seed_id}`\n"
        )

    def log_sae_merge_skipped(
        self,
        final_id: str,
        total_gens: int,
    ) -> None:
        """Log Phase 2 section for SAE (no assembler merge needed)."""
        self._write(
            f"\n---\n\n"
            f"## Phase 2: SAE Complete (No Cross-Island Merge)\n\n"
            f"SAE ran {total_gens} generation(s) of T→DP alternation. "
            f"DP always worked on T's latest topology, so no cross-island "
            f"merge is needed.\n\n"
            f"- **Final Solution:** `{final_id}`\n"
        )

    def log_evolver_variants(
        self,
        dimension: str,
        generation: int,
        max_generations: int,
        seed_id: str,
        variants: List["EvolutionarySolution"],
    ) -> None:
        label = "DP" if dimension == "dynamic_prompt" else "T"
        self._write(
            f"\n### Generation {generation + 1}/{max_generations} ({label})\n\n"
            f"**Seed:** `{seed_id}`\n\n"
            f"**Evolver Output ({len(variants)} variants):**\n"
        )
        for vi, v in enumerate(variants):
            strategy = getattr(v, "_applied_strategy", "unknown")
            edit_count = len(getattr(v, "_latest_edits", []))
            is_fallback = v.mutation_log and (
                "PARSE_FAIL" in v.mutation_log or "NO_EDITS" in v.mutation_log
            )

            self._write(
                f"\n#### Variant {vi + 1}: `{v.solution_id}`\n"
                f"- Strategy: `{strategy}`\n"
                f"- Parent: `{v.parent_id}`\n"
                f"- Edits Applied: {edit_count}"
                f"{' (FALLBACK - no edits)' if is_fallback else ''}\n"
                f"- Mutation Log: {v.mutation_log or '--'}\n"
                f"{_edit_log_block(v)}"
            )

    def log_tournament_round(
        self,
        dimension: str,
        generation: int,
        round_num: int,
        total_rounds: int,
        best_id: str,
        variant_id: str,
        result: "JudgeResult",
    ) -> None:
        if result.is_tie:
            outcome = "**Tie** -- keeping current best"
        elif result.winner and result.winner.solution_id == best_id:
            outcome = f"**Current best wins** (`{best_id}`)"
        else:
            winner_id = result.winner.solution_id if result.winner else "?"
            outcome = f"**Variant wins** (`{winner_id}`)"

        self._write(
            f"\n**Tournament Round {round_num}/{total_rounds}** "
            f"(Edit-Log Judge):\n"
            f"`{best_id}` vs `{variant_id}`\n\n"
            f"- Result: {outcome}\n"
            f"- Scores: {result.score_a:.1f} vs {result.score_b:.1f}\n"
            f"- Feedback: {result.feedback[:2000] if result.feedback else '--'}\n"
        )

    def log_generation_best(
        self,
        dimension: str,
        generation: int,
        max_generations: int,
        best: "EvolutionarySolution",
    ) -> None:
        label = "DP" if dimension == "dynamic_prompt" else "T"
        self._write(
            f"\n**-> {label} Generation {generation + 1} Best:** "
            f"`{best.solution_id}` (fitness={best.fitness_score:.1f})\n\n"
            f"{_edit_history_summary(best)}"
            f"{_plan_summary_table(best)}\n"
        )

    def log_assembly(
        self,
        t_best: "EvolutionarySolution",
        dp_best: "EvolutionarySolution",
        assembled: "EvolutionarySolution",
        rationale: str,
        risks: List[str],
    ) -> None:
        t_edit_count = len(getattr(t_best, "_edit_history", []))
        dp_edit_count = len(getattr(dp_best, "_edit_history", []))
        same_winner = t_best.solution_id == dp_best.solution_id

        self._write(
            f"\n---\n\n"
            f"## Phase 2: Programmatic Orthogonal Merge\n\n"
            f"- **T_best:** `{t_best.solution_id}` "
            f"(fitness={t_best.fitness_score:.1f}, {t_edit_count} cumulative edits)\n"
            f"- **DP_best:** `{dp_best.solution_id}` "
            f"(fitness={dp_best.fitness_score:.1f}, {dp_edit_count} cumulative edits)\n"
            f"- **Same winner:** {'Yes (skip merge)' if same_winner else 'No (orthogonal merge)'}\n"
            f"- **Assembled:** `{assembled.solution_id}`\n\n"
            f"**Merge Rationale:** {rationale}\n\n"
        )
        if risks:
            self._write("**Merge Conflicts/Risks:**\n" + "\n".join(f"- {r}" for r in risks) + "\n")
        self._write(
            f"\n### Assembled Plan\n\n"
            f"{_plan_summary_table(assembled)}\n"
        )

    def log_sequential_handoff(
        self,
        t_best: "EvolutionarySolution",
        dp_init_score: float,
    ) -> None:
        """Log the T→DP handoff point in Sequential mode."""
        edit_count = len(getattr(t_best, "_edit_history", []))
        self._write(
            f"\n---\n\n"
            f"## Phase 1 Handoff: T-Best → DP-Island\n\n"
            f"- **T-Best:** `{t_best.solution_id}` "
            f"(fitness={t_best.fitness_score:.1f}, "
            f"{edit_count} cumulative topo edits)\n"
            f"- **DP-Judge score on T-Best (DP Init):** {dp_init_score:.1f}/10\n"
            f"- **Mode:** Sequential — DP island will now run all its "
            f"generation(s) on this fixed topology.\n\n"
            f"{_plan_summary_table(t_best)}\n"
        )

    def log_refinement(
        self,
        p_final: "EvolutionarySolution",
        refinement_log: str,
    ) -> None:
        fix_count = len(getattr(p_final, "_latest_edits", []))

        # SAE-No-Merge path: assembler is disabled, so there are no separate
        # consistency fixes — the "fixes" would be the last DP-island edits
        # already listed above.  Use a clear heading to avoid the false
        # impression that 12 extra repairs were applied.
        if refinement_log.startswith("Assembler disabled"):
            self._write(
                f"\n---\n\n"
                f"## Phase 2: Direct Blueprint Packaging\n\n"
                f"- **P_final:** `{p_final.solution_id}`\n"
                f"- **Mode:** SAE-No-Merge (assembler disabled)\n"
                f"- **Note:** P_final = last DP-island winner. The "
                f"{fix_count} edit(s) shown above in DP-Gen-N are already "
                f"part of the cumulative edit history; no separate "
                f"consistency fixes were applied.\n"
                f"- **Log:** {refinement_log[:300]}\n\n"
                f"### Final Plan\n\n"
                f"{_plan_summary_table(p_final)}\n"
            )
            return

        # Standard assembler path: show real consistency fixes.
        self._write(
            f"\n---\n\n"
            f"## Stage 3: Consistency Check\n\n"
            f"- **P_final:** `{p_final.solution_id}`\n"
            f"- **Fixes Applied:** {fix_count}\n"
            f"- **Checker Log:**\n\n"
            f"> {refinement_log[:500].replace(chr(10), chr(10) + '> ')}\n\n"
        )
        if fix_count > 0:
            self._write(
                f"### Consistency Fixes\n\n"
                f"{_edit_log_block(p_final)}\n"
            )
        self._write(
            f"### Final Plan\n\n"
            f"{_plan_summary_table(p_final)}\n"
        )

    def log_validity_check(
        self,
        p_final: "EvolutionarySolution",
        p0: "EvolutionarySolution",
        result: "JudgeResult",
        outcome: str,
    ) -> None:
        self._write(
            f"\n---\n\n"
            f"## Phase 3: Validity Check (P_final vs P0)\n\n"
            f"- **P_final:** `{p_final.solution_id}` (score={result.score_a:.1f})\n"
            f"- **P0:** `{p0.solution_id}` (score={result.score_b:.1f})\n"
            f"- **Outcome:** {outcome}\n"
            f"- **Feedback:**\n\n"
            f"> {result.feedback[:500].replace(chr(10), chr(10) + '> ') if result.feedback else '--'}\n"
        )

    def log_finish(
        self,
        succeeded: bool,
        final_solution: "EvolutionarySolution",
        p0: "EvolutionarySolution",
        elapsed: float,
        winning_strategies: Dict[str, str],
        validity_score_final: Optional[float] = None,
        validity_score_p0: Optional[float] = None,
        mode: str = "sae",
    ) -> None:
        total_edits = len(getattr(final_solution, "_edit_history", []))
        # fitness_score on each solution is set by the judge of the *last*
        # tournament round that evaluated it — P0 is scored by the Topology
        # Judge (Phase 1 Init / T-island), while the final evolved solution is
        # scored by the DP Judge (last DP-island round).  These two values are
        # NOT directly comparable (different judges, different dimensions).
        # The Validity Check scores (Phase 3, Topology Judge, same conditions
        # for both plans) are the authoritative apples-to-apples comparison.
        rows = (
            f"| Succeeded | {'Y' if succeeded else 'N (fallback to P0)'} |\n"
            f"| Evolution Mode | {mode.upper()} |\n"
            f"| P0 Fitness (Topo Judge, last tournament) | {p0.fitness_score:.1f} |\n"
            f"| Final Fitness (DP Judge, last tournament) | {final_solution.fitness_score:.1f} |\n"
        )
        if validity_score_final is not None and validity_score_p0 is not None:
            rows += (
                f"| P_final Score (Topo Judge, Phase 3) | {validity_score_final:.1f} |\n"
                f"| P0 Score (Topo Judge, Phase 3) | {validity_score_p0:.1f} |\n"
                f"| Fitness Gain (Phase 3, same judge) | {validity_score_final - validity_score_p0:+.1f} |\n"
            )
        else:
            rows += (
                f"| Fitness Gain (⚠ cross-judge, not comparable) | "
                f"{final_solution.fitness_score - p0.fitness_score:+.1f} |\n"
            )
        rows += (
            f"| Total Edits Applied | {total_edits} |\n"
            f"| Elapsed | {elapsed:.1f}s |\n"
            f"| Final Solution | `{final_solution.solution_id}` |\n"
        )
        self._write(
            f"\n---\n\n"
            f"## Summary\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"{rows}\n"
        )
        if winning_strategies:
            self._write("**Winning Strategies:**\n")
            for island, strategy in winning_strategies.items():
                self._write(f"- {island}: `{strategy}`\n")

        self._write(
            f"\n**End Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        if self._path:
            logger.info(f"[MIPE-LOG] Evolution log saved to: {self._path}")
