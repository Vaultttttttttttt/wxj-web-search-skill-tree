"""Parameterized Static configuration for the three-layer prompt architecture.

Sits between frozen Static prompts (output format, tool protocols, safety)
and fully Dynamic prompts (report_policy, subtask directives). Provides a
finite set of discrete "knobs" that can be adjusted during evolution search
without the cost of rewriting free-text prompts.

Three-layer architecture:
  Static (frozen)  →  Parameterized Static (adjustable knobs)  →  Dynamic (free-form)

MIPE integration:
  - mutate(dim, value)   — produce a single-knob variant for island evolution
  - to_snapshot()        — export to dict for storage in PlanBlueprint
  - from_snapshot(dict)  — reconstruct from PlanBlueprint.parameterized_static_snapshot
  - all_options()        — enumerate full search space for the MIPE evolver
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# =====================================================================
# Discrete Parameter Enums
# =====================================================================


class NarrativeMode(str, Enum):
    """How the report structures its argumentation."""
    ARGUMENT_FIRST = "argument_first"       # 论证优先：先结论后论据
    DATA_DRIVEN = "data_driven"             # 数据驱动：用数据引领叙事
    CASE_STUDY = "case_study"               # 案例解读：通过具体案例展开分析
    COMPARATIVE = "comparative"             # 对比分析：通过对比揭示差异与趋势
    NARRATIVE = "narrative"                 # 叙事式：按时间线或因果链展开


class EvidenceStrategy(str, Enum):
    """How evidence is gathered and integrated."""
    BREADTH = "breadth"                     # 广度优先：覆盖多维度、多来源
    DEPTH = "depth"                         # 深度优先：少数维度深入挖掘
    CONTRASTIVE = "contrastive"             # 对比式：正反观点、多方案对比
    TRIANGULATION = "triangulation"         # 三角验证：多来源交叉验证同一结论


class AudienceLevel(str, Enum):
    """Target audience expertise level."""
    ACADEMIC = "academic"                   # 学术：面向研究者，术语密集
    PROFESSIONAL = "professional"           # 商业/专业：面向决策者，平衡深度与可读性
    GENERAL = "general"                     # 通俗：面向非专业读者，通俗易懂
    EXECUTIVE = "executive"                 # 高管：面向C-level，结论导向、精炼


class StructurePattern(str, Enum):
    """Overall report structure pattern."""
    PROGRESSIVE = "progressive"             # 渐进式：从背景到核心到结论层层深入
    DEDUCTIVE = "deductive"                 # 总分式：先总论后分述
    PROBLEM_DRIVEN = "problem_driven"       # 问题驱动：围绕核心问题展开分析
    COMPARATIVE_FRAMEWORK = "comparative_framework"  # 对比框架：按对比维度组织
    CHRONOLOGICAL = "chronological"         # 时间线：按时间发展顺序组织


# =====================================================================
# Configuration Model
# =====================================================================


class ParameterizedStaticConfig(BaseModel):
    """Configuration for Parameterized Static layer knobs.

    Each field has a current value (used for this execution) and the full
    set of allowed values is defined by the enum types above. During MIPE
    evolution, the evolver adjusts these knobs by selecting different enum
    values — combinatorial search over a small discrete space.
    """

    narrative_mode: NarrativeMode = Field(
        default=NarrativeMode.ARGUMENT_FIRST,
        description="How the report structures its argumentation style",
    )
    evidence_strategy: EvidenceStrategy = Field(
        default=EvidenceStrategy.BREADTH,
        description="How evidence is gathered and integrated across the report",
    )
    audience_level: AudienceLevel = Field(
        default=AudienceLevel.PROFESSIONAL,
        description="Target audience expertise level and writing style",
    )
    structure_pattern: StructurePattern = Field(
        default=StructurePattern.PROGRESSIVE,
        description="Overall report structure pattern",
    )

    def to_context_block(self) -> str:
        """Serialize to an XML context block for injection into Planner prompt.

        This block tells the Planner which stylistic and structural
        constraints to follow when generating the plan.
        """
        mode_descriptions = {
            NarrativeMode.ARGUMENT_FIRST: "论证优先 — 每个章节先给出核心结论，再展开论据和案例",
            NarrativeMode.DATA_DRIVEN: "数据驱动 — 用量化数据和统计引领叙事，先数据后解读",
            NarrativeMode.CASE_STUDY: "案例解读 — 通过具体案例和实例展开分析，从特殊到一般",
            NarrativeMode.COMPARATIVE: "对比分析 — 通过方案/产品/观点对比揭示差异与趋势",
            NarrativeMode.NARRATIVE: "叙事式 — 按时间线或因果链展开，讲述发展故事",
        }
        evidence_descriptions = {
            EvidenceStrategy.BREADTH: "广度优先 — 覆盖多维度、多来源、多视角",
            EvidenceStrategy.DEPTH: "深度优先 — 聚焦少数核心维度，深入挖掘机理和细节",
            EvidenceStrategy.CONTRASTIVE: "对比式 — 主动收集正反观点、不同方案的证据进行对比",
            EvidenceStrategy.TRIANGULATION: "三角验证 — 多来源交叉验证同一结论，强调证据可靠性",
        }
        audience_descriptions = {
            AudienceLevel.ACADEMIC: "学术读者 — 术语密集、引用严谨、理论深度优先",
            AudienceLevel.PROFESSIONAL: "专业决策者 — 平衡深度与可读性，数据支撑结论",
            AudienceLevel.GENERAL: "通俗读者 — 通俗易懂、少用术语、注重解释",
            AudienceLevel.EXECUTIVE: "高管读者 — 结论导向、精炼概要、可行动建议",
        }
        structure_descriptions = {
            StructurePattern.PROGRESSIVE: "渐进式 — 从背景到核心分析到结论，层层深入",
            StructurePattern.DEDUCTIVE: "总分式 — 先总论后分述，开篇即给出全局视图",
            StructurePattern.PROBLEM_DRIVEN: "问题驱动 — 围绕核心问题展开，逐个分析解答",
            StructurePattern.COMPARATIVE_FRAMEWORK: "对比框架 — 按对比维度组织章节结构",
            StructurePattern.CHRONOLOGICAL: "时间线 — 按时间发展顺序组织叙事",
        }

        return (
            "<parameterized_static>\n"
            "以下是本次报告的风格和结构约束参数（Parameterized Static 层），"
            "请在生成计划时遵循这些约束：\n\n"
            f"  <narrative_mode>{mode_descriptions.get(self.narrative_mode, self.narrative_mode.value)}</narrative_mode>\n"
            f"  <evidence_strategy>{evidence_descriptions.get(self.evidence_strategy, self.evidence_strategy.value)}</evidence_strategy>\n"
            f"  <audience_level>{audience_descriptions.get(self.audience_level, self.audience_level.value)}</audience_level>\n"
            f"  <structure_pattern>{structure_descriptions.get(self.structure_pattern, self.structure_pattern.value)}</structure_pattern>\n"
            "</parameterized_static>"
        )

    # ------------------------------------------------------------------
    # MIPE Evolution Helpers
    # ------------------------------------------------------------------

    def mutate(self, dimension: str, value: str) -> "ParameterizedStaticConfig":
        """Return a new config with exactly one knob changed.

        Used by MIPE Island-B (Budget) evolver to explore the discrete
        parameter space without rewriting free-text prompts.

        Args:
            dimension: One of 'narrative_mode', 'evidence_strategy',
                       'audience_level', 'structure_pattern'.
            value: New enum value for that dimension (string form).

        Returns:
            New ParameterizedStaticConfig with the mutated knob.

        Raises:
            ValueError: If dimension is unknown or value is not a valid enum entry.
        """
        allowed_dims = {"narrative_mode", "evidence_strategy", "audience_level", "structure_pattern"}
        if dimension not in allowed_dims:
            raise ValueError(
                f"Unknown dimension '{dimension}'. Must be one of {sorted(allowed_dims)}."
            )
        updates = self.model_dump()
        updates[dimension] = value
        try:
            return ParameterizedStaticConfig(**updates)
        except Exception as exc:
            raise ValueError(
                f"Invalid value '{value}' for dimension '{dimension}': {exc}"
            ) from exc

    def to_snapshot(self) -> Dict[str, str]:
        """Export current knob settings to a plain string dict.

        Suitable for storage in PlanBlueprint.parameterized_static_snapshot
        and for the MIPE Archive entry.
        """
        return {
            "narrative_mode": self.narrative_mode.value,
            "evidence_strategy": self.evidence_strategy.value,
            "audience_level": self.audience_level.value,
            "structure_pattern": self.structure_pattern.value,
        }

    @classmethod
    def from_snapshot(cls, snapshot: Dict[str, str]) -> "ParameterizedStaticConfig":
        """Reconstruct from a snapshot dict (inverse of to_snapshot).

        Used when the Archive or PlanBlueprint returns a previously
        winning configuration for use as a MIPE seed.

        Args:
            snapshot: Dict mapping dimension names to enum value strings.

        Returns:
            ParameterizedStaticConfig with the stored knob settings.
        """
        return cls(**snapshot)

    @classmethod
    def all_options(cls) -> Dict[str, List[str]]:
        """Return all available options for each parameter.

        Useful for MIPE evolution to enumerate the full search space
        before deciding which dimensions to mutate.
        """
        return {
            "narrative_mode": [e.value for e in NarrativeMode],
            "evidence_strategy": [e.value for e in EvidenceStrategy],
            "audience_level": [e.value for e in AudienceLevel],
            "structure_pattern": [e.value for e in StructurePattern],
        }

    @classmethod
    def search_space_size(cls) -> int:
        """Total number of distinct parameter combinations.

        With current enums: 5 × 4 × 4 × 5 = 400.
        """
        options = cls.all_options()
        size = 1
        for values in options.values():
            size *= len(values)
        return size

    def neighbors(self) -> List["ParameterizedStaticConfig"]:
        """Return all single-knob mutations reachable from this config.

        Useful for MIPE to enumerate the local neighbourhood without
        generating all 400 combinations.
        """
        result: List["ParameterizedStaticConfig"] = []
        current = self.to_snapshot()
        for dim, options in self.all_options().items():
            for val in options:
                if val != current[dim]:
                    result.append(self.mutate(dim, val))
        return result
