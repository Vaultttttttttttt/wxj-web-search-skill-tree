"""Convenience exports for ROMA DSPy signatures and data models."""

from .signatures import (
    AtomizerSignature,
    PlannerSignature,
    ExecutorSignature,
    AggregatorSignature,
    VerifierSignature,
    # MIPE Evolution (Edit-Script Architecture)
    DPEvolverSignature,
    TopoEvolverSignature,
    PlanJudgeSignature,
    EditLogJudgeSignature,
    SelfDiagnoseSignature,
    ValidityJudgeSignature,
    ConsistencyCheckerSignature,
    CrossIslandMergeSignature,
    # Runtime Adaptation
    SentinelSignature,
)
from .base_models.subtask import SubTask
from .base_models.task_node import TaskNode
from .base_models.parameterized_static import ParameterizedStaticConfig
from .base_models.plan_blueprint import (
    PlanBlueprint,
    DAGSkeleton,
    SkeletonNode,
    DirectiveTemplate,
    DepthAllocation,
    NodeRigidity,
    MandateItem,
    MandateChecklist,
)
from .base_models.evolutionary_solution import EvolutionarySolution
from .base_models.mutation_patch import PromptEdit, TopoEdit, TopoEditOp, TopoEditParams

__all__ = [
    # Core Signatures
    "AtomizerSignature",
    "PlannerSignature",
    "ExecutorSignature",
    "AggregatorSignature",
    "VerifierSignature",
    # MIPE Evolution Signatures (Edit-Script)
    "DPEvolverSignature",
    "TopoEvolverSignature",
    "PlanJudgeSignature",
    "EditLogJudgeSignature",
    "SelfDiagnoseSignature",
    "ValidityJudgeSignature",
    "ConsistencyCheckerSignature",
    "CrossIslandMergeSignature",
    # Phase 2: Runtime Adaptation Signatures
    "SentinelSignature",
    # Data Models
    "SubTask",
    "TaskNode",
    # Phase 0.3: Parameterized Static
    "ParameterizedStaticConfig",
    # Phase 0.4: PlanBlueprint and supporting models
    "PlanBlueprint",
    "DAGSkeleton",
    "SkeletonNode",
    "DirectiveTemplate",
    "DepthAllocation",
    "NodeRigidity",
    # Mandate Checklist (structured per-WRITE constraint channel)
    "MandateItem",
    "MandateChecklist",
    # Phase 1.1: EvolutionarySolution
    "EvolutionarySolution",
    # Edit-Script Patch Models
    "PromptEdit",
    "TopoEdit",
    "TopoEditOp",
    "TopoEditParams",
]
