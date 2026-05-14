"""Execution management endpoints."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger

from roma_dspy.api.schemas import (
    ResearchPlanDraftRequest,
    ResearchPlanDraftResponse,
    ResearchPlanExecutionRequest,
    SolveRequest,
    ExecutionResponse,
    ExecutionDetailResponse,
    ExecutionListResponse,
    StatusPollingResponse,
    ExecutionDataResponse,
    ErrorResponse,
)
from roma_dspy.api.helpers import (
    execution_to_response,
    execution_to_detail_response,
    calculate_progress,
)
from roma_dspy.api.dependencies import (
    get_storage,
    verify_execution_exists,
    validate_pagination,
)
from roma_dspy.core.storage.postgres_storage import PostgresStorage
from roma_dspy.core.engine.dag import TaskDAG
from roma_dspy.types import TaskStatus

router = APIRouter()


@router.post(
    "/research-plans/draft", response_model=ResearchPlanDraftResponse, status_code=201
)
async def draft_research_plan(
    request: Request, draft_request: ResearchPlanDraftRequest
) -> ResearchPlanDraftResponse:
    """Generate a reviewable research plan draft without starting execution."""
    app_state = request.app.state.app_state

    if app_state.execution_service is None:
        raise HTTPException(
            status_code=503,
            detail="ExecutionService not available (storage may be disabled)",
        )

    try:
        return await app_state.execution_service.draft_research_plan(
            goal=draft_request.goal,
            max_depth=draft_request.max_depth,
            config_profile=draft_request.config_profile,
            config_overrides=draft_request.config_overrides,
            metadata=draft_request.metadata,
            user_notes=draft_request.user_notes,
        )
    except Exception as e:
        logger.error(f"Failed to draft research plan: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to draft research plan: {str(e)}"
        )


@router.get(
    "/research-plans/{plan_id}", response_model=ResearchPlanDraftResponse
)
async def get_research_plan(request: Request, plan_id: str) -> ResearchPlanDraftResponse:
    """Fetch an in-memory research plan draft."""
    app_state = request.app.state.app_state

    if app_state.execution_service is None:
        raise HTTPException(
            status_code=503,
            detail="ExecutionService not available (storage may be disabled)",
        )

    draft = app_state.execution_service.get_research_plan(plan_id)
    if not draft:
        raise HTTPException(status_code=404, detail=f"Research plan {plan_id} not found")
    return draft


@router.post(
    "/research-plans/{plan_id}/execute", response_model=ExecutionResponse, status_code=202
)
async def execute_research_plan(
    request: Request, plan_id: str, execution_request: ResearchPlanExecutionRequest
) -> ExecutionResponse:
    """Start an execution from a user-approved research plan."""
    app_state = request.app.state.app_state

    if app_state.execution_service is None:
        raise HTTPException(
            status_code=503,
            detail="ExecutionService not available (storage may be disabled)",
        )

    approved_plan = execution_request.approved_plan
    if approved_plan.plan_id != plan_id:
        raise HTTPException(
            status_code=400,
            detail="Path plan_id does not match approved_plan.plan_id",
        )

    # Check cache for logging purposes only — the approved_plan from the request
    # body is the source of truth. The cache may be empty if the server was
    # restarted or the plan was restored from client-side storage.
    cached_plan = app_state.execution_service.get_research_plan(plan_id)
    if not cached_plan:
        logger.warning(
            f"Research plan {plan_id} not found in server cache "
            "(likely restored from client storage). Proceeding with approved_plan from request."
        )

    try:
        execution_id = await app_state.execution_service.start_execution(
            goal=approved_plan.refined_goal,
            max_depth=execution_request.max_depth,
            config_profile=execution_request.config_profile,
            config_overrides=execution_request.config_overrides,
            metadata=execution_request.metadata,
            approved_plan=approved_plan,
        )

        storage = app_state.storage
        execution = await storage.get_execution(execution_id)
        if not execution:
            raise HTTPException(
                status_code=500, detail="Failed to create execution record"
            )

        return execution_to_response(execution)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start execution from research plan: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start execution from research plan: {str(e)}",
        )


@router.post("/executions", response_model=ExecutionResponse, status_code=202)
async def create_execution(
    request: Request, solve_request: SolveRequest
) -> ExecutionResponse:
    """
    Start a new task execution.

    Creates a background task that decomposes and executes the goal.

    Returns:
        ExecutionResponse with execution_id for polling status
    """
    app_state = request.app.state.app_state

    if app_state.execution_service is None:
        raise HTTPException(
            status_code=503,
            detail="ExecutionService not available (storage may be disabled)",
        )

    try:
        # Deserialize approved_plan if provided so the planner is skipped
        approved_plan = None
        if solve_request.approved_plan:
            try:
                from roma_dspy.api.schemas import ApprovedResearchPlan
                approved_plan = ApprovedResearchPlan.model_validate(solve_request.approved_plan)
            except Exception as parse_err:
                logger.warning(f"Failed to parse approved_plan in SolveRequest, ignoring: {parse_err}")

        # Start execution
        execution_id = await app_state.execution_service.start_execution(
            goal=solve_request.goal,
            max_depth=solve_request.max_depth,
            config_profile=solve_request.config_profile,
            config_overrides=solve_request.config_overrides,
            metadata=solve_request.metadata,
            approved_plan=approved_plan,
        )

        # Get execution record
        storage = app_state.storage
        execution = await storage.get_execution(execution_id)

        if not execution:
            raise HTTPException(
                status_code=500, detail="Failed to create execution record"
            )

        return execution_to_response(execution)

    except Exception as e:
        logger.error(f"Failed to create execution: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to create execution: {str(e)}"
        )


@router.get("/executions", response_model=ExecutionListResponse)
async def list_executions(
    storage: PostgresStorage = Depends(get_storage),
    status: Optional[str] = Query(None, description="Filter by status"),
    experiment_name: Optional[str] = Query(
        None, description="Filter by experiment name"
    ),
    profile: Optional[str] = Query(None, description="Filter by profile"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> ExecutionListResponse:
    """
    List all executions with optional filtering.

    Args:
        status: Optional status filter (running, completed, failed)
        experiment_name: Optional experiment name filter
        profile: Optional profile name filter
        offset: Number of records to skip
        limit: Maximum number of records to return

    Returns:
        List of executions with pagination info
    """
    # Validate pagination
    offset, limit = validate_pagination(offset, limit)

    try:
        # Get executions from storage
        executions = await storage.list_executions(
            status=status,
            experiment_name=experiment_name,
            profile=profile,
            offset=offset,
            limit=limit,
        )

        # Get total count (without pagination)
        total = await storage.count_executions(
            status=status, experiment_name=experiment_name, profile=profile
        )

        # Convert to response schemas
        execution_responses = [
            execution_to_response(execution) for execution in executions
        ]

        return ExecutionListResponse(
            executions=execution_responses, total=total, offset=offset, limit=limit
        )

    except Exception as e:
        logger.error(f"Failed to list executions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to list executions: {str(e)}"
        )


@router.get("/executions/{execution_id}", response_model=ExecutionDetailResponse)
async def get_execution(
    execution_id: str = Depends(verify_execution_exists),
    storage: PostgresStorage = Depends(get_storage),
) -> ExecutionDetailResponse:
    """
    Get detailed execution information including DAG visualization.

    Args:
        execution_id: Execution ID

    Returns:
        Detailed execution info with DAG snapshot
    """
    try:
        execution = await storage.get_execution(execution_id)

        if not execution:
            raise HTTPException(
                status_code=404, detail=f"Execution {execution_id} not found"
            )

        # Convert to detail response (includes DAG snapshot from checkpoints)
        return await execution_to_detail_response(execution, storage=storage)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get execution {execution_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get execution: {str(e)}"
        )


@router.get("/executions/{execution_id}/status", response_model=StatusPollingResponse)
async def get_execution_status(
    request: Request, execution_id: str = Depends(verify_execution_exists)
) -> StatusPollingResponse:
    """
    Get current execution status for polling.

    This endpoint is optimized for frequent polling with caching.

    Args:
        execution_id: Execution ID

    Returns:
        Current execution status with progress information
    """
    app_state = request.app.state.app_state

    if app_state.execution_service is None:
        raise HTTPException(status_code=503, detail="ExecutionService not available")

    try:
        # Get status (uses cache)
        status_data = await app_state.execution_service.get_execution_status(
            execution_id
        )

        if not status_data:
            raise HTTPException(
                status_code=404, detail=f"Execution {execution_id} not found"
            )

        # Get execution from storage for progress calculation
        storage = app_state.storage
        execution = await storage.get_execution(execution_id)

        if not execution:
            raise HTTPException(
                status_code=404, detail=f"Execution {execution_id} not found"
            )

        # Get current task from checkpoint DAG snapshot
        current_task_id = None
        current_task_goal = None

        dag_data = None
        # Read from checkpoint (primary and only source post-migration)
        try:
            checkpoint = await storage.get_latest_checkpoint(
                execution_id, valid_only=True
            )
            if checkpoint and checkpoint.root_dag:
                dag_data = checkpoint.root_dag
                # Convert DAGSnapshot model to dict if needed
                if hasattr(dag_data, "model_dump"):
                    dag_data = dag_data.model_dump(mode="python")
        except Exception as e:
            logger.warning(f"Failed to load checkpoint for current task: {e}")

        if dag_data:
            try:
                dag = TaskDAG.from_dict(dag_data)
                
                # Update execution stats from live DAG snapshot
                # This ensures progress bar updates even if execution record is stale
                all_tasks = dag.get_all_tasks()
                if all_tasks:
                    execution.total_tasks = len(all_tasks)
                    execution.completed_tasks = sum(
                        1 for t in all_tasks if t.status == TaskStatus.COMPLETED
                    )
                    execution.failed_tasks = sum(
                        1 for t in all_tasks if t.status == TaskStatus.FAILED
                    )

                active_tasks = [
                    task
                    for task in all_tasks
                    if getattr(task.status, "is_active", False)
                ]
                if active_tasks:
                    current_task = min(
                        active_tasks,
                        key=lambda task: (
                            task.depth,
                            task.started_at or task.created_at,
                            task.task_id,
                        ),
                    )
                    current_task_id = current_task.task_id
                    current_task_goal = current_task.goal
            except Exception as e:
                logger.warning(f"Failed to extract current task from DAG: {e}")

        # Calculate progress (using updated stats from checkpoint if available)
        progress = calculate_progress(execution)

        return StatusPollingResponse(
            execution_id=execution_id,
            status=execution.status,
            progress=progress,
            current_task_id=current_task_id,
            current_task_goal=current_task_goal,
            completed_tasks=execution.completed_tasks,
            total_tasks=execution.total_tasks,
            estimated_remaining_seconds=None,  # Could be calculated with timing data
            last_updated=execution.updated_at,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get execution status {execution_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get execution status: {str(e)}"
        )


@router.post("/executions/{execution_id}/cancel", response_model=ExecutionResponse)
async def cancel_execution(
    request: Request, execution_id: str = Depends(verify_execution_exists)
) -> ExecutionResponse:
    """
    Cancel a running execution.

    Args:
        execution_id: Execution ID

    Returns:
        Updated execution with cancelled status
    """
    app_state = request.app.state.app_state

    if app_state.execution_service is None:
        raise HTTPException(status_code=503, detail="ExecutionService not available")

    try:
        # Cancel execution
        cancelled = await app_state.execution_service.cancel_execution(execution_id)

        if not cancelled:
            raise HTTPException(
                status_code=400,
                detail=f"Execution {execution_id} is not running (cannot cancel)",
            )

        # Get updated execution
        storage = app_state.storage
        execution = await storage.get_execution(execution_id)

        if not execution:
            raise HTTPException(
                status_code=404, detail=f"Execution {execution_id} not found"
            )

        return execution_to_response(execution)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel execution {execution_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to cancel execution: {str(e)}"
        )


@router.get("/executions/{execution_id}/data", response_model=ExecutionDataResponse)
async def get_execution_data(
    request: Request, execution_id: str, storage: PostgresStorage = Depends(get_storage)
) -> ExecutionDataResponse:
    """
    Get consolidated execution data from MLflow traces.

    This endpoint fetches and consolidates MLflow trace data for real-time visualization.
    It uses ExecutionDataService to fetch traces and build task/agent execution structure.

    Use this for:
    - Live TUI updates (poll this endpoint periodically)
    - Real-time visualization of task progress
    - Accessing detailed span/token metrics

    Args:
        execution_id: Execution ID

    Returns:
        Consolidated execution data with tasks, agent executions, spans, and metrics
    """
    # Verify execution exists in storage
    execution = await storage.get_execution(execution_id)
    if not execution:
        raise HTTPException(
            status_code=404, detail=f"Execution {execution_id} not found"
        )

    # Get MLflow tracking URI from environment
    # Docker sets MLFLOW_TRACKING_URI=http://mlflow:5000
    import os

    mlflow_tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")

    try:
        # Import ExecutionDataService here to avoid circular dependencies
        from roma_dspy.core.services.execution_data_service import ExecutionDataService

        # Create service instance
        # Searches all experiments by execution_id tag (no experiment name needed)
        service = ExecutionDataService(
            mlflow_tracking_uri=mlflow_tracking_uri,
        )

        # Get consolidated data
        data = service.get_execution_data(execution_id)

        return ExecutionDataResponse(**data)

    except ValueError as e:
        # Handle case where no MLflow traces found (e.g., execution still running)
        error_msg = str(e)
        if "No MLflow traces found" in error_msg:
            logger.warning(
                f"No MLflow traces found for execution {execution_id}, returning empty data"
            )
            # Return empty data structure instead of 500 error
            return ExecutionDataResponse(
                execution_id=execution_id,
                tasks=[],
                summary={},
                traces=[],
                fallback_spans=[],
            )
        # Re-raise other ValueError exceptions
        logger.error(f"Failed to get execution data for {execution_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get execution data: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Failed to get execution data for {execution_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get execution data: {str(e)}"
        )


@router.get("/executions/{execution_id}/checkpoint")
async def get_latest_checkpoint(
    execution_id: str, storage: PostgresStorage = Depends(get_storage)
) -> dict:
    """
    Get the latest valid checkpoint for an execution.

    Returns checkpoint data including DAG snapshot with task dependencies.
    This is used by the TUI for DAG visualization.

    Args:
        execution_id: Execution ID
        storage: Storage dependency

    Returns:
        Checkpoint data with execution_id, dag (with dependencies), tasks, etc.
    """
    # Verify execution exists
    execution = await storage.get_execution(execution_id)
    if not execution:
        raise HTTPException(
            status_code=404, detail=f"Execution {execution_id} not found"
        )

    try:
        # Get latest valid checkpoint
        checkpoint = await storage.get_latest_checkpoint(execution_id, valid_only=True)

        if not checkpoint:
            # Return empty checkpoint structure
            return {
                "execution_id": execution_id,
                "tasks": {},
                "root_goal": "",
                "status": "unknown",
                "checkpoints": [],
            }

        # Convert checkpoint to dict for API response
        checkpoint_dict = {
            "execution_id": execution_id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "created_at": checkpoint.created_at.isoformat()
            if checkpoint.created_at
            else None,
            "trigger": checkpoint.trigger.value
            if hasattr(checkpoint, "trigger")
            else None,
        }

        # Include DAG snapshot if available
        if checkpoint.root_dag:
            dag_snapshot = checkpoint.root_dag
            # Convert to dict if it's a Pydantic model
            if hasattr(dag_snapshot, "model_dump"):
                dag_dict = dag_snapshot.model_dump(mode="python")
            elif hasattr(dag_snapshot, "dict"):
                dag_dict = dag_snapshot.dict()
            else:
                dag_dict = dict(dag_snapshot) if isinstance(dag_snapshot, dict) else {}

            checkpoint_dict["dag"] = dag_dict

            # Extract tasks from DAG snapshot
            if "tasks" in dag_dict:
                checkpoint_dict["tasks"] = dag_dict["tasks"]

        return checkpoint_dict

    except Exception as e:
        logger.error(f"Failed to get checkpoint for {execution_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get checkpoint: {str(e)}"
        )
