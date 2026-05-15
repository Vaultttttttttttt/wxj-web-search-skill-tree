# ROMA Web Search API 接口说明文档

> **服务模型名称**：`roma-web-search`  
> **默认本地地址**：`http://127.0.0.1:8099`  
> **规范接口前缀**：`/web-search/v1`  
> **兼容接口前缀**：`/deepsearch/v1`  
> **鉴权方式**：Bearer Token 或 `X-API-Key`

---

## 目录

1. [接口概览](#接口概览)
2. [鉴权说明](#鉴权说明)
3. [方式一：同步调用](#方式一同步调用)
   - [请求格式](#请求格式)
   - [请求字段说明](#请求字段说明)
   - [非流式响应](#非流式响应)
   - [流式响应](#流式响应)
   - [完整示例](#完整示例同步调用)
4. [方式二：异步任务调用](#方式二异步任务调用)
   - [第一步：创建任务](#第一步创建任务)
   - [第二步：查询任务状态](#第二步查询任务状态)
   - [第三步：读取最终结果](#第三步读取最终结果)
   - [任务状态流转](#任务状态流转)
   - [完整示例](#完整示例异步任务)
5. [结果结构说明](#结果结构说明)
6. [历史记录查询](#历史记录查询)
7. [错误响应](#错误响应)
8. [两种调用方式对比](#两种调用方式对比)
9. [Python 调用示例](#python-调用示例)
10. [部署说明](#部署说明)

---

## 接口概览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/web-search/v1/chat/completions` | `POST` | 同步检索接口，支持非流式与 SSE 流式返回 |
| `/web-search/v1/create_task` | `POST` | 异步创建检索任务，立即返回 `task_id` |
| `/web-search/v1/query_task` | `GET` | 通过 query param 查询任务状态与结果 |
| `/web-search/v1/query_task` | `POST` | 通过 request body 查询任务状态与结果 |
| `/web-search/v1/history` | `GET` | 查询当前 API key 的历史检索记录 |
| `/deepsearch/v1/*` | 同上 | 与上述接口等价的兼容前缀 |

该服务封装的是 ROMA 当前 Web Search 检索链路，底层会复用：

- `AdaptiveRetrieveToolkit(default_mode="web", web_backend="skill_tree")`
- Web Search 技能树
- Union Search
- News Aggregator
- ROMA 当前已经接入的多源搜索与路由逻辑

---

## 鉴权说明

服务启动时会读取本地白名单文件：

```text
script/api_keys.txt
```

默认实现中每行一个 key，例如：

```text
sk-your-api-key
sk-your-api-key
```

请求时支持两种写法，任选其一：

```http
Authorization: Bearer <API_KEY>
```

或：

```http
X-API-Key: <API_KEY>
```

未携带或校验失败时返回 `401 Unauthorized`。

同步结果、异步任务和历史记录都会按 API key 隔离。服务端不会把原始 API key 明文写入历史文件，只保存 masked key 与 `api_key_hash`。

---

## 方式一：同步调用

适用于：

- 前端或服务端希望一次请求直接拿到搜索结果
- 检索耗时在可接受范围内
- 调试接口或做小规模调用

### 请求格式

```http
POST /web-search/v1/chat/completions
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

```json
{
  "model": "roma-web-search",
  "messages": [
    {
      "role": "user",
      "content": "中国土地财政 2021 年土地出让收入 房产税改革 官方数据"
    }
  ],
  "top_n": 12,
  "stream": false
}
```

### 请求字段说明

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `model` | string | 否 | `"roma-web-search"` | 返回中的模型名 |
| `messages` | array | 否 | `[]` | OpenAI 风格消息列表 |
| `messages[].role` | string | 是 | — | 通常使用 `"user"` |
| `messages[].content` | string / array | 是 | — | 查询内容 |
| `query` | string | 否 | — | 直接指定查询词；若存在，将优先于 `messages` |
| `top_n` | integer | 否 | 环境变量配置值 | 期望返回的候选结果数，会被服务端裁剪到最大上限 |
| `stream` | boolean | 否 | `false` | 是否启用 SSE 流式响应 |
| `stream_chunk_chars` | integer | 否 | 环境变量配置值 | 流式正文每个 chunk 的字符数，仅 `stream=true` 时生效 |
| `stream_chunk_delay_ms` | integer | 否 | 环境变量配置值 | 流式正文 chunk 间隔毫秒数，仅 `stream=true` 时生效 |

查询提取逻辑：

1. 优先使用 `query`
2. 否则取最后一条 `role=user` 的消息内容
3. 若仍为空，再取最后一条非空消息内容
4. 全部为空时返回 `422`

### 非流式响应

当：

```json
"stream": false
```

时，返回 OpenAI 风格完整 JSON：

```json
{
  "id": "chatcmpl-7c73f40f7a8f4ca28fbd22af22d2bb94",
  "object": "chat.completion",
  "created": 1778560000,
  "model": "roma-web-search",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "检索报告\n决策模式: WEB\n..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "roma_result": {
    "query": "中国土地财政 2021 年土地出让收入 房产税改革 官方数据",
    "decision": "web",
    "confidence": 1.0,
    "contexts": [],
    "sources": [],
    "debug": {}
  },
  "artifact_json_path": "/path/to/outputs/search_history.json",
  "artifact_markdown_path": "/path/to/outputs/result.md",
  "artifact_record_id": "20260515T061117Z_xxx_query",
  "api_key": "sk-xxx...xxxx"
}
```

### 流式响应

当：

```json
"stream": true
```

时，返回 `text/event-stream`。

每个 SSE 块格式：

```text
data: <JSON 字符串>
```

响应 chunk 采用 OpenAI 兼容结构：

```json
{
  "id": "chatcmpl-c759c875094b463981a4c96ee4727c82",
  "object": "chat.completion.chunk",
  "created": 1778560000,
  "model": "roma-web-search",
  "choices": [
    {
      "index": 0,
      "delta": {
        "content": "检索报告\n决策模式: WEB\n..."
      },
      "finish_reason": null
    }
  ]
}
```

结束时返回：

```text
data: [DONE]
```

### 流式接口的重要说明

当前 ROMA Web Search API 的流式实现是：

- 先立即建立 SSE 连接并返回首个控制 chunk
- 服务端在流式 generator 内继续执行底层检索
- 检索完成后，将最终检索文本按 `WEB_API_STREAM_CHUNK_CHARS` 切片输出
- 每个正文 chunk 之间默认间隔 `WEB_API_STREAM_CHUNK_DELAY_MS` 毫秒，便于前端逐段渲染

它不是 DeepResearch 引擎那种“边规划、边检索、边输出思考事件”的长链路事件流。

因此：

- `stream=true` 会真实建立 HTTP SSE 流
- 但目前仍不会持续推送底层各搜索源的中间检索事件
- 中间进度展示应优先使用异步任务接口的任务状态字段

### 完整示例（同步调用）

#### 非流式

```bash
curl -X POST "http://127.0.0.1:8099/web-search/v1/chat/completions" \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "roma-web-search",
    "messages": [
      {
        "role": "user",
        "content": "检索 Singapore IT2000 的官方资料与论文证据"
      }
    ],
    "top_n": 12,
    "stream": false
  }'
```

#### 流式

```bash
curl -N -X POST "http://127.0.0.1:8099/web-search/v1/chat/completions" \
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
    "top_n": 12,
    "stream": true,
    "stream_chunk_chars": 24,
    "stream_chunk_delay_ms": 120
  }'
```

---

## 方式二：异步任务调用

适用于：

- 模型平台集成
- 队列化调用
- 希望快速返回 `task_id`
- 查询任务状态与结果分离

### 第一步：创建任务

#### 请求格式

```http
POST /web-search/v1/create_task
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

```json
{
  "model": "roma-web-search",
  "messages": [
    {
      "role": "user",
      "content": "检索美国住房券制度的官方政策、学术研究与执行效果"
    }
  ],
  "top_n": 24
}
```

#### 返回示例

```json
{
  "code": 0,
  "message": "OK",
  "data": {
    "task_id": "90d0beaf-9f15-4d13-a049-bd1114ed3d59",
    "status": "pending",
    "created_at": "2026-05-12T08:30:12.123456Z"
  },
  "requestId": "4af258f2-c098-4389-93b8-b6539b889fb2"
}
```

### 第二步：查询任务状态

#### GET 方式

```bash
curl -G "http://127.0.0.1:8099/web-search/v1/query_task" \
  -H "Authorization: Bearer sk-your-api-key" \
  --data-urlencode "task_id=90d0beaf-9f15-4d13-a049-bd1114ed3d59"
```

#### POST 方式

```bash
curl -X POST "http://127.0.0.1:8099/web-search/v1/query_task" \
  -H "Authorization: Bearer sk-your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "90d0beaf-9f15-4d13-a049-bd1114ed3d59"
  }'
```

#### 执行中返回示例

```json
{
  "code": 0,
  "message": "OK",
  "data": {
    "task_id": "90d0beaf-9f15-4d13-a049-bd1114ed3d59",
    "status": "running",
    "created_at": "2026-05-12T08:30:12.123456Z",
    "updated_at": "2026-05-12T08:30:13.123456Z",
    "progress": {
      "current": 50,
      "total": 100,
      "message": "任务执行中"
    }
  },
  "requestId": "1c069463-3d02-4673-885e-209f07766aea"
}
```

### 第三步：读取最终结果

任务完成后，查询接口返回：

```json
{
  "code": 0,
  "message": "OK",
  "data": {
    "task_id": "90d0beaf-9f15-4d13-a049-bd1114ed3d59",
    "status": "completed",
    "created_at": "2026-05-12T08:30:12.123456Z",
    "updated_at": "2026-05-12T08:30:16.123456Z",
    "progress": {
      "current": 100,
      "total": 100,
      "message": "任务已完成"
    },
    "result": {
      "content": "检索报告\n决策模式: WEB\n...",
      "roma_result": {
        "query": "检索美国住房券制度的官方政策、学术研究与执行效果",
        "decision": "web",
        "confidence": 1.0,
        "contexts": [],
        "sources": [],
        "debug": {}
      },
      "artifact_json_path": "/path/to/outputs/search_history.json",
      "artifact_markdown_path": "/path/to/outputs/result.md",
      "artifact_record_id": "90d0beaf-9f15-4d13-a049-bd1114ed3d59"
    },
    "statistics": {
      "result_count": 12,
      "duration_ms": 4238
    },
    "session": {
      "session_id": "90d0beaf-9f15-4d13-a049-bd1114ed3d59",
      "status": "finished",
      "model": "roma-web-search",
      "question": "检索美国住房券制度的官方政策、学术研究与执行效果"
    }
  },
  "requestId": "f44f0817-e3c8-4cc3-91c4-e3ab20478b0a"
}
```

### 任务状态流转

| 状态 | 说明 |
|------|------|
| `pending` | 已入队，等待开始 |
| `running` | 检索执行中 |
| `completed` | 已完成，可读取最终结果 |
| `failed` | 执行失败，结果中会出现 `error` 字段 |

任务默认采用内存存储，并按 TTL 过期清理。默认 TTL 由：

```text
WEB_API_TASK_TTL_SECONDS
```

控制。

### 完整示例（异步任务）

```bash
TASK_ID=$(
  curl -s -X POST "http://127.0.0.1:8099/web-search/v1/create_task" \
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
    }' | jq -r '.data.task_id'
)

curl -s -G "http://127.0.0.1:8099/web-search/v1/query_task" \
  -H "Authorization: Bearer sk-your-api-key" \
  --data-urlencode "task_id=${TASK_ID}" | jq .
```

---

## 结果结构说明

### `choices[0].message.content`

这是给大模型或调用方直接消费的文本化检索报告，格式与 ROMA 当前 Web Search 输出保持一致。

通常包含：

- 检索决策模式
- 置信度
- 触发原因
- 搜索结果列表
- 来源摘要

### `roma_result`

这是 ROMA 原生结构化结果，保留给平台侧二次处理。

典型字段：

| 字段 | 说明 |
|------|------|
| `query` | 实际检索词 |
| `decision` | 当前检索决策 |
| `confidence` | 决策置信度 |
| `contexts` | 文本上下文集合 |
| `sources` | 来源集合 |
| `debug` | 调试信息 |

`contexts[].source` 对外表示可读来源，例如：

- `academic_research`
- `semantic_scholar`
- `mof.gov.cn`
- `yicai.com`

如果底层是通过 Exa 等发现引擎找到的网页，结果会额外保留：

| 字段 | 说明 |
|------|------|
| `source_type` | 来源类型，Web 检索结果为 `"web"` |
| `discovery_backend` | 底层发现引擎，例如 `"exa"` |

### `artifact_json_path`、`artifact_record_id` 与 `artifact_markdown_path`

每次检索都会自动落盘：

- 一份追加到统一 JSON 历史文件中的结构化记录
- 一份独立 Markdown 文件

默认目录：

```text
script/outputs
```

默认 JSON 历史文件：

```text
script/outputs/search_history.json
```

其中：

| 字段 | 说明 |
|------|------|
| `artifact_json_path` | 统一 JSON 历史文件路径 |
| `artifact_record_id` | 本次检索在历史文件中的记录 ID；异步任务默认等于 `task_id` |
| `artifact_markdown_path` | 本次检索对应的独立 Markdown 文件路径 |
| `api_key` | masked API key，例如 `sk-xxx...abcd` |
| `api_key_hash` | API key 的 SHA-256 哈希，用于服务端隔离查询 |

服务端可以据此做：

- 历史追踪
- 调试回放
- 结果审计

---

## 历史记录查询

查询当前 API key 产生过的检索记录：

```bash
curl -G "http://127.0.0.1:8099/web-search/v1/history" \
  -H "Authorization: Bearer sk-your-api-key" \
  --data-urlencode "limit=20" \
  --data-urlencode "offset=0"
```

返回示例：

```json
{
  "code": 0,
  "message": "OK",
  "data": {
    "total": 1,
    "limit": 20,
    "offset": 0,
    "records": [
      {
        "query": "中国土地财政 2021 年土地出让收入 房产税改革 官方数据",
        "model": "roma-web-search",
        "content": "检索报告\n...",
        "roma_result": {},
        "artifact_json_path": "/path/to/outputs/search_history.json",
        "artifact_markdown_path": "/path/to/outputs/20260515T061117Z_xxx.md",
        "artifact_record_id": "20260515T061117Z_xxx_query",
        "api_key": "sk-you...-key",
        "api_key_hash": "..."
      }
    ]
  },
  "requestId": "..."
}
```

该接口只返回当前请求 API key 的历史记录。不同 key 创建的异步任务也无法互相查询。

---

## 错误响应

### 401 鉴权失败

```json
{
  "detail": "Invalid or missing API key."
}
```

### 404 任务不存在

```json
{
  "code": 404,
  "message": "Task not found.",
  "data": {
    "task_id": "missing-task-id"
  },
  "requestId": "5bdb7820-770d-48a3-94e2-7ef9a6a48333"
}
```

### 422 查询为空

```json
{
  "detail": "A non-empty `query` or user message is required."
}
```

### 500 检索执行失败

```json
{
  "detail": "Web search execution failed: <error message>"
}
```

---

## 两种调用方式对比

| 方式 | 接口 | 适合场景 | 优点 | 注意点 |
|------|------|----------|------|--------|
| 同步非流式 | `/chat/completions` + `stream=false` | 调试、轻量集成 | 一次请求拿完整结果 | 客户端需等待返回 |
| 同步流式 | `/chat/completions` + `stream=true` | 需要 SSE 协议兼容 | 可分块消费文本 | 当前不代表实时检索进度 |
| 异步任务 | `/create_task` + `/query_task` | 平台接入、任务化管理 | 立即拿到 `task_id`，可轮询 | 需要自行设计轮询策略 |

---

## Python 调用示例

### 方式一：使用 `requests`

```python
import requests

base_url = "http://127.0.0.1:8099"
headers = {
    "Authorization": "Bearer sk-your-api-key",
    "Content-Type": "application/json",
}

payload = {
    "model": "roma-web-search",
    "messages": [
        {
            "role": "user",
            "content": "检索中国土地财政 2021 年土地出让收入与房产税改革资料",
        }
    ],
    "top_n": 12,
    "stream": False,
}

resp = requests.post(
    f"{base_url}/web-search/v1/chat/completions",
    headers=headers,
    json=payload,
    timeout=300,
)
resp.raise_for_status()

data = resp.json()
print(data["choices"][0]["message"]["content"])
```

### 方式二：OpenAI SDK 兼容风格

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

---

## 部署说明

### 结论先说

现在推荐把 **`script/` 作为唯一后端部署目录**。

该目录已经集中包含：

1. FastAPI API 包装层：`roma_web_search_api/`
2. ROMA 检索源码快照：`vendor/ROMA_v2/src/`
3. Web Search 技能树：`skills/web-search-innospark-tree/`
4. Union Search：`skills/union-search-skill/`
5. News Aggregator：`skills/news-aggregator-skill/`
6. 学术检索辅助 skill：`skills/academic-research-skills/` 与 `skills/gs-skills/`

### 最小部署文件集合

服务器部署时，优先只上传：

```text
script/
```

旧入口 `web_api.main` 仍然可用，但只是兼容转发；真正的后端代码与依赖都已经集中到 `script` 内。

### 推荐启动方式

在服务器上：

```bash
cd roma-web-search
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run_server.py
```

也可以直接使用：

```bash
uvicorn roma_web_search_api.main:app --host 0.0.0.0 --port 8099
```

### 可选 Docker 部署

Docker 不是当前 Web Search API 的必需依赖。默认部署不需要 Redis、PostgreSQL、RAGFlow 或其他 sidecar 容器；Docker 只用于打包 Python 运行环境。

`script/` 下已经提供：

```text
Dockerfile
docker-compose.yml
.dockerignore
DOCKER.md
```

在服务器上：

```bash
cd roma-web-search
cp .env.example .env
cp api_keys.example.txt api_keys.txt
mkdir -p outputs
docker compose up -d --build
```

检查：

```bash
curl -s http://127.0.0.1:8099/healthz | jq .
```

若采用 Docker，容器内路径通常是：

```text
/app/outputs/search_history.json
/app/api_keys.txt
```

宿主机对应：

```text
script/outputs/search_history.json
script/api_keys.txt
```

完整 Docker 注意事项见 `script/DOCKER.md`。

### 必要环境变量

可参考 `script/.env.example`。若不设置路径变量，服务默认使用 `script` 内部的相对目录。

| 变量 | 作用 |
|------|------|
| `ROMA_SRC_ROOT` | ROMA 源码目录 |
| `WEB_SEARCH_SKILL_ROOT` | Web Search 技能树目录 |
| `WEB_SEARCH_UNION_ROOT` | Union Search 目录 |
| `WEB_SEARCH_NEWS_AGGREGATOR_ROOT` | News Aggregator 目录 |
| `WEB_API_ARTIFACT_DIR` | 检索结果落盘目录 |
| `WEB_API_KEYS_FILE` | 本地 API Key 白名单文件 |
| `TAVILY_API_KEY` | 若启用 Tavily，则需要提供 |

### 路径注意事项

`script/.env` 支持相对路径。相对路径会按 `script` 目录解析，例如 `WEB_API_KEYS_FILE=./api_keys.txt` 会解析为部署目录下的 `api_keys.txt`。

启动后可检查：

```bash
curl -s http://127.0.0.1:8099/healthz | jq .
```

确认 `package_root`、`roma_src_root`、`skill_root`、`union_search_root`、`news_aggregator_root` 都指向部署后的 `script` 目录。

### API Key 管理建议

当前实现采用：

- 本地文本文件白名单
- 服务启动时加载

如果后续要支持：

- 动态新增 key
- 每个师兄独立额度
- 请求计费与审计

则下一步应把 `api_keys.txt` 升级为数据库或配置中心。
