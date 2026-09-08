# Self-hosted Langfuse observability (downstream-only)

`observability/` adds a **local, downstream-only tracing projection**: a Docker
Compose stack pinning Langfuse v3.114.0 plus a stdlib-only one-shot exporter
that submits sealed run evidence to it. The sealed artifacts under
`artifacts/` remain the only authority — deleting the Langfuse stack
**cannot alter** source evidence bytes (proven by
`tests/observability/test_acceptance.py`). This document is the operating
contract; the README only points here.

## Quick path

1. Satisfy the [prerequisites](#prerequisites-and-resources) and export the
   [required environment variables](#required-environment-variables-names-only).
2. Validate without starting anything:
   `docker compose -f observability/docker-compose.yml config --quiet`
   (values only in your shell or an uncommitted `.env`).
3. Start: `docker compose -f observability/docker-compose.yml up -d`
4. Wait for readiness, then open the UI at <http://localhost:3000>.
5. Optionally export sealed evidence with
   `python3 -m observability export --artifacts-root ROOT --langfuse-url http://localhost:3000`.
6. Stop with `down`, or `down -v` to also delete downstream volumes.

## Prerequisites and resources

| Requirement | Expectation |
|-------------|-------------|
| Docker + Compose v2 | The stack is six containers; no other tooling is needed. |
| Memory / disk | Plan roughly 4 GB RAM and a few GB disk (Postgres, ClickHouse, MinIO, Redis, two Next.js services); the first start pulls images and runs migrations. |
| Network | Only the Langfuse UI port `3000` is published; internal services have no published ports. |
| Credentials | All secrets are environment-only with non-empty `${VAR:?}` guards; nothing sensitive is committed. |

## Pins and provenance

Every image is an exact tag. Research date: **2026-09-08**, from official
sources only.

| Image (exact pin) | Provenance |
|-------------------|------------|
| `langfuse/langfuse:3.114.0` | Official release `v3.114.0` (published 2025-10-01, verified via GitHub API); tag exists on Docker Hub. |
| `langfuse/langfuse-worker:3.114.0` | Same release; worker tag exists on Docker Hub. |
| `clickhouse/clickhouse-server:24.3.18.7` | Official dev compose pins the `24.3` line; `24.3.18.7` is the final 24.3 patch release. |
| `minio/minio:RELEASE.2025-09-07T16-13-09Z` | No MinIO tag exists in the official Compose path (helm uses a legacy channel); pinned to a release-era upstream tag verified in the registry. |
| `redis:7.2.4` | Exact pin from the official dev compose. |
| `postgres:17.5` | Official compose ships a floating `postgres:17`; pinned to the newest 17.x at release era. |

Official sources (researched 2026-09-08):

- Release: <https://github.com/langfuse/langfuse/releases/tag/v3.114.0>
- Official compose (service shape, env contract, healthchecks):
  <https://raw.githubusercontent.com/langfuse/langfuse/v3.114.0/docker-compose.yml>
- Official dev compose (dependency pins):
  <https://raw.githubusercontent.com/langfuse/langfuse/v3.114.0/docker-compose.dev.yml>
- Self-hosting docs: <https://langfuse.com/docs/deployment/self-hosting>

Deviations from the official compose shape (all declared in the SDD change):
Redis gets a named volume the official file lacks, the web container gets a
wget healthcheck on `/api/public/health` (alpine-based official images ship
busybox wget), internal ports are not published at all, and Postgres is pinned
instead of floating. No separate MinIO init container exists — the official
entrypoint creates the `langfuse` bucket.

## Required environment variables (names only)

Supply values in your shell or an uncommitted `.env` next to the compose file.
**Never commit values.** Generate secrets with `openssl rand -hex 32`.

Required by Compose (hard `${VAR:?}` guards — startup refuses without them):

| Variable | Used by |
|----------|---------|
| `NEXTAUTH_SECRET` | langfuse-web |
| `SALT`, `ENCRYPTION_KEY` | langfuse-web, langfuse-worker |
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | postgres (+ DATABASE_URL) |
| `CLICKHOUSE_PASSWORD` | clickhouse (+ clients) |
| `REDIS_AUTH` | redis |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | minio |
| `LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID`, `LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY` | blob storage (MinIO) |

Optional overrides (safe defaults built in): `NEXTAUTH_URL`
(`http://localhost:3000`), `POSTGRES_DB` (`langfuse`), `CLICKHOUSE_USER`
(`clickhouse`), `LANGFUSE_S3_EVENT_UPLOAD_BUCKET` (`langfuse`),
`TELEMETRY_ENABLED` (`false`).

Required by the exporter (created in the UI: project → API keys):

| Variable | Used by |
|----------|---------|
| `LANGFUSE_PUBLIC_KEY` | `python3 -m observability` |
| `LANGFUSE_SECRET_KEY` | `python3 -m observability` |

## Start, validate, readiness, UI

```text
# validate configuration without starting anything (ephemeral shell env only)
env NEXTAUTH_SECRET=<ephemeral> SALT=<ephemeral> ENCRYPTION_KEY=<ephemeral> \
    POSTGRES_USER=<ephemeral> POSTGRES_PASSWORD=<ephemeral> \
    CLICKHOUSE_PASSWORD=<ephemeral> REDIS_AUTH=<ephemeral> \
    MINIO_ROOT_USER=<ephemeral> MINIO_ROOT_PASSWORD=<ephemeral> \
    LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=<ephemeral> \
    LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=<ephemeral> \
    docker compose -f observability/docker-compose.yml config --quiet

# start the stack
docker compose -f observability/docker-compose.yml up -d
```

Readiness: `docker compose -f observability/docker-compose.yml ps` shows all
six services healthy — the four dependencies run official healthchecks and
`langfuse-web` polls `/api/public/health`; the worker starts only after its
dependencies are healthy. First visit: sign up at <http://localhost:3000>,
create an organization/project, and generate the exporter API keys.

## Export sealed evidence

```text
python3 -m observability export --artifacts-root ROOT --langfuse-url http://localhost:3000 [--include-text]
```

Metadata-only by default; `--include-text` opts into eligible, secret-scanned
response/tool-argument text. Exit codes: `0` complete with no skips, `1` any
skip/failure (safe `{path, reason}` lines on stderr), `2` usage/config without
touching artifacts. Export is deterministic: re-running over unchanged
evidence creates zero new objects. The redacted machine-readable report on
stdout never contains raw text or credentials.

## Optional gated real-instance smoke

Normal tests remain **stub-only** (in-process HTTP stub on an ephemeral port);
this smoke is **optional** and manual, gated like the Luna live-acceptance
procedure in `docs/sdd/phase6-langgraph-harness.md` — never part of CI:

1. Start the stack, create a project, export its keys into the environment.
2. Copy a sealed run root to a temporary directory (never export in place).
3. Run the export command above; confirm the trace, generations, and scores in
   the UI; re-run and confirm no new objects (idempotent replay).
4. Tear down with `down` (or `down -v`).

Never point the exporter at a hosted Langfuse cloud, and never place provider
keys in traces or export commands.

## Stop, teardown, rollback

```text
# stop and remove containers; downstream volumes are kept
docker compose -f observability/docker-compose.yml down

# additionally delete the named volumes (postgres/clickhouse/minio/redis data)
docker compose -f observability/docker-compose.yml down -v
```

`down -v` is irreversible for Langfuse data only. **Rollback is
downstream-only**: remove `observability/`, `tests/observability/`, and this
documentation; sealed artifacts, the frozen core, and the harness need no
migration or rollback.

## Authority and limitations

- Sealed artifacts (`leaderboard.json`, `attempts/*/launch.json`,
  `core/runs/*` receipts) stay authoritative; Langfuse is a projection and
  never a score or evidence authority.
- The compose stack runs no exporter, watcher, daemon, or scheduler service;
  export is a one-shot CLI decision.
- The exporter is stdlib-only, uses no Langfuse SDK, and neither the frozen
  core nor the harness imports `observability/` (AST-proven).
- This is a local projection, not production hardening: no TLS, no external
  auth, no backup policy beyond the named volumes.
