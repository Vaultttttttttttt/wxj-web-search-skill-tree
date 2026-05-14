"""Pre-execution warm-up pipeline — always uses MIPE evolution.

The warm-up phase runs the MIPE evolution engine to produce an optimized
PlanBlueprint before the real execution:

  Phase 0:  Planner + Atomizer recursive decomposition → seed P0
  Phase 1:  Island-DP ‖ Island-T evolve in parallel from P0
  Phase 2:  Cross-island merge → P_final
  Phase 3:  Optional validity check: P_final vs P0

The result (PlanBlueprint with per-subtask dynamic_prompts) is injected
into the real execution phase via the context store to guide each executor.

On MIPE failure, an empty WarmupResult is returned (graceful degradation).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from roma_dspy.config.schemas.warmup import WarmupConfig

_sig_counter = itertools.count()


# =====================================================================
# Data Structures
# =====================================================================


@dataclass
class WarmupResult:
    """Result of the warm-up phase."""

    directives: Dict[str, Optional[str]]
    demos: Dict[str, list]
    evaluation_scores: Dict[str, Any] = field(default_factory=dict)
    iteration_count: int = 0
    success: bool = False
    plan_blueprint: Optional[Any] = None


# =====================================================================
# Warmup Orchestrator
# =====================================================================


class WarmupOrchestrator:
    """Orchestrates the MIPE-based pre-execution warm-up pipeline.

    Warm-up always runs MIPE evolution.  The Planner is constructed here
    and passed to MIPEOrchestrator together with an Atomizer for recursive
    plan decomposition.  On MIPE failure, an empty WarmupResult is returned.
    """

    def __init__(
        self,
        config: "WarmupConfig",
        roma_config: Any = None,
        mlflow_manager: Any = None,
        logs_dir: Optional[str] = None,
    ) -> None:
        self.config = config
        self.logs_dir = logs_dir
        self.roma_config = roma_config
        self.mlflow_manager = mlflow_manager

        self._planner: Optional[Any] = None

    # ------------------------------------------------------------------
    # Agent Construction
    # ------------------------------------------------------------------

    def _build_agents(self) -> None:
        """Lazily build warmup agents (Planner only)."""
        if self._planner is not None:
            return

        from roma_dspy.config.schemas.agents import AgentConfig
        from roma_dspy.core.factory.agent_factory import AgentFactory
        from roma_dspy.types import AgentType

        factory = AgentFactory()

        raw_llm = None
        if self.roma_config:
            default_agent = getattr(self.roma_config.agents, AgentType.PLANNER.value, None)
            if default_agent and hasattr(default_agent, "llm"):
                raw_llm = default_agent.llm

        agent_config = AgentConfig(
            llm=raw_llm,
            prediction_strategy="chain_of_thought",
            signature_instructions=(
                "prompt_optimization.prompts.seed_prompts.dr_planner_seed:PLANNER_DR_PROMPT"
            ),
            demos=(
                "prompt_optimization.prompts.seed_prompts.dr_planner_seed:PLANNER_DR_DEMOS"
            ),
        )
        self._planner = factory.create_agent(AgentType.PLANNER, agent_config)

    def _create_atomizer_for_mipe(self) -> Any:
        """Create an Atomizer agent for MIPE Phase 0 recursive decomposition.

        Inherits ``signature_instructions`` and ``demos`` from the profile's
        atomizer config (``roma_config.agents.atomizer``) so that Warm-up uses
        the same few-shot examples and instruction prompt as the real pipeline.
        Falls back to the DR seed-prompt module paths when the profile does not
        provide explicit values.
        """
        from roma_dspy.config.schemas.agents import AgentConfig
        from roma_dspy.core.factory.agent_factory import AgentFactory
        from roma_dspy.types import AgentType

        _FALLBACK_INSTRUCTIONS = (
            "prompt_optimization.prompts.seed_prompts.dr_atomizer_seed:ATOMIZER_DR_PROMPT"
        )
        _FALLBACK_DEMOS = (
            "prompt_optimization.prompts.seed_prompts.dr_atomizer_seed:ATOMIZER_DR_DEMOS"
        )

        factory = AgentFactory()
        llm_config = None
        signature_instructions: Any = _FALLBACK_INSTRUCTIONS
        demos: Any = _FALLBACK_DEMOS

        if self.roma_config:
            atomizer_cfg = getattr(self.roma_config.agents, "atomizer", None)
            if atomizer_cfg:
                if hasattr(atomizer_cfg, "llm"):
                    llm_config = atomizer_cfg.llm
                # Inherit prompt config from the profile when available
                if getattr(atomizer_cfg, "signature_instructions", None):
                    signature_instructions = atomizer_cfg.signature_instructions
                if getattr(atomizer_cfg, "demos", None):
                    demos = atomizer_cfg.demos

            if llm_config is None:
                executor_cfg = getattr(self.roma_config.agents, "executor", None)
                if executor_cfg and hasattr(executor_cfg, "llm"):
                    llm_config = executor_cfg.llm

        agent_config = AgentConfig(
            llm=llm_config,
            prediction_strategy="chain_of_thought",
            signature_instructions=signature_instructions,
            demos=demos,
        )
        return factory.create_agent(AgentType.ATOMIZER, agent_config)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        goal: str,
        directive_override: Optional[Dict[str, Optional[str]]] = None,
    ) -> WarmupResult:
        """Execute the MIPE warm-up pipeline.

        Builds the Planner, runs MIPE evolution, and returns a WarmupResult
        carrying the optimized PlanBlueprint.  On any failure, returns an
        empty WarmupResult so the real execution can proceed unblocked.
        """
        self._build_agents()

        evolution_cfg = getattr(self.config, "evolution", None)
        planner_context = self._build_warmup_context(
            goal, depth=0, include_parameterized_static=True
        )

        try:
            from roma_dspy.core.engine.evolution.mipe_orchestrator import MIPEOrchestrator

            atomizer_agent = self._create_atomizer_for_mipe()
            max_depth = 2
            if self.roma_config and hasattr(self.roma_config, "runtime"):
                max_depth = getattr(self.roma_config.runtime, "max_depth", 2)

            orchestrator = MIPEOrchestrator(
                evolution_config=evolution_cfg,
                roma_config=self.roma_config,
                planner_agent=self._planner,
                atomizer_agent=atomizer_agent,
                max_depth=max_depth,
                storage_dir=getattr(evolution_cfg, "storage_dir", ".mipe_store"),
                mlflow_manager=self.mlflow_manager,
                logs_dir=self.logs_dir,
            )
            mipe_result = await orchestrator.run(goal, planner_context)

            logger.info(
                f"[WARMUP] MIPE evolution complete: "
                f"fitness={mipe_result.blueprint.fitness_score:.1f}, "
                f"generation={mipe_result.blueprint.generation}"
            )

            plan_context = {
                "report_policy": mipe_result.solution.report_policy,
                "subtask_count": len(mipe_result.solution.subtasks),
            }

            return WarmupResult(
                directives=plan_context,
                demos={},
                evaluation_scores={},
                iteration_count=1,
                success=True,
                plan_blueprint=mipe_result.blueprint,
            )

        except Exception as e:
            logger.warning(f"[WARMUP] MIPE evolution failed, skipping warm-up: {e}")
            return WarmupResult(
                directives={},
                demos={},
                evaluation_scores={},
                iteration_count=0,
                success=False,
            )

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _build_warmup_context(
        self, goal: str, depth: int = 0, include_parameterized_static: bool = False
    ) -> str:
        """Build a minimal structured context for warmup agents.

        Mirrors the real pipeline's XML context structure so the warmup
        planner sees the same ``<fundamental_context>`` layout (including
        ``<overall_objective>`` with temporal info and recursion metadata)
        that the production planner receives.

        When ``include_parameterized_static`` is True, the Parameterized
        Static config block is injected to guide Planner style decisions.
        """
        now = datetime.now(timezone.utc)
        max_depth = 2

        ps_block = ""
        if include_parameterized_static:
            ps_config = self._get_parameterized_static_config()
            ps_block = f"\n{ps_config.to_context_block()}\n"

        return (
            "<context>\n"
            "<fundamental_context>\n"
            f"  <overall_objective>{self._escape_xml(goal)}</overall_objective>\n"
            f"  <temporal>\n"
            f"    <current_date>{now.strftime('%Y-%m-%d')}</current_date>\n"
            f"    <current_year>{now.year}</current_year>\n"
            f"  </temporal>\n"
            f"  <recursion>\n"
            f"    <current_depth>{depth}</current_depth>\n"
            f"    <max_depth>{max_depth}</max_depth>\n"
            f"    <at_limit>{'true' if depth >= max_depth else 'false'}</at_limit>\n"
            f"  </recursion>\n"
            "</fundamental_context>\n"
            f"{ps_block}"
            "</context>"
        )

    def _get_parameterized_static_config(self) -> Any:
        """Resolve ParameterizedStaticConfig from roma_config, warmup config, or defaults.

        Resolution order:
          1. roma_config.parameterized_static  (top-level YAML key, preferred)
          2. self.config.parameterized_static   (WarmupConfig field, legacy fallback)
          3. ParameterizedStaticConfig defaults  (all enum defaults)
        """
        from roma_dspy.core.signatures.base_models.parameterized_static import (
            ParameterizedStaticConfig,
        )

        ps_cfg = getattr(self.roma_config, "parameterized_static", None)
        if ps_cfg is None:
            ps_cfg = getattr(self.config, "parameterized_static", None)

        if ps_cfg and isinstance(ps_cfg, dict):
            return ParameterizedStaticConfig(**ps_cfg)
        if ps_cfg and isinstance(ps_cfg, ParameterizedStaticConfig):
            return ps_cfg
        return ParameterizedStaticConfig()

    @staticmethod
    def _escape_xml(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )
