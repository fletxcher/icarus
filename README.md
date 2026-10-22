# Icarus

An LLM chat app for airfoil analysis and design.

- `frontend/`: Next.js + shadcn/ui chat interface
- `backend/`: FastAPI service that stores chats in MongoDB and runs a LangGraph agent
  (Mistral) with tools for XFOIL analyses and CFD simulations on the OCI simulation VM

Configuration for both lives in the `.env` at the repo root.

## Frontend

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

Chats are currently stored in the browser and replies are mocked
(`frontend/lib/chat/mock-stream.ts`) until the frontend is wired to the backend.

## Backend

```bash
cd backend
uv sync
uv run backend   # http://localhost:8000, docs at /docs
```

Every setting is in `backend/src/backend/config.py`. The API starts without
`MISTRAL_API_TOKEN`; chat storage works and sending a message returns 503 until it's set.

### Local MongoDB

The `icarus-db` container doesn't publish its port, so `localhost:27017` won't reach it.
Either recreate it with `-p 27017:27017`, or point `MONGODB_URI` at the container's IP
(`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' icarus-db`),
which can change when the container restarts. The container stores data in an anonymous
volume, so mount that volume explicitly if you recreate it and want to keep the data.

### Layout

```
backend/src/backend/
  app.py              FastAPI app, lifespan (Mongo + services), /api/health, /api/models
  config.py           Settings from env vars / the repo-root .env
  db.py               Mongo client and indexes
  services.py         Builds the shared services and the agent at startup
  chats/              Threads + messages: schemas, repository, routes
  agent/
    graph.py          LangGraph graph: agent node <-> tool node loop
    tools.py          Agent tools (XFOIL polar, NACA search, CFD submit/status)
    stream.py         Runs one turn, streams SSE events, persists the reply
    prompts.py        System prompt
    models.py         Selectable Mistral models
  compute/
    runner.py         CommandRunner: LocalRunner (this server) / SSHRunner (VM)
    xfoil.py          XFOIL script builder, polar parser, XfoilService
    simulations.py    CFD job submission and polling
    router.py         /api/simulations
backend/vm/run_case.sh  Placeholder CFD entrypoint to install on the VM
```

### API

| Method | Path | |
|---|---|---|
| GET | `/api/threads` | List threads, newest first |
| POST | `/api/threads` | Create a thread `{title}` |
| GET | `/api/threads/{id}` | Thread with messages |
| PATCH | `/api/threads/{id}` | Rename / pin `{title?, pinned?}` |
| DELETE | `/api/threads/{id}` | Delete thread and messages |
| DELETE | `/api/threads/{id}/messages/{message_id}` | Delete a message and all later ones |
| POST | `/api/threads/{id}/messages` | Send `{content, model?}` and stream the reply (SSE) |
| GET | `/api/simulations/{job_id}` | CFD job status (refreshes from the VM) |
| GET | `/api/models`, `/api/health` | |

**Edit** a message: delete from it, then POST the new content.
**Regenerate** a reply: delete from the reply, then POST `{}` (no content).

#### Streaming events

`POST /api/threads/{id}/messages` responds with `text/event-stream`:

| Event | Data |
|---|---|
| `start` | `{message_id}` |
| `token` | `{text}` |
| `tool_call` | `{id, name, args}` |
| `tool_result` | `{id, name, content}` |
| `done` | `{message_id}` |
| `error` | `{message}` |

The assistant message is saved as `streaming`, then finalized as `done`, `stopped`
(client disconnected) or `error`, including its tool calls and results.

The agent is rebuilt from stored messages each turn, so edits and regenerations apply
naturally. Tool calls from earlier turns aren't replayed; their outcomes live in the
assistant's text.

### Simulation VM

The backend reaches the VM over SSH (`VM_HOST`, `VM_USER`, `VM_SSH_KEY_PATH`). The VM's
host key must be in `~/.ssh/known_hosts` (or `VM_KNOWN_HOSTS_PATH`).

**CFD** jobs are submit-and-poll, since they outlast any HTTP request:

1. The backend writes `{VM_WORKDIR}/jobs/{job_id}/case.json`.
2. It launches `{VM_CFD_SCRIPT} {job_dir}` detached.
3. The script keeps `{job_dir}/status.json` updated with
   `{"status": "running" | "completed" | "failed", "result": {...}, "error": "..."}`.
4. `get_simulation_status` / `GET /api/simulations/{id}` read it back into Mongo.

`backend/vm/run_case.sh` implements that contract with a placeholder solve step. Swap in
the real mesh + solver pipeline.

**XFOIL** runs wherever `XFOIL_RUNNER` points (`vm` or `local`), through the same
`CommandRunner` interface. One polar takes about a second, so CPU isn't the deciding
factor. Running it on the VM is the default because serverless hosts (like Vercel)
can't install the Fortran XFOIL binary. Use `local` when the API runs on a normal
server or container with `xfoil` installed.

### Deployment notes

- Vercel can host FastAPI (`backend.app:app`), but functions have a maximum duration.
  Chat streaming and XFOIL calls fit within it; CFD is already asynchronous.
- Set `CORS_ORIGINS` to the frontend's deployed URL.
- There's no authentication yet. Add it before exposing the API publicly.

### Development

```bash
cd backend
uv run ruff check . && uv run ruff format .
MONGODB_URI=mongodb://<host>:27017 uv run pytest
```

API tests use a throwaway database and a scripted chat model in place of Mistral; they
skip when MongoDB isn't reachable.
