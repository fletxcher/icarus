# Icarus

An LLM chat app for airfoil analysis and design.

- `frontend/`: Next.js + shadcn/ui chat interface
- `backend/`: FastAPI service that stores chats in MongoDB and runs a LangGraph agent
  (Mistral) with tools for XFOIL analyses and CFD simulations on the simulation work station

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

### MongoDB: local or remote

`MONGODB_TARGET` is `local` (default) or `remote` and picks which connection is used:
`MONGODB_LOCAL_URI` / `MONGODB_LOCAL_NAME`, or `MONGODB_REMOTE_URI` /
`MONGODB_REMOTE_NAME` (e.g. MongoDB Atlas; the URI carries the credentials, and
`remote` without it refuses to start). The startup log shows which one is in use. Tests
always use the local one.

#### Local MongoDB

The `icarus-db` container doesn't publish its port, so `localhost:27017` won't reach it.
Either recreate it with `-p 27017:27017`, or point `MONGODB_LOCAL_URI` at the container's IP
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
- commands run locally or on the work station (exit code, duration, stderr on failure) and CFD
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
    runner.py         CommandRunner: LocalRunner (this server) / SSHRunner (work station)
    xfoil.py          XFOIL script builder, polar parser, XfoilService
    simulations.py    CFD job submission and polling
    router.py         /api/simulations
ws/setup.sh             Prepares (or cleans up) a fresh simulation work station
ws/run_case.sh          Placeholder CFD entrypoint, installed on the work station by setup.sh
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
| GET | `/api/simulations/{job_id}` | CFD job status (refreshes from the work station) |
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

### Database

MongoDB, database `MONGODB_LOCAL_NAME` or `MONGODB_REMOTE_NAME`. IDs are UUID strings and dates are timezone-aware UTC.

Each collection's schema and indexes are written out in `database/<collection>.json`
(`threads`, `messages`, `files`, `simulations`, `users`, `sessions`): a `$jsonSchema`
validator, the indexes, and whether it's `enforced`. Those files are generated from the
Python definitions (`backend/src/api/catalog.py`), which the backend reads, not the
files. After changing a definition, run `uv run export-schemas` in `backend/`; a test
fails if the files are out of date (`--check` does the same).

At startup the backend builds every index and, for the enforced collections (`users`,
`sessions`), creates them with their validator or re-applies it, so a schema change
reaches the database on the next start. The other four hold data from earlier versions of
the app, so their schemas document what is written today and are checked by tests (every
kind of document the app produces is inserted into a collection that enforces them), not
by MongoDB.

| Collection | Holds |
|---|---|
| `threads`, `messages` | Chats and their messages (a reply's `parts`: reasoning, tool calls, text) |
| `files` | Uploaded text files attached to messages |
| `simulations` | CFD jobs submitted to the work station |
| `users` | Accounts (validated) |
| `sessions` | Signed-in browsers (validated) |

**`users`**: `email` (lowercase, unique), `email_verified_at`, `name`, `avatar_url`,
`password_hash` (Argon2id; null for OAuth-only users), `identities` (`provider` +
`subject`, unique across users), `role` (`user` / `admin`), `status` (`active` /
`disabled`), `preferences` (`default_model`, `theme`), `created_at`, `updated_at`,
`last_login_at`. The API returns `UserPublic`, which leaves out the hash and identities.

**`sessions`**: `user_id`, `token_hash`, `created_at`, `last_seen_at`, `expires_at`,
`revoked_at`, `user_agent`, `ip`. The browser cookie holds a random token; only its
SHA-256 hash is stored (unique), so a database leak doesn't expose live sessions.
MongoDB deletes a session after `expires_at` (TTL index), and `(user_id, created_at)` is
indexed for listing or revoking a user's sessions.

The models are in `backend/src/api/auth/schemas.py` (what the app reads and writes) and
mirrored as validators in `definitions.py`, so the database itself rejects bad roles,
uppercase emails, unknown fields and so on. Tests insert documents from the models into
a validated collection to keep the two in step.

### Tools and what the model knows about them

Tools are only given to the model when they can actually run, checked at startup:

| Tool | Available when |
|---|---|
| `create_plot` | always |
| `run_xfoil_polar`, `search_naca4_airfoils` | `XFOIL_RUNNER=local` with `xfoil` (and `xvfb-run` unless `XFOIL_DISPLAY=none`) installed, or `XFOIL_RUNNER=ws` with a work station |
| `submit_cfd_simulation`, `get_simulation_status` | a work station is configured (`WS_HOST`) |

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

### Simulation work station

The backend reaches the work station over SSH (`WS_HOST`, `WS_USER`, `WS_SSH_KEY_PATH`). The work station's
host key must be in `~/.ssh/known_hosts` (or `WS_KNOWN_HOSTS_PATH`).

**CFD** jobs are submit-and-poll, since they outlast any HTTP request:

1. The backend writes `{WS_WORKDIR}/jobs/{job_id}/case.json`.
2. It launches `{WS_CFD_SCRIPT} {job_dir}` detached.
3. The script keeps `{job_dir}/status.json` updated with
   `{"status": "running" | "completed" | "failed", "result": {...}, "error": "..."}`.
4. `get_simulation_status` / `GET /api/simulations/{id}` read it back into Mongo.

`ws/run_case.sh` implements that contract: gmsh meshes the case (one cell thick,
boundary layer resolved to y+ ~ 1), `gmshToFoam` converts it, and OpenFOAM solves it on
up to 4 cores with the k-omega SST model (laminar below Re 1000). `ws/cfd_case.py` writes
the mesh script and the OpenFOAM dictionaries from `case.json` and reads the results
back; each step logs to `{job_dir}/log.<step>`, and a failure puts the end of that log in
the job's `error`. The flow is incompressible, so `mach` is ignored (and flagged above
Mach 0.3).

