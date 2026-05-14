"""Warm-up phase configuration schema for ROMA-DSPy."""

from pydantic.dataclasses import dataclass
from typing import Any, Dict, List, Optional

from roma_dspy.config.schemas.base import LLMConfig


@dataclass
class WarmupAgentConfig:
    """Configuration for a single warm-up agent."""

    llm: Optional[LLMConfig] = None
    signature_instructions: Optional[str] = None
    demos: Optional[str] = None


@dataclass
class EvolutionConfig:
    """MIPE (Micro-Island Plan Evolution) configuration.

    Controls the evolutionary search that optimizes the initial plan (P0)
    through functional-specialized islands, head-to-head tournament selection,
    and cross-island assembly.

    Recommended default: (2 islands, 2 variants, 2 generations)
      -> 8 evolver calls + 8 judge calls (4 topology + 4 dynamic_prompt)
         + 1 assembler = ~18 LLM calls

    judge_topology:      evaluates T-island (DAG structure, MECE, evidence
                         coverage) and Phase 3 overall validity.
    judge_dynamic_prompt: evaluates DP-island (prompt quality, HOW guidance,
                          scope enforcement).
    """

    sentinel_enabled: bool = False
    pop_size: int = 2
    num_islands: int = 2
    max_generations: int = 1
    enable_validity_check: bool = True

    # Evolution mode.
    # "sae"        — Serial Alternating Evolution (default): T and DP islands
    #                alternate every generation with bridge-judge calls.
    # "sequential" — T-island completes ALL generations first, then hands off
    #                to DP-island.  Provides better same-dimension signal
    #                continuity and fewer bridge-judge LLM calls.
    mode: str = "sae"

    # Per-island generation counts for "sequential" mode.
    # When None, falls back to max_generations for both islands.
    # Example: max_generations_t=1, max_generations_dp=2 lets DP refine
    # prompts over two rounds on a stable topology.
    max_generations_t: Optional[int] = None
    max_generations_dp: Optional[int] = None

    mutation_strategies: Optional[Dict[str, List[str]]] = None

    evolver: Optional[WarmupAgentConfig] = None
    judge_topology: Optional[WarmupAgentConfig] = None
    judge_dynamic_prompt: Optional[WarmupAgentConfig] = None
    assembler: Optional[WarmupAgentConfig] = None
    sentinel: Optional[WarmupAgentConfig] = None
    storage_dir: str = ".mipe_store"


@dataclass
class WarmupConfig:
    """Configuration for the pre-execution warm-up phase.

    When enabled, the MIPE evolution engine runs before the real execution
    to generate an optimized PlanBlueprint with per-subtask dynamic_prompts.

    ``parameterized_static`` is an optional override for the Parameterized
    Static knobs at the warmup level. When present it takes precedence over
    the top-level ``parameterized_static`` section in the profile YAML.
    Accepted formats:
      - dict: {'narrative_mode': 'data_driven', ...}  (deserialized from YAML)
      - ParameterizedStaticConfig instance             (set programmatically)
    """

    enabled: bool = False

    evolution: Optional[EvolutionConfig] = None

    parameterized_static: Optional[Any] = None
