"""MIPEOrchestrator — MASS-inspired parallel evolution pipeline.

Flow:
  Phase 0 :  Planner + Atomizer recursive decomposition → seed P0
  Init Eval: topo_judge.diagnose(P0) → t_feedback, t_focus for gen-1 T-island
  Phase 1 :  Serial Alternating Evolution (SAE):
               Gen N: T-island(t_focus) → T-Best
                      → dp_judge.diagnose(T-Best) → dp_focus  [bridge T→DP]
                      → DP-island(dp_focus) → DP-Best
                      → topo_judge.diagnose(DP-Best) → t_focus [bridge DP→T, if more gens]
  Phase 2 :  Blueprint packaging → P_final
  Phase 3 :  Validity check: P_final vs P0 (debiased plan judge)
             → Fallback to P0 only if P0 clearly wins (ties keep P_final)

LLM call budget: ~(3 + (pop_size+1)*2*G) + Phase 0  (G = max_generations)
  Init Eval:  1 judge (topology only)                       = 1
  Phase 0:    1 planner + N atomizer + M child planner     = variable
  Phase 1:    G × (pop_size evolver + 1 bridge judge) × 2  (T + DP islands)
  Phase 2:    Direct blueprint packaging (no LLM call)      = 0
  Phase 3:    2 judge (×2 swap, debiased)                  = 2
             (skipped when EvolutionConfig.enable_validity_check=False)

Anytime property:
  After Phase 1 T-step: T_best is a valid output (optimised topology).
  After Phase 1 DP-step: DP_best is the best output so far.
  After Phase 2: P_final is the best output.
  Phase 3: P0 is returned only if it clearly beats P_final.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from typing import Any, Dict, List, Optional, Tuple, Type, TYPE_CHECKING

import dspy
from loguru import logger

from roma_dspy.core.signatures.base_models.evolutionary_solution import (
    EvolutionarySolution,
)
from roma_dspy.core.signatures.base_models.plan_blueprint import PlanBlueprint
from roma_dspy.core.signatures.signatures import (
    DPEvolverSignature,
    TopoEvolverSignature,
    PlanJudgeSignature,
)
from roma_dspy.core.engine.evolution.evolver import (
    Evolver,
    MutationStrategyTracker,
    ISLAND_DIMENSIONS,
)
from roma_dspy.core.engine.evolution.judge import PlanJudge, JudgeResult
from roma_dspy.core.engine.evolution.tournament import Tournament, TournamentResult
from roma_dspy.core.engine.evolution.assembler import _solution_to_blueprint
from roma_dspy.core.engine.evolution.strategy_store import StrategyStore

if TYPE_CHECKING:
    from roma_dspy.config.schemas.warmup import EvolutionConfig, WarmupAgentConfig
    from roma_dspy.core.modules.base_module import BaseModule

_sig_counter = itertools.count()


class MIPEOrchestrator:
    """Orchestrates the MASS-inspired MIPE sequential evolution search."""

    _DEFAULT_MAX_CONCURRENT_LLM: int = 8

    def __init__(
        self,
        evolution_config: "EvolutionConfig",
        roma_config: Any = None,
        planner_agent: Optional["BaseModule"] = None,
        atomizer_agent: Optional["BaseModule"] = None,
        max_depth: int = 2,
        storage_dir: str = ".mipe_store",
        mlflow_manager: Optional[Any] = None,
        max_concurrent_llm: Optional[int] = None,
        logs_dir: Optional[str] = None,
    ) -> None:
        self.config = evolution_config
        self.roma_config = roma_config
        self.planner_agent = planner_agent
        self.atomizer_agent = atomizer_agent
        self.max_depth = max_depth

        concurrency = max_concurrent_llm or self._DEFAULT_MAX_CONCURRENT_LLM
        self._llm_semaphore = asyncio.Semaphore(concurrency)

        self._strategy_store = StrategyStore(storage_dir=storage_dir)
        self._tracker: Optional[MutationStrategyTracker] = None
        self._evolvers: Dict[str, Evolver] = {}
        self._judges: Dict[str, PlanJudge] = {}
        self._built = False

        from roma_dspy.core.observability.mipe_span_logger import MIPESpanLogger
        self._span_logger = MIPESpanLogger(mlflow_manager)

        from roma_dspy.core.engine.evolution.evolution_logger import MIPEEvolutionLogger
        self._evo_logger = MIPEEvolutionLogger(logs_dir)

    # ------------------------------------------------------------------
    # Agent Construction
    # ------------------------------------------------------------------

    def _build_agents(self) -> None:
        if self._built:
            return

        self._tracker = MutationStrategyTracker(self._strategy_store)

        strategies_map = getattr(self.config, "mutation_strategies", None) or {}

        dp_evolver_agent = self._make_agent(
            custom_signature=DPEvolverSignature,
            warmup_agent_cfg=getattr(self.config, "evolver", None),
            fallback_instructions=(
                "prompt_optimization.prompts.seed_prompts.dr_dp_evolver_seed:DP_EVOLVER_PROMPT"
            ),
            fallback_demos=(
                "prompt_optimization.prompts.seed_prompts.dr_dp_evolver_seed:DP_EVOLVER_DEMOS"
            ),
        )
        self._evolvers["dynamic_prompt"] = Evolver(
            agent=dp_evolver_agent,
            dimension="dynamic_prompt",
            tracker=self._tracker,
            strategies=strategies_map.get("dynamic_prompt"),
        )

        topo_evolver_agent = self._make_agent(
            custom_signature=TopoEvolverSignature,
            warmup_agent_cfg=getattr(self.config, "evolver", None),
            fallback_instructions=(
                "prompt_optimization.prompts.seed_prompts.dr_topo_evolver_seed:TOPO_EVOLVER_PROMPT"
            ),
            fallback_demos=(
                "prompt_optimization.prompts.seed_prompts.dr_topo_evolver_seed:TOPO_EVOLVER_DEMOS"
            ),
        )
        self._evolvers["topology"] = Evolver(
            agent=topo_evolver_agent,
            dimension="topology",
            tracker=self._tracker,
            strategies=strategies_map.get("topology"),
        )

        from prompt_optimization.prompts.seed_prompts.dr_judge_topo_seed import (
            TOPOLOGY_JUDGE_PROMPT,
            TOPOLOGY_JUDGE_CHECKLIST,
        )
        from prompt_optimization.prompts.seed_prompts.dr_judge_dp_seed import (
            DP_JUDGE_PROMPT,
            DP_JUDGE_CHECKLIST,
        )

        # ── Topology Judge (T-island + Phase 3 overall) ───────────────────
        topo_judge_agent = self._make_agent(
            custom_signature=PlanJudgeSignature,
            warmup_agent_cfg=getattr(self.config, "judge_topology", None),
            fallback_instructions=(
                "prompt_optimization.prompts.seed_prompts"
                ".dr_judge_topo_seed:TOPOLOGY_JUDGE_PROMPT"
            ),
            fallback_demos=(
                "prompt_optimization.prompts.seed_prompts"
                ".dr_judge_topo_seed:TOPOLOGY_JUDGE_DEMOS"
            ),
        )

        # ── DP Judge (DP-island) ──────────────────────────────────────────
        dp_judge_agent = self._make_agent(
            custom_signature=PlanJudgeSignature,
            warmup_agent_cfg=getattr(self.config, "judge_dynamic_prompt", None),
            fallback_instructions=(
                "prompt_optimization.prompts.seed_prompts"
                ".dr_judge_dp_seed:DP_JUDGE_PROMPT"
            ),
            fallback_demos=(
                "prompt_optimization.prompts.seed_prompts"
                ".dr_judge_dp_seed:DP_JUDGE_DEMOS"
            ),
        )

        self._judges = {
            "topology": PlanJudge(
                agent=topo_judge_agent,
                static_checklist=TOPOLOGY_JUDGE_CHECKLIST,
            ),
            "dynamic_prompt": PlanJudge(
                agent=dp_judge_agent,
                static_checklist=DP_JUDGE_CHECKLIST,
            ),
        }

        self._built = True

    def _make_agent(
        self,
        custom_signature: Type[dspy.Signature],
        warmup_agent_cfg: Optional["WarmupAgentConfig"] = None,
        fallback_instructions: Optional[str] = None,
        fallback_demos: Optional[str] = None,
    ) -> "BaseModule":
        from roma_dspy.config.schemas.agents import AgentConfig
        from roma_dspy.core.modules.base_module import BaseModule
        from roma_dspy.core.utils.instruction_loader import InstructionLoader
        from roma_dspy.core.utils.demo_loader import DemoLoader

        instructions_path = fallback_instructions
        demos_path = fallback_demos
        llm_config = None

        if warmup_agent_cfg is not None:
            instructions_path = getattr(warmup_agent_cfg, "signature_instructions", None) or fallback_instructions
            demos_path = getattr(warmup_agent_cfg, "demos", None) or fallback_demos
            llm_config = getattr(warmup_agent_cfg, "llm", None)

        instructions = None
        if instructions_path:
            try:
                instructions = InstructionLoader().load(instructions_path)
            except Exception as e:
                logger.warning(f"[MIPE] Failed to load instructions {instructions_path}: {e}")

        signature = custom_signature
        if instructions:
            name = f"{custom_signature.__name__}MIPE{next(_sig_counter)}"
            signature = type(name, (custom_signature,), {
                "__doc__": instructions,
                "__module__": custom_signature.__module__,
            })

        config_demos: list = []
        if demos_path:
            try:
                config_demos = DemoLoader().load(demos_path)
            except Exception as e:
                logger.warning(f"[MIPE] Failed to load demos {demos_path}: {e}")

        if llm_config is None and self.roma_config:
            default_agent = getattr(self.roma_config.agents, "executor", None)
            if default_agent and hasattr(default_agent, "llm"):
                llm_config = default_agent.llm

        return BaseModule(
            signature=signature,
            config=AgentConfig(llm=llm_config, prediction_strategy="chain_of_thought"),
            config_demos=config_demos,
        )

    # ------------------------------------------------------------------
    # Main Entry Point
    # ------------------------------------------------------------------

    async def run(
        self,
        goal: str,
        planner_context: Optional[str] = None,
    ) -> "MIPEResult":
        """Execute the full MIPE evolution pipeline.

        Dispatches to ``_run_sequential`` when ``config.mode == 'sequential'``;
        otherwise runs the default Serial Alternating Evolution (SAE) loop.

        Returns a MIPEResult.  On any critical failure, P0 is returned as
        the safe fallback (Anytime guarantee).
        """
        mode = getattr(self.config, "mode", "sae")
        if mode == "sequential":
            return await self._run_sequential(goal, planner_context)

        self._build_agents()
        pop_size = getattr(self.config, "pop_size", 2)
        max_generations = getattr(self.config, "max_generations", 1)
        num_islands = getattr(self.config, "num_islands", 2)
        enable_validity_check = getattr(self.config, "enable_validity_check", True)
        t_start = time.monotonic()
        sl = self._span_logger
        evo = self._evo_logger

        sl.log_params({
            "mipe.pop_size": pop_size,
            "mipe.num_islands": num_islands,
            "mipe.max_generations": max_generations,
            "mipe.enable_validity_check": enable_validity_check,
        })

        evo.log_start(goal, pop_size=pop_size, max_generations=max_generations, num_islands=num_islands)

        # ── Phase 0: seed ─────────────────────────────────────────────
        with sl.phase("MIPE Phase 0: Seed Generation"):
            logger.info("[MIPE] Phase 0: Generating seed P0")
            p0 = await self._generate_seed(goal, planner_context)
            if p0 is None:
                raise RuntimeError("MIPE Phase 0 failed: Planner returned no subtasks")
            logger.info(f"[MIPE] P0 generated: {len(p0.subtasks)} subtasks id={p0.solution_id}")
            evo.log_p0(p0)
            sl.log_metrics({
                "mipe.p0_subtask_count": float(len(p0.subtasks)),
                "mipe.p0_fitness": p0.fitness_score,
            })

        winning_strategy_trajectory: Dict[str, str] = {}

        # ── Phase 1 Init: Evaluate P0 on topology dimension ────────────
        #    Single topo judge call on P0 gives the T-island its first
        #    targeted feedback and focus_nodes, breaking "blind mutation".
        #    No quality gate or regen — the evaluation is purely a signal.
        with sl.phase("MIPE Phase 1 Init: Topology Evaluation"):
            logger.info("[MIPE] Init: Evaluating P0 on topology dimension")
            t_feedback, t_focus, t_init_score = await self._evaluate_dimension(
                p0, "topology", goal
            )
            logger.info(
                f"[MIPE] Init evaluation complete. "
                f"t_feedback={'yes' if t_feedback else 'no'}, "
                f"t_focus={t_focus}, score={t_init_score:.1f}"
            )
            evo.log_init_evaluation(t_feedback)
            sl.log_metrics({"mipe.init_topo_score": t_init_score})

        # ── Phase 1: Serial Alternating Evolution (SAE) ────────────────
        #    T and DP alternate: each island receives freshly-computed
        #    focus_nodes from a bridge judge call on the previous island's
        #    winner, eliminating cross-topology index aliasing entirely.
        logger.info(
            f"[MIPE] Phase 1: Serial Alternating Evolution "
            f"(max_generations={max_generations}, pop_size={pop_size})"
        )
        current = p0
        dp_feedback: Optional[str] = None
        dp_focus: Optional[List[int]] = None
        t_result: Optional[TournamentResult] = None
        dp_result: Optional[TournamentResult] = None
        t_best = p0
        dp_best = p0

        for gen in range(max_generations):
            # Step A: T-island evolves topology using current topo evaluation
            with sl.phase(
                f"MIPE Phase 1-T (gen {gen+1}/{max_generations})",
                {"island": "topology", "generation": gen},
            ):
                t_best, t_result = await self._run_island_one_gen(
                    dimension="topology",
                    seed=current,
                    gen=gen,
                    feedback=t_feedback,
                    focus_nodes=t_focus,
                    goal=goal,
                    pop_size=pop_size,
                    p0=p0,
                    winning_strategies=winning_strategy_trajectory,
                    sl=sl,
                    evo=evo,
                    total_gens=max_generations,
                )

            # Bridge T→DP: evaluate T-winner on DP dimension.
            # dp_focus is produced on the current (post-T-edit) topology,
            # so indices are guaranteed to be valid for the DP evolver.
            with sl.phase(
                f"MIPE Bridge T→DP (gen {gen+1}/{max_generations})",
                {"island": "bridge_t_to_dp", "generation": gen},
            ):
                dp_feedback, dp_focus, dp_bridge_score = await self._evaluate_dimension(
                    t_best, "dynamic_prompt", goal
                )
                logger.info(
                    f"[MIPE] Bridge T→DP gen {gen+1}: "
                    f"dp_focus={dp_focus}, score={dp_bridge_score:.1f}"
                )
                sl.log_metrics({f"mipe.bridge_dp_score_gen{gen}": dp_bridge_score})

            # Step B: DP-island evolves prompts on T's latest topology
            with sl.phase(
                f"MIPE Phase 1-DP (gen {gen+1}/{max_generations})",
                {"island": "dynamic_prompt", "generation": gen},
            ):
                dp_best, dp_result = await self._run_island_one_gen(
                    dimension="dynamic_prompt",
                    seed=t_best,
                    gen=gen,
                    feedback=dp_feedback,
                    focus_nodes=dp_focus,
                    goal=goal,
                    pop_size=pop_size,
                    p0=p0,
                    winning_strategies=winning_strategy_trajectory,
                    sl=sl,
                    evo=evo,
                    total_gens=max_generations,
                )

            current = dp_best

            # Bridge DP→T: evaluate DP-winner on topology dimension.
            # Prepares t_feedback/t_focus for the next generation's T-island.
            if gen < max_generations - 1:
                with sl.phase(
                    f"MIPE Bridge DP→T (gen {gen+1}/{max_generations})",
                    {"island": "bridge_dp_to_t", "generation": gen},
                ):
                    t_feedback, t_focus, t_bridge_score = await self._evaluate_dimension(
                        dp_best, "topology", goal
                    )
                    logger.info(
                        f"[MIPE] Bridge DP→T gen {gen+1}: "
                        f"t_focus={t_focus}, score={t_bridge_score:.1f}"
                    )
                    sl.log_metrics({f"mipe.bridge_t_score_gen{gen}": t_bridge_score})

        self._tracker.persist()

        # ── Phase 2: Blueprint packaging (assembler disabled) ───────────
        p_final = current
        p0_unchanged = (p_final.solution_id == p0.solution_id)

        if evo:
            evo.log_sae_merge_skipped(p_final.solution_id, max_generations)

        # validity_score_* track the Phase-3 Topology Judge scores (the only
        # apples-to-apples comparison between P_final and P0).  They are set
        # only when a real validity check is executed; None means the check
        # was skipped (P0 unchanged or enable_validity_check=False) so the
        # Summary will display a cross-judge warning instead.
        validity_score_final: Optional[float] = None
        validity_score_p0: Optional[float] = None

        if p0_unchanged:
            logger.info("[MIPE] P0 unchanged after evolution — skipping Phase 3")
            final_solution = p0
            final_blueprint = _solution_to_blueprint(
                p0, rationale="P0 won all rounds; no evolution improvement.",
                known_risks=[],
            )
            succeeded = True
            failure_reason = None
            evo.log_refinement(p0, "Skipped — P0 unchanged")
            evo.log_validity_check(
                p0, p0,
                JudgeResult(winner=p0, loser=None, is_tie=False,
                            feedback="Short-circuit: P0 unchanged",
                            score_a=10.0, score_b=10.0),
                "P0 unchanged — no validity check needed",
            )
        else:
            final_blueprint_candidate = _solution_to_blueprint(
                p_final,
                rationale=(
                    f"SAE: {max_generations} generations of T→DP alternation. "
                    f"Assembler disabled; blueprint emitted directly from evolved plan."
                ),
                known_risks=[],
            )
            logger.info(
                f"[MIPE] Blueprint packaged directly from SAE result. P_final={p_final.solution_id}"
            )
            evo.log_refinement(
                p_final,
                "Assembler disabled — direct blueprint packaging from evolved plan.",
            )

            # ── Phase 3: Validity check P_final vs P0 ────────────────────
            if not enable_validity_check:
                logger.info("[MIPE] Phase 3: Validity check skipped (enable_validity_check=False).")
                final_solution = p_final
                final_blueprint = final_blueprint_candidate
                succeeded = True
                failure_reason = None
                evo.log_validity_check(
                    p_final, p0,
                    JudgeResult(winner=p_final, loser=None, is_tie=False,
                                feedback="Validity check disabled via enable_validity_check=False",
                                score_a=10.0, score_b=10.0),
                    "Skipped — enable_validity_check=False",
                )
            else:
                with sl.phase("MIPE Phase 3: Validity Check"):
                    logger.info("[MIPE] Phase 3: Validity check P_final vs P0")
                    validity_result = await self._judges["topology"].compare(
                        sol_a=p_final, sol_b=p0, goal=goal, dimension="overall"
                    )
                    # Capture the Topology Judge's scores for the Summary log.
                    # These are the authoritative same-judge scores that can be
                    # compared directly (unlike the per-island fitness_score values).
                    validity_score_final = validity_result.score_a
                    validity_score_p0 = validity_result.score_b

                    p0_clearly_wins = (
                        validity_result.winner is not None
                        and validity_result.winner.solution_id == p0.solution_id
                    )
                    if p0_clearly_wins:
                        logger.warning("[MIPE] Phase 3: P0 clearly wins. Falling back to P0.")
                        final_solution = p0
                        final_blueprint = _solution_to_blueprint(
                            p0, rationale="Validity check: P0 clearly won.", known_risks=[]
                        )
                        succeeded = False
                        failure_reason = (
                            f"P_final lost to P0 in validity check. "
                            f"Feedback: {validity_result.feedback[:200]}"
                        )
                        validity_outcome = "P0 clearly wins — fallback to P0"
                    else:
                        outcome = "P_final_wins" if not validity_result.is_tie else "tie_keep_evolved"
                        logger.info(f"[MIPE] Phase 3: {outcome}. Keeping evolved plan.")
                        final_solution = p_final
                        final_blueprint = final_blueprint_candidate
                        succeeded = True
                        failure_reason = None
                        validity_outcome = "P_final wins" if not validity_result.is_tie else "Tie — keeping evolved plan"

                    evo.log_validity_check(p_final, p0, validity_result, validity_outcome)

        elapsed = time.monotonic() - t_start

        logger.info(
            f"[MIPE] Complete. succeeded={succeeded} "
            f"fitness={final_solution.fitness_score:.1f} "
            f"elapsed={elapsed:.1f}s"
        )

        evo.log_finish(
            succeeded=succeeded,
            final_solution=final_solution,
            p0=p0,
            elapsed=elapsed,
            winning_strategies=winning_strategy_trajectory,
            validity_score_final=validity_score_final,
            validity_score_p0=validity_score_p0,
            mode="sae",
        )

        edit_stats = _collect_edit_script_stats_sae(final_solution, p_final)
        sl.log_mipe_summary(
            succeeded=succeeded,
            p0_fitness=p0.fitness_score,
            final_fitness=final_solution.fitness_score,
            dp_best_fitness=dp_best.fitness_score if dp_best else 0.0,
            t_best_fitness=t_best.fitness_score if t_best else 0.0,
            elapsed_s=elapsed,
            winning_strategies=winning_strategy_trajectory,
            edit_script_stats=edit_stats,
        )

        self._strategy_store.append_evolution_log(
            goal=goal,
            succeeded=succeeded,
            winning_strategy_trajectory=winning_strategy_trajectory,
            final_fitness=final_solution.fitness_score,
            failure_reason=failure_reason,
            extra={"elapsed_s": round(elapsed, 1)},
        )

        return MIPEResult(
            blueprint=final_blueprint,
            solution=final_solution,
            seed=p0,
            dp_result=dp_result,
            t_result=t_result,
            succeeded=succeeded,
        )

    # ------------------------------------------------------------------
    # Sequential mode entry point
    # ------------------------------------------------------------------

    async def _run_sequential(
        self,
        goal: str,
        planner_context: Optional[str],
    ) -> "MIPEResult":
        """Sequential two-island evolution: T-island → DP-island (no alternation).

        Flow:
          Phase 0 : Planner + Atomizer → seed P0
          Init    : topo_judge.diagnose(P0) → t_feedback, t_focus
          Phase 1-T: _run_island(topology,   max_generations_t gens)  → T-Best
          Handoff : dp_judge.diagnose(T-Best) → dp_feedback, dp_focus
          Phase 1-DP: _run_island(dynamic_prompt, max_generations_dp gens) → DP-Best
          Phase 2 : Direct blueprint packaging (no assembler)
          Phase 3 : Topo Judge P_final vs P0 (debiased)

        Advantages over SAE:
          - Same-dimension feedback is continuous within each island.
          - Only 1 bridge judge call (T→DP) instead of G bridge calls.
          - T and DP can run different numbers of generations independently.
          - P1-B (cross-judge scale drift) disappears: T uses Topo Judge
            throughout; DP uses DP Judge throughout.
          - P3-B (P0 Init vs Phase-3 score stochasticity): Init score is
            saved on P0 and used to stabilise the Phase-3 comparison.
        """
        self._build_agents()
        pop_size = getattr(self.config, "pop_size", 2)
        max_gen = getattr(self.config, "max_generations", 1)
        t_gens = getattr(self.config, "max_generations_t", None) or max_gen
        dp_gens = getattr(self.config, "max_generations_dp", None) or max_gen
        enable_validity_check = getattr(self.config, "enable_validity_check", True)
        t_start = time.monotonic()
        sl = self._span_logger
        evo = self._evo_logger

        sl.log_params({
            "mipe.mode": "sequential",
            "mipe.pop_size": pop_size,
            "mipe.t_gens": t_gens,
            "mipe.dp_gens": dp_gens,
            "mipe.enable_validity_check": enable_validity_check,
        })

        evo.log_start(
            goal,
            pop_size=pop_size,
            max_generations=max(t_gens, dp_gens),
            num_islands=2,
        )

        # ── Phase 0: seed ─────────────────────────────────────────────
        with sl.phase("MIPE-Seq Phase 0: Seed Generation"):
            logger.info("[MIPE-Seq] Phase 0: Generating seed P0")
            p0 = await self._generate_seed(goal, planner_context)
            if p0 is None:
                raise RuntimeError(
                    "MIPE-Seq Phase 0 failed: Planner returned no subtasks"
                )
            logger.info(
                f"[MIPE-Seq] P0 generated: {len(p0.subtasks)} subtasks "
                f"id={p0.solution_id}"
            )
            evo.log_p0(p0)
            sl.log_metrics({
                "mipe.p0_subtask_count": float(len(p0.subtasks)),
                "mipe.p0_fitness": p0.fitness_score,
            })

        # ── Phase 1 Init: Topo Judge on P0 ────────────────────────────
        with sl.phase("MIPE-Seq Phase 1 Init: Topology Evaluation"):
            logger.info("[MIPE-Seq] Init: Evaluating P0 on topology dimension")
            t_feedback, t_focus, t_init_score = await self._evaluate_dimension(
                p0, "topology", goal
            )
            logger.info(
                f"[MIPE-Seq] Init complete. score={t_init_score:.1f}, "
                f"t_focus={t_focus}"
            )
            # P3-B: save Init topo score so Phase-3 can stabilise the P0 baseline.
            p0._initial_topo_score = t_init_score  # type: ignore[attr-defined]
            evo.log_init_evaluation(t_feedback)
            sl.log_metrics({"mipe.init_topo_score": t_init_score})

        winning_strategy_trajectory: Dict[str, str] = {}

        # ── Phase 1-T: run ALL T-island generations ────────────────────
        logger.info(
            f"[MIPE-Seq] Phase 1-T: {t_gens} generation(s), pop={pop_size}"
        )
        with sl.phase(
            "MIPE-Seq Phase 1-T: All T-island generations",
            {"island": "topology", "t_gens": t_gens},
        ):
            t_best, t_result = await self._run_island(
                dimension="topology",
                seed=p0,
                initial_feedback=t_feedback,
                initial_focus_nodes=t_focus,
                goal=goal,
                pop_size=pop_size,
                max_generations=t_gens,
                p0=p0,
                winning_strategies=winning_strategy_trajectory,
                sl=sl,
                evo=evo,
            )
        sl.log_metrics({"mipe.t_best_fitness": t_best.fitness_score})

        # ── Handoff T→DP: single DP-Judge evaluation on T-Best ────────
        with sl.phase("MIPE-Seq Handoff T→DP"):
            dp_feedback, dp_focus, dp_init_score = await self._evaluate_dimension(
                t_best, "dynamic_prompt", goal
            )
            logger.info(
                f"[MIPE-Seq] Handoff T→DP: dp_init_score={dp_init_score:.1f}, "
                f"dp_focus={dp_focus}"
            )
            sl.log_metrics({"mipe.seq_handoff_dp_score": dp_init_score})
            if evo:
                evo.log_sequential_handoff(t_best, dp_init_score)

        # ── Phase 1-DP: run ALL DP-island generations ──────────────────
        logger.info(
            f"[MIPE-Seq] Phase 1-DP: {dp_gens} generation(s), pop={pop_size}"
        )
        with sl.phase(
            "MIPE-Seq Phase 1-DP: All DP-island generations",
            {"island": "dynamic_prompt", "dp_gens": dp_gens},
        ):
            dp_best, dp_result = await self._run_island(
                dimension="dynamic_prompt",
                seed=t_best,
                initial_feedback=dp_feedback,
                initial_focus_nodes=dp_focus,
                goal=goal,
                pop_size=pop_size,
                max_generations=dp_gens,
                p0=p0,
                winning_strategies=winning_strategy_trajectory,
                sl=sl,
                evo=evo,
            )
        sl.log_metrics({"mipe.dp_best_fitness": dp_best.fitness_score})

        self._tracker.persist()

        # ── Phase 2: Direct blueprint packaging ────────────────────────
        p_final = dp_best
        rationale = (
            f"Sequential: {t_gens} T-gen(s) then {dp_gens} DP-gen(s). "
            f"Assembler disabled; blueprint emitted directly from evolved plan."
        )
        final_blueprint_candidate = _solution_to_blueprint(
            p_final, rationale=rationale, known_risks=[]
        )
        logger.info(
            f"[MIPE-Seq] Blueprint packaged. P_final={p_final.solution_id}"
        )
        if evo:
            evo.log_refinement(
                p_final,
                "Assembler disabled — direct blueprint packaging from evolved plan.",
            )

        # ── Phase 3: Validity check P_final vs P0 (Topo Judge, debiased)
        validity_score_final: Optional[float] = None
        validity_score_p0: Optional[float] = None
        succeeded = True
        failure_reason = None

        if not enable_validity_check:
            logger.info(
                "[MIPE-Seq] Phase 3: Validity check skipped "
                "(enable_validity_check=False)."
            )
            final_solution = p_final
            final_blueprint = final_blueprint_candidate
            if evo:
                evo.log_validity_check(
                    p_final, p0,
                    JudgeResult(
                        winner=p_final, loser=None, is_tie=False,
                        feedback="Validity check disabled via enable_validity_check=False",
                        score_a=10.0, score_b=10.0,
                    ),
                    "Skipped — enable_validity_check=False",
                )
        else:
            with sl.phase("MIPE-Seq Phase 3: Validity Check"):
                logger.info(
                    "[MIPE-Seq] Phase 3: Validity check P_final vs P0"
                )
                validity_result = await self._judges["topology"].compare(
                    sol_a=p_final, sol_b=p0, goal=goal, dimension="overall"
                )
                validity_score_final = validity_result.score_a
                validity_score_p0 = validity_result.score_b

                # P3-B: stabilise P0 score against Topology-Judge stochasticity.
                # If the Phase-3 re-evaluation of P0 drifts ≥0.5 pts from the
                # Phase-1 Init score, use max(init, phase3) so that random
                # downward fluctuations cannot manufacture a spurious P_final win.
                p0_init = getattr(p0, "_initial_topo_score", None)
                if p0_init is not None and abs(p0_init - validity_score_p0) >= 0.5:
                    logger.warning(
                        f"[MIPE-Seq] Phase-3 judge re-scored P0 "
                        f"{validity_score_p0:.1f} vs Init {p0_init:.1f}. "
                        f"Using max as stable P0 baseline."
                    )
                    validity_score_p0 = max(p0_init, validity_score_p0)
                    validity_result.score_b = validity_score_p0

                p0_clearly_wins = (
                    validity_result.winner is not None
                    and validity_result.winner.solution_id == p0.solution_id
                )
                if p0_clearly_wins:
                    logger.warning(
                        "[MIPE-Seq] Phase 3: P0 clearly wins. Falling back to P0."
                    )
                    final_solution = p0
                    final_blueprint = _solution_to_blueprint(
                        p0,
                        rationale="Validity check: P0 clearly won.",
                        known_risks=[],
                    )
                    succeeded = False
                    failure_reason = (
                        f"P_final lost to P0 in validity check. "
                        f"Feedback: {validity_result.feedback[:200]}"
                    )
                    validity_outcome = "P0 clearly wins — fallback to P0"
                else:
                    outcome_tag = (
                        "tie_keep_evolved" if validity_result.is_tie
                        else "P_final_wins"
                    )
                    logger.info(
                        f"[MIPE-Seq] Phase 3: {outcome_tag}. Keeping evolved plan."
                    )
                    final_solution = p_final
                    final_blueprint = final_blueprint_candidate
                    validity_outcome = (
                        "Tie — keeping evolved plan"
                        if validity_result.is_tie
                        else "P_final wins"
                    )

                if evo:
                    evo.log_validity_check(
                        p_final, p0, validity_result, validity_outcome
                    )

        elapsed = time.monotonic() - t_start

        logger.info(
            f"[MIPE-Seq] Complete. succeeded={succeeded} "
            f"fitness={final_solution.fitness_score:.1f} elapsed={elapsed:.1f}s"
        )

        if evo:
            evo.log_finish(
                succeeded=succeeded,
                final_solution=final_solution,
                p0=p0,
                elapsed=elapsed,
                winning_strategies=winning_strategy_trajectory,
                validity_score_final=validity_score_final,
                validity_score_p0=validity_score_p0,
                mode="sequential",
            )

        edit_stats = _collect_edit_script_stats_sae(final_solution, p_final)
        sl.log_mipe_summary(
            succeeded=succeeded,
            p0_fitness=p0.fitness_score,
            final_fitness=final_solution.fitness_score,
            dp_best_fitness=dp_best.fitness_score if dp_best else 0.0,
            t_best_fitness=t_best.fitness_score if t_best else 0.0,
            elapsed_s=elapsed,
            winning_strategies=winning_strategy_trajectory,
            edit_script_stats=edit_stats,
        )

        self._strategy_store.append_evolution_log(
            goal=goal,
            succeeded=succeeded,
            winning_strategy_trajectory=winning_strategy_trajectory,
            final_fitness=final_solution.fitness_score,
            failure_reason=failure_reason,
            extra={"elapsed_s": round(elapsed, 1), "mode": "sequential"},
        )

        return MIPEResult(
            blueprint=final_blueprint,
            solution=final_solution,
            seed=p0,
            dp_result=dp_result,
            t_result=t_result,
            succeeded=succeeded,
        )

    # ------------------------------------------------------------------
    # Phase helpers
    # ------------------------------------------------------------------

    async def _generate_seed(
        self, goal: str, context: Optional[str]
    ) -> Optional[EvolutionarySolution]:
        """Generate P0 via recursive Planner + Atomizer decomposition.

        Phase 0 is a **pure planning phase**: the Atomizer judges whether
        each node is atomic, and if not, the Planner is called recursively
        to decompose it further.  Leaf nodes (atomic or at max_depth) are
        marked with ``is_leaf=True`` but are **never executed** here.
        All real execution happens after MIPE completes.
        """
        if self.planner_agent is None:
            logger.error("[MIPE] No planner agent available for seed generation")
            return None
        try:
            flat_subtasks, report_policy = await self._generate_seed_recursive(
                goal=goal,
                context=context,
                depth=0,
                parent_id=None,
                root_goal=goal,
            )
            if not flat_subtasks:
                return None

            leaf_count = sum(1 for st in flat_subtasks if st.is_leaf)
            logger.info(
                f"[MIPE] Recursive seed: {len(flat_subtasks)} total nodes, "
                f"{leaf_count} leaves, max_depth={max(st.depth for st in flat_subtasks)}"
            )

            return EvolutionarySolution.from_flat_multilevel(
                flat_subtasks=flat_subtasks,
                report_policy=report_policy,
            )
        except Exception as e:
            logger.error(f"[MIPE] Seed generation failed: {e}")
            return None

    @staticmethod
    def _escape_xml(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )

    def _build_atomizer_context(
        self,
        parent_goal: str,
        task_type: str,
        current_depth: int,
        max_depth: int,
        siblings_count: int,
    ) -> str:
        """Build a minimal structured context string for Atomizer calls.

        Gives the Atomizer enough semantic grounding to make correct
        PLAN / EXECUTE decisions—especially for WRITE subtasks—without
        blowing the token budget.
        """
        remaining = max_depth - current_depth
        return (
            "<context>\n"
            f"  <parent_goal>{self._escape_xml(parent_goal)}</parent_goal>\n"
            "  <current_task>\n"
            f"    <task_type>{task_type}</task_type>\n"
            f"    <depth>{current_depth}</depth>\n"
            f"    <max_depth>{max_depth}</max_depth>\n"
            f"    <remaining_decomposition_levels>{remaining}</remaining_decomposition_levels>\n"
            f"    <siblings_at_this_level>{siblings_count}</siblings_at_this_level>\n"
            "  </current_task>\n"
            "  <decomposition_hint>\n"
            "    WRITE tasks covering broad topics, multiple sections, or likely\n"
            "    exceeding ~1000 words should be NON-ATOMIC (PLAN) so they can\n"
            "    be further split into focused per-section WRITE subtasks.\n"
            "    Only classify as ATOMIC when all required content is already\n"
            "    explicit in the goal and the expected output is short and focused.\n"
            "  </decomposition_hint>\n"
            "</context>"
        )

    def _build_child_context(
        self,
        overall_objective: str,
        parent_goal: str,
        subtask_goal: str,
        task_type: str,
        child_depth: int,
        max_depth: int,
    ) -> str:
        """Build a structured context string for child-level Planner calls.

        Mirrors the XML layout used by ``WarmupOrchestrator._build_warmup_context``
        so that child-level planners and atomizers see a familiar, information-rich
        context rather than an empty string.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return (
            "<context>\n"
            "<fundamental_context>\n"
            f"  <overall_objective>{self._escape_xml(overall_objective)}</overall_objective>\n"
            "  <current_task>\n"
            f"    <goal>{self._escape_xml(subtask_goal)}</goal>\n"
            f"    <task_type>{task_type}</task_type>\n"
            f"    <parent_goal>{self._escape_xml(parent_goal)}</parent_goal>\n"
            "  </current_task>\n"
            "  <temporal>\n"
            f"    <current_date>{now.strftime('%Y-%m-%d')}</current_date>\n"
            "  </temporal>\n"
            "  <recursion>\n"
            f"    <current_depth>{child_depth}</current_depth>\n"
            f"    <max_depth>{max_depth}</max_depth>\n"
            f"    <at_limit>{'true' if child_depth >= max_depth else 'false'}</at_limit>\n"
            "  </recursion>\n"
            "</fundamental_context>\n"
            "</context>"
        )

    async def _atomize_one(self, goal: str, context: Optional[str] = None) -> bool:
        """Atomize a single subtask. Returns True if it should be recursed.

        ``context`` carries structured metadata (parent goal, task_type, depth)
        so the Atomizer can make better PLAN / EXECUTE decisions instead of
        relying solely on the subtask ``goal`` string.
        """
        if self.atomizer_agent is None:
            return False
        async with self._llm_semaphore:
            try:
                result = await self.atomizer_agent.aforward(
                    goal=goal, context=context
                )
                return not getattr(result, "is_atomic", True)
            except Exception as e:
                logger.warning(
                    f"[MIPE] Atomizer failed for '{goal[:50]}', "
                    f"treating as leaf: {e}"
                )
                return False

    def _get_typed_planner_for(
        self,
        parent_task_type: Optional[str],
        depth: int,
        parent_id: Optional[str],
    ) -> Any:
        """Return a shallow copy of planner_agent with type-specific seed demos.

        Replicates the ``_replace_demos`` pattern from ModuleRuntime so that
        each Planner call during MIPE seed generation sees only the demos that
        match the current decomposition context (root / WRITE / THINK / RETRIEVE),
        eliminating the cross-type confusion caused by injecting all 5 demos at
        every recursion level.

        Uses ``copy.copy`` (shallow) so the LM / predictor objects are shared
        but ``_config_demos`` is a fresh list unique to this call.
        """
        import copy as _copy
        from prompt_optimization.prompts.seed_prompts.dr_planner_seed import (
            PLANNER_DR_DEMOS_ROOT,
            PLANNER_DR_DEMOS_WRITE,
            PLANNER_DR_DEMOS_THINK,
            PLANNER_DR_DEMOS_RETRIEVE,
        )
        from roma_dspy.types import TaskType

        is_root = depth == 0 and parent_id is None
        if is_root:
            typed_demos = PLANNER_DR_DEMOS_ROOT
        else:
            _seed_map = {
                TaskType.WRITE.value:    PLANNER_DR_DEMOS_WRITE,
                TaskType.THINK.value:    PLANNER_DR_DEMOS_THINK,
                TaskType.RETRIEVE.value: PLANNER_DR_DEMOS_RETRIEVE,
            }
            typed_demos = _seed_map.get(
                parent_task_type or "", PLANNER_DR_DEMOS_ROOT
            )

        typed_agent = _copy.copy(self.planner_agent)
        typed_agent._config_demos = list(typed_demos)
        return typed_agent

    async def _recurse_one(
        self,
        st: Any,
        flat_idx: int,
        depth: int,
        parent_goal: str,
        root_goal: str,
    ) -> Tuple[int, List[Any]]:
        """Recursively decompose one non-atomic subtask.

        Returns ``(flat_idx, child_nodes)`` so the caller can merge results
        in deterministic order.  On failure the subtask is degraded to a
        leaf (empty child list).

        ``parent_goal`` is the goal of the *current* recursion level; it is
        forwarded as ``overall_objective`` context to the child-level Planner
        so the child sees a meaningful semantic anchor instead of an empty context.
        """
        try:
            task_type_val = (
                st.task_type.value
                if hasattr(st.task_type, "value")
                else str(st.task_type)
            )
            child_context = self._build_child_context(
                overall_objective=root_goal,
                parent_goal=parent_goal,
                subtask_goal=st.goal,
                task_type=task_type_val,
                child_depth=depth + 1,
                max_depth=self.max_depth,
            )
            child_nodes, _ = await self._generate_seed_recursive(
                goal=st.goal,
                context=child_context,
                depth=depth + 1,
                parent_id=str(flat_idx),
                parent_task_type=task_type_val,
                root_goal=root_goal,
            )
            return flat_idx, child_nodes
        except Exception as e:
            logger.warning(
                f"[MIPE] Recursive decomposition failed for "
                f"'{st.goal[:50]}', degrading to leaf: {e}"
            )
            return flat_idx, []

    async def _generate_seed_recursive(
        self,
        goal: str,
        context: Optional[str],
        depth: int,
        parent_id: Optional[str],
        parent_task_type: Optional[str] = None,
        root_goal: Optional[str] = None,
    ) -> tuple:
        """Recursively decompose via Planner + Atomizer (parallelised).

        Uses a two-stage pipeline at each recursion level:
          Stage 1 — parallel Atomize:  all same-level subtasks are atomised
                    concurrently via ``asyncio.gather``.
          Stage 2 — parallel Recurse:  all non-atomic subtasks are recursively
                    decomposed concurrently.

        Returns ``(flat_subtasks, report_policy)`` where flat_subtasks is a
        list of SubTask with depth / parent_node_id / children_ids / is_leaf
        correctly populated.  report_policy comes from the root-level call.
        """
        from roma_dspy.core.signatures.base_models.subtask import SubTask
        if root_goal is None:
            root_goal = goal

        # ── Planner call (1 LLM call) ────────────────────────────────────
        # Use a type-specific demo copy so MIPE sees the same demo routing
        # as the real execution path (runtime._resolve_demos_for_agent).
        typed_planner = self._get_typed_planner_for(parent_task_type, depth, parent_id)
        async with self._llm_semaphore:
            result = await typed_planner.aforward(
                goal=goal, context=context, parent_task_type=parent_task_type,
            )
        subtasks: List[Any] = getattr(result, "subtasks", []) or []
        report_policy = getattr(result, "report_policy", None)

        if not subtasks:
            return [], report_policy

        # ── Stage 1: parallel Atomize all children ───────────────────────
        need_atomize = (
            self.atomizer_agent is not None and depth < self.max_depth - 1
        )
        if need_atomize:
            def _atomizer_context_for(st: Any) -> Optional[str]:
                task_type_str = (
                    st.task_type.value
                    if hasattr(st.task_type, "value")
                    else str(st.task_type)
                )
                return self._build_atomizer_context(
                    parent_goal=root_goal,
                    task_type=task_type_str,
                    current_depth=depth + 1,
                    max_depth=self.max_depth,
                    siblings_count=len(subtasks),
                )

            should_recurse_flags: List[bool] = list(
                await asyncio.gather(
                    *(
                        self._atomize_one(st.goal, _atomizer_context_for(st))
                        for st in subtasks
                    )
                )
            )
        else:
            should_recurse_flags = [False] * len(subtasks)

        logger.debug(
            f"[MIPE] depth={depth}: atomized {len(subtasks)} subtasks, "
            f"recurse={sum(should_recurse_flags)}"
        )

        # ── Pre-assign flat indices (no LLM call, instant) ──────────────
        flat_nodes: List[SubTask] = []
        local_to_flat: Dict[int, int] = {}

        for local_idx, st in enumerate(subtasks):
            st.depth = depth
            st.parent_node_id = parent_id
            flat_idx = len(flat_nodes)
            local_to_flat[local_idx] = flat_idx

            if should_recurse_flags[local_idx]:
                st.is_leaf = False
            else:
                st.is_leaf = True
                st.children_ids = []

            flat_nodes.append(st)

        # ── Stage 2: parallel recursive decomposition ────────────────────
        recurse_tasks = [
            self._recurse_one(
                subtasks[li], local_to_flat[li], depth, goal, root_goal
            )
            for li, flag in enumerate(should_recurse_flags) if flag
        ]

        if recurse_tasks:
            recurse_results: List[Tuple[int, List[Any]]] = list(
                await asyncio.gather(*recurse_tasks)
            )

            # Merge in deterministic order (sorted by parent flat_idx) so
            # that the flat list is stable regardless of completion order.
            for parent_flat_idx, child_nodes in sorted(
                recurse_results, key=lambda x: x[0]
            ):
                parent_st = flat_nodes[parent_flat_idx]

                if not child_nodes:
                    parent_st.is_leaf = True
                    parent_st.children_ids = []
                    continue

                child_start = len(flat_nodes)
                parent_id_str = str(parent_flat_idx)

                for cn in child_nodes:
                    cn.dependencies = [
                        str(int(d) + child_start)
                        if d.lstrip("-").isdigit() else d
                        for d in cn.dependencies
                    ]
                    if (
                        cn.parent_node_id is not None
                        and cn.parent_node_id != parent_id_str
                    ):
                        if cn.parent_node_id.lstrip("-").isdigit():
                            cn.parent_node_id = str(
                                int(cn.parent_node_id) + child_start
                            )
                    cn.children_ids = [
                        str(int(c) + child_start)
                        if c.lstrip("-").isdigit() else c
                        for c in cn.children_ids
                    ]

                flat_nodes.extend(child_nodes)
                parent_st.children_ids = [
                    str(child_start + ci)
                    for ci in range(len(child_nodes))
                    if child_nodes[ci].parent_node_id == parent_id_str
                ]

        # ── Remap dependencies from local 0-based to flat indices ────────
        for local_idx, st in enumerate(subtasks):
            fi = local_to_flat[local_idx]
            remapped_deps = []
            for dep in st.dependencies:
                try:
                    dep_local = int(dep)
                    if dep_local in local_to_flat:
                        remapped_deps.append(str(local_to_flat[dep_local]))
                except (ValueError, TypeError):
                    remapped_deps.append(dep)
            flat_nodes[fi].dependencies = remapped_deps

        # ── Remove self-dependencies (Planner hallucination guard) ────────
        # The Planner LLM may output a subtask that depends on itself
        # (e.g. local index 5 depends on "5").  After flat-index remapping
        # this becomes a self-reference that would either deadlock or get
        # silently dropped downstream, leaving the task with no deps.
        for fi, node in enumerate(flat_nodes):
            fi_str = str(fi)
            if fi_str in node.dependencies:
                logger.warning(
                    f"[MIPE] Removing self-dependency at flat index {fi} "
                    f"(goal='{node.goal[:60]}...')"
                )
                node.dependencies = [d for d in node.dependencies if d != fi_str]

        return flat_nodes, report_policy

    async def _evaluate_dimension(
        self,
        solution: EvolutionarySolution,
        dimension: str,
        goal: str,
    ) -> Tuple[Optional[str], List[int], float]:
        """Evaluate *solution* on *dimension* and return ``(feedback, focus_nodes, score)``.

        This is the single, unified entry point for all pre-island evaluations:
        - Initial evaluation of P0 before the first T-island step.
        - Bridge evaluation of T-Best before DP-island (T→DP bridge).
        - Bridge evaluation of DP-Best before next-gen T-island (DP→T bridge).

        The method has no threshold logic, no retries, and no quality gates —
        it purely diagnoses the solution and returns the judge's signal.
        """
        feedback, focus_nodes, score = await self._judges[dimension].diagnose(
            solution, goal=goal, dimension=dimension
        )
        return feedback, focus_nodes, score

    async def _run_island_one_gen(
        self,
        dimension: str,
        seed: EvolutionarySolution,
        gen: int,
        feedback: Optional[str],
        focus_nodes: Optional[List[int]],
        goal: str,
        pop_size: int,
        p0: EvolutionarySolution,
        winning_strategies: Dict[str, str],
        sl: Any,
        evo: Any = None,
        total_gens: int = 1,
    ) -> Tuple[EvolutionarySolution, TournamentResult]:
        """Run a single generation of island evolution (SAE step).

        Returns ``(best, tournament_result)``.  The tournament_result carries
        the intra-island winner and is used only for logging; cross-island
        focus_nodes are produced by ``_evaluate_dimension`` bridge calls in
        the SAE loop, not by reading tournament_result.focus_nodes.
        """
        dim_short = "dp" if dimension == "dynamic_prompt" else "t"
        dim_label = "DP" if dimension == "dynamic_prompt" else "T"

        if evo:
            evo.log_sae_step_start(
                dimension=dimension,
                gen=gen,
                total_gens=total_gens,
                pop_size=pop_size,
                seed_id=seed.solution_id,
            )

        logger.info(
            f"[MIPE] SAE gen {gen+1}/{total_gens} Island-{dim_label}: "
            f"seed={seed.solution_id}, has_feedback={feedback is not None}, "
            f"focus_nodes={focus_nodes}"
        )
        evolver = self._evolvers[dimension]
        variants = await evolver.generate_variants(
            seed, goal=goal, count=pop_size,
            previous_feedback=feedback,
            focus_nodes=focus_nodes,
        )

        if evo:
            evo.log_evolver_variants(dimension, gen, total_gens, seed.solution_id, variants)

        tournament = Tournament(judge=self._judges[dimension])
        result = await tournament.run(
            seed=seed, variants=variants, goal=goal, dimension=dimension,
            evo_logger=evo, generation=gen,
        )
        best = result.best

        self._update_tracker_from_tournament(result, dimension, winning_strategies)

        sl.log_metrics({
            f"mipe.{dim_short}_best_fitness_gen{gen}": best.fitness_score,
            f"mipe.{dim_short}_best_fitness": best.fitness_score,
        })

        if evo:
            evo.log_generation_best(dimension, gen, total_gens, best)

        logger.info(
            f"[MIPE] SAE gen {gen+1}/{total_gens} Island-{dim_label} done. "
            f"best={best.solution_id} fitness={best.fitness_score:.1f}"
        )
        return best, result

    async def _run_island(
        self,
        dimension: str,
        seed: EvolutionarySolution,
        initial_feedback: Optional[str],
        initial_focus_nodes: Optional[List[int]],
        goal: str,
        pop_size: int,
        max_generations: int,
        p0: EvolutionarySolution,
        winning_strategies: Dict[str, str],
        sl: Any,
        evo: Any = None,
    ) -> Tuple[EvolutionarySolution, Optional[TournamentResult]]:
        """Run one island's full multi-generation evolution (sequential mode).

        The island starts from ``initial_feedback`` + ``initial_focus_nodes``
        (e.g., T→DP handoff focus in sequential mode), then refreshes
        focus after each generation using the same-dimension tournament
        judge signal (``TournamentResult.focus_nodes``).
        """
        dim_short = "dp" if dimension == "dynamic_prompt" else "t"
        phase_label = "1-DP" if dimension == "dynamic_prompt" else "1-T"
        dim_label = "DP" if dimension == "dynamic_prompt" else "T"
        gain_key = "mipe.gain_dp" if dimension == "dynamic_prompt" else "mipe.gain_topology"

        if evo:
            evo.log_island_start(dimension, max_generations, pop_size)

        best = seed
        result: Optional[TournamentResult] = None
        feedback: Optional[str] = initial_feedback
        focus_nodes: Optional[List[int]] = initial_focus_nodes

        for gen in range(max_generations):
            gen_label = f"gen {gen+1}/{max_generations}"
            with sl.phase(
                f"MIPE Phase {phase_label} ({gen_label})",
                {"island": dimension, "generation": gen},
            ):
                logger.info(
                    f"[MIPE] Phase {phase_label} {gen_label}: Island-{dim_label} "
                    f"(seed={best.solution_id}, has_feedback={feedback is not None}, "
                    f"focus_nodes={focus_nodes})"
                )
                evolver = self._evolvers[dimension]
                variants = await evolver.generate_variants(
                    best, goal=goal, count=pop_size,
                    previous_feedback=feedback,
                    focus_nodes=focus_nodes,
                )

                if evo:
                    evo.log_evolver_variants(dimension, gen, max_generations, best.solution_id, variants)

                tournament = Tournament(judge=self._judges[dimension])
                result = await tournament.run(
                    seed=best, variants=variants, goal=goal, dimension=dimension,
                    evo_logger=evo, generation=gen,
                )
                best = result.best

                # Build inter-generation failure memory so the next generation's
                # evolver knows exactly which approaches failed and why, instead
                # of only receiving the raw judge text.
                failure_memory = _build_failure_memory(
                    variants, result.best, result.combined_feedback
                )
                if failure_memory:
                    judge_text = result.combined_feedback or ""
                    feedback = (
                        f"{failure_memory}\n\n"
                        f"--- Full Judge Feedback ---\n{judge_text}"
                    ).strip() or None
                else:
                    feedback = result.combined_feedback or None

                # Keep mutation context anchored to the latest same-dimension
                # judge diagnostics so each generation edits a focused window.
                if result.focus_nodes:
                    focus_nodes = result.focus_nodes

                logger.info(
                    f"[MIPE] Phase {phase_label} {gen_label} done. "
                    f"{dim_label}_best={best.solution_id} fitness={best.fitness_score:.1f} "
                    f"next_focus={focus_nodes}"
                )
                if evo:
                    evo.log_generation_best(dimension, gen, max_generations, best)

                self._update_tracker_from_tournament(result, dimension, winning_strategies)

                variant_edit_counts = [
                    len(getattr(v, "_latest_edits", [])) for v in variants
                ]
                fallback_count = sum(
                    1 for v in variants
                    if v.mutation_log and ("PARSE_FAIL" in v.mutation_log or "NO_EDITS" in v.mutation_log)
                )
                sl.log_metrics({
                    f"mipe.{dim_short}_best_fitness_gen{gen}": best.fitness_score,
                    f"mipe.{dim_short}_best_fitness": best.fitness_score,
                    gain_key: best.fitness_score - p0.fitness_score,
                    f"mipe.{dim_short}_edits_gen{gen}": float(sum(variant_edit_counts)),
                    f"mipe.{dim_short}_fallbacks_gen{gen}": float(fallback_count),
                })

        return best, result

    def _update_tracker_from_tournament(
        self,
        result: TournamentResult,
        island_id: str,
        trajectory: Dict[str, str],
    ) -> None:
        """Update MutationStrategyTracker win-rates from tournament outcomes.

        Only variants that were produced by *this* island's evolver (i.e.
        whose ``island_id`` matches the current *island_id* argument) are
        eligible to update the tracker and the winning-strategy trajectory.

        This prevents a DP-island seed that carries a ``set_scope_boundary``
        ``_applied_strategy`` from being misrecorded as the topology island's
        winning strategy when it wins (or ties) as the elite in a subsequent
        T-island tournament round.
        """
        for variant in (result.best, result.runner_up):
            if variant is None:
                continue
            # Skip empty-edit variants (NO_EDITS / PARSE_FAIL) — they carry no
            # real strategy signal and would corrupt the Tracker's win-rate stats.
            if getattr(variant, "_is_empty_variant", False):
                logger.debug(
                    f"[TRACKER] Skipping empty-edit variant {variant.solution_id} "
                    f"— no real strategy applied."
                )
                continue
            strategy = getattr(variant, "_applied_strategy", None)
            if not strategy:
                continue
            # Guard: only accept this strategy if the variant was produced by
            # the same island that is currently running.  Variants cloned by a
            # different island carry that island's id; the seed arriving from a
            # previous island step will have an id like "dynamic_prompt" while
            # the T-island is running under island_id="topology" (or vice-versa).
            variant_island = getattr(variant, "island_id", None)
            if variant_island is not None and variant_island != island_id:
                logger.debug(
                    f"[TRACKER] Skipping strategy '{strategy}' from island "
                    f"'{variant_island}' during {island_id} tournament — "
                    f"dimension mismatch."
                )
                continue
            won = (variant.solution_id == result.best.solution_id)
            self._tracker.update(island_id, strategy, won)
            if won:
                trajectory[island_id] = strategy


