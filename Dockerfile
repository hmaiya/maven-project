# Batch job image: runs the pipeline against data mounted at /work/data/raw and
# writes /work/out/warehouse.db. Same shape works on ECS/Cloud Run jobs/K8s CronJob.
#   docker build -t unify .
#   docker run --rm -v "$PWD/data:/work/data" -v "$PWD/out:/work/out" unify run
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /work
ENTRYPOINT ["unify"]
CMD ["--help"]
# TODO: SQLite in a volume is fine for a demo. For a real deployment, point
# load.py at Postgres/warehouse, pass UNIFY_API_TOKEN from a secret manager,
# and schedule `unify fetch ... && unify run` instead of running by hand.
