# Docker Deployment

This image packages the standalone `script/` backend bundle. Runtime secrets are
not baked into the image: provide `.env` and `api_keys.txt` when starting the
container.

The image runs the backend from `/app`. It also creates `/app/script` as a
compatibility link to `/app`, so both standalone paths such as `./outputs` and
repository-root paths such as `./script/outputs` work inside Docker.

## Build

Run from the `web_api` repository root:

```bash
docker build -t roma-web-search-api .
```

If the server cannot reach Docker Hub, use a reachable Python 3.12 slim mirror:

```bash
docker build \
  --build-arg PYTHON_IMAGE=<your-python-3.12-slim-image> \
  -t roma-web-search-api .
```

## Prepare Runtime Files

```bash
cp script/.env.example script/.env
cp script/api_keys.example.txt script/api_keys.txt
mkdir -p script/outputs
```

Edit `script/.env` with provider keys such as `TAVILY_API_KEY`, and put allowed
caller keys in `script/api_keys.txt`, one key per line.

## Run

```bash
docker run --rm \
  --name roma-web-search-api \
  -p 8110:8110 \
  --env-file script/.env \
  -v "$PWD/script/api_keys.txt:/app/api_keys.txt:ro" \
  -v "$PWD/script/outputs:/app/outputs" \
  roma-web-search-api
```

## Check

```bash
curl -s http://127.0.0.1:8110/healthz | jq .
```

Open the test UI:

```text
http://127.0.0.1:8110/test-ui
```

Search artifacts are written to the mounted host directory:

```text
script/outputs/
```

## Notes

- Do not copy real `.env` or `api_keys.txt` into the image.
- The `.dockerignore` file excludes secrets and historical outputs from the
  build context.
- Fresh deployments should copy `script/.env.example` to `script/.env`. If an
  older `.env` still contains `./script/...` paths, the image keeps those paths
  compatible through `/app/script`.
