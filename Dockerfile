# syntax=docker/dockerfile:1
# AgentDispatch: the broker (web UI, JSON API, MCP over HTTP) and the optional Engine Room, in one small image.
# Build:  docker build -t agentdispatch:local .
# Run:    docker run --rm -p 127.0.0.1:8870:8870 -v agentdispatch-data:/data agentdispatch:local
# The image holds no keys, tokens or data: configuration comes from environment variables at run time
# and all state lives in the /data volume.

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- builder: install the package into a venv
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /src
COPY pyproject.toml README.md LICENSE VERSION ./
COPY agentdispatch ./agentdispatch
COPY engineroom ./engineroom
# psutil (the "engineroom" extra) gives exact CPU/RAM figures; the broker itself needs only the standard library
RUN pip install ".[engineroom]" \
 && pip uninstall -y pip \
 && rm -rf /src

# ---------------------------------------------------------------- runtime: the venv and a non-root user, nothing else
FROM python:${PYTHON_VERSION}-slim AS runtime
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid ${APP_GID} agentdispatch \
 && useradd --system --uid ${APP_UID} --gid ${APP_GID} --home-dir /home/agentdispatch --create-home --shell /usr/sbin/nologin agentdispatch \
 && mkdir -p /data /projects \
 && chown ${APP_UID}:${APP_GID} /data /projects
COPY --from=builder /opt/venv /opt/venv

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENTDISPATCH_DATA=/data \
    AGENTDISPATCH_HOST=0.0.0.0 \
    AGENTDISPATCH_PROJECTS_ROOT=/projects \
    AGENTDISPATCH_ALLOWED_ROOTS=/projects \
    AGENTDISPATCH_WORK_DIR=/tmp/agentdispatch-work \
    ENGINEROOM_HOST=0.0.0.0 \
    ENGINEROOM_DATA=/data/engineroom

USER ${APP_UID}:${APP_GID}
WORKDIR /home/agentdispatch
VOLUME ["/data"]
# 8870: broker web UI, JSON API, MCP over HTTP and the OpenAI-compatible router; 8898: the optional Engine Room
EXPOSE 8870 8898

# /api/stats is a cheap, unauthenticated read of the board counters (Host 127.0.0.1 passes the DNS-rebinding guard)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8870/api/stats', timeout=4).status == 200 else 1)"

ENTRYPOINT ["python", "-m", "agentdispatch"]
CMD ["serve", "--port", "8870"]