# =====================================================================
# Inter-generation failure memory
# =====================================================================


def _build_failure_memory(
    variants: List["EvolutionarySolution"],
    best: "EvolutionarySolution",
    judge_feedback: Optional[str],
) -> Optional[str]:
    """Build a structured failure-memory block from a generation's lost variants.

    The failure memory is prepended to the next generation's ``previous_feedback``
    so the evolver can avoid repeating the same mistakes.  Returns ``None`` when
    all variants won (no failures to record) or when no useful information is
    available.

    Format injected into the next call::

        === FAILURE MEMORY: Previous Generation Failed Attempts ===
        The following approaches FAILED. Study the reasons and do NOT repeat them.

        [FAILED 1]  strategy: expand_write_chapters
          Summary: ...
          ...

        --- Judge Verdict ---
        ...judge text (first 600 chars)...

        ======================================================
    """
    failed = [
        v for v in variants
        if v is not None and v.solution_id != best.solution_id
    ]
    if not failed:
        return None

    lines: List[str] = [
        "=== FAILURE MEMORY: Previous Generation Failed Attempts ===",
        "The following approaches FAILED. Study the reasons and do NOT repeat them.\n",
    ]

    for i, v in enumerate(failed, start=1):
        strategy = getattr(v, "_applied_strategy", "unknown") or "unknown"
        log = (v.mutation_log or "(no mutation log)").strip()
        lines.append(f"[FAILED {i}]  strategy: {strategy}")
        lines.append(f"  Summary: {log[:300]}")
        lines.append("")

    if judge_feedback:
        lines.append("--- Judge Verdict ---")
        lines.append(judge_feedback[:600].strip())

    lines.append("======================================================")
    return "\n".join(lines)


