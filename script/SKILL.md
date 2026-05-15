---
name: roma-web-search-api
description: Use when Codex needs to call, test, debug, or integrate the ROMA Web Search API for web, academic, policy, news, and multi-source retrieval. This skill covers synchronous search, streaming search, asynchronous task creation/polling, response interpretation, and local deployment checks.
---

# ROMA Web Search API

Use this skill when a task needs web-search results from the ROMA search stack through its HTTP API, or when helping another project integrate with that API.

## Defaults

- Default local base URL: `http://127.0.0.1:8099`
- Default model: `roma-web-search`
- Prefer reading credentials from `ROMA_WEB_SEARCH_API_KEY`.
- Prefer reading the API base URL from `ROMA_WEB_SEARCH_BASE_URL`.
- Authenticate with `Authorization: Bearer <api_key>` or `X-API-Key: <api_key>`.
- Local development keys may be stored in `api_keys.txt`; do not expose real keys in final user-facing answers.
- The full API reference is at `API_Reference.md`.
- The deployable backend bundle is this directory.

## Health Check

Before debugging requests, verify that the API server is reachable:

```bash
BASE_URL="${ROMA_WEB_SEARCH_BASE_URL:-http://127.0.0.1:8099}"
curl -s "$BASE_URL/healthz"
```

If the server is not running locally, start it from this standalone bundle:

```bash
python run_server.py
```

If using the full repository rather than this standalone bundle, the old compatibility command still works from the repository parent:

```bash
python -m uvicorn web_api.main:app --host 127.0.0.1 --port 8099
```

## Synchronous Search

Use synchronous search when the user wants one direct result and the query is expected to finish in a single request.

Endpoint:

```text
POST /web-search/v1/chat/completions
```

Example:

```bash
BASE_URL="${ROMA_WEB_SEARCH_BASE_URL:-http://127.0.0.1:8099}"
API_KEY="${ROMA_WEB_SEARCH_API_KEY:-sk-your-api-key}"

curl -s -X POST "$BASE_URL/web-search/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "roma-web-search",
    "messages": [
      {
        "role": "user",
        "content": "中国土地财政 2021 年土地出让收入 房产税改革 官方数据"
      }
    ],
    "top_n": 12,
    "stream": false
  }' | jq .
```

Read the answer from:

- `choices[0].message.content`: Markdown retrieval report.
- `roma_result.contexts`: structured retrieved evidence.
- `artifact_json_path`: shared JSON history file path on the API server.
- `artifact_record_id`: current request's record ID inside the shared JSON history file.
- `artifact_markdown_path`: persisted Markdown result path on the API server.

## Streaming Search

Use streaming when the caller wants an SSE response. The API sends an initial control chunk quickly, then emits final text chunks after retrieval completes. It is a real HTTP stream, but it is not a per-source progress stream.

Endpoint:

```text
POST /web-search/v1/chat/completions
```

Example:

```bash
BASE_URL="${ROMA_WEB_SEARCH_BASE_URL:-http://127.0.0.1:8099}"
API_KEY="${ROMA_WEB_SEARCH_API_KEY:-sk-your-api-key}"

curl -N -X POST "$BASE_URL/web-search/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "roma-web-search",
    "messages": [
      {
        "role": "user",
        "content": "差别税率 房地产税 理论依据 优缺点"
      }
    ],
    "top_n": 8,
    "stream": true,
    "stream_chunk_chars": 180,
    "stream_chunk_delay_ms": 80
  }'
```

If output appears to arrive all at once, check that the client is not buffering. For curl, use `-N`. For browsers, consume the response body incrementally with `ReadableStream`.

## Asynchronous Search

Use asynchronous tasks for long searches, multiple concurrent requests, UI testing, or platform integration where the caller should poll for completion.

Create task:

```bash
BASE_URL="${ROMA_WEB_SEARCH_BASE_URL:-http://127.0.0.1:8099}"
API_KEY="${ROMA_WEB_SEARCH_API_KEY:-sk-your-api-key}"

curl -s -X POST "$BASE_URL/web-search/v1/create_task" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "roma-web-search",
    "messages": [
      {
        "role": "user",
        "content": "Singapore IT2000 intelligent island National Information Infrastructure"
      }
    ],
    "top_n": 12
  }' | jq .
```

Poll task:

```bash
TASK_ID="<task_id_from_create_task>"

curl -s "$BASE_URL/web-search/v1/query_task?task_id=$TASK_ID" \
  -H "Authorization: Bearer $API_KEY" | jq .
```

