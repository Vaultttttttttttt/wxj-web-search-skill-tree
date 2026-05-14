import re

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema
from typing import Dict, List, Optional, Set
from roma_dspy.types import TaskType

_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


class SubTask(BaseModel):
    """
    Individual subtask in a decomposition plan.

    Multi-level topology support: ``depth`` and ``parent_node_id`` enable
    a flat list of SubTasks to represent a hierarchical DAG.  Depth-0
    nodes are direct children of the root query; depth-1 nodes are
    children of depth-0 nodes, etc.  ``children_ids`` is the inverse
    link for quick tree traversal.  ``is_leaf`` marks nodes that should
    be directly executed (not further decomposed).
    """

    goal: str = Field(..., min_length=1, description="Precise subtask objective")
    task_type: TaskType = Field(..., description="Type of subtask")
    dependencies: List[str] = Field(
        default_factory=list, description="List of subtask IDs this depends on"
    )
    result: Optional[str] = Field(
        default=None, description="Result of subtask execution (for aggregation)"
    )
    dynamic_prompt: Optional[str] = Field(
        default=None,
        description=(
            "LLM-generated dynamic prompt for this specific subtask. "
            "Contrasts with static prompts (frozen system instructions): "
            "dynamic_prompt is tailored per goal/input to guide the executor "
            "toward better output quality. May contain {placeholder} tokens "
            "for runtime filling within a PlanBlueprint template."
        ),
    )
    context_input: Optional[str] = Field(
        default=None, description="Context from dependent tasks (left-to-right flow)"
    )
    mandate_checklist: Optional[str] = Field(
        default=None,
        description=(
            "Per-WRITE mandate checklist XML block (<mandate_checklist>...</mandate_checklist>) "
            "produced by the Outline THINK executor and filled in by the Planner. "
            "Contains verbatim items (strings that must appear character-for-character) "
            "and dimension items (analytical angles that must each have a dedicated paragraph). "
            "Only set for WRITE subtasks; None for RETRIEVE/THINK."
        ),
    )
    sources: Optional[List[str]] = Field(
        default=None, description="Source URLs from execution (for citation propagation)"
    )
    agg_task_id: Optional[str] = Field(
        default=None,
        description="Internal: task_id carried through aggregation for precise matching (not serialised to LLM)",
        exclude=True,
    )

    # === Multi-level topology fields ===
    # Wrapped in SkipJsonSchema so they are invisible to the LLM when DSPy
    # generates the output format from the Pydantic model schema.  These fields
    # are set programmatically by the MIPE assembler/evolver — the planner LLM
    # should never output them (all ordering is expressed via `dependencies`).
    # Omitting them from the schema also prevents the LLM from generating a
    # hierarchical parent-child structure instead of a flat sibling list.
    depth: SkipJsonSchema[int] = Field(
        default=0,
        description="Decomposition depth (0 = direct child of root query)",
    )
    parent_node_id: SkipJsonSchema[Optional[str]] = Field(
        default=None,
        description=(
            "Index of the parent subtask in the flat list (as string). "
            "None for depth-0 nodes."
        ),
    )
    children_ids: SkipJsonSchema[List[str]] = Field(
        default_factory=list,
        description="Indices of child subtasks (inverse of parent_node_id)",
    )
    is_leaf: SkipJsonSchema[bool] = Field(
        default=False,
        description=(
            "True if this node should be directly executed without further "
            "decomposition (either atomic or at max_depth)."
        ),
    )

    def get_dynamic_prompt_placeholders(self) -> Set[str]:
        """Extract placeholder names from the dynamic_prompt template.

        Returns an empty set if dynamic_prompt is None or has no placeholders.
        """
        if not self.dynamic_prompt:
            return set()
        return set(_PLACEHOLDER_RE.findall(self.dynamic_prompt))

    def fill_dynamic_prompt(self, values: Dict[str, str]) -> Optional[str]:
        """Fill dynamic_prompt placeholders with concrete values.

        Unfilled placeholders are left as-is so they can be filled later
        or flagged by Sentinel.

        Returns None if dynamic_prompt is None.
        """
        if not self.dynamic_prompt:
            return None
        result = self.dynamic_prompt
        for key, value in values.items():
            result = result.replace(f"{{{key}}}", value)
        return result
