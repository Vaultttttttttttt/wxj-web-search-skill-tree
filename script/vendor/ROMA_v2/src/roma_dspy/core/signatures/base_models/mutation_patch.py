"""Edit-script data models for MIPE incremental evolution.

Instead of outputting a complete plan, Evolver LLMs output a list of
targeted edits.  A deterministic Python function (``apply_prompt_edits``
/ ``apply_topo_edits`` on EvolutionarySolution) applies the edits,
guaranteeing that untouched nodes remain unchanged.

Two edit types, one per island:
  - ``PromptEdit``  (DP island): anchor-section targeted edits per node
  - ``TopoEdit``    (T island):  structural DAG atomic operations
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# =====================================================================
# DP Island — PromptEdit (anchor-section operations)
# =====================================================================


class PromptEditOp(str, Enum):
    """Atomic anchor-section operations for the DP island.

    Each op targets exactly one named anchor section within a node's
    dynamic_prompt, making every mutation structurally verifiable.
    """
    SET_SCOPE_BOUNDARY = "set_scope_boundary"
    """Replace the STRICTLY AVOID section with a new, task-specific boundary."""

    SET_EVIDENCE_SPEC = "set_evidence_spec"
    """Replace SOURCE PRIORITY + EXTRACTION SCHEMA for a RETRIEVE node."""

    SET_EXECUTION_METHOD = "set_execution_method"
    """Replace the core HOW instruction (WHO & HOW / WHAT TO FIND / ANALYTICAL FRAME)."""

    SET_DEPTH_ALLOCATION = "set_depth_allocation"
    """Replace DEPTH BUDGET or DEPTH ALLOCATION section."""

    TRANSFER_ANCHOR = "transfer_anchor"
    """Copy one anchor section from a high-quality node to the target node."""


class PromptEdit(BaseModel):
    """A single DP-island edit: targeted anchor-section rewrite for one node."""

    node_index: str = Field(
        description="Index of the subtask to modify (matches the flat list position)"
    )
    op: PromptEditOp = Field(
        description=(
            "Which anchor-section operation to apply. "
            "set_scope_boundary: replace STRICTLY AVOID. "
            "set_evidence_spec: replace SOURCE PRIORITY + EXTRACTION SCHEMA. "
            "set_execution_method: replace WHO & HOW / WHAT TO FIND / ANALYTICAL FRAME. "
            "set_depth_allocation: replace DEPTH BUDGET / DEPTH ALLOCATION. "
            "transfer_anchor: copy an anchor section from source_node."
        )
    )
    section_content: str = Field(
        description=(
            "The new content for the target anchor section. "
            "Must be task-specific; generic filler will be penalised. "
            "For transfer_anchor, this is the copied section text."
        )
    )
    source_node: Optional[str] = Field(
        default=None,
        description=(
            "For transfer_anchor only: the node index to copy the anchor section from. "
            "Leave None for all other ops."
        )
    )
    rationale: str = Field(
        description="Why this anchor-section change improves the plan quality."
    )


# =====================================================================
# T Island — TopoEdit (atomic structural operations)
# =====================================================================


class TopoEditOp(str, Enum):
    """Atomic structural operations for the T island.

    Four primitives, each with bounded and locally verifiable side-effects.
    """
    SPLIT = "split"
    """Replace one node with 2-N focused parallel nodes."""

    ADD = "add"
    """Insert one new node at a specified position in the DAG."""

    DELETE = "delete"
    """Remove one node; downstream deps are rerouted via reroute_to."""

    REORDER_DEPS = "reorder_deps"
    """Replace the dependency list of one node (no structural change)."""


class TopoEditParams(BaseModel):
    """Typed operation parameters for TopoEdit.

    Only the fields relevant to the chosen ``op`` need to be filled;
    the rest should be left as ``None``.
    """

    # split: break target_node into multiple focused nodes
    new_goals: Optional[List[str]] = None
    new_types: Optional[List[str]] = None
    # Per-child prompts for split — preferred; length MUST equal len(new_goals).
    # If omitted, the system falls back to the parent node's prompt.
    dynamic_prompts: Optional[List[str]] = None
    # split: parallelism control.
    # True  = all children share the original deps and run in parallel (no head→tail chain).
    # False = children 2..N depend on child 1 (legacy chain behaviour).
    # None  = auto: parallel for depth>=1 nodes and for depth-0 RETRIEVE/WRITE nodes;
    #         chain (False) for depth-0 THINK nodes.
    parallel_split: Optional[bool] = Field(
        default=None,
        description=(
            "Split parallelism. True=all children share original deps and run in "
            "parallel. False=children 2..N depend on child 1 (legacy chain). "
            "None=auto: parallel for depth>=1 nodes and for depth-0 RETRIEVE/WRITE; "
            "chain for depth-0 THINK."
        ),
    )

    # add: insert a new node
    goal: Optional[str] = None
    task_type: Optional[str] = None
    after_node: Optional[str] = None
    deps: Optional[List[str]] = None
    dynamic_prompt: Optional[str] = None
    # add: explicit hierarchy fields (REQUIRED for creating depth-1 children).
    # When set, these override the values inherited from after_node.
    parent_node_id: Optional[str] = None
    depth: Optional[int] = None
    # add: optional symbolic name — sibling add ops can use "#<symbolic_id>"
    # in their deps list; apply_topo_edits resolves these after all ops.
    symbolic_id: Optional[str] = None

    # delete: reroute downstream nodes that depended on the deleted node.
    # List the node indices (or "#symbolic_id" refs) that should replace
    # the deleted node in all downstream dependency lists.
    reroute_to: Optional[List[str]] = None

    # reorder_deps: new dependency list for target_node
    new_deps: Optional[List[str]] = None


class TopoEdit(BaseModel):
    """A single T-island edit: one atomic structural DAG operation."""

    op: TopoEditOp = Field(
        description=(
            "Atomic structural operation: "
            "split (replace one node with N parallel focused nodes), "
            "add (insert one new node), "
            "delete (remove one node with dep rerouting), "
            "reorder_deps (change dependency list only)."
        )
    )
    target_node: str = Field(description="Index of the node this operation targets")
    params: TopoEditParams = Field(
        default_factory=TopoEditParams,
        description="Operation-specific parameters",
    )
    rationale: str = Field(
        description="Why this structural change improves DAG efficiency or evidence coverage"
    )
