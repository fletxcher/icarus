# Icarus

An AI engineering assistant. Describe what you need (a NACA profile, a polar at some Reynolds
number, a CFD run, a bracket to design and check) and Icarus runs the real solvers and shows its
reasoning, every tool call with its result, and the answer.

- `frontend/`: Next.js + shadcn/ui chat interface
- `backend/`: FastAPI service that stores chats in MongoDB and runs a LangGraph agent with tools
- `workstation/`: scripts and the Docker image that run the solvers on a remote machine

Configuration for both lives in the `.env` at the repo root.

## What it can do

| Tool | Used for | Links |
|---|---|---|
| XFOIL | Fast 2D airfoil polars | [Home page](https://web.mit.edu/drela/Public/web/xfoil/) |
| OpenFOAM (ESI) | 2D and 3D CFD (incompressible RANS) | [openfoam.com](https://www.openfoam.com) · [Documentation](https://www.openfoam.com/documentation) |
| CalculiX | Finite element analysis (structural and thermal) of uploaded solids | [calculix.de](https://www.calculix.de) |
| gmsh | Meshing for the CFD cases | [gmsh.info](https://gmsh.info) |
| ParaView, ffmpeg | Flow images and video | [paraview.org](https://www.paraview.org) · [ffmpeg.org](https://ffmpeg.org) |
| build123d | Designing parts from code | [build123d](https://github.com/gumyr/build123d) |

Plots are drawn from Plotly specs, and users can attach text, CSV, STL, OBJ, STEP and PDF files.
Simulations run on a machine the user connects; the chat is told when a job ends.

## Running it

**Frontend**

```bash
cd frontend
npm install
npm run dev   # http://localhost:3000
```

**Backend**

```bash
cd backend
uv sync
uv run api   # http://localhost:8000, docs at /docs
```

Every setting is in `backend/src/api/config.py`. The API starts without a model provider key;
sending a message returns 503 until one is set.

Main `.env` settings:

| Setting | Purpose |
|---|---|
| `MISTRAL_API_TOKEN`, `OPENROUTER_API_KEY`, `OLLAMA_BASE_URL` | Model providers. Users can also add their own keys in Settings |
| `DEFAULT_MODEL` | Model chosen when the user doesn't pick one |
| `MONGODB_TARGET` | `local` (default) or `remote`, with `MONGODB_LOCAL_URI` / `MONGODB_REMOTE_URI` and the matching `_NAME` |
| `CREDENTIALS_KEY` | Encrypts users' saved keys and SSH keys. Keep a copy: losing it makes them unreadable |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional "Sign in with Google" (see below) |
| `XFOIL_RUNNER` | `ws` (a connected machine) or `local` (this server, needs `xfoil` and `xvfb`) |
| `ALLOW_PRIVATE_WORKSTATIONS` | Set `false` on a public server to refuse private network addresses |

Generate a credentials key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

**Sign in with Google.** In the [Google Cloud console](https://console.cloud.google.com/apis/credentials)
create an OAuth client ID of type *Web application* with the redirect URI
`{API_PUBLIC_URL}/api/auth/google/callback` (locally `http://localhost:8000/api/auth/google/callback`),
put the id and secret in `.env`, and set `API_PUBLIC_URL` and `FRONTEND_URL`. While the consent
screen is in "Testing", add the allowed accounts as test users.

**MongoDB.** A local MongoDB or a remote one such as Atlas both work. A local `icarus-db`
container doesn't publish its port by default, so start it with `-p 27017:27017`.

## Connecting a machine

Icarus runs simulations on a machine the user already owns, reached over SSH. It doesn't run them on
the server or on the computer the browser is on. The machine must be Linux (x86-64 or ARM64) with at
least 4 GB of memory and 15 GB of free disk.

In Settings → Work station, add a machine with its user, address, port and password, and approve its
SSH fingerprint. The backend then:

1. logs in once with the password and installs an SSH key it made itself, so the password isn't kept;
2. pins the machine's host key, and connects only with that key from then on;
3. copies the runner scripts to `~/icarus` and checks what's installed;
4. offers **Set up this machine**, which installs Docker if needed and gets the solver image.

Users can save several machines and connect one at a time. Each job is saved with the machine it
started on and stays there. Removing a machine cleans it up over SSH (removes the image, job folders
and Icarus's key) before forgetting it.

Secrets (API keys, SSH private keys) are encrypted with `CREDENTIALS_KEY`, never returned by the
API, and never logged. The setup password is used for the login and for `sudo` during setup and then
discarded.

### The solver image

All solvers run in one Docker image (`workstation/Dockerfile`), tagged
`icarus-solvers:<hash of the Dockerfile>`. Setup first tries to pull it from
`ghcr.io/fletxcher/icarus-solvers` and builds it on the machine only if the pull fails (set
`ICARUS_REGISTRY=none` to always build).

The `solver-image` GitHub workflow builds and publishes the image whenever `workstation/Dockerfile`
changes, or on demand from the Actions tab (ARM64 is optional, because it's slow). The package needs to
be public so machines can pull it without a login.

To set a machine up by hand, run this on it:

```bash
./setup.sh --install     # installs Docker if needed, then gets the solver image
./setup.sh --uninstall   # removes what an install added (asks before deleting job data)
```

Everything that runs a solver goes through `bin/icarus-exec`, which runs the command in the container
as the calling user.

## How a simulation runs

Jobs are submit-and-poll, because they outlast any HTTP request: the backend writes `case.json` in a
job folder on the machine and starts a script detached (`run_cfd2d.sh`, `run_cfd3d.sh` or
`run_fea.sh`). The script keeps `status.json` up to date, and the backend reads it back. A watcher
adds a notice to the chat when a job ends, so the agent never has to wait. Each job has its own
page with progress, residuals, logs, results, images and flow video.

## Deployment

The root `vercel.json` deploys `frontend/` and `backend/` as two services on one domain (`/api/*` goes
to the backend). In production leave `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` unset, use a remote
MongoDB (`MONGODB_TARGET=remote`), set `CREDENTIALS_KEY` and `ALLOW_PRIVATE_WORKSTATIONS=false`, and
use `XFOIL_RUNNER=ws`, since XFOIL can't be installed on Vercel. Vercel functions have a maximum
duration, so long-running work (CFD, machine setup) must stay on the connected machine.

## Development

```bash
cd backend
uv run ruff check . && uv run ruff format .
MONGODB_LOCAL_URI=mongodb://<host>:27017 uv run pytest
```

API tests use a throwaway database and a scripted chat model, and skip when MongoDB isn't reachable.
Start MongoDB with `--ulimit nofile=65536:65536` if you see "Too many open files".

Database schemas are written out in `database/<collection>.json`, generated from
`backend/src/api/catalog.py`. After changing a definition, run `uv run export-schemas` in `backend/`
(a test fails if the files are out of date).

## License

MIT, see [LICENSE](LICENSE). The solvers in the work station image (OpenFOAM, CalculiX and the rest)
keep their own licenses.
