from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .config import Settings, load_settings
from .schemas import SearchExecution, TaskQueryRequest, WebSearchRequest
from .service import WebSearchService, extract_query
from .task_store import TaskRecord, TaskStore


settings: Settings = load_settings()
search_service = WebSearchService(settings)
task_store = TaskStore(search_service, ttl_seconds=settings.task_ttl_seconds)


def _load_api_keys() -> set[str]:
    path = settings.api_keys_file
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


api_keys = _load_api_keys()


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        yield
    finally:
        await search_service.cleanup()


app = FastAPI(
    title="ROMA Web Search API",
    version="0.1.0",
    description="UniFuncs-style sync/async wrapper around ROMA web search.",
    lifespan=lifespan,
)
router = APIRouter()


def _request_id() -> str:
    return str(uuid.uuid4())


async def require_auth(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
) -> str:
    candidate = None
    if authorization and authorization.startswith("Bearer "):
        candidate = authorization.removeprefix("Bearer ").strip()
    elif x_api_key:
        candidate = x_api_key.strip()

    if candidate and candidate in api_keys:
        return candidate
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key.",
    )


def _chat_completion_payload(execution: SearchExecution) -> Dict[str, Any]:
    created = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": created,
        "model": execution.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": execution.content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "roma_result": execution.roma_result,
        "artifact_json_path": execution.artifact_json_path,
        "artifact_markdown_path": execution.artifact_markdown_path,
        "artifact_record_id": execution.artifact_record_id,
        "api_key": execution.api_key,
    }


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    if not text:
        return [""]
    return [text[idx : idx + chunk_size] for idx in range(0, len(text), chunk_size)]


def _stream_chunk_chars(payload: WebSearchRequest) -> int:
    requested = payload.stream_chunk_chars
    if requested is None:
        return settings.stream_chunk_chars
    return max(1, min(int(requested), 2000))


def _stream_chunk_delay_ms(payload: WebSearchRequest) -> int:
    requested = payload.stream_chunk_delay_ms
    if requested is None:
        return settings.stream_chunk_delay_ms
    return max(0, min(int(requested), 5000))


async def _stream_chat_completion(payload: WebSearchRequest, api_key: str) -> AsyncIterator[str]:
    created = int(time.time())
    stream_id = f"chatcmpl-{uuid.uuid4().hex}"
    chunk_chars = _stream_chunk_chars(payload)
    chunk_delay_ms = _stream_chunk_delay_ms(payload)

    first = {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": payload.model or settings.default_model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"

    try:
        execution = await search_service.execute(payload, api_key=api_key)
    except Exception as exc:
        error_chunk = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": payload.model or settings.default_model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": f"\n[ERROR] Web search execution failed: {exc}"},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    for piece in _chunk_text(execution.content, chunk_chars):
        chunk = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": execution.model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": piece},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        if chunk_delay_ms:
            await asyncio.sleep(chunk_delay_ms / 1000)

    final = {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": execution.model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def _task_progress(task: TaskRecord) -> Dict[str, Any]:
    if task.status == "completed":
        return {"current": 100, "total": 100, "message": "任务已完成"}
    if task.status == "failed":
        return {"current": 100, "total": 100, "message": "任务执行失败"}
    if task.status == "running":
        return {"current": 50, "total": 100, "message": "任务执行中"}
    return {"current": 0, "total": 100, "message": "任务等待执行"}


def _task_data(task: TaskRecord) -> Dict[str, Any]:
    query = extract_query(task.payload)
    data: Dict[str, Any] = {
        "task_id": task.task_id,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "progress": _task_progress(task),
    }

    if task.status == "completed" and task.result is not None:
        result_count = len(task.result.roma_result.get("contexts", []))
        duration_ms = task.result.roma_result.get("debug", {}).get("duration_ms", 0)
        data.update(
            {
                "result": {
                    "content": task.result.content,
                    "roma_result": task.result.roma_result,
                    "artifact_json_path": task.result.artifact_json_path,
                    "artifact_markdown_path": task.result.artifact_markdown_path,
                    "artifact_record_id": task.result.artifact_record_id,
                },
                "statistics": {
                    "result_count": result_count,
                    "duration_ms": duration_ms,
                },
                "session": {
                    "session_id": task.task_id,
                    "status": "finished",
                    "model": task.result.model,
                    "question": query,
                },
            }
        )
    elif task.status == "failed":
        data["error"] = task.error or "Unknown task failure."

    return data


def _task_envelope(task: TaskRecord) -> Dict[str, Any]:
    return {
        "code": 0,
        "message": "OK",
        "data": _task_data(task),
        "requestId": _request_id(),
    }


def _task_not_found(task_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "code": 404,
            "message": "Task not found.",
            "data": {"task_id": task_id},
            "requestId": _request_id(),
        },
    )


