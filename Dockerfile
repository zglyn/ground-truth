FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://raw.githubusercontent.com/getopencrawl/opencrawl-core/main/cli/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
ENV PYTHONPATH=/app/src
ENV SWARM_DB=/data/swarm.sqlite
EXPOSE 8080
CMD ["python", "-m", "ground_truth"]
