"""Tournament — intra-island elimination selection for MIPE.

Runs a sequential elimination tournament within a single island:
  P0 (elite seed) vs P1 → winner vs P2 → island_best

The elite seed P0 is never mutated but always participates in the
tournament as a baseline — this implements Elitism.
"""

from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

from roma_dspy.core.signatures.base_models.evolutionary_solution import (
    EvolutionarySolution,
)
from roma_dspy.core.engine.evolution.judge import PlanJudge, JudgeResult


class Tournament:
    """Intra-island elimination tournament using PlanJudge."""

    def __init__(self, judge: PlanJudge) -> None:
        self.judge = judge

    async def run(
        self,
        seed: EvolutionarySolution,
        variants: List[EvolutionarySolution],
        goal: str,
        dimension: str,
        evo_logger: Optional[Any] = None,
        generation: int = 0,
    ) -> TournamentResult:
        """Run elimination tournament: seed vs variants[0] → winner vs variants[1] → ...

        Args:
            seed: The elite P0 solution (participates but was not mutated).
            variants: Mutated variants from this island's evolver.
            goal: Original user query (passed to judge).
            dimension: Which dimension this island optimizes.
            evo_logger: Optional MIPEEvolutionLogger for recording round details.
            generation: Current generation index (for logging).

        Returns:
            TournamentResult with the island's best solution and feedback log.
        """
        if not variants:
            logger.warning(f"[TOURNAMENT] No variants for {dimension}, returning seed")
            return TournamentResult(
                best=seed, runner_up=None, feedback_log=[], dimension=dimension
            )

        current_best = seed
        runner_up: Optional[EvolutionarySolution] = None
        feedback_log: List[str] = []
        # Accumulate focus_nodes across all rounds (union).
        # Note: TournamentResult.focus_nodes is a diagnostic snapshot only —
        # the orchestrator obtains cross-island focus signals via dedicated
        # _evaluate_dimension bridge calls, not from this field.
        all_focus_nodes: set = set()

        for i, variant in enumerate(variants):
            pre_round_best_id = current_best.solution_id
            logger.info(
                f"[TOURNAMENT] {dimension} round {i+1}/{len(variants)}: "
                f"{current_best.solution_id} vs {variant.solution_id}"
            )
            result: JudgeResult = await self.judge.compare(
                sol_a=current_best,
                sol_b=variant,
                goal=goal,
                dimension=dimension,
                debiased=False,
                seed=seed,
            )

            if result.is_tie:
                logger.info(
                    f"[TOURNAMENT] Tie — keeping current best "
                    f"(scores: {result.score_a:.1f} vs {result.score_b:.1f})"
                )
                runner_up = variant
                feedback_log.append(
                    f"Round {i+1}: Tie ({current_best.solution_id} vs "
                    f"{variant.solution_id}). {result.feedback}"
                )
            elif result.winner is current_best:
                logger.info(
                    f"[TOURNAMENT] Current best wins "
                    f"({result.score_a:.1f} vs {result.score_b:.1f})"
                )
                runner_up = variant
                feedback_log.append(
                    f"Round {i+1}: {current_best.solution_id} wins. "
                    f"{result.feedback}"
                )
            else:
                logger.info(
                    f"[TOURNAMENT] Variant wins "
                    f"({result.score_b:.1f} vs {result.score_a:.1f})"
                )
                runner_up = current_best
                current_best = variant
                feedback_log.append(
                    f"Round {i+1}: {variant.solution_id} wins. "
                    f"{result.feedback}"
                )

            current_best.fitness_score = max(
                result.score_a, result.score_b
            )
            all_focus_nodes.update(result.focus_nodes)

            if evo_logger:
                evo_logger.log_tournament_round(
                    dimension=dimension,
                    generation=generation,
                    round_num=i + 1,
                    total_rounds=len(variants),
                    best_id=pre_round_best_id,
                    variant_id=variant.solution_id,
                    result=result,
                )

        return TournamentResult(
            best=current_best,
            runner_up=runner_up,
            feedback_log=feedback_log,
            dimension=dimension,
            focus_nodes=sorted(all_focus_nodes),
        )


class TournamentResult:
    """Outcome of an intra-island tournament."""

    __slots__ = ("best", "runner_up", "feedback_log", "dimension", "focus_nodes")

    def __init__(
        self,
        best: EvolutionarySolution,
        runner_up: Optional[EvolutionarySolution],
        feedback_log: List[str],
        dimension: str,
        focus_nodes: Optional[List[int]] = None,
    ) -> None:
        self.best = best
        self.runner_up = runner_up
        self.feedback_log = feedback_log
        self.dimension = dimension
        self.focus_nodes: List[int] = focus_nodes or []

    @property
    def combined_feedback(self) -> str:
        return "\n".join(self.feedback_log)
