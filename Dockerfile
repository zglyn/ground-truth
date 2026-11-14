FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
ENV PYTHONPATH=/app/src
ENV SWARM_DB=/data/swarm.sqlite
EXPOSE 8080
CMD ["python", "-m", "ground_truth"]
