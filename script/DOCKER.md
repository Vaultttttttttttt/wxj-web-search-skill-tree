# Docker Deployment

Docker is optional for this Web Search API. The current API does not require
Redis, PostgreSQL, RAGFlow, or any extra sidecar service. It can run directly
with `python run_server.py`.

Use Docker when the server team wants a reproducible runtime and one-command
startup.

## Files

```text
script/
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env.example
├── api_keys.example.txt
└── outputs/
```

## One-Time Setup

From the `script/` directory:

```bash
cp .env.example .env
cp api_keys.example.txt api_keys.txt
mkdir -p outputs
```

Edit `.env` and fill provider keys, for example:

```text
TAVILY_API_KEY=tvly-...
SERPER_API_KEY=...
S2_API_KEY=...
```

Edit `api_keys.txt` and put caller keys, one per line:

```text
sk-team-user-001
sk-team-user-002
```

## Build And Run

```bash
docker compose up -d --build
```

Check health:

```bash
curl -s http://127.0.0.1:8099/healthz | jq .
```

The response should show:

```json
{
  "artifact_dir": "/app/outputs",
  "history_file": "/app/outputs/search_history.json",
  "api_keys_file": "/app/api_keys.txt"
}
```

On the host machine, those files are persisted in:

```text
script/outputs/
script/api_keys.txt
```

because `docker-compose.yml` mounts them into the container.

## Call The API

```bash
curl -s -X POST "http://127.0.0.1:8099/web-search/v1/chat/completions" \
  -H "Authorization: Bearer sk-team-user-001" \
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

Query current key history:

```bash
curl -s -G "http://127.0.0.1:8099/web-search/v1/history" \
  -H "Authorization: Bearer sk-team-user-001" \
  --data-urlencode "limit=20" \
  --data-urlencode "offset=0" | jq .
```

## Stop / Restart

```bash
docker compose down
docker compose up -d
docker compose logs -f roma-web-search
```

## Notes

- Do not commit `.env`, `api_keys.txt`, or `outputs/`.
- If Docker cannot resolve external websites, check the server firewall/proxy
  first; the API depends on normal outbound HTTPS access.
- The bundled news aggregator includes optional Playwright helper scripts, but
  the default Web Search API deployment does not require a browser container.
  If a future route explicitly needs those browser-only sources, add
  `playwright` to `requirements.txt` and run `playwright install chromium`
  inside the image.
