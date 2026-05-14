"""ROMA-DSPy: modular hierarchical task decomposition framework."""

__version__ = "0.1.0"

from typing import Optional, Sequence

# Apply LiteLLM compatibility patches (noop if LiteLLM unavailable).
try:  # pragma: no cover - defensive import
    from .utils.litellm_patch import (
        patch_litellm_logging_worker as _patch_litellm_logging_worker,
        patch_dspy_responses_dict_support as _patch_dspy_responses_dict_support,
    )
except Exception:  # pragma: no cover - LiteLLM optional or import issue
    _patch_litellm_logging_worker = None
    _patch_dspy_responses_dict_support = None

try:  # pragma: no cover - defensive import
    from .utils.dspy_json_repair_patch import (
        patch_dspy_json_adapter_repair as _patch_dspy_json_adapter_repair,
    )
except Exception:  # pragma: no cover - optional patch
    _patch_dspy_json_adapter_repair = None

if _patch_litellm_logging_worker is not None:
    _patch_litellm_logging_worker()
if _patch_dspy_responses_dict_support is not None:
    _patch_dspy_responses_dict_support()
if _patch_dspy_json_adapter_repair is not None:
    _patch_dspy_json_adapter_repair()

from .core import (
    TaskDAG,
    RecursiveSolver,
    solve,
    async_solve,
    event_solve,
    async_event_solve,
    Atomizer,
    Planner,
    Executor,
    Aggregator,
    Verifier,
    AtomizerSignature,
    PlannerSignature,
    ExecutorSignature,
    AggregatorSignature,
    VerifierSignature,
    SubTask,
    TaskNode,
    RecursiveSolverModule,
)

__all__ = [
    "__version__",
    "TaskDAG",
    "RecursiveSolver",
    "solve",
    "async_solve",
    "event_solve",
    "async_event_solve",
    "Atomizer",
    "Planner",
    "Executor",
    "Aggregator",
    "Verifier",
    "AtomizerSignature",
    "PlannerSignature",
    "ExecutorSignature",
    "AggregatorSignature",
    "VerifierSignature",
    "SubTask",
    "TaskNode",
    "RecursiveSolverModule",
    "main",
]
