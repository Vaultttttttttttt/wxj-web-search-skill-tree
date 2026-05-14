"""MIPE (Micro-Island Plan Evolution) engine — 2-island architecture.

Two functional-specialized islands:
  - Island-T (Topology): optimizes DAG structure, node types, dependencies
  - Island-DP (Dynamic Prompt): optimizes per-subtask execution guidance,
    including content focus, constraints, and depth/length emphasis

Provides tournament selection, cross-island assembly, and the
main MIPE orchestrator.
"""

from roma_dspy.core.engine.evolution.strategy_store import StrategyStore
from roma_dspy.core.engine.evolution.evolver import (
    Evolver,
    MutationStrategyTracker,
)
from roma_dspy.core.engine.evolution.judge import PlanJudge, JudgeResult
from roma_dspy.core.engine.evolution.tournament import Tournament, TournamentResult
from roma_dspy.core.engine.evolution.assembler import (
    Assembler,
    AssemblyOutput,
    RefinementOutput,
)
from roma_dspy.core.engine.evolution.mipe_orchestrator import (
    MIPEOrchestrator,
    MIPEResult,
)

__all__ = [
    "StrategyStore",
    "MutationStrategyTracker",
    "Evolver",
    "PlanJudge",
    "JudgeResult",
    "Tournament",
    "TournamentResult",
    "Assembler",
    "AssemblyOutput",
    "RefinementOutput",
    "MIPEOrchestrator",
    "MIPEResult",
]
