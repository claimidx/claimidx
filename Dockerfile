# Glama / container MCP entrypoint: stdio claimidx-mcp for tools/list and hosted try-outs.
# Build: docker build -t claimidx-mcp .
# Run:   docker run -i --rm -e CLAIMIDX_OWNER=did:claimidx:your-agent claimidx-mcp
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 claimidx

ENV PYTHONUNBUFFERED=1 \
    CLAIMIDX_OWNER=did:claimidx:glama \
    CLAIMIDX_AGENT=glama \
    HOME=/home/claimidx

USER claimidx
WORKDIR /home/claimidx

# mcp-proxy (Glama) wraps this; local use needs docker run -i
CMD ["claimidx-mcp"]
