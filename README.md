# Icarus

An AI engineering assistant. Describe what you need (a NACA profile, a polar at some Reynolds
number, a part to design) and Icarus runs the tools and shows its reasoning, every tool call with
its result, and the answer.

- `frontend/`: Next.js + shadcn/ui chat interface
- `backend/`: FastAPI service that stores chats in MongoDB and runs a LangGraph agent with tools
- `workstation/`: the scripts the compute host needs

Configuration for both lives in the `.env` at the repo root.

## What it can do

| Tool | Used for | Links |
|---|---|---|
| XFOIL | Fast 2D airfoil polars | [Home page](https://web.mit.edu/drela/Public/web/xfoil/) |
| build123d | Designing parts from code, exported as STEP, STL and more | [build123d](https://github.com/gumyr/build123d) |
| Web search | Searches the web and reads pages, and cites what it used with links that are checked (see below) | [Tavily](https://tavily.com) or [Brave Search](https://brave.com/search/api/) |
| Python | Code the agent writes and runs in a sandbox: numpy, scipy, pandas, scikit-learn and Cantera (combustion, equilibrium, flame temperatures) | [Cantera](https://cantera.org) |

Plots are drawn from Plotly specs, and users can attach text, CSV, STL, OBJ, STEP and PDF files.
The tools run on one compute host that the server reaches over SSH, not on the user's computer.
(The earlier version also ran OpenFOAM, CalculiX and ParaView on machines users connected; that
code is in the git history at the commit `checkpoint: full solvers`.)

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
| `OPENROUTER_API_KEY`, `OLLAMA_BASE_URL` | Model providers the server supplies. Users add their own OpenAI, Anthropic, OpenRouter or Ollama keys in Settings |
| `DEFAULT_MODEL` | Model chosen when the user doesn't pick one |
| `MONGODB_TARGET` | `local` (default) or `remote`, with `MONGODB_LOCAL_URI` / `MONGODB_REMOTE_URI` and the matching `_NAME` |
| `CREDENTIALS_KEY` | Encrypts users' saved keys and SSH keys. Keep a copy: losing it makes them unreadable |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional "Sign in with Google" (see below) |
| `DAILY_MESSAGE_LIMIT` | Messages each user may send in any 24 hours (default 5; a prompt and its reply is one; 0 means no limit) |
| `WS_HOST`, `WS_SSH_KEY`, `WS_HOST_KEY` | The compute host the tools run on, reached over SSH. All three are needed (see below). `WS_USER` (default `icarus`), `WS_PORT` (22) and `WS_WORKDIR` (`~/icarus`) are optional |
| `XFOIL_RUNNER` | `ws` (the compute host) or `local` (this server, needs `xfoil` and `xvfb`) |
| `TAVILY_API_KEY` or `BRAVE_API_KEY` | Turns on web search for the agent (Tavily is used if both are set). Without one the agent has no web tools. `WEB_MAX_RESULTS` (6), `WEB_TIMEOUT_S` (8), `WEB_FETCH_MAX_BYTES` and `WEB_FETCH_MAX_CHARS` bound what it fetches |
| `HOST_MAX_CONCURRENCY`, `HOST_QUEUE_WAIT_S` | How many heavy jobs (the agent's Python, part builds) run on the compute host at once (1), and how long one waits for its turn before telling the user the host is busy (30 s) |
| `PYTHON_TIMEOUT_S`, `PYTHON_OUTPUT_MAX_CHARS` | Limits on the Python the agent runs: time per script (60 s) and how much output the agent sees (8000 characters) |
| `ALLOW_PRIVATE_WORKSTATIONS` | Set `false` on a public server so a user's Ollama address can't point at private networks |

Generate a credentials key with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.

**Sign in with Google.** In the [Google Cloud console](https://console.cloud.google.com/apis/credentials)
create an OAuth client ID of type *Web application* with the redirect URI
`{API_PUBLIC_URL}/api/auth/google/callback` (locally `http://localhost:8000/api/auth/google/callback`),
put the id and secret in `.env`, and set `API_PUBLIC_URL` and `FRONTEND_URL`. While the consent
screen is in "Testing", add the allowed accounts as test users.

**Daily message limit.** Each user gets `DAILY_MESSAGE_LIMIT` messages in any rolling 24 hours, to protect
the server's model keys. Sending, editing and regenerating all count; a reply that fails on the provider's
side is given back. Admins and models reached with the user's own API key (Settings) aren't counted. Make
an account an admin with `uv run make-admin you@example.com` in `backend/`. Over the limit the API answers 429,
and the composer shows how many messages are left (`GET /api/usage/messages`).

**MongoDB.** A local MongoDB or a remote one such as Atlas both work. A local `icarus-db`
container doesn't publish its port by default, so start it with `-p 27017:27017`.

## Web search and cited links

The agent's `web_search` and `fetch_page` tools run on the API server, not the compute host. Every
link in a search result is requested before the agent sees it, and dead ones are dropped. When the
agent finishes, every link in its answer is checked again (code blocks excluded): a link it got from
its tools in that chat is kept, any other is requested, and one that doesn't load (or points
at an address the server refuses to open) is replaced with "(link removed)" in the saved answer.
Sites that answer but refuse automated visitors (401, 403, 429) count as live. Pages are fetched
over http(s) on ports 80 and 443 only, never from a private, loopback or link-local address, and the
address is checked again at each redirect.

## The compute host

XFOIL and part builds run on one Debian or Ubuntu machine that the server reaches over SSH with a
key (the `WS_*` settings). Users don't connect machines of their own. Set it up by running this on
it, as the user the server logs in as:

```bash
./setup.sh --install     # xfoil, xvfb, the Python packages, bubblewrap and the scripts in ~/icarus
./setup.sh --check       # what is and isn't in place
./setup.sh --uninstall   # removes ~/icarus (the apt packages stay)
```

Then give the server the three settings. They are text, not file paths, so they work the same
locally and on Vercel (mark the key Sensitive there). Print the two values and paste them:

```bash
awk '{printf "%s\\n", $0}' ~/.ssh/icarus_vm      # WS_SSH_KEY: the private key, on one line
ssh-keyscan -t ed25519 203.0.113.7 2>/dev/null   # WS_HOST_KEY: the host's public key
```

```
WS_HOST=203.0.113.7
WS_SSH_KEY="-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl...\n-----END OPENSSH PRIVATE KEY-----\n"
WS_HOST_KEY="203.0.113.7 ssh-ed25519 AAAAC3Nz..."
```

The matching public key
(`icarus_vm.pub`) goes in the host's `~/.ssh/authorized_keys`. The server only connects when the
host presents exactly `WS_HOST_KEY`.

The agent's Python runs in a `bubblewrap` sandbox (`workstation/run-sandboxed`): no network (so no
internet, and not a cloud provider's metadata server either), no view of the machine's files
beyond a scratch directory, no environment variables, and limits on time, memory and processes.
If the sandbox can't start, for example where unprivileged user namespaces are turned off
(Ubuntu 24.04 restricts them by default), nothing is run: `./setup.sh --check` says so.

On a machine with under 2 GB of memory and no swap it also adds a swap file. It was tested on
Debian 12 (ARM64) in a container; the swap step and Ubuntu are untested.

## Deployment

The root `vercel.json` deploys `frontend/` and `backend/` as two services on one domain (`/api/*` goes
to the backend). In production leave `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` unset, use a remote
MongoDB (`MONGODB_TARGET=remote`), set `CREDENTIALS_KEY` and `ALLOW_PRIVATE_WORKSTATIONS=false`, and
use `XFOIL_RUNNER=ws`, since XFOIL can't be installed on Vercel, and point `WS_HOST` at a machine
the server can reach over the internet.

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

MIT, see [LICENSE](LICENSE). The tools installed on the compute host (XFOIL, build123d, Cantera and the rest)
keep their own licenses.
