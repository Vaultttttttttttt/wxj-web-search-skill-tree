from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from .schemas import SearchExecution, WebSearchRequest
from .service import WebSearchService


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class TaskRecord:
    task_id: str
    payload: WebSearchRequest
    api_key_hash: str
    api_key: Optional[str] = field(default=None, repr=False)
    status: str = "pending"
    created_at: str = field(default_factory=iso_now)
    updated_at: str = field(default_factory=iso_now)
    result: Optional[SearchExecution] = None
    error: Optional[str] = None
    created_monotonic: float = field(default_factory=time.monotonic)


class TaskStore:
    def __init__(self, service: WebSearchService, ttl_seconds: int) -> None:
        self.service = service
        self.ttl_seconds = max(60, ttl_seconds)
        self._tasks: Dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, payload: WebSearchRequest, api_key: Optional[str] = None) -> TaskRecord:
        await self._prune_expired()
        task = TaskRecord(
            task_id=str(uuid.uuid4()),
            payload=payload,
            api_key=api_key,
            api_key_hash=self.service._api_key_hash(api_key),
        )
        async with self._lock:
            self._tasks[task.task_id] = task
        asyncio.create_task(self._run(task.task_id))
        return task

    async def get(self, task_id: str) -> Optional[TaskRecord]:
        await self._prune_expired()
        async with self._lock:
            return self._tasks.get(task_id)

    async def _run(self, task_id: str) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = "running"
            task.updated_at = iso_now()

        try:
            result = await self.service.execute(
                task.payload,
                artifact_id=task.task_id,
                api_key=task.api_key,
            )
            async with self._lock:
                latest = self._tasks.get(task_id)
                if latest is None:
                    return
                latest.status = "completed"
                latest.result = result
                latest.updated_at = iso_now()
        except Exception as exc:
            async with self._lock:
                latest = self._tasks.get(task_id)
                if latest is None:
                    return
                latest.status = "failed"
                latest.error = str(exc)
                latest.updated_at = iso_now()

    async def _prune_expired(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        async with self._lock:
            stale = [
                task_id
                for task_id, task in self._tasks.items()
                if task.created_monotonic < cutoff
            ]
            for task_id in stale:
                self._tasks.pop(task_id, None)
