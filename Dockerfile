ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_API_HOST=0.0.0.0 \
    WEB_API_PORT=8110 \
    WEB_API_KEYS_FILE=./api_keys.txt \
    WEB_API_ARTIFACT_DIR=./outputs \
    WEB_API_HISTORY_FILE=./outputs/search_history.json \
    ROMA_SRC_ROOT=./vendor/ROMA_v2/src \
    WEB_SEARCH_SKILL_ROOT=./skills/web-search-innospark-tree \
    WEB_SEARCH_UNION_ROOT=./skills/union-search-skill \
    WEB_SEARCH_NEWS_AGGREGATOR_ROOT=./skills/news-aggregator-skill \
    ACADEMIC_RESEARCH_SKILLS_ROOT=./skills/academic-research-skills \
    GOOGLE_SCHOLAR_SKILLS_ROOT=./skills/gs-skills

WORKDIR /app

COPY script/requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY script/ /app/

RUN mkdir -p /app/outputs \
    && ln -s /app /app/script

EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=3).read()" || exit 1

CMD ["python", "run_server.py"]
