# Docker deployment (Compose)

Run the full stack as three containers with one shared data volume. For a
host-native run see [`LOCAL.md`](LOCAL.md); for the air-gapped enclave build see
[`AIRGAP_RHEL.md`](AIRGAP_RHEL.md).

## The three images (one concern each)

| Service | Image / Dockerfile | Role | Ports |
|---------|--------------------|------|-------|
| `api` | `deploy/docker/Dockerfile.api.local` | FastAPI; ingests in-process, serves HTTP, enqueues jobs | `8000` |
| `worker` | `deploy/docker/Dockerfile.worker.local` | drains the async job queue (the only image with LibreOffice + pinned fonts + CPU torch) | — |
| `frontend` | `deploy/docker/Dockerfile.frontend` | nginx serving the built React app, proxies `/api` → `api` | `8080` |

A named `data` volume holds `DATA_DIR` (the jobs DB, projects, indexes) and is
shared by `api` and `worker`.

> The `.local` Dockerfiles target local/dev runs on Debian-based images with the
> full offline capability baked in (real MiniLM embeddings, tesseract, LibreOffice).
> The enclave uses the UBI variants (`Dockerfile.api`, `Dockerfile.rhel`) — see
> [`AIRGAP_RHEL.md`](AIRGAP_RHEL.md).

## Prerequisites

- Docker with BuildKit, and the Compose plugin (`docker compose`).

## Build

```bash
# from the repo root
DOCKER_BUILDKIT=1 docker compose build
```

The `worker` and `api` images bake the MiniLM embedding weights and install
CPU `torch` — the first build is large (~3 GB each) and takes several minutes.
The `frontend` image is small (built React app on nginx).

## Run

Start detached (`-d`) so the containers are owned by the Docker daemon and
survive your shell/session — the recommended way to run the stack:

```bash
docker compose up -d
```

- Frontend: `http://localhost:8080` (proxies `/api` → the `api` service)
- API: `http://localhost:8000` (`/health`, `/projects`)
- The `worker` has no published port; it claims jobs from the shared volume.

Observe and manage the detached stack:

```bash
docker compose ps                 # status of the three services
docker compose logs -f            # follow logs (Ctrl-C just stops following)
docker compose logs -f worker     # one service
```

(Running `docker compose up` in the foreground also works, but the containers
then stop when you Ctrl-C or the terminal closes; use `-d` for a persistent run.)

To enable the in-app diagnostics page (and the diagnostics e2e spec):

```bash
DIAGNOSTICS_ENABLED=true docker compose up -d
```

Tear down (and remove the network); add `-v` to also drop the data volume:

```bash
docker compose down          # keep data
docker compose down -v       # also wipe DATA_DIR
```

## Configuration (environment)

All have offline-safe defaults; override via the shell or an `.env` file that
Compose reads.

| Variable | Default | Notes |
|----------|---------|-------|
| `EMBEDDING_BACKEND` | `sentence-transformers` | `hashing` forces the zero-dep fallback |
| `OCR_ENABLED` | `true` | worker image carries tesseract |
| `SAMPLES_ENABLED` | `false` | expose the in-app Samples tab |
| `DIAGNOSTICS_ENABLED` | `false` | expose `/diagnostics` |
| `WORKER_CONCURRENCY` | `2` | worker threads (I/O-bound ops) |
| `BEDROCK_ENABLED` | `false` | optional model tier; no-op offline |

### How the worker runs (not per-job)

The `worker` is a **long-lived pool**, not a container-per-job: each worker runs
a persistent `claim → run → complete → claim next` loop against the shared
queue, so one worker handles many jobs over its lifetime and the expensive
startup cost (loading the embedding model, importing the stack) is paid **once**
at container start — never per job. Jobs are pulled off the SQLite queue; they
never spawn containers.

### Scaling the worker across cores

CPU-bound ops scale with worker **processes** (threads are GIL-bound). Two ways:

- Set `WORKER_PROCESSES` on the `worker` service (int or `auto` = cgroup cores−1)
  — N worker processes inside one container.
- Or scale the service to multiple containers:

```bash
docker compose up -d --scale worker=4
```

Either is safe: the SQLite job claim is transactional (`BEGIN IMMEDIATE` + WAL)
and verified to never double-claim a job across processes **or** containers.

## AWS credentials (optional Bedrock tier)

The `api`/`worker` services mount `${AWS_DIR:-$USERPROFILE/.aws}` read-only so,
when `BEDROCK_ENABLED=true`, boto3 resolves the host's profile inside the
container. Harmless when absent (no creds → offline, the safe default). On Linux
set `AWS_DIR=$HOME/.aws` if you want this; the `USERPROFILE` default is a
Windows convenience and warns harmlessly on Linux.

## Notes

- PostgreSQL is stubbed in `docker-compose.yml` for a later upgrade; the current
  store is JSON/SQLite on the `data` volume.
- The end-to-end Playwright suite (`frontend/e2e/`) runs against this live stack:
  bring it up with `DIAGNOSTICS_ENABLED=true`, then from `frontend/` run
  `npm run build && npm run e2e`.
