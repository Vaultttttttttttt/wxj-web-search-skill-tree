ARG PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_API_HOST=0.0.0.0 \
    WEB_API_PORT=8110

WORKDIR /app

COPY script/requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY script/ /app/

RUN mkdir -p /app/outputs

EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8110/healthz', timeout=3).read()" || exit 1

CMD ["python", "run_server.py"]