def _task_visible_to_api_key(task: TaskRecord, api_key: str) -> bool:
    return task.api_key_hash == search_service._api_key_hash(api_key)


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "roma-web-search",
        "backend": settings.web_backend,
        "canonical_prefix": settings.canonical_prefix,
        "compatibility_prefix": settings.compatibility_prefix,
        "artifact_dir": str(settings.artifact_dir.resolve()),
        "history_file": str(settings.history_file.resolve()),
        "config_env_file": str(settings.env_file.resolve()) if settings.env_file else None,
        "package_root": str(settings.project_root.resolve()),
        "roma_src_root": str(settings.roma_src_root.resolve()),
        "skill_root": str(settings.skill_root.resolve()),
        "union_search_root": str(settings.union_search_root.resolve()),
        "news_aggregator_root": str(settings.news_aggregator_root.resolve()),
        "api_key_count": len(api_keys),
        "api_keys_file": str(settings.api_keys_file.resolve()),
    }


@app.get("/test-ui", include_in_schema=False)
async def test_ui() -> FileResponse:
    return FileResponse(settings.project_root / "test_ui.html")


@router.post("/chat/completions")
async def create_chat_completion(
    payload: WebSearchRequest,
    api_key: str = Depends(require_auth),
) -> Any:
    if payload.stream:
        try:
            extract_query(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        return StreamingResponse(
            _stream_chat_completion(payload, api_key),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        execution = await search_service.execute(payload, api_key=api_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Web search execution failed: {exc}",
        ) from exc

    return JSONResponse(_chat_completion_payload(execution))


@router.post("/create_task")
async def create_task(
    payload: WebSearchRequest,
    api_key: str = Depends(require_auth),
) -> Dict[str, Any]:
    try:
        extract_query(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    task = await task_store.create(payload, api_key=api_key)
    return {
        "code": 0,
        "message": "OK",
        "data": {
            "task_id": task.task_id,
            "status": task.status,
            "created_at": task.created_at,
        },
        "requestId": _request_id(),
    }


@router.get("/query_task")
async def query_task_get(
    task_id: str = Query(..., min_length=1),
    api_key: str = Depends(require_auth),
) -> Any:
    task = await task_store.get(task_id)
    if task is None or not _task_visible_to_api_key(task, api_key):
        return _task_not_found(task_id)
    return _task_envelope(task)


@router.post("/query_task")
async def query_task_post(
    payload: TaskQueryRequest,
    api_key: str = Depends(require_auth),
) -> Any:
    task = await task_store.get(payload.task_id)
    if task is None or not _task_visible_to_api_key(task, api_key):
        return _task_not_found(payload.task_id)
    return _task_envelope(task)


@router.get("/history")
async def query_history(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    api_key: str = Depends(require_auth),
) -> Dict[str, Any]:
    return {
        "code": 0,
        "message": "OK",
        "data": await search_service.history_for_api_key(
            api_key,
            limit=limit,
            offset=offset,
        ),
        "requestId": _request_id(),
    }


app.include_router(router, prefix=settings.canonical_prefix)
if settings.compatibility_prefix != settings.canonical_prefix:
    app.include_router(router, prefix=settings.compatibility_prefix)
