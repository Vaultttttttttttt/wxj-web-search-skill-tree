"""Evolver — LLM-powered plan mutator for MIPE functional islands.

Edit-script architecture: instead of outputting a complete plan, each
island's Evolver outputs targeted edits that the system applies
deterministically.

  - Island-DP: outputs List[PromptEdit] via DPEvolverSignature
  - Island-T:  outputs List[TopoEdit] via TopoEvolverSignature

MutationStrategyTracker provides influence-weighted strategy selection
(MASS-inspired): historical win-rates are tracked via EMA and used in
Softmax-weighted sampling, replacing uniform random choice.
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import Dict, List, Optional, TYPE_CHECKING

from loguru import logger

from roma_dspy.core.signatures.base_models.evolutionary_solution import (
    EvolutionarySolution,
    _set_edit_history,
)
from roma_dspy.core.engine.evolution.strategy_store import StrategyStore

if TYPE_CHECKING:
    from roma_dspy.core.modules.base_module import BaseModule


ISLAND_DIMENSIONS = {
    "topology": [
        "add_node", "split_node", "redirect_deps", "delete_node",
    ],
    "dynamic_prompt": [
        "set_scope_boundary", "set_evidence_spec", "set_execution_method",
        "set_depth_allocation", "transfer_anchor",
    ],
}

_DEFAULT_WIN_RATES: Dict[str, Dict[str, float]] = {
    island: {s: 0.5 for s in strategies}
    for island, strategies in ISLAND_DIMENSIONS.items()
}

_EMA_ALPHA = 0.3


# =====================================================================
# MutationStrategyTracker
# =====================================================================


class MutationStrategyTracker:
    """Tracks per-strategy historical win-rates and provides weighted sampling.

    Win rates are persisted to ``StrategyStore`` across MIPE runs,
    implementing the MASS-inspired influence-weighted mutation selection.

    Strategy selection:
      - Uses Softmax-weighted sampling over historical win rates.
      - ``temperature`` controls exploration vs exploitation:
          high (0.8-1.0)  → near-uniform (cold-start / little history)
          low  (0.3-0.5)  → exploit high-win strategies (mature archive)
      - Temperature is auto-scaled based on the number of completed runs
        stored in the StrategyStore (< 10 runs → high temp; ≥ 20 → low temp).

    Win-rate updates use Exponential Moving Average (α=0.3) to prevent
    early noise from locking in bad choices.
    """

    _COLD_START_RUNS = 10
    _WARM_RUNS = 20
    _TEMP_HIGH = 0.9
    _TEMP_LOW = 0.35

    def __init__(self, store: StrategyStore) -> None:
        self._store = store
        loaded = store.load_strategy_win_rates()
        self.win_rates: Dict[str, Dict[str, float]] = {
            island: dict(strategies)
            for island, strategies in _DEFAULT_WIN_RATES.items()
        }
        if loaded:
            for island, rates in loaded.items():
                if island in self.win_rates:
                    for strategy, rate in rates.items():
                        if strategy in self.win_rates[island]:
                            self.win_rates[island][strategy] = float(rate)

        self._temperature = self._compute_temperature(store.total_runs())
        logger.debug(
            f"[STRATEGY-TRACKER] Loaded win_rates. "
            f"temperature={self._temperature:.2f} "
            f"(runs={store.total_runs()})"
        )

    def select_strategy(self, island: str) -> str:
        """Softmax-weighted strategy sampling."""
        if island not in self.win_rates:
            raise ValueError(f"Unknown island '{island}'")
        rates = self.win_rates[island]
        keys = list(rates.keys())
        probs = _softmax(list(rates.values()), temperature=self._temperature)
        return _weighted_choice(keys, probs)

    def update(self, island: str, strategy: str, won: bool) -> None:
        """EMA update after a tournament round."""
        if island not in self.win_rates or strategy not in self.win_rates[island]:
            return
        old = self.win_rates[island][strategy]
        reward = 1.0 if won else 0.0
        self.win_rates[island][strategy] = (1 - _EMA_ALPHA) * old + _EMA_ALPHA * reward
        logger.debug(
            f"[STRATEGY-TRACKER] {island}/{strategy}: "
            f"{old:.3f} → {self.win_rates[island][strategy]:.3f} "
            f"(won={won})"
        )

    def persist(self) -> None:
        """Save current win rates back to the StrategyStore."""
        self._store.save_strategy_win_rates(self.win_rates)

    def _compute_temperature(self, total_runs: int) -> float:
        if total_runs < self._COLD_START_RUNS:
            return self._TEMP_HIGH
        if total_runs >= self._WARM_RUNS:
            return self._TEMP_LOW
        ratio = (total_runs - self._COLD_START_RUNS) / (
            self._WARM_RUNS - self._COLD_START_RUNS
        )
        return self._TEMP_HIGH + ratio * (self._TEMP_LOW - self._TEMP_HIGH)


# =====================================================================
# Evolver (Edit-Script Architecture)
# =====================================================================


class Evolver:
    """Generates mutated plan variants using the edit-script paradigm.

    The evolver LLM autonomously selects the best mutation strategy based
    on diagnostic feedback — the Tracker is only used for post-hoc
    statistical recording, not for strategy selection.

    DP island: LLM outputs ``chosen_strategy`` + ``List[PromptEdit]``,
    applied via ``solution.apply_prompt_edits()``.

    T island: LLM outputs ``chosen_strategy`` + ``List[TopoEdit]``,
    applied via ``solution.apply_topo_edits()``.
    """

    def __init__(
        self,
        agent: "BaseModule",
        dimension: str,
        tracker: MutationStrategyTracker,
        strategies: Optional[List[str]] = None,
    ) -> None:
        if dimension not in ISLAND_DIMENSIONS:
            raise ValueError(
                f"Unknown dimension '{dimension}'. "
                f"Must be one of: {list(ISLAND_DIMENSIONS)}"
            )
        self.agent = agent
        self.dimension = dimension
        self.tracker = tracker
        self.strategies = strategies or ISLAND_DIMENSIONS[dimension]

    def _build_adaptive_skeleton(
        self,
        solution: EvolutionarySolution,
        focus_nodes: Optional[List[int]],
    ) -> str:
        """Build island-specific L1 overview + L2 window from *focus_nodes*.

        When *focus_nodes* is provided (from the previous Judge output),
        returns a compact ``[PLAN OVERVIEW] + [FOCUS WINDOW]`` instead
        of the full skeleton, reducing token consumption by ~60%.
        """
        if not focus_nodes:
            return solution.to_skeleton_json()

        if self.dimension == "dynamic_prompt":
            overview = solution.to_skeleton_overview_dp()
        else:
            overview = solution.to_skeleton_overview_topo()

        window = solution.to_skeleton_window(
            focus_indices=focus_nodes,
            hops=1,
            include_prompts=(self.dimension == "dynamic_prompt"),
        )
        return f"[PLAN OVERVIEW]\n{overview}\n\n[FOCUS WINDOW]\n{window}"

    async def mutate(
        self,
        solution: EvolutionarySolution,
        goal: Optional[str] = None,
        previous_feedback: Optional[str] = None,
        focus_nodes: Optional[List[int]] = None,
    ) -> EvolutionarySolution:
        """Produce a single mutated variant of *solution* via edit-script.

        The evolver LLM receives the full strategy menu and autonomously
        selects the best one based on ``previous_feedback``.
        """
        effective_goal = goal or _extract_goal_from_solution(solution)
        available = ", ".join(self.strategies)

        logger.info(
            f"[EVOLVER] island={self.dimension} "
            f"available_strategies=[{available}] "
            f"parent={solution.solution_id}"
        )

        if self.dimension == "dynamic_prompt":
            variant = await self._mutate_dp(solution, effective_goal, available, previous_feedback, focus_nodes)
        else:
            variant = await self._mutate_topo(solution, effective_goal, available, previous_feedback, focus_nodes)

        return variant

    async def _mutate_dp(
        self,
        solution: EvolutionarySolution,
        goal: str,
        available_strategies: str,
        previous_feedback: Optional[str],
        focus_nodes: Optional[List[int]] = None,
    ) -> EvolutionarySolution:
        """DP-island edit-script mutation."""
        skeleton = self._build_adaptive_skeleton(solution, focus_nodes)

        try:
            result = await self.agent.aforward(
                goal=goal,
                plan_skeleton=skeleton,
                available_strategies=available_strategies,
                previous_feedback=previous_feedback,
            )
        except Exception as e:
            logger.warning(
                f"[EVOLVER] DP edit-script call failed: {e}, keeping parent"
            )
            clone = solution.clone_with_mutation(
                island_id=self.dimension,
                mutation_log=f"PARSE_FAIL: {str(e)[:100]}",
            )
            # Preserve cumulative edit history so Total-Edits-Applied in the
            # final summary correctly reflects all edits made up to this point.
            _set_edit_history(clone, solution, [])
            clone._is_empty_variant = True  # type: ignore[attr-defined]
            return clone

        chosen = getattr(result, "chosen_strategy", "unknown")
        edits = getattr(result, "prompt_edits", None) or []
        mutation_log = getattr(result, "mutation_log", f"dp/{chosen}")

        if not edits:
            logger.warning(
                f"[EVOLVER] DP evolver returned empty edits (chose {chosen}), "
                f"keeping parent"
            )
            clone = solution.clone_with_mutation(
                island_id=self.dimension,
                mutation_log=f"NO_EDITS: {chosen} returned empty edit list",
            )
            # Preserve cumulative edit history so Total-Edits-Applied in the
            # final summary correctly reflects all edits made up to this point.
            _set_edit_history(clone, solution, [])
            clone._applied_strategy = chosen  # type: ignore[attr-defined]
            clone._is_empty_variant = True  # type: ignore[attr-defined]
            return clone

        # Defensive truncation: cap at MAX_DP_EDITS_PER_VARIANT edits.
        # Prioritise unique-node coverage (one edit per node first), then fill
        # remaining slots with same-node edits to maximise breadth.
        _MAX_DP_EDITS = 8
        if len(edits) > _MAX_DP_EDITS:
            logger.warning(
                f"[EVOLVER] DP variant emitted {len(edits)} edits "
                f"(>{_MAX_DP_EDITS}). Truncating to top-{_MAX_DP_EDITS} "
                f"preserving node coverage."
            )
            seen_nodes: set = set()
            truncated: list = []
            # Pass 1: one edit per unique node
            for e in edits:
                node_idx = getattr(e, "node_index", None)
                if node_idx not in seen_nodes:
                    truncated.append(e)
                    seen_nodes.add(node_idx)
                    if len(truncated) >= _MAX_DP_EDITS:
                        break
            # Pass 2: fill remaining slots with multi-edit nodes
            if len(truncated) < _MAX_DP_EDITS:
                for e in edits:
                    if e not in truncated:
                        truncated.append(e)
                        if len(truncated) >= _MAX_DP_EDITS:
                            break
            edits = truncated

        variant = solution.apply_prompt_edits(
            edits,
            island_id=self.dimension,
            mutation_log=mutation_log,
        )
        variant._applied_strategy = chosen  # type: ignore[attr-defined]
        return variant

    async def _mutate_topo(
        self,
        solution: EvolutionarySolution,
        goal: str,
        available_strategies: str,
        previous_feedback: Optional[str],
        focus_nodes: Optional[List[int]] = None,
    ) -> EvolutionarySolution:
        """T-island edit-script mutation.

        Uses TopoEvolverSignature. The LLM outputs chosen_strategy +
        List[TopoEdit] which are applied via solution.apply_topo_edits().
        """
        skeleton = self._build_adaptive_skeleton(solution, focus_nodes)

        try:
            result = await self.agent.aforward(
                goal=goal,
                plan_skeleton=skeleton,
                available_strategies=available_strategies,
                previous_feedback=previous_feedback,
            )
        except Exception as e:
            logger.warning(
                f"[EVOLVER] Topo edit-script call failed: {e}, keeping parent"
            )
            clone = solution.clone_with_mutation(
                island_id=self.dimension,
                mutation_log=f"PARSE_FAIL: {str(e)[:100]}",
            )
            # Preserve cumulative edit history so Total-Edits-Applied in the
            # final summary correctly reflects all edits made up to this point.
            _set_edit_history(clone, solution, [])
            clone._is_empty_variant = True  # type: ignore[attr-defined]
            return clone

        chosen = getattr(result, "chosen_strategy", "unknown")
        edits = getattr(result, "topo_edits", None) or []
        mutation_log = getattr(result, "mutation_log", f"topo/{chosen}")

        if not edits:
            logger.warning(
                f"[EVOLVER] Topo evolver returned empty edits (chose {chosen}), "
                f"keeping parent"
            )
            clone = solution.clone_with_mutation(
                island_id=self.dimension,
                mutation_log=f"NO_EDITS: {chosen} returned empty edit list",
            )
            # Preserve cumulative edit history so Total-Edits-Applied in the
            # final summary correctly reflects all edits made up to this point.
            _set_edit_history(clone, solution, [])
            clone._applied_strategy = chosen  # type: ignore[attr-defined]
            clone._is_empty_variant = True  # type: ignore[attr-defined]
            return clone

        variant = solution.apply_topo_edits(
            edits,
            island_id=self.dimension,
            mutation_log=mutation_log,
        )
        variant._applied_strategy = chosen  # type: ignore[attr-defined]
        return variant

    async def generate_variants(
        self,
        seed: EvolutionarySolution,
        goal: Optional[str] = None,
        count: int = 2,
        previous_feedback: Optional[str] = None,
        focus_nodes: Optional[List[int]] = None,
    ) -> List[EvolutionarySolution]:
        """Generate *count* distinct variants from *seed*.

        Each variant independently invokes the evolver LLM which
        autonomously selects its strategy based on feedback analysis.
        After all variants are generated concurrently, duplicate variants
        (same strategy + same mutation log) are detected and regenerated
        with a diversity hint to ensure meaningful population diversity.
        """
        async def _mutate_one(
            diversity_hint: Optional[str] = None,
        ) -> Optional[EvolutionarySolution]:
            fb = previous_feedback
            if diversity_hint:
                fb = f"{fb}\n\n{diversity_hint}" if fb else diversity_hint
            try:
                return await self.mutate(seed, goal=goal, previous_feedback=fb, focus_nodes=focus_nodes)
            except Exception as e:
                logger.error(
                    f"[EVOLVER] Failed to generate variant "
                    f"({self.dimension}): {e}"
                )
                return None

        # Step 1: Generate all variants concurrently.
        results = await asyncio.gather(
            *[_mutate_one() for _ in range(count)]
        )
        variants = [v for v in results if v is not None]

        # Step 2: Recover empty-edit variants when too many produced no edits.
        # If ≥ ceil(count/2) variants are empty (NO_EDITS / PARSE_FAIL),
        # regenerate them once with an explicit recovery hint so the LLM does
        # not keep returning empty lists and polluting the tournament pool.
        def _is_empty(v: EvolutionarySolution) -> bool:
            return getattr(v, "_is_empty_variant", False)

        empty_count = sum(1 for v in variants if _is_empty(v))
        if empty_count and empty_count >= max(1, count // 2):
            logger.warning(
                f"[EVOLVER] {empty_count}/{count} variants returned empty edits. "
                f"Regenerating with recovery hint."
            )
            recovery_hint = (
                "--- RECOVERY HINT ---\n"
                "One or more previous attempts produced an empty edit list. "
                "You MUST output at least 1 non-trivial edit. "
                "If no improvement seems necessary for the current strategy, "
                "switch to a DIFFERENT strategy from the available menu and "
                "target a different node or defect."
            )
            async def _passthrough(v: EvolutionarySolution) -> EvolutionarySolution:
                return v

            recovery_coros = [
                _mutate_one(diversity_hint=recovery_hint) if _is_empty(v) else
                _passthrough(v)
                for v in variants
            ]
            recovered = await asyncio.gather(*recovery_coros)
            variants = [v for v in recovered if v is not None]

        # Step 3: Detect and resolve duplicate variants.
        variants = await self._ensure_diversity(
            variants, seed, goal, previous_feedback, focus_nodes=focus_nodes
        )
        return variants

    async def _ensure_diversity(
        self,
        variants: List[EvolutionarySolution],
        seed: EvolutionarySolution,
        goal: Optional[str],
        previous_feedback: Optional[str],
        focus_nodes: Optional[List[int]] = None,
    ) -> List[EvolutionarySolution]:
        """Detect duplicate variants and regenerate them with a diversity hint.

        Two variants are considered duplicates when they share the same
        ``chosen_strategy`` AND an identical ``mutation_log`` (case-insensitive
        first 200 characters).  When duplicates are found the later copies are
        re-generated with a hint that describes what has already been attempted,
        forcing the LLM to choose a different strategy or target different nodes.
        """
        if len(variants) < 2:
            return variants

        def _variant_key(v: EvolutionarySolution) -> str:
            strategy = getattr(v, "_applied_strategy", "") or ""
            log_prefix = (v.mutation_log or "")[:200].strip().lower()
            return f"{strategy}||{log_prefix}"

        seen_keys: dict = {}
        unique_variants: List[EvolutionarySolution] = []
        duplicate_indices: List[int] = []

        for i, v in enumerate(variants):
            key = _variant_key(v)
            if key in seen_keys:
                duplicate_indices.append(i)
                logger.warning(
                    f"[EVOLVER] Variant {i} is a duplicate of variant "
                    f"{seen_keys[key]} (strategy={getattr(v, '_applied_strategy', '?')}). "
                    f"Will regenerate with diversity hint."
                )
            else:
                seen_keys[key] = i
                unique_variants.append(v)

        if not duplicate_indices:
            return variants

        # Build a diversity hint that lists already-attempted approaches.
        attempted = []
        for v in unique_variants:
            strategy = getattr(v, "_applied_strategy", "unknown")
            log_snippet = (v.mutation_log or "")[:150].strip()
            attempted.append(f"  - Strategy: {strategy}\n    Summary: {log_snippet}")

        diversity_hint = (
            "--- DIVERSITY CONSTRAINT (mandatory) ---\n"
            "The following strategies/edits were already produced in this generation.\n"
            "You MUST select a DIFFERENT strategy OR target completely different nodes.\n"
            "Already attempted:\n"
            + "\n".join(attempted)
        )

        logger.info(
            f"[EVOLVER] Regenerating {len(duplicate_indices)} duplicate variant(s) "
            f"with diversity hint."
        )

        # Regenerate duplicates sequentially (they are rare; latency impact minimal).
        result_list = list(variants)
        for idx in duplicate_indices:
            fb = (
                f"{previous_feedback}\n\n{diversity_hint}"
                if previous_feedback
                else diversity_hint
            )
            try:
                new_v = await self.mutate(seed, goal=goal, previous_feedback=fb, focus_nodes=focus_nodes)
                if new_v is not None:
                    result_list[idx] = new_v
            except Exception as e:
                logger.error(
                    f"[EVOLVER] Failed to regenerate diverse variant {idx}: {e}"
                )

        return result_list


# =====================================================================
# Helpers
# =====================================================================


def _softmax(values: List[float], temperature: float = 1.0) -> List[float]:
    """Temperature-scaled softmax."""
    if temperature <= 0:
        temperature = 1e-8
    scaled = [v / temperature for v in values]
    max_v = max(scaled)
    exps = [math.exp(v - max_v) for v in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def _weighted_choice(keys: List[str], probs: List[float]) -> str:
    r = random.random()
    cumulative = 0.0
    for key, prob in zip(keys, probs):
        cumulative += prob
        if r <= cumulative:
            return key
    return keys[-1]


def _extract_goal_from_solution(solution: EvolutionarySolution) -> str:
    if solution.subtasks:
        return solution.subtasks[0].goal
    return ""