# =====================================================================
# Edit-script stats collection
# =====================================================================


def _collect_edit_script_stats_sae(
    final_solution: EvolutionarySolution,
    p_final: EvolutionarySolution,
) -> Dict[str, Any]:
    """Collect edit-script metrics for MLflow logging (SAE mode, no assembler)."""
    final_history = getattr(final_solution, "_edit_history", [])
    consistency_fixes = getattr(p_final, "_latest_edits", [])

    prompt_edits = sum(1 for e in final_history if hasattr(e, "new_dynamic_prompt"))
    topo_edits = sum(1 for e in final_history if hasattr(e, "op"))

    return {
        "total_edits": len(final_history),
        "prompt_edits": prompt_edits,
        "topo_edits": topo_edits,
        "parse_failures": 0,
        "consistency_fixes": len(consistency_fixes),
        "merge_mode": "sae_no_merge",
    }


# =====================================================================
# Result dataclass
# =====================================================================


class MIPEResult:
    """Final output of the MIPE evolution pipeline."""

    __slots__ = (
        "blueprint", "solution", "seed",
        "dp_result", "t_result", "succeeded",
    )

    def __init__(
        self,
        blueprint: PlanBlueprint,
        solution: EvolutionarySolution,
        seed: EvolutionarySolution,
        dp_result: TournamentResult,
        t_result: TournamentResult,
        succeeded: bool,
    ) -> None:
        self.blueprint = blueprint
        self.solution = solution
        self.seed = seed
        self.dp_result = dp_result
        self.t_result = t_result
        self.succeeded = succeeded
