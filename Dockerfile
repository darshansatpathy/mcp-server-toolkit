# syntax=docker/dockerfile:1
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /wheels \
 && pip wheel --no-cache-dir --wheel-dir /wheels "/wheels/$(ls /wheels)[redis]"

FROM python:3.13-slim
RUN useradd --system --uid 10001 --home /app mcp && mkdir -p /app /data && chown mcp /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels "mcp-server-toolkit[redis]" && rm -rf /wheels
USER 10001
WORKDIR /app
ENV MCPKIT_DATABASE=/data/tickets.db \
    MCPKIT_JWKS_FILE= \
    PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz').status != 200)"
ENTRYPOINT ["mcpkit", "serve", "--host", "0.0.0.0", "--port", "8000"]