| `shape` | Needs | Reference length | Default regime |
|---|---|---|---|
| `airfoil` | `naca` (4-digit, or 5-digit without reflex) | chord | steady (`simpleFoam`) |
| `cylinder` | | diameter | transient (`pimpleFoam`) |
| `ellipse` | `aspect_ratio` (length / height) | frontal height | transient |
| `wedge` | `half_angle` (apex faces the flow) | base height | transient |
| `rectangle` | `aspect_ratio` (length / height) | frontal height | transient |

`regime` overrides the default. Steady runs stop when the residuals converge (or after
2500 / 3500 / 5000 iterations for coarse / medium / fine meshes) and report the average of
the last iterations; transient runs go to 80 convective times, average over the second
half and report the Strouhal number when the lift oscillates. Results carry `cl`, `cd`,
`cm` (per unit span, on the reference length), `history` for plotting, final residuals,
the airfoil's y+, the cell count and `warnings`.

**Simulations.** The chart icon at the top right opens a panel on the right (the count on
it is the running jobs). It lists the running simulations, each with a progress bar, stage
and time left, or "No simulations running", plus the most recent finished ones. Clicking
one goes to its own page, `/simulations/<id>`, which has the full monitor: stages,
progress, Courant number and live force-coefficient plots (Overview), residuals, setup
(case, solver, mesh quality, the OpenFOAM dictionaries), the log tail and the results with
the flow video. The trash button on that page deletes the job after a confirmation: a
running job is stopped, then its record and its files on the work station are removed
(`DELETE /api/simulations/{id}`). A CFD tool card in the chat links to its job's page too. The backend
serves it from `GET /api/simulations` (the list), `/api/simulations/{id}/info`,
`/progress?log_offset=&coeff_offset=` (only what is new since the offsets the last call
returned) and `/files/flow.mp4|flow.png`; these read the work station's logs over SSH
through `ws/cfd_case.py`.

**Images and flow video.** With `video` on (the default), every run gets a
`velocity.png` and a `pressure.png` (pressure coefficient) of the final flow field, and
transient runs also get `flow.mp4`, the z-vorticity at every saved time step. Transient runs
save a snapshot every 0.25 convective times, afterwards `foamToVTK` converts them and
`ws/render_video.py` (ParaView's `pvbatch` on a virtual display) draws them, zoomed on the
body and its wake with fixed colour ranges, and ffmpeg encodes the video (25 fps). The
Results tab shows them, and the result's `media` entry lists them. If ParaView or ffmpeg is
missing or rendering fails, the job still completes and a warning says why (see
`log.render`); the VTK files and frames are deleted afterwards. `log.results` records what
was written to `status.json`.

Wall conditions on the body (`wall_type`): `no_slip`, `slip`, `moving` (surface velocity
`wall_velocity_x` / `wall_velocity_y` in freestream speeds) and `rotating` (a cylinder with
`spin_ratio` = surface speed / freestream speed). `roughness_height` (fraction of the
reference length) adds a rough-wall function and sizes the first cell for y+ ~ 30. The
flow direction is rotated by `alpha`, so the farfield and mesh stay the same.

**Setting up a work station.** Copy `ws/` to a fresh Debian/Ubuntu work station and run, as the user the
backend connects as:

```bash
./setup.sh --install     # XFOIL, gmsh, OpenFOAM, ParaView, ffmpeg; ~/icarus/bin/run_case.sh; ~/icarus/jobs
./setup.sh --uninstall   # removes what --install added (asks before deleting job data)
```

`--install` adds the OpenFOAM apt repository if it's missing (it needs `sudo`), installs
`OPENFOAM_PACKAGE` (default `openfoam2512`, about 1 GB), checks that XFOIL and OpenFOAM
work and prints the matching `.env` values. `--uninstall` only removes packages that
`--install` itself installed, then runs `apt-get autoremove`.

**XFOIL** runs wherever `XFOIL_RUNNER` points (`ws` or `local`), through the same
`CommandRunner` interface. One polar takes about a second, so CPU isn't the deciding
factor. Running it on the work station is the default because serverless hosts (like Vercel)
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
- Set the backend's variables (`MONGODB_TARGET=remote`, `MONGODB_REMOTE_*`, provider keys, `WS_*`, ...) in the Vercel
  project. `MONGODB_REMOTE_URI` must be reachable from Vercel, e.g. MongoDB Atlas; a local
  Docker container isn't. XFOIL can't be installed on Vercel, so use `XFOIL_RUNNER=ws`.
- There's no authentication yet. Add it before exposing the API publicly.

### Development

```bash
cd backend
uv run ruff check . && uv run ruff format .
MONGODB_LOCAL_URI=mongodb://<host>:27017 uv run pytest
```

Tests share one throwaway database per run, emptied between tests. (A database per test
creates enough files to exhaust MongoDB's open-file limit, which is 1024 in a default
Docker container, and crash it. Start the container with
`--ulimit nofile=65536:65536` if you see "Too many open files".)

API tests use a throwaway database and a scripted chat model in place of Mistral; they
skip when MongoDB isn't reachable.
