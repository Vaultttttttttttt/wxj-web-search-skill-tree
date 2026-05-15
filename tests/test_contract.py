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
        artifact_record_id="fake-record",
        api_key="tes...-key",
    )


def test_chat_completion_contract(monkeypatch) -> None:
    async def fake_execute(payload, artifact_id=None, api_key=None):
        assert api_key == "test-key"
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
    assert body["artifact_record_id"] == "fake-record"


def test_compat_prefix_contract(monkeypatch) -> None:
    async def fake_execute(payload, artifact_id=None, api_key=None):
        assert api_key == "test-key"
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


def test_service_persists_history_json_and_markdown(tmp_path, monkeypatch) -> None:
    settings = replace(
        load_settings(),
        artifact_dir=tmp_path,
        history_file=tmp_path / "search_history.json",
    )
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
        return await service.execute(payload, artifact_id="artifact-test", api_key="test-key")

    execution = asyncio.run(run())
    assert execution.artifact_json_path is not None
    assert execution.artifact_markdown_path is not None
    history_path = Path(execution.artifact_json_path)
    assert history_path.exists()
    assert history_path.name == "search_history.json"
    assert Path(execution.artifact_markdown_path).exists()
    payload = history_path.read_text(encoding="utf-8")
    assert '"artifact_record_id": "artifact-test"' in payload
    assert '"api_key": "tes***"' in payload
    assert '"api_key_hash":' in payload


def test_service_exposes_public_source_label(tmp_path, monkeypatch) -> None:
    settings = replace(
        load_settings(),
        artifact_dir=tmp_path,
        history_file=tmp_path / "search_history.json",
    )
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
    async def fake_execute(payload, artifact_id=None, api_key=None):
        assert api_key == "test-key"
        execution = _fake_execution()
        execution.artifact_json_path = "/tmp/search_history.json"
        execution.artifact_markdown_path = f"/tmp/{artifact_id}.md"
        execution.artifact_record_id = artifact_id
        return execution

    monkeypatch.setattr(task_store.service, "execute", fake_execute)

    async def run():
        task = await task_store.create(
            WebSearchRequest(
                model="roma-web-search",
                messages=[{"role": "user", "content": "async query"}],
            ),
            api_key="test-key",
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
    assert body["data"]["result"]["artifact_json_path"].endswith("search_history.json")
    assert body["data"]["result"]["artifact_markdown_path"].endswith(f"{task_id}.md")
    assert body["data"]["result"]["artifact_record_id"] == task_id


def test_task_query_isolated_by_api_key(monkeypatch) -> None:
    api_keys.add("other-key")

    async def fake_execute(payload, artifact_id=None, api_key=None):
        execution = _fake_execution()
        execution.artifact_json_path = "/tmp/search_history.json"
        execution.artifact_markdown_path = f"/tmp/{artifact_id}.md"
        execution.artifact_record_id = artifact_id
        return execution

    monkeypatch.setattr(task_store.service, "execute", fake_execute)

    async def run():
        task = await task_store.create(
            WebSearchRequest(
                model="roma-web-search",
                messages=[{"role": "user", "content": "private async query"}],
            ),
            api_key="test-key",
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
        headers={"Authorization": "Bearer other-key"},
        params={"task_id": task_id},
    )
    assert response.status_code == 404


def test_history_endpoint_filters_current_api_key(monkeypatch) -> None:
    async def fake_history_for_api_key(api_key, *, limit=50, offset=0):
        assert api_key == "test-key"
        assert limit == 10
        assert offset == 0
        return {
            "total": 1,
            "limit": limit,
            "offset": offset,
            "records": [{"query": "mine", "api_key": "tes***"}],
        }

    monkeypatch.setattr(search_service, "history_for_api_key", fake_history_for_api_key)

    client = TestClient(app)
    response = client.get(
        "/web-search/v1/history",
        headers={"Authorization": "Bearer test-key"},
        params={"limit": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["records"][0]["query"] == "mine"


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
