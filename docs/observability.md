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
| Credentials | All secrets are environment-only with non-empty `${VAR:?}` guards; nothing sensitive is committed. They remain plain container configuration: any Docker-privileged user can read them (e.g. `docker inspect`, `docker exec`). |

## Pins and provenance

Every image is an exact tag. Research date: **2026-09-08**, from official
sources only.

| Image (exact pin) | Provenance |
|-------------------|------------|
| `langfuse/langfuse:3.114.0` | Official release `v3.114.0` (published 2025-10-01, verified via GitHub API); tag exists on Docker Hub. |
| `langfuse/langfuse-worker:3.114.0` | Same release; worker tag exists on Docker Hub. |
| `clickhouse/clickhouse-server:24.3.18.7` | Official dev compose pins the `24.3` line; `24.3.18.7` is the final 24.3 patch release. |
| `quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z` | No MinIO tag exists in the official Compose path (helm uses a legacy channel); the release-era upstream tag was verified in the official Quay registry. |
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
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | minio — and the S3 event-upload credentials derived from them |

The S3 event-upload credentials are deliberately **not** separate
prerequisites: the official v3.114.0 deployment defaults them to the same
values as the MinIO root user/password (official defaults
`minio`/`miniosecret`), and this stack provisions no separate MinIO user.
Compose derives the upload credentials directly from the MinIO root pair —
set one pair, rotate one pair.

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
    docker compose -f observability/docker-compose.yml config --quiet

# start the stack
docker compose -f observability/docker-compose.yml up -d
```

Readiness: `docker compose -f observability/docker-compose.yml ps` should show
five `healthy` services — `langfuse-web` (polls `/api/public/health`) plus the
four dependencies (official healthchecks). `langfuse-worker` defines **no
healthcheck**: it is only expected to reach `running`; the worker binds to its
container hostname/interface, so the smoke independently proves readiness with
`docker exec <container-id> sh -c 'wget --no-verbose --tries=1 --spider
"http://${HOSTNAME}:3030/api/health"'`. A running worker is not proof that
ingestion works — event-upload/S3 or ClickHouse failures surface in
`docker compose -f observability/docker-compose.yml logs langfuse-worker`,
not in any health status. The first start also runs database migrations, so
allow extra time before the first health check turns green. First visit: sign
up at <http://localhost:3000>, create an organization/project, and generate
the exporter API keys.

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

## Automated real-instance smoke

The smoke proves the repository's observability contract against a real,
ephemeral Langfuse v3.114.0 stack as an **optional red check**: it runs for
pull requests touching smoke-affecting surfaces and on manual dispatch in
`.github/workflows/langfuse-smoke.yml` — never on a schedule or push trigger,
and never as a merge-blocking gate. Required-check promotion is a later manual
branch-protection decision; the workflow cannot enact it.
It uses no repository or GitHub secrets: the runtime generates short-lived
stack credentials, masks them before first use, scans outputs, and fails
closed on any exposure, then headlessly provisions an ephemeral organization,
project, and API key through Langfuse v3.114.0's official `LANGFUSE_INIT_*`
interface.

Run the same procedure locally (the same contract as CI):

```text
python3 scripts/langfuse_smoke.py
# Optional non-secret options: --compose-file PATH --report PATH --timeout-seconds N
```

Plan for **4 GB or more RAM**, several GB of free disk, and **20–30 minutes**
on a cold run while images download and migrations finish. The smoke picks a
free loopback host port per run (`LANGFUSE_WEB_HOST_PORT` override) and a
unique Compose project label, aborts on any pre-existing project-labelled
resource, and never touches unrelated Docker resources.

The bounded phase machine runs: dependency readiness → web health (fixed
v3.114.0 response) → an **independent worker proof** (a stable no-restart
window followed by an in-container `GET /api/health` probe via BusyBox `wget`
using the worker container's `$HOSTNAME` interface — `running` alone never
suffices) → an empty
scoped API baseline → **two identical real metadata-only CLI exports** (both
reporting exactly processed=1, accepted_items=1, accepted_events=3, failed=0,
skipped=0, error_events=0; `exporter_invocations` must equal 2) → authenticated
API proofs of **0 → 1 → 1** trace/generation/score counts with deterministic
IDs, identical ID sets, and zero growth. A 207 ingestion response is transport
accounting only and is never treated as proof that an object is new.

Every run reports sanitized **diagnostics before teardown**, six-service
**image provenance** (requested image, resolved digest, runtime image ID, or
an explicit unavailable record), and one authoritative report: cleanup always
runs `down -v --remove-orphans` against the exact project label only — which
destructively deletes the smoke project's downstream volumes — and a cleanup
failure makes the overall result `FAIL` with `reason: cleanup_failed` even
when all assertions passed. The report carries promotion evidence fields but
never self-promotes: merge-blocking promotion needs **10 successful
independent PR runs over 14 days**, including a cold start and a deliberate
failure drill, correct cleanup in every run, no rerun-derived green, and a
window reset on any flake or false acceptance.

The smoke is Langfuse downstream-only: it builds a deterministic sealed
metadata-only fixture in a system temporary directory (never importing test
fixtures or touching the checkout), and never modifies authoritative repository
artifacts. Never point it at hosted Langfuse Cloud or put provider keys in
traces or command arguments.

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
