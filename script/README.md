# ROMA Web Search API Standalone Bundle

`script` is the deployable backend bundle. It contains the API server, the ROMA retrieval source snapshot, and the local web-search skills used by the API.

## Layout

```text
script/
├── roma_web_search_api/          # FastAPI app and service code
├── vendor/ROMA_v2/src/           # ROMA runtime source used by the app
├── skills/
│   ├── web-search-innospark-tree/
│   ├── union-search-skill/
│   ├── news-aggregator-skill/
│   ├── academic-research-skills/
│   └── gs-skills/
├── api_keys.txt                  # Local API-key whitelist
├── outputs/                      # Search artifacts and search_history.json
├── test_ui.html                  # Browser test UI
├── .env.example
├── requirements.txt
└── run_server.py
```

## Deploy

Copy only this folder to the server if the Python dependencies are available:

```bash
scp -r . user@server:roma-web-search
```

On the server:

```bash
cd roma-web-search
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with provider keys such as `TAVILY_API_KEY`, then put allowed caller keys in `api_keys.txt`.

Start:

```bash
python run_server.py
```

Equivalent uvicorn command:

```bash
uvicorn roma_web_search_api.main:app --host 0.0.0.0 --port 8099
```

## Check

```bash
curl -s http://127.0.0.1:8099/healthz | jq .
```

The health response should show paths under the deployed `script` directory, especially `package_root`, `roma_src_root`, `skill_root`, `union_search_root`, and `news_aggregator_root`.

Open the test UI:

```text
http://127.0.0.1:8099/test-ui
```

Searches write one Markdown file per request under `outputs/` and append the structured record to `outputs/search_history.json` by default. The JSON history stores a masked API key plus `api_key_hash`, so `/web-search/v1/history` can return only the current caller's records.

## Compatibility

Existing local commands still work from the parent workspace because `web_api.main` is now a thin compatibility wrapper:

```bash
uvicorn web_api.main:app --host 127.0.0.1 --port 8099
```

For server deployment, prefer `cd script && python run_server.py` so the backend does not depend on sibling folders outside `script`.
