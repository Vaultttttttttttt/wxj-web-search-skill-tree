# ROMA Web Search API

这个服务把 ROMA 现有的 Web Search 能力封装成 UniFuncs 风格接口，保留两种接入方式。

现在后端已经集中为一个可部署目录：

```text
script/
```

服务器部署时优先只上传这个目录。旧的 `web_api.main` 入口仍可用，但只是兼容转发，真正的 API 代码、ROMA 源码快照、Web Search 技能树、Union Search、News Aggregator 和学术检索辅助 skill 都在 `script` 下面。

1. OpenAI 兼容同步接口：`POST /web-search/v1/chat/completions`
2. 异步任务接口：
   - `POST /web-search/v1/create_task`
   - `GET|POST /web-search/v1/query_task`
3. 当前 API key 历史记录接口：`GET /web-search/v1/history`

同时提供 `/deepsearch/v1/*` 兼容别名，便于按 UniFuncs 深搜风格接入。

更完整的接口说明见：

```text
web_api/API_Reference.md
```

作为 Codex skill 使用时，仓库还包含：

```text
scripts/roma_web_search_client.py
references/api-reference.md
```

因此可以直接把本仓库 clone 到 `~/.codex/skills/roma-web-search-api`，重启 Codex 后使用。

本地调试 UI：

```text
http://127.0.0.1:8099/test-ui
```

## 返回语义

- `choices[0].message.content`：
  - 保持 ROMA 现有 `AdaptiveRetrieveToolkit` 的检索报告文本格式。
- `roma_result`：
  - 保留 ROMA 的结构化检索对象：
    - `query`
    - `decision`
    - `confidence`
    - `contexts`
    - `sources`
    - `debug`

异步任务完成后，结果位于：

```json
data.result.content
data.result.roma_result
```

每次检索还会自动落盘：

```json
artifact_json_path
artifact_record_id
artifact_markdown_path
```

其中 `artifact_json_path` 指向统一历史文件，默认是 `script/outputs/search_history.json`；`artifact_record_id` 是本次检索在该 JSON 文件中的记录 ID；`artifact_markdown_path` 仍然是一次请求一个 Markdown 文件。统一历史文件会保存 masked API key 和 `api_key_hash`，用于后续按 key 查询和隔离。

默认输出目录：

```text
script/outputs
```

## 安装与启动

### 推荐：独立部署包启动

```bash
cd script
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run_server.py
```

服务会默认读取：

```text
script/api_keys.txt
```

也可以直接：

```bash
cd script
uvicorn roma_web_search_api.main:app --host 0.0.0.0 --port 8099
```

### 可选：Docker 启动

当前 Web Search API **不强制依赖 Docker**，也不需要 Redis、PostgreSQL、RAGFlow 等额外容器。Docker 只是为了服务器部署时环境更稳定。

```bash
cd script
cp .env.example .env
cp api_keys.example.txt api_keys.txt
mkdir -p outputs
docker compose up -d --build
```

容器内默认写入 `/app/outputs`，宿主机通过 volume 挂载到：

```text
script/outputs
```

详细说明见：

```text
script/DOCKER.md
```

### 兼容：旧入口启动

旧命令仍然可用：

```bash
cd ..
uvicorn web_api.main:app --host 0.0.0.0 --port 8099
```

每行一个 key。请求时必须带：

```text
Authorization: Bearer <your-key>
```

也支持：

```text
X-API-Key: <your-key>
```

## 同步调用

```bash
curl -X POST "http://127.0.0.1:8099/web-search/v1/chat/completions" \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "roma-web-search",
    "messages": [
      {
        "role": "user",
        "content": "检索中国土地财政 2021 年土地出让收入与房产税改革资料"
      }
    ],
    "top_n": 12,
    "stream": false
  }'
```

## OpenAI SDK 兼容调用

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8099/web-search/v1",
    api_key="sk-your-api-key",
)

resp = client.chat.completions.create(
    model="roma-web-search",
    messages=[
        {
            "role": "user",
            "content": "检索 Singapore IT2000 的官方资料与论文证据",
        }
    ],
    extra_body={"top_n": 12},
    stream=False,
)

print(resp.choices[0].message.content)
```

## 异步任务调用

创建任务：

```bash
curl -X POST "http://127.0.0.1:8099/web-search/v1/create_task" \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "roma-web-search",
    "messages": [
      {
        "role": "user",
        "content": "检索日本数字政府改革 2020-2025 的官方政策材料"
      }
    ],
    "top_n": 24
  }'
```

轮询任务：

```bash
curl -G "http://127.0.0.1:8099/web-search/v1/query_task" \
  -H "Authorization: Bearer sk-your-api-key" \
  --data-urlencode "task_id=<task-id>"
```

也支持：

```bash
curl -X POST "http://127.0.0.1:8099/web-search/v1/query_task" \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"task_id": "<task-id>"}'
```

查询当前 key 的历史记录：

```bash
curl -s -G "http://127.0.0.1:8099/web-search/v1/history" \
  -H "Authorization: Bearer sk-your-api-key" \
  --data-urlencode "limit=20" \
  --data-urlencode "offset=0" | jq .
```

## 环境变量

见 `script/.env.example`。最关键的是：

- `ROMA_SRC_ROOT`
- `WEB_SEARCH_SKILL_ROOT`
- `WEB_SEARCH_UNION_ROOT`
- `WEB_SEARCH_NEWS_AGGREGATOR_ROOT`
- `WEB_API_ARTIFACT_DIR`
- `WEB_API_HISTORY_FILE`
- `WEB_API_KEYS_FILE`
- `TAVILY_API_KEY`

流式输出可调：

- `WEB_API_STREAM_CHUNK_CHARS`：每个正文 chunk 的字符数
- `WEB_API_STREAM_CHUNK_DELAY_MS`：正文 chunk 之间的间隔毫秒数

也可以在单次请求中覆盖：

```json
{
  "stream": true,
  "stream_chunk_chars": 24,
  "stream_chunk_delay_ms": 120
}
```

这套服务默认走 `AdaptiveRetrieveToolkit(default_mode="web", web_backend="skill_tree")`，因此输出会复用 ROMA 当前的 Web Search 技能树、union search、RSS fallback、news aggregator 以及学术/政策路由逻辑。

启动后可用 `/healthz` 检查当前加载的部署路径。正常情况下，`package_root`、`roma_src_root`、`skill_root`、`union_search_root`、`news_aggregator_root` 都应指向 `script` 内部。
