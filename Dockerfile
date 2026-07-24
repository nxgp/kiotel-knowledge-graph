# Kiotel knowledge-graph MCP server
# Serves graphify-out/graph.json over Streamable HTTP MCP on :8080.
#
# Build:  docker build -t kiotel-graph-mcp .
# Run:    docker run -d -p 8080:8080 --name kiotel-graph kiotel-graph-mcp
# Auth:   docker run -d -p 8080:8080 -e GRAPHIFY_API_KEY=<key> kiotel-graph-mcp
FROM python:3.12-slim

RUN pip install --no-cache-dir "graphifyy[mcp]==0.9.25"

WORKDIR /app
COPY graphify-out/graph.json /app/graph.json

EXPOSE 8080

# --stateless so the container can sit behind a load balancer / restart freely.
ENTRYPOINT ["python", "-m", "graphify.serve", "/app/graph.json", \
            "--transport", "http", "--host", "0.0.0.0", "--port", "8080", \
            "--stateless"]
