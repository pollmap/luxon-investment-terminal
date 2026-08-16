# LUXON Local Operations

This is the Windows beta operating path for Postgres, FastAPI, and Next.js.
All published ports bind to `127.0.0.1`; the stack is not a LAN or internet
deployment.

## Prerequisites

- Windows PowerShell 5.1 or PowerShell 7
- Docker Desktop with Docker Compose v2
- Docker Desktop running before start, status, backup, or scheduled backup

Create ignored `.env.local` from `.env.example`. Add the following Compose
variables without committing or printing their values:

```dotenv
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
```

Use a long URL-safe random value for `POSTGRES_PASSWORD`; Compose uses the same
value when it constructs FastAPI's PostgreSQL URL.

The default database and role names are both `luxon`. Optional non-secret host
port overrides are `LUXON_WEB_PORT`, `LUXON_API_PORT`, and `LUXON_DB_PORT`.
These `LUXON_*` names belong only to the Compose operating layer and do not
replace application env names. Connector and authentication secrets keep their
existing env names, including `OPENDART_API_KEY`, `DART_API_KEY`, `SEC_USER_AGENT`,
`EDINET_API_KEY`, `JQUANTS_REFRESH_TOKEN`, `FRED_API_KEY`, `ECOS_API_KEY`,
`KOSIS_API_KEY`, `ESTAT_APP_ID`, `AUTH_SECRET`, `AUTH_GITHUB_ID`,
`AUTH_GITHUB_SECRET`, and `PF_COOKIE_SECRET`.

Do not run `docker compose config` without `--quiet` in a shared terminal or
capture because a rendered config can include expanded environment values.

## Start, inspect, and stop

From the repository root:

```powershell
.\scripts\windows\start-local.ps1
.\scripts\windows\status-local.ps1
.\scripts\windows\stop-local.ps1
```

`start-local.ps1` validates the Compose model, builds images, waits for all
health checks, and applies Alembic migrations before FastAPI starts. Use
`-SkipBuild` only when the local images already match the checked-out code.

Default local endpoints:

- Web: `http://127.0.0.1:3000`
- API health: `http://127.0.0.1:8000/api/health`
- Postgres: `127.0.0.1:5432`

The stop script uses `docker compose stop`. It does not run `down`, remove
containers, delete the Postgres volume, or touch raw and warehouse files.

For bounded diagnostics:

```powershell
docker compose --project-name luxon-local --env-file .env.local --file docker-compose.yml logs --tail 100 postgres api web
```

## Persistence

| Data | Persistence |
| --- | --- |
| Postgres | Named volume `luxon-local_luxon-postgres-data` with the default project name |
| Append-only payloads | Host bind `data/raw` |
| DuckDB and Parquet warehouse | Host bind `data/warehouse` |
| Imports, cache, charts, and run manifests | Host bind `storage` |
| Backups | Ignored host directory `.local-backups` |

Compose starts no ingestion schedule. Run ingestion commands explicitly after
the matching provider contract and env name are configured. For example, a
non-writing readiness check can run inside the API image:

```powershell
docker compose --project-name luxon-local --env-file .env.local --file docker-compose.yml exec api python -m services.ingestion_worker.cli doctor --markets KR --strict
```

Missing keys or contracts must remain `missing_key` or `missing_contract`; do
not enable fixture fallback for the local source-backed stack.

## Backups

Create an additive backup while Postgres is running:

```powershell
.\scripts\windows\backup-local.ps1
```

Each timestamp directory contains:

- a custom-format `pg_dump`;
- a ZIP of `data/raw`, `data/warehouse`, and `storage`;
- a manifest with UTC time, Git commit, and SHA-256 checksums.

The script never overwrites or prunes an existing backup. It also leaves an
incomplete timestamp directory in place if a step fails so the failure can be
inspected. Restore is intentionally not automated because it overwrites state;
review the selected backup and target database before performing a manual
restore.

## Optional daily backup schedule

No scheduled task is installed by start, build, or Compose. Install one only by
explicitly running:

```powershell
.\scripts\windows\install-backup-schedule.ps1 -At 02:00
```

The task runs as the current user with limited privileges and only while that
user has an interactive logon. Docker Desktop must be running. An existing task
is not replaced unless `-Force` is explicitly supplied.

The installer schedules backups only. It does not schedule ingestion, image
updates, deployment, cleanup, or deletion.
