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

Fresh server path:

```bash
git clone https://github.com/Vaultttttttttttt/wxj-web-search-skill-tree.git
cd wxj-web-search-skill-tree/script
```

Create the Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create runtime config:

```bash
cp .env.example .env
cp api_keys.example.txt api_keys.txt
mkdir -p outputs
```

Fill `.env` with provider keys such as `TAVILY_API_KEY`, then put allowed
caller keys in `api_keys.txt`, one key per line.

Start:

```bash
python run_server.py
```

If you prefer copying only the backend bundle instead of cloning the full repo,
copy this `script/` folder to the server and run the same commands from that
folder.

Equivalent uvicorn command:

```bash
uvicorn roma_web_search_api.main:app --host 0.0.0.0 --port 8110
```

## Docker

When building from the repository root, use the root `Dockerfile`. It copies
this standalone `script/` bundle into `/app` and expects runtime secrets to be
provided with `--env-file` and mounted files. See `../DOCKER.md`.

## Check

```bash
curl -s http://127.0.0.1:8110/healthz | jq .
```

The health response should show paths under the deployed `script` directory, especially `package_root`, `roma_src_root`, `skill_root`, `union_search_root`, and `news_aggregator_root`.

Runtime config is loaded from `script/.env` first. If that file does not exist
and the backend is running inside a full repository checkout, the service falls
back to the repository root `.env`. Check `config_env_file` in `/healthz` when
debugging port or output-directory confusion.

Check path/key/network readiness before judging source quality:

```bash
python scripts/check_search_environment.py --live
```

If key-based providers still look weak, run small API probes. This may consume a
minimal amount of provider quota:

```bash
python scripts/check_search_environment.py --live --api-probe
```

If TCP/TLS checks fail for `DuckDuckGo`, `Google Scholar`, `GitHub API`, or
provider API hosts, the server network is filtering or blocking those sources.
In that case, configure `HTTP_PROXY`/`HTTPS_PROXY` in `.env` or run the service
behind a machine/network that can reach those hosts.

Open the test UI:

```text
http://127.0.0.1:8110/test-ui
```

Searches write one Markdown file per request under `outputs/` and append the structured record to `outputs/search_history.json` by default. The JSON history stores a masked API key plus `api_key_hash`, so `/web-search/v1/history` can return only the current caller's records.

## Compatibility

Existing local commands still work from the parent workspace because `web_api.main` is now a thin compatibility wrapper:

```bash
uvicorn web_api.main:app --host 127.0.0.1 --port 8110
```

For server deployment, prefer `cd script && python run_server.py` so the backend does not depend on sibling folders outside `script`.
