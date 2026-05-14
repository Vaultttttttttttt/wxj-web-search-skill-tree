"""PlanJudge — unified plan evaluator (PlanJudgeSignature).

Uses a single ``PlanJudgeSignature`` for both inter-island evaluation (diagnose)
and tournament comparison.  Diagnose mode is the degenerate case where
edit logs are left at their defaults.

Modes:
  - Tournament (debiased=False): single edit-log comparison for speed.
  - Tournament (debiased=True):  two calls with A/B order swapped.
  - Diagnose: single-plan evaluation (edit logs default to empty).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import List, Optional, Set, TYPE_CHECKING

from loguru import logger

from roma_dspy.core.signatures.base_models.evolutionary_solution import (
    EvolutionarySolution,
    format_edit_log,
)

if TYPE_CHECKING:
    from roma_dspy.core.modules.base_module import BaseModule


@dataclass
class JudgeResult:
    """Outcome of a head-to-head comparison."""

    winner: Optional[EvolutionarySolution]
    loser: Optional[EvolutionarySolution]
    is_tie: bool
    feedback: str
    score_a: float
    score_b: float
    focus_nodes: List[int] = field(default_factory=list)


class PlanJudge:
    """Evaluate plans using PlanJudgeSignature (unified diagnose + compare)."""

    def __init__(
        self,
        agent: "BaseModule",
        static_checklist: str,
        diagnose_agent: Optional["BaseModule"] = None,
        validity_agent: Optional["BaseModule"] = None,
    ) -> None:
        self.agent = agent
        self.static_checklist = static_checklist
        # diagnose_agent kept for backward compat but ignored — use self.agent
        self._diagnose_agent = diagnose_agent
        self._validity_agent = validity_agent

    async def compare(
        self,
        sol_a: EvolutionarySolution,
        sol_b: EvolutionarySolution,
        goal: str,
        dimension: str = "overall",
        debiased: bool = True,
        seed: Optional[EvolutionarySolution] = None,
    ) -> JudgeResult:
        """Compare two plans using edit-log mode when seed is available.

        Uses L1+L2 adaptive skeleton: the judge sees a compact overview of
        the full plan plus detailed information for edited nodes only.

        Args:
            debiased: When True, runs two LLM calls with A/B order swapped.
            seed: The common ancestor. When provided, enables edit-log mode
                (comparing edits rather than full plans).
        """
        effective_seed = seed or sol_a

        edited_indices: set = set()
        for sol in (sol_a, sol_b):
            for edit in getattr(sol, "_latest_edits", []) or []:
                if hasattr(edit, "node_index"):
                    try:
                        edited_indices.add(int(edit.node_index))
                    except (ValueError, TypeError):
                        pass
                if hasattr(edit, "target_node"):
                    try:
                        edited_indices.add(int(edit.target_node))
                    except (ValueError, TypeError):
                        pass

        if edited_indices:
            if dimension == "topology":
                overview = effective_seed.to_skeleton_overview_topo()
            else:
                overview = effective_seed.to_skeleton_overview_dp()
            window = effective_seed.to_skeleton_window(
                sorted(edited_indices), hops=1,
                include_prompts=(dimension == "dynamic_prompt"),
            )
            seed_skeleton = (
                f"[PLAN OVERVIEW]\n{overview}\n\n"
                f"[EDITED REGION DETAIL]\n{window}"
            )
        else:
            seed_skeleton = effective_seed.to_skeleton_json(include_prompts_for=["all"])

        edit_log_a = self._build_edit_log(sol_a, dimension=dimension)
        edit_log_b = self._build_edit_log(sol_b, dimension=dimension)

        if not debiased:
            return await self._compare_editlog(
                sol_a, sol_b, seed_skeleton, edit_log_a, edit_log_b,
                goal, dimension,
            )

        forward, reversed_ = await asyncio.gather(
            self._single_editlog_compare(
                goal=goal,
                seed_skeleton=seed_skeleton,
                edit_log_a=edit_log_a,
                edit_log_b=edit_log_b,
                dimension=dimension,
            ),
            self._single_editlog_compare(
                goal=goal,
                seed_skeleton=seed_skeleton,
                edit_log_a=edit_log_b,
                edit_log_b=edit_log_a,
                dimension=dimension,
            ),
        )

        fwd_pref = forward["preferred"]
        rev_pref = _flip_preference(reversed_["preferred"])

        logger.debug(
            f"[JUDGE] forward={fwd_pref} reversed(flipped)={rev_pref} "
            f"dim={dimension}"
        )

        # Merge focus_nodes from both directions (union, deduplicated)
        merged_focus = sorted(set(
            forward.get("focus_nodes", []) + reversed_.get("focus_nodes", [])
        ))

        if fwd_pref == rev_pref and fwd_pref in ("A", "B"):
            winner = sol_a if fwd_pref == "A" else sol_b
            loser = sol_b if fwd_pref == "A" else sol_a
            feedback = forward["improvement_signals"]
            return JudgeResult(
                winner=winner,
                loser=loser,
                is_tie=False,
                feedback=feedback,
                score_a=forward["score_a"],
                score_b=forward["score_b"],
                focus_nodes=merged_focus,
            )

        avg_a = (forward["score_a"] + reversed_["score_b"]) / 2
        avg_b = (forward["score_b"] + reversed_["score_a"]) / 2
        feedback = (
            f"Tie (inconsistent position swap). "
            f"Forward: {forward['improvement_signals']} | "
            f"Reversed: {reversed_['improvement_signals']}"
        )
        return JudgeResult(
            winner=None,
            loser=None,
            is_tie=True,
            feedback=feedback,
            score_a=avg_a,
            score_b=avg_b,
            focus_nodes=merged_focus,
        )

    async def _compare_editlog(
        self,
        sol_a: EvolutionarySolution,
        sol_b: EvolutionarySolution,
        seed_skeleton: str,
        edit_log_a: str,
        edit_log_b: str,
        goal: str,
        dimension: str,
    ) -> JudgeResult:
        """Single-direction edit-log comparison (1 LLM call)."""
        result = await self._single_editlog_compare(
            goal=goal,
            seed_skeleton=seed_skeleton,
            edit_log_a=edit_log_a,
            edit_log_b=edit_log_b,
            dimension=dimension,
        )
        pref = result["preferred"]
        if pref == "A":
            winner, loser = sol_a, sol_b
        elif pref == "B":
            winner, loser = sol_b, sol_a
        else:
            winner, loser = None, None
        return JudgeResult(
            winner=winner,
            loser=loser,
            is_tie=(pref not in ("A", "B")),
            feedback=result["improvement_signals"],
            score_a=result["score_a"],
            score_b=result["score_b"],
            focus_nodes=result.get("focus_nodes", []),
        )

    async def diagnose(
        self,
        solution: EvolutionarySolution,
        goal: str,
        dimension: str = "overall",
    ) -> tuple:
        """Self-evaluate a plan to bootstrap feedback for the first generation.

        Uses the same ``PlanJudgeSignature`` as ``compare()``, but with
        edit logs left at their defaults (diagnose = degenerate compare).

        Returns:
            ``(feedback_str, focus_nodes, score)`` where *feedback_str*
            may be ``None`` if the LLM found no issues.
        """
        try:
            plan_skeleton = solution.to_skeleton_json(include_prompts_for=["all"])
            result = await self.agent.aforward(
                goal=goal,
                seed_plan_skeleton=plan_skeleton,
                evaluation_dimension=dimension,
                static_checklist=self.static_checklist,
            )

            signals = getattr(result, "improvement_signals", "") or ""
            score = _safe_float(getattr(result, "plan_a_score", 10.0))
            defects = getattr(result, "plan_a_defects", []) or []
            raw_focus = getattr(result, "focus_nodes", [])
            # Supplement LLM focus_nodes with indices extracted from text fields
            # so the Evolver's focus window always covers every node the judge cited.
            focus = _supplement_focus(
                raw_focus,
                signals,
                *(str(d) for d in defects),
            )

            defect_str = "; ".join(str(d) for d in defects) if defects else ""

            if not signals:
                return None, focus, score

            parts = [f"[Pre-Island Evaluation ({dimension})] score={score:.1f}/10"]
            if defect_str:
                parts.append(f"Defects: {defect_str}")
            parts.append(signals)
            parts.append("Target mutations at the identified defects.")
            feedback_str = "\n".join(parts)
            return feedback_str, focus, score

        except Exception as e:
            logger.warning(f"[JUDGE] P0 self-diagnosis failed for {dimension}: {e}")
            return None, [], 0.0

    async def _single_editlog_compare(
        self,
        goal: str,
        seed_skeleton: str,
        edit_log_a: str,
        edit_log_b: str,
        dimension: str,
    ) -> dict:
        """Run one direction of the edit-log comparison."""
        try:
            result = await self.agent.aforward(
                goal=goal,
                seed_plan_skeleton=seed_skeleton,
                edit_log_a=edit_log_a,
                edit_log_b=edit_log_b,
                evaluation_dimension=dimension,
                static_checklist=self.static_checklist,
            )
            preferred = str(getattr(result, "preferred_plan", "tie")).strip().upper()
            if preferred not in ("A", "B", "TIE"):
                preferred = "TIE"

            defects_a = getattr(result, "plan_a_defects", None) or []
            defects_b = getattr(result, "plan_b_defects", None) or []
            signals = getattr(result, "improvement_signals", "") or ""

            rich_feedback_parts = []
            if signals:
                rich_feedback_parts.append(f"Signals: {signals}")
            if defects_a:
                rich_feedback_parts.append(f"Variant-A defects: {'; '.join(str(d) for d in defects_a)}")
            if defects_b:
                rich_feedback_parts.append(f"Variant-B defects: {'; '.join(str(d) for d in defects_b)}")

            raw_focus = getattr(result, "focus_nodes", [])
            # Supplement LLM focus_nodes with indices extracted from text fields
            # so the Evolver's focus window always covers every node the judge cited.
            focus = _supplement_focus(
                raw_focus,
                signals,
                *(str(d) for d in defects_a),
                *(str(d) for d in defects_b),
            )

            return {
                "preferred": preferred,
                "score_a": _safe_float(getattr(result, "plan_a_score", 5.0)),
                "score_b": _safe_float(getattr(result, "plan_b_score", 5.0)),
                "improvement_signals": " | ".join(rich_feedback_parts) if rich_feedback_parts else "",
                "focus_nodes": focus,
            }
        except Exception as e:
            logger.error(f"[JUDGE] Edit-log compare failed: {e}")
            return {
                "preferred": "TIE",
                "score_a": 5.0,
                "score_b": 5.0,
                "improvement_signals": f"Judge error: {e}",
                "focus_nodes": [],
            }

    @staticmethod
    def _build_edit_log(
        solution: EvolutionarySolution,
        dimension: str = "overall",
    ) -> str:
        """Format a solution's edit history as a readable log for the judge.

        Args:
            dimension: The evaluation dimension ("topology", "dynamic_prompt",
                "overall").  When set to "dynamic_prompt" the full
                ``mutation_log`` text from a previous topology step is
                suppressed — only the current step's structured edits and
                strategy are forwarded.  This prevents stale node-index
                descriptions from a topology mutation ("deleted Node 30")
                from polluting the DP judge's understanding of the
                post-topology plan where indices may have shifted.
        """
        edits = getattr(solution, "_latest_edits", None)
        strategy = getattr(solution, "_applied_strategy", "unknown")
        mutation_log = solution.mutation_log or ""

        parts = [f"Strategy: {strategy}"]

        # Only include the free-text mutation_log when it is safe to do so.
        # For DP evaluation, topo mutation_log text can reference stale node
        # indices (e.g. "deleted Node 30") that no longer exist in the
        # current plan, misleading the DP judge about plan structure.
        # We include it for topo/overall evaluation where structural context
        # is always relevant.
        include_mutation_log = (dimension != "dynamic_prompt") or (
            # For DP dimension, only include if all edits are prompt edits
            # (no topology ops), meaning the mutation_log is safe.
            edits is not None
            and all(hasattr(e, "section_content") or hasattr(e, "new_dynamic_prompt")
                    for e in edits)
        )
        if mutation_log and include_mutation_log:
            parts.append(f"Mutation log: {mutation_log}")

        if edits:
            # For DP evaluation, filter to only show prompt-type edits.
            if dimension == "dynamic_prompt":
                dp_edits = [
                    e for e in edits
                    if hasattr(e, "section_content") or hasattr(e, "new_dynamic_prompt")
                ]
                topo_edits = [e for e in edits if e not in dp_edits]
                if topo_edits:
                    parts.append(
                        f"[Note: {len(topo_edits)} topology edit(s) from a "
                        f"prior T-island step are omitted here — "
                        f"node indices in this DP evaluation reflect the "
                        f"post-topology plan.]"
                    )
                if dp_edits:
                    parts.append(
                        f"Edits:\n{format_edit_log(dp_edits, prompt_preview_chars=0, include_input_mode=True)}"
                    )
                else:
                    parts.append("(no prompt edits in this step — seed plan for DP dimension)")
            else:
                # Judge must see full prompt rewrites for reliable anchor scoring.
                parts.append(
                    f"Edits:\n{format_edit_log(edits, prompt_preview_chars=0, include_input_mode=True)}"
                )
        else:
            parts.append("(seed plan — no edits applied)")
        return "\n".join(parts)


def _flip_preference(pref: str) -> str:
    """Flip A↔B for position-swap debiasing."""
    if pref == "A":
        return "B"
    if pref == "B":
        return "A"
    return "TIE"


def _safe_float(value, default: float = 5.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_NODE_IDX_RE = re.compile(r"\bnodes?\s+(\d+)", re.IGNORECASE)


def _extract_node_indices(text: str) -> Set[int]:
    """Extract integer node indices mentioned in *text* (e.g. "node 5", "nodes 3,4").

    Used as a belt-and-suspenders post-processor to ensure that any node index
    cited in improvement_signals or defect descriptions is included in the
    focus_nodes list, even if the LLM omitted it from the JSON array.
    """
    return {int(m) for m in _NODE_IDX_RE.findall(text)}


def _supplement_focus(
    focus: object,
    *texts: Optional[str],
) -> List[int]:
    """Merge LLM-returned *focus* list with indices extracted from *texts*.

    Normalises *focus* to a list regardless of what the LLM returned,
    then takes the union with every node index mentioned in the text args.
    Returns a sorted, deduplicated list.
    """
    if isinstance(focus, (list, tuple)):
        base: Set[int] = set()
        for item in focus:
            try:
                base.add(int(item))
            except (TypeError, ValueError):
                pass
    else:
        base = set()

    for text in texts:
        if text:
            base.update(_extract_node_indices(str(text)))

    return sorted(base)
