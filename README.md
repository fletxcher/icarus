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

The frontend talks to the backend at `NEXT_PUBLIC_API_URL` (default
`http://localhost:8000`), read from the root `.env` by `next.config.ts`. It renders each
reply as it streams: the model's reasoning, every tool call with its arguments, status
and result, then the answer.

## Backend

```bash
cd backend
uv sync
uv run api   # http://localhost:8000, docs at /docs
```

Every setting is in `backend/src/api/config.py`. The API starts without any model
provider key; chat storage works and sending a message returns 503 until one is set.

### Local MongoDB

The `icarus-db` container doesn't publish its port, so `localhost:27017` won't reach it.
Either recreate it with `-p 27017:27017`, or point `MONGODB_URI` at the container's IP
(`docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' icarus-db`),
which can change when the container restarts. The container stores data in an anonymous
volume, so mount that volume explicitly if you recreate it and want to keep the data.

### Logging

Every log line carries the id of the HTTP request that produced it (also returned as
the `X-Request-ID` response header). Logged:

- each request and response: method, path, status, duration and bodies (streamed
  responses are summarized), plus the traceback of any unhandled error;
- each agent turn, model call (duration, token usage, tools requested) and tool call
  (arguments, result or traceback, duration);
- commands run locally or on the VM (exit code, duration, stderr on failure) and CFD
  job status changes.

`LOG_LEVEL` (default `INFO`) and `LOG_BODY_MAX_CHARS` (default 2000, longer payloads are
truncated) control it.

### Layout

