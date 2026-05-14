from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from web_api.config import load_settings
from web_api.main import api_keys, app, search_service, task_store
from web_api.schemas import SearchExecution, WebSearchRequest
from web_api.service import WebSearchService


api_keys.add("test-key")


def _fake_execution() -> SearchExecution:
    return SearchExecution(
        query="test query",
        model="roma-web-search",
        content="### 检索报告\n- **决策模式**: WEB",
        roma_result={
            "query": "test query",
            "decision": "web",
            "confidence": 1.0,
            "contexts": [],
            "sources": [],
            "debug": {"duration_ms": 12},
        },
        artifact_json_path="/tmp/fake.json",
        artifact_markdown_path="/tmp/fake.md",
    )


def test_chat_completion_contract(monkeypatch) -> None:
    async def fake_execute(payload):
        return _fake_execution()

    monkeypatch.setattr(search_service, "execute", fake_execute)

    client = TestClient(app)
    response = client.post(
        "/web-search/v1/chat/completions",
        headers={"Authorization": "Bearer test-key"},
        json={
            "model": "roma-web-search",
            "messages": [{"role": "user", "content": "test query"}],
            "stream": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"].startswith("### 检索报告")
    assert body["roma_result"]["decision"] == "web"
    assert body["artifact_json_path"] == "/tmp/fake.json"
    assert body["artifact_markdown_path"] == "/tmp/fake.md"


def test_compat_prefix_contract(monkeypatch) -> None:
    async def fake_execute(payload):
        return _fake_execution()

    monkeypatch.setattr(search_service, "execute", fake_execute)

    client = TestClient(app)
    response = client.post(
        "/deepsearch/v1/chat/completions",
        headers={"Authorization": "Bearer test-key"},
        json={
            "model": "roma-web-search",
            "messages": [{"role": "user", "content": "test query"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["roma_result"]["query"] == "test query"


def test_missing_task_returns_unifuncs_style_envelope() -> None:
    client = TestClient(app)
    response = client.get(
        "/web-search/v1/query_task",
        headers={"Authorization": "Bearer test-key"},
        params={"task_id": "missing-task"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == 404
    assert body["data"]["task_id"] == "missing-task"


def test_service_persists_json_and_markdown(tmp_path, monkeypatch) -> None:
    settings = replace(load_settings(), artifact_dir=tmp_path)
    service = WebSearchService(settings)

    async def fake_toolkit():
        class Toolkit:
            async def adaptive_retrieve_async(self, query, mode, top_n):
                class Result:
                    def to_dict(self):
                        return {
                            "query": query,
                            "decision": "web",
                            "confidence": 1.0,
                            "contexts": [],
                            "sources": [],
                            "debug": {"duration_ms": 7},
                        }

                return Result()

            def _format_contexts_for_llm(self, result):
                return "### 检索报告\n- **决策模式**: WEB"

        return Toolkit()

    async def run():
        monkeypatch.setattr(service, "_ensure_toolkit", fake_toolkit)
        payload = WebSearchRequest(
            model="roma-web-search",
            messages=[{"role": "user", "content": "persist me"}],
        )
        return await service.execute(payload, artifact_id="artifact-test")

    execution = asyncio.run(run())
    assert execution.artifact_json_path is not None
    assert execution.artifact_markdown_path is not None
    assert Path(execution.artifact_json_path).exists()
    assert Path(execution.artifact_markdown_path).exists()


def test_service_exposes_public_source_label(tmp_path, monkeypatch) -> None:
    settings = replace(load_settings(), artifact_dir=tmp_path)
    service = WebSearchService(settings)

    async def fake_toolkit():
        class Toolkit:
            async def adaptive_retrieve_async(self, query, mode, top_n):
                class Result:
                    def to_dict(self):
                        return {
                            "query": query,
                            "decision": "web",
                            "confidence": 1.0,
                            "contexts": [
                                {
                                    "text": "paper snippet",
                                    "source": "exa",
                                    "url": "https://example.test/paper",
                                    "title": "[academic_research] paper title",
                                    "score": 0.7,
                                }
                            ],
                            "sources": ["https://example.test/paper"],
                            "debug": {"duration_ms": 7},
                        }

                return Result()

            def _format_contexts_for_llm(self, result):
                return "### 检索报告\n#### [1] [academic_research] paper title"

        return Toolkit()

    async def run():
        monkeypatch.setattr(service, "_ensure_toolkit", fake_toolkit)
        payload = WebSearchRequest(
            model="roma-web-search",
            messages=[{"role": "user", "content": "academic query"}],
        )
        return await service.execute(payload, artifact_id="source-label-test")

    execution = asyncio.run(run())
    context = execution.roma_result["contexts"][0]
    assert context["source"] == "academic_research"
    assert context["source_type"] == "web"
    assert context["discovery_backend"] == "exa"


def test_task_envelope_exposes_artifact_paths(monkeypatch) -> None:
    async def fake_execute(payload, artifact_id=None):
        execution = _fake_execution()
        execution.artifact_json_path = f"/tmp/{artifact_id}.json"
        execution.artifact_markdown_path = f"/tmp/{artifact_id}.md"
        return execution

    monkeypatch.setattr(task_store.service, "execute", fake_execute)

    async def run():
        task = await task_store.create(
            WebSearchRequest(
                model="roma-web-search",
                messages=[{"role": "user", "content": "async query"}],
            )
        )
        for _ in range(10):
            latest = await task_store.get(task.task_id)
            if latest and latest.status == "completed":
                break
            await asyncio.sleep(0)
        return task.task_id

    task_id = asyncio.run(run())
    client = TestClient(app)
    response = client.get(
        "/web-search/v1/query_task",
        headers={"Authorization": "Bearer test-key"},
        params={"task_id": task_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["result"]["artifact_json_path"].endswith(f"{task_id}.json")
    assert body["data"]["result"]["artifact_markdown_path"].endswith(f"{task_id}.md")


def test_auth_rejects_missing_key() -> None:
    client = TestClient(app)
    response = client.get(
        "/web-search/v1/query_task",
        params={"task_id": "missing-task"},
    )
    assert response.status_code == 401


def test_auth_accepts_key_from_whitelist(monkeypatch) -> None:
    api_keys.add("test-key")

    client = TestClient(app)
    response = client.get(
        "/web-search/v1/query_task",
        headers={"Authorization": "Bearer test-key"},
        params={"task_id": "missing-task"},
    )
    assert response.status_code == 404


def test_default_settings_point_to_script_bundle() -> None:
    settings = load_settings()

    assert settings.project_root.name == "script"
    assert settings.roma_src_root.parts[-3:] == ("vendor", "ROMA_v2", "src")
    assert settings.skill_root.parts[-2:] == ("skills", "web-search-innospark-tree")
    assert settings.union_search_root.parts[-2:] == ("skills", "union-search-skill")
    assert settings.news_aggregator_root.parts[-2:] == ("skills", "news-aggregator-skill")