Completed task results are under:

- `data.status`: `pending`, `running`, `completed`, or `failed`.
- `data.progress`: progress estimate.
- `data.result.content`: Markdown retrieval report.
- `data.result.roma_result.contexts`: structured evidence.
- `data.result.artifact_json_path`: shared JSON history file path.
- `data.result.artifact_record_id`: current request's record ID inside that history file.
- `data.result.artifact_markdown_path`: persisted Markdown result path.

The task store is in memory by default. If the API server restarts, old task IDs may disappear. Completed search records still remain in the JSON history file unless `outputs/` is cleared.

## History Query

Use `/web-search/v1/history` to list records for the current API key:

```bash
BASE_URL="${ROMA_WEB_SEARCH_BASE_URL:-http://127.0.0.1:8099}"
API_KEY="${ROMA_WEB_SEARCH_API_KEY:-sk-your-api-key}"

curl -s "$BASE_URL/web-search/v1/history?limit=20&offset=0" \
  -H "Authorization: Bearer $API_KEY" | jq .
```

The history endpoint is API-key isolated. The shared JSON file stores masked `api_key` and `api_key_hash`, not the raw key.

## Deployment Bundle

For server deployment, copy this directory as the backend package. It contains:

- `roma_web_search_api/`: FastAPI app and API service code.
- `vendor/ROMA_v2/src/`: ROMA runtime source snapshot.
- `skills/web-search-innospark-tree/`: skill-tree search scripts.
- `skills/union-search-skill/`: multi-platform search layer.
- `skills/news-aggregator-skill/`: news fallback layer.
- `skills/academic-research-skills/` and `skills/gs-skills/`: academic-search helper skills.
- `api_keys.txt`, `.env.example`, `requirements.txt`, `run_server.py`, and `test_ui.html`.
- `outputs/search_history.json` is the default shared JSON history database; Markdown artifacts remain one file per request under `outputs/`.

On a server, prefer:

```bash
cd /path/to/roma-web-search
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run_server.py
```

Docker is optional. The API has no required Redis/PostgreSQL/RAGFlow sidecar in
the current deployment. If a server team wants containerized startup:

```bash
cp .env.example .env
cp api_keys.example.txt api_keys.txt
mkdir -p outputs
docker compose up -d --build
```

Read `DOCKER.md` before changing the container setup. It documents the volume
mapping for `outputs/` and `api_keys.txt`.

After startup, use `/healthz` and verify that `package_root`, `roma_src_root`, `skill_root`, `union_search_root`, and `news_aggregator_root` all point inside the deployed `script` directory.

## Response Interpretation

For each item in `roma_result.contexts`:

- `text`: snippet or extracted evidence text.
- `title`: result title.
- `url`: source URL.
- `score`: relevance score when available.
- `source`: public source label, such as `tavily`, `academic_research`, `google_scholar`, or another skill/source label.
- `source_type`: coarse category such as `web`.
- `discovery_backend`: lower-level provider used internally, such as `exa`, when relevant.

If `source` looks like a backend name while the title has a bracketed skill label, prefer the bracketed skill label as the user-facing source label and treat the backend as implementation detail.

## Compatibility Paths

The canonical routes use `/web-search/v1/*`.

Compatibility aliases may also exist under `/deepsearch/v1/*`:

- `POST /deepsearch/v1/chat/completions`
- `POST /deepsearch/v1/create_task`
- `GET /deepsearch/v1/query_task`
- `POST /deepsearch/v1/query_task`
- `GET /deepsearch/v1/history`

Prefer `/web-search/v1/*` for new integrations.

## Local Test UI

If available, open:

```text
http://127.0.0.1:8099/test-ui
```

Use the UI to test health, synchronous calls, streaming calls, async task creation, task polling, and multiple async tasks.

## Troubleshooting

- `401 Unauthorized`: API key is missing or not listed in the server key store.
- `404 task_not_found`: wrong task ID, server restarted, or task store was cleared.
- Empty retrieval result: simplify or split the query, increase `top_n`, or force a specific route/mode if the platform exposes one.
- Mostly one source appears: inspect `roma_result.contexts[*].source` and `discovery_backend`; source labels may represent skills while discovery backends represent providers.
- Stream feels non-streaming: use a non-buffering client and set `stream_chunk_chars` plus `stream_chunk_delay_ms` for visible chunking during UI tests.
