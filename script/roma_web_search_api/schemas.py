from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    content: Any


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "roma-web-search"
    messages: List[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    query: Optional[str] = None
    top_n: Optional[int] = Field(default=None, ge=1)
    stream_chunk_chars: Optional[int] = Field(default=None, ge=1, le=2000)
    stream_chunk_delay_ms: Optional[int] = Field(default=None, ge=0, le=5000)


class TaskQueryRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(min_length=1)


class SearchExecution(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query: str
    model: str
    content: str
    roma_result: Dict[str, Any]
    artifact_json_path: Optional[str] = None
    artifact_markdown_path: Optional[str] = None
