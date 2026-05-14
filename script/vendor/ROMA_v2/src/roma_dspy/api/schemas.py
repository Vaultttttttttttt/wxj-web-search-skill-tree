"""Pydantic schemas for API requests and responses."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from roma_dspy.core.signatures.base_models.subtask import SubTask


# ============================================================================
# Request Schemas
# ============================================================================


class SolveRequest(BaseModel):
    """Request schema for starting a new task execution."""

    goal: str = Field(
        ..., min_length=1, description="Task goal to decompose and execute"
    )
    max_depth: int = Field(
        default=2, ge=0, le=10, description="Maximum recursion depth"
    )
    config_profile: str = Field(
        default="default", description="Configuration profile name (required)"
    )
    config_overrides: Optional[Dict[str, Any]] = Field(
        default=None, description="Configuration overrides"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata"
    )
    approved_plan: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Pre-approved research plan payload to skip planner re-generation",
    )


class ResearchPlanDirectives(BaseModel):
    """User-facing directive bundle for a research plan."""

    report_policy: Optional[str] = Field(
        default=None, description="Global report strategy and policy"
    )


class ResearchPlanDraftRequest(BaseModel):
    """Request schema for generating a reviewable research plan draft."""

    goal: str = Field(..., min_length=1, description="Original user research query")
    max_depth: int = Field(
        default=2, ge=0, le=10, description="Maximum recursion depth"
    )
    config_profile: str = Field(
        default="default", description="Configuration profile name"
    )
    config_overrides: Optional[Dict[str, Any]] = Field(
        default=None, description="Configuration overrides"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata"
    )
    user_notes: Optional[str] = Field(
        default=None,
        description="Optional scope notes or constraints that should influence planning",
    )


class ApprovedResearchPlan(BaseModel):
    """Editable research plan confirmed by the user before execution."""

    plan_id: str = Field(..., description="Research plan draft identifier")
    goal: str = Field(..., min_length=1, description="Original user query")
    refined_goal: str = Field(
        ..., min_length=1, description="Goal after user refinement/clarification"
    )
    subtasks: List[SubTask] = Field(
        default_factory=list, description="User-approved subtask list"
    )
    dependencies_graph: Optional[Dict[str, List[str]]] = Field(
        default=None,
        description="Dependency mapping using subtask indices as strings",
    )
    directives: ResearchPlanDirectives = Field(
        default_factory=ResearchPlanDirectives,
        description="User-approved directive bundle",
    )
    user_notes: Optional[str] = Field(
        default=None, description="Freeform notes captured during plan review"
    )
    clarifications: List[str] = Field(
        default_factory=list,
        description="Explicit user clarifications or edits applied to the draft",
    )


class ResearchPlanDraftResponse(BaseModel):
    """Response schema for a generated research plan draft."""

    plan_id: str
    status: str = Field(default="draft")
    goal: str
    refined_goal: str
    subtasks: List[SubTask]
    dependencies_graph: Optional[Dict[str, List[str]]] = None
    directives: ResearchPlanDirectives = Field(
        default_factory=ResearchPlanDirectives
    )
    max_depth: int
    config_profile: str
    config_overrides: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    user_notes: Optional[str] = None
    summary: Dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = True
    created_at: datetime
    updated_at: datetime


class ResearchPlanExecutionRequest(BaseModel):
    """Request schema for starting execution from an approved research plan."""

    approved_plan: ApprovedResearchPlan
    max_depth: int = Field(
        default=2, ge=0, le=10, description="Maximum recursion depth"
    )
    config_profile: str = Field(
        default="default", description="Configuration profile name"
    )
    config_overrides: Optional[Dict[str, Any]] = Field(
        default=None, description="Configuration overrides"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Additional metadata"
    )


class CheckpointRestoreRequest(BaseModel):
    """Request schema for restoring from checkpoint."""

    checkpoint_id: str = Field(..., description="Checkpoint ID to restore from")
    resume: bool = Field(default=True, description="Resume execution after restore")


class ConfigUpdateRequest(BaseModel):
    """Request schema for updating configuration."""

    profile: Optional[str] = Field(
        default=None, description="Configuration profile name"
    )
    overrides: Dict[str, Any] = Field(..., description="Configuration overrides")


# ============================================================================
# Response Schemas
# ============================================================================


class TaskNodeResponse(BaseModel):
    """Response schema for a single task node."""

    task_id: str
    goal: str
    status: str
    depth: int
    node_type: Optional[str] = None
    parent_id: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class DAGStatisticsResponse(BaseModel):
    """Response schema for DAG statistics."""

    dag_id: str
    total_tasks: int
    status_counts: Dict[str, int]
    depth_distribution: Dict[int, int]
    num_subgraphs: int
    is_complete: bool


class ExecutionResponse(BaseModel):
    """Response schema for execution metadata."""

    execution_id: str
    status: str
    initial_goal: str
    max_depth: int
    profile: str
    experiment_name: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    created_at: datetime
    updated_at: datetime
    config: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any]
    final_result: Optional[Dict[str, Any]] = None


class ExecutionDetailResponse(ExecutionResponse):
    """Extended execution response with statistics."""

    statistics: Optional[DAGStatisticsResponse] = None


class ExecutionListResponse(BaseModel):
    """Response schema for listing executions."""

    executions: List[ExecutionResponse]
    total: int
    offset: int
    limit: int


class CheckpointResponse(BaseModel):
    """Response schema for checkpoint metadata."""

    checkpoint_id: str
    execution_id: str
    created_at: datetime
    trigger: str
    state: str
    file_path: Optional[str] = None
    file_size_bytes: Optional[int] = None
    compressed: bool
    dag_snapshot: Optional[Dict[str, Any]] = Field(
        default=None,
        description="DAG snapshot containing task hierarchy and execution state",
    )


class CheckpointListResponse(BaseModel):
    """Response schema for listing checkpoints."""

    checkpoints: List[CheckpointResponse]
    total: int


class TaskTraceResponse(BaseModel):
    """Response schema for task trace."""

    trace_id: int
    execution_id: str
    task_id: str
    parent_task_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    task_type: str
    node_type: Optional[str] = None
    status: str
    depth: int
    retry_count: int
    goal: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class LMTraceResponse(BaseModel):
    """Response schema for LM trace."""

    trace_id: int
    execution_id: str
    task_id: Optional[str] = None
    module_name: str
    created_at: datetime
    model: str
    temperature: Optional[float] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: Optional[float] = None
    latency_ms: Optional[int] = None
    prompt: Optional[str] = None
    response: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    """Response schema for system health check."""

    status: str
    version: str
    uptime_seconds: float
    active_executions: int
    storage_connected: bool
    cache_size: int
    timestamp: datetime


class ErrorResponse(BaseModel):
    """Response schema for API errors."""

    error: str
    detail: Optional[str] = None
    execution_id: Optional[str] = None
    timestamp: datetime


class MetricsResponse(BaseModel):
    """Response schema for execution metrics."""

    execution_id: str
    total_lm_calls: int
    total_tokens: int
    total_cost_usd: float
    average_latency_ms: float
    task_breakdown: Dict[str, Dict[str, Any]]


class StatusPollingResponse(BaseModel):
    """Response schema for status polling."""

    execution_id: str
    status: str
    progress: float  # 0.0 to 1.0
    current_task_id: Optional[str] = None
    current_task_goal: Optional[str] = None
    completed_tasks: int
    total_tasks: int
    estimated_remaining_seconds: Optional[int] = None
    last_updated: datetime


class ExecutionDataResponse(BaseModel):
    """Response schema for consolidated execution data from MLflow traces.

    This endpoint provides real-time trace data suitable for live visualization.
    Data is fetched from MLflow and consolidated by ExecutionDataService.
    Searches all MLflow experiments by execution_id tag.
    """

    execution_id: str
    tasks: List[Dict[str, Any]]  # Task entries with agent_executions
    summary: Dict[str, Any]  # Aggregated metrics
    traces: List[Dict[str, Any]]  # Trace metadata
    fallback_spans: List[Dict[str, Any]]  # Spans without task_id
