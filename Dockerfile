# Kiotel knowledge-graph MCP server
# Serves graphify-out/graph.json over Streamable HTTP MCP on :8080.
#
# Build:  docker build -t kiotel-graph-mcp .
# Run:    docker run -d -p 8080:8080 --name kiotel-graph kiotel-graph-mcp
# Auth:   docker run -d -p 8080:8080 -e GRAPHIFY_API_KEY=<key> kiotel-graph-mcp
FROM python:3.12-slim

# Pin mcp<2: graphify 0.9.25's serve module imports `mcp.types.AnyUrl`, which
# mcp 2.0.0 removed. The [mcp] extra doesn't cap the version, so an unpinned
# install silently pulls 2.x and the server crashes at startup with a
# misleading "mcp not installed". Pinning keeps the image reproducible.
RUN pip install --no-cache-dir "graphifyy[mcp]==0.9.25" "mcp<2"

WORKDIR /app
COPY graphify-out/graph.json /app/graph.json
COPY graphify-out/graph.html /app/graph-viz.html
COPY graphify-out/file-sizes.json /app/file-sizes.json
COPY graphify-out/cost.json /app/cost.json
COPY graphify-out/manifest.json /app/manifest.json
COPY mcp/metrics_server.py /app/metrics_server.py
COPY mcp/dashboard.html /app/dashboard.html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8080), 2).close()"

# metrics_server wraps graphify's stateless Streamable-HTTP app and adds
# /dashboard + /stats (usage metrics in SQLite at $METRICS_DB, default /data).
ENTRYPOINT ["python", "/app/metrics_server.py"]
