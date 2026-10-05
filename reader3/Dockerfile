FROM python:3.12-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install uv

# Dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .

ENV READER3_LIBRARY=/data \
    HOST=0.0.0.0 \
    PORT=8123 \
    PATH="/app/.venv/bin:$PATH"
VOLUME /data
EXPOSE 8123

CMD ["python", "server.py"]