```
backend/src/api/
  app.py              FastAPI app, lifespan (Mongo + services), /api/health, /api/models
  config.py           Settings from env vars / the repo-root .env
  log_config.py       Logging setup (request ids on every line)
  middleware.py       Request/response logging
  db.py               Mongo client and indexes
  services.py         Builds the shared services and the agent at startup
  chats/              Threads + messages: schemas, repository, routes
  agent/
    graph.py          LangGraph graph: agent node <-> tool node loop
    tools.py          Agent tools (XFOIL polar, NACA search, CFD submit/status)
    stream.py         Runs one turn, streams SSE events, persists the reply
    logging_callbacks.py  Logs model and tool calls
    prompts.py        System prompt
    models.py         Selectable Mistral models
  compute/
    runner.py         CommandRunner: LocalRunner (this server) / SSHRunner (VM)
    xfoil.py          XFOIL script builder, polar parser, XfoilService
    simulations.py    CFD job submission and polling
    router.py         /api/simulations
vm/setup.sh             Prepares (or cleans up) a fresh simulation VM
vm/run_case.sh          Placeholder CFD entrypoint, installed on the VM by setup.sh
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
| POST | `/api/threads/{id}/messages` | Send `{content, model?, attachments?}` and stream the reply (SSE) |
| POST | `/api/files` | Upload a text file (multipart `file`) to attach; returns its id |
| DELETE | `/api/files/{id}` | Remove a file that hasn't been sent yet |
| GET | `/api/simulations/{job_id}` | CFD job status (refreshes from the VM) |
| GET | `/api/models`, `/api/health` | |

**Edit** a message: delete from it, then POST the new content.
**Regenerate** a reply: delete from the reply, then POST `{}` (no content).

#### Streaming events

`POST /api/threads/{id}/messages` responds with `text/event-stream`:

| Event | Data |
|---|---|
| `start` | `{message_id}` |
| `reasoning` | `{text}` |
| `token` | `{text}` |
| `tool_call` | `{id, name, args}` |
| `tool_result` | `{id, name, status, content}` (status `success` or `error`) |
| `title` | `{title}` (first message of a chat only; sent before `start` when ready within 5 s) |
| `done` | `{message_id}` |
| `error` | `{message}` |

The assistant message is saved as `streaming`, then finalized as `done`, `stopped`
(client disconnected) or `error`. Its `parts` record the turn in order: `reasoning`,
`tool` (args, status, result) and `text`; `content` holds the answer text alone.

A failing tool doesn't end the turn: the error goes back to the model as the tool's
result so it can explain or retry.

The agent is rebuilt from stored messages each turn, so edits and regenerations apply
naturally. Reasoning and tool calls from earlier turns aren't replayed; their outcomes
live in the assistant's text.

### Tools and what the model knows about them

Tools are only given to the model when they can actually run, checked at startup:

| Tool | Available when |
|---|---|
| `create_plot` | always |
| `run_xfoil_polar`, `search_naca4_airfoils` | `XFOIL_RUNNER=local` with `xfoil` (and `xvfb-run` unless `XFOIL_DISPLAY=none`) installed, or `XFOIL_RUNNER=vm` with a VM |
| `submit_cfd_simulation`, `get_simulation_status` | a VM is configured (`VM_HOST`) |

The system prompt lists the available tools and names each missing capability with the
reason, and tells the model to say plainly when something isn't available instead of
attempting it or inventing tools. The startup log shows which tools are active and why
others aren't. A call to a tool that doesn't exist comes back to the model as an error.

### Plots

`create_plot` takes a Plotly figure spec (JSON with `data` traces and a `layout`), not
code, so the same spec always draws the same plot and nothing runs on the server.
Plotly's own validator checks every spec; invalid properties go back to the model as
errors naming what to fix and showing the right shape. Free models make a few
predictable mistakes, so these are repaired instead of failing (each retry costs a
request): arrays wrapped as `{"item": [...]}`, numbers written as strings (`"42"`; only
whole arrays of numeric strings convert, so category labels stay), and a figure sent as
a JSON string, a bare list of traces or a single trace. Limits: 20 traces, 50,000
points, 2 MB.

The model gets a one-line summary; the validated figure is stored on the reply's tool
part as `artifact` and drawn inline by the frontend (Plotly loads only when a chat has a
plot). The app applies its own theme from a colorblind-validated palette, with separate
light and dark steps: categorical colors in a fixed order, a one-hue sequential scale
and a blue/gray/red diverging scale for fields. Specs' own templates, backgrounds and
fonts are dropped. Each plot has a hover layer, a PNG download and a "Show data" table.
Plot text doesn't render LaTeX, so the model is told to use Unicode and `<sub>`/`<sup>`.

### Chat titles

A new chat starts with its first message as a placeholder title. Alongside the first
reply, the model is asked for a 2–6 word title as a `ChatTitle` tool call (free-text
requests let `openrouter/free` route to safety classifiers that answer "safe"). It's
saved and streamed as a `title` event ahead of the reply: the reply keeps generating
but is held for up to 5 seconds so the title shows first, and a slower title follows
as soon as it's ready. Renaming a chat stops this from ever replacing your
title. It costs one extra model request per new chat; `AUTO_TITLE=false` turns it off.

### Attachments

The **+** button in the composer (or dropping files onto it) uploads files right away;
they're sent with the next message. Only text files are accepted for now (airfoil
`.dat` coordinates, `.csv`, `.txt`, `.json`, ...), up to `FILE_MAX_BYTES` (1 MB) each and
10 per message. Their contents are added to the user's message in the model's context,
cut at `FILE_CONTEXT_MAX_CHARS` (20,000) characters per file. Files are stored in the
`files` collection and deleted with their chat.

### Models and reasoning

Models are offered per provider, and only when that provider's key is in `.env`;
`/api/models` and the model picker list just those:

| Key | Models |
|---|---|
| `MISTRAL_API_TOKEN` | `mistral-medium-3-5`, `mistral-small-latest` |
| `OPENROUTER_API_KEY` | `openrouter/free` (routes to a free model; 50 requests a day, 1,000 after buying $10 of credits) |

An agent turn makes several model requests (one per step plus the answer), so free
quotas run out quickly. `DEFAULT_MODEL` picks the default; if it isn't available, the
first available model is used, with a warning in the logs.

`REASONING_EFFORT=high` (default) streams the model's thinking before its answer; `none`
turns it off. Mistral sends thinking as content blocks and OpenRouter as
`reasoning_content`; both stream as `reasoning` events.

### Simulation VM

The backend reaches the VM over SSH (`VM_HOST`, `VM_USER`, `VM_SSH_KEY_PATH`). The VM's
host key must be in `~/.ssh/known_hosts` (or `VM_KNOWN_HOSTS_PATH`).

**CFD** jobs are submit-and-poll, since they outlast any HTTP request:

1. The backend writes `{VM_WORKDIR}/jobs/{job_id}/case.json`.
2. It launches `{VM_CFD_SCRIPT} {job_dir}` detached.
3. The script keeps `{job_dir}/status.json` updated with
   `{"status": "running" | "completed" | "failed", "result": {...}, "error": "..."}`.
4. `get_simulation_status` / `GET /api/simulations/{id}` read it back into Mongo.

`vm/run_case.sh` implements that contract with a placeholder solve step. Swap in the
real mesh + solver pipeline.

**Setting up a VM.** Copy `vm/` to a fresh Debian/Ubuntu VM and run, as the user the
backend connects as:

```bash
./setup.sh --install     # XFOIL, xvfb, xauth; ~/icarus/bin/run_case.sh; ~/icarus/jobs
./setup.sh --uninstall   # removes what --install added (asks before deleting job data)
```

`--install` checks that XFOIL runs headless and prints the matching `.env` values.
`--uninstall` only removes packages that `--install` itself installed, then runs
`apt-get autoremove`. No CFD solver is installed yet.

**XFOIL** runs wherever `XFOIL_RUNNER` points (`vm` or `local`), through the same
`CommandRunner` interface. One polar takes about a second, so CPU isn't the deciding
factor. Running it on the VM is the default because serverless hosts (like Vercel)
can't install the Fortran XFOIL binary. Use `local` when the API runs on a normal
server or container with `xfoil` installed.

Without a display, XFOIL must not open its plot window. The Debian/Ubuntu `xfoil`
package crashes (SIGFPE) when its graphics are turned off, so by default
(`XFOIL_DISPLAY=xvfb`) it runs under a virtual display with `xvfb-run`; install it with
`sudo apt install xvfb` on whichever machine runs XFOIL. For an XFOIL build without that
crash, `XFOIL_DISPLAY=none` turns its graphics off instead.

### Deployment notes

- **Vercel services.** The root `vercel.json` deploys `frontend/` (Next.js) and
  `backend/` (FastAPI) as two services on one domain: `/api/*` goes to the backend,
  everything else to the frontend. The backend receives the original path (it sees
  `/api/threads`, not `/threads`), which matches its routes. No service calls another on
  the server (the browser calls the API), so no bindings are declared.
- Vercel finds the backend through `[tool.vercel] entrypoint = "src.api.app:app"` in
  `backend/pyproject.toml` (its default detection doesn't look in `src/api/`). The
  backend uses relative imports, so the same code runs as `src.api` there and as `api`
  locally (`uv run api`, tests) without the project having to be installed.
- In production the frontend calls the API on the same origin, so leave
  `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` unset on Vercel. Locally the frontend defaults
  to `http://localhost:8000` (`vercel dev` runs both services together instead).
- Vercel functions have a maximum duration (300 s on Hobby). Chat streaming and XFOIL
  calls fit within it; CFD is already asynchronous.
- Set the backend's variables (`MONGODB_URI`, provider keys, `VM_*`, ...) in the Vercel
  project. `MONGODB_URI` must be reachable from Vercel, e.g. MongoDB Atlas; a local
  Docker container isn't. XFOIL can't be installed on Vercel, so use `XFOIL_RUNNER=vm`.
- There's no authentication yet. Add it before exposing the API publicly.

### Development

```bash
cd backend
uv run ruff check . && uv run ruff format .
MONGODB_URI=mongodb://<host>:27017 uv run pytest
```

API tests use a throwaway database and a scripted chat model in place of Mistral; they
skip when MongoDB isn't reachable.
