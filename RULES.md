# ⚖️ Strict Rules — LoomCore

The central set of project rules. Any violation means changes are **not
accepted** until fixed. Read before making any edits.

> ⚠️ **Before editing: understand the difference between the Core (Hub) and
> Plugins.** The core is the "brain", plugins are the "hands". Do not mix their
> logic.

---

## 0. AI communicates with the developer in English

All AI chat messages (explanations, reports, questions, errors) are **strictly
in English**. Code identifiers are in English. Comments and project
documentation are in English. Commit messages are **in English**.

Task specs live in files named `TZ_RU.md` and reports in `REPORT_RU.md`
(historical filenames); their **content is English**, like everything else.

---

## 0a. Coordinator vs. executor (pair work)

- **Coordinator** sets tasks (per the spec), reviews the result, and **commits**.
- **Executor** executes the task per the spec, **does not commit**, and does not
  go beyond the task; the coordinator commits after acceptance.
- **All task messages and all executor reports are only in fenced code
  blocks** (``` ```) so they can be copied without losing structure.
- The executor report (in a code block) contains: what was done, what was
  verified, changed files, notes/risks, and the exact final line
  `Commits created: none`.
- The executor does not take a new task until the report is submitted and the
  coordinator has confirmed acceptance.
- The executor does not silently make disputed/ambiguous decisions — they are
  recorded in the report and the coordinator decides.
- The coordinator's workspace and recovery instructions are in
  `Agents/README.md`. Executors are not coordinators and do not use it; they
  work from the task spec.

---

## 0b. Core vs. plugins: the dividing line

- **Core (`src/`)** — the "brain": HTTP/WebSocket transport, auth, sessions,
  LLM router, the plan-and-execute agent, the plugin manager, the registry.
- **Plugins (`plugins/<id>/`)** — the "hands": actual work with the system
  (WireGuard, host operations, services, files). They run as separate
  subprocesses.
- New system functionality belongs in a plugin. The core only grows when the
  capability is needed by **all** plugins (auth, sessions, registry, logging).

---

## 0c. Interfaces and ports

**LoomCore** is a server with a centralized API. Interfaces are clients.

| Interface | Protocol | Port (Dev) | What it is |
|---|---|---|---|
| **Web UI** | WebSocket + REST | `8000` | Main dashboard: chat, monitoring, plugin management. |
| **Frontend** | HTTP (Next.js) | `3000` | The hub client; talks to the hub API. |
| **API** | HTTP / REST | `8000` | Internal core API for the Web UI, the frontend and plugins. |
| **Postgres** | TCP | `5432` | Database (bound to `127.0.0.1` only). |

**Rules:**

- There is one codebase (the LoomCore). Interfaces are clients only.
- The Web UI and the frontend work with the same hub API.
- Do not write logic tied to an interface type (except for output format).
- On the VDS, only nginx (`80`/`443`) is public; the hub, frontend and Postgres
  bind to loopback.

---

## 0d. Business logic and the role of AI

LoomCore is an infrastructure orchestrator.

- **Hub (Core)**: orchestration, history storage, routing requests to the LLM,
  session management, approval and audit.
- **Plugins**: actual work with the system (hardware, network, files, services).
- **AI (LLM)**: the "brain". It translates human language into structured tool
  calls (MCP/JSON-RPC).
- A plugin **must not know** who called it (Web UI, frontend, API). It receives
  JSON and returns a result.

---

## 0e. We work only inside the project

- AI works ONLY in the project folder (where this `RULES.md` lives) and its
  subfolders. Outside the project (Desktop, home folder, other projects) it does
  not read, create, or modify files.
- All AI working files (drafts, configs, logs, reports) go ONLY into `tmp/`
  (in `.gitignore`). The coordinator workspace is `Agents/`.
- Acting outside the project is allowed only on the owner's explicit request.
- Switching to another project is allowed only with the owner's explicit
  permission.

---

## 0f. Deleting files — only to tmp/_trash

- Do NOT delete anything without the owner's explicit permission: even if a file
  looks like "garbage" — check the documentation first. When in doubt, ask.
- If the owner authorized deletion, the file is NOT physically erased but moved
  to the project trash `tmp/_trash/`.
- All of `tmp/` is in `.gitignore`.

---

## 1. LoomCore is a broker and orchestrator: the core is thin, everything else is plugins

> **Important:** the LoomCore (`src/`) is transport and the "brain" only. All
> system logic (network, files, services) lives in **Plugins**.

- **Core (`src/`)**: API, WebSocket, Postgres, LLM router, plugin manager,
  auth, sessions, audit.
- **Plugins (`plugins/`)**: separate processes communicating via **MCP
  (Model Context Protocol)**.
- New functionality = a new plugin (or an edit to an existing one), not core
  bloat.
- Only what all plugins need goes into the core: auth, sessions, plugin
  registry, logging, contracts.
- `main.py` is only the create-app/uvicorn launcher; routes live in `src/api/*`.

---

## 2. Plugin isolation: a plugin is a subprocess

- If a plugin crashes, **the core keeps running**. A plugin crash (segfault,
  infinite loop) must not take down FastAPI.
- A plugin runs as a separate process (subprocess); launch is
  `sys.executable plugins/<id>/main.py`.
- Communication: **MCP over stdio**. The core writes a JSON-RPC request to the
  plugin's `stdin` and reads `stdout`.
- **MCP contract rules:**
    - A plugin must handle `initialize` and `tools/call` (and advertise tools
      via `tools/list`).
    - A plugin must return a JSON-RPC response.
    - Call timeout: 30 seconds. If the plugin does not respond — kill the
      process and log it.
- If a plugin crashes, the manager restarts it automatically (reboot logic).
- A plugin never writes to the core tables (`users`, `sessions`, `messages`,
  `audit_log`) or another plugin's schema. Its own relational data goes only in
  its own PostgreSQL schema; its files go only in its own plugin directory.
- Plugin routes (if any) are always protected by core middleware (authorization).
  A plugin does not expose itself.

---

## 3. Ports and services are fixed

- **API / Web — always `8000`** (`uvicorn src.main:app`).
- **Frontend — `3000`** (Next.js standalone).
- **Postgres — `5432`** (bound to `127.0.0.1`; not published publicly).
- **Nginx (reverse proxy) — `80`/`443`** (the only public entrypoint; also
  proxies the WebSocket).
- New services (Redis, queues, brokers) — only with the owner's permission.
  Pick a free port and check `ss -tlnp` first.
- Do not touch other projects' ports or processes.

---

## 4. Docker discipline: other projects live on the machine

The host (VDS) may run other containers.

- **Never** stop, restart, rename, or remove other people's containers, networks
  or volumes.
- All LoomCore resources use the `loom_` / `loom-` prefix:
    - Containers: `loom_core`, `loom_db`, `loom_frontend`, `loom_nginx`.
    - Volumes: defined in `docker-compose.yml` (compose prefixes them with the
      project name) — do not rename them.
- `hub`, `frontend` and `nginx` use `network_mode: host` so WireGuard and NAT
  live in the host network namespace; the DB stays on the compose network.
- Only nginx publishes a public port. The hub (`8000`), frontend (`3000`) and DB
  (`5432`) bind to `127.0.0.1` only.
- Before creating anything new: check `docker ps -a`, `docker volume ls` and
  `ss -tlnp` for name/port conflicts.

---

## 5. One project — one folder — one database

- The single working copy is this folder. Do not run code from copies
  (e.g. `LoomCore_backup/`); each copy would come up with a different state.
- Launch: via `docker compose up -d --build` (main method for Dev and Prod), or
  `uvicorn src.main:app` for a local run.
- Data lives in Postgres (`loom_db` container). Code reaches the DB via
  `DATABASE_URL`.
- `tmp/` — drafts and logs, not tracked by git.

---

## 6. Database (PostgreSQL)

- The primary DBMS is **PostgreSQL in Docker** (`docker compose up -d db`).
- The schema is managed via **SQLAlchemy models + Alembic migrations
  (migrations only — never `create_all` in production code paths)**. The app
  startup and `docker-compose.yml` do **not** run migrations;
  `deploy/install.sh` runs `alembic upgrade head`. For manual starts, run
  `alembic upgrade head` yourself.
- Core tables: `users`, `sessions`, `messages`, `audit_log`.
    - `users` — identity, provider (`web`/`telegram`), role (`admin`/`user`).
    - `sessions` — chat context (session id, user id, status).
    - `messages` — conversation history (`role`: `user`/`assistant`/`system`/
      `tool`/`plan`), `tool_calls` JSONB, `tool_result`.
    - `audit_log` — append-only, hash-chained record of every mutation the
      agent performs (no foreign keys; survives deletions).
- Timestamps are stored as **naive UTC ISO strings**; compare against naive UTC
  only. All dates are UTC.
- Plugin relational data: the plugin creates and owns its own schema
  (`CREATE SCHEMA IF NOT EXISTS`) and reads the dialect from the engine.
- Additive model changes only (new tables/columns with defaults); every change
  ships with an Alembic migration.

---

## 7. Plugin contract (MCP)

Each plugin is a separate project in `plugins/<id>/`. Main file: `main.py`.

- **Manifest:** every plugin ships a `manifest.json` (id, name, version,
  description, entry, protocol, permissions, env, `default_enabled`) — schema:
  `docs/architecture/PLUGIN_MANIFEST.md`. The core reads and validates it
  (identity, version, permissions, `default_enabled`, `requires_core`,
  `protocol`) and **warns**; a mismatch never blocks startup except that a
  structurally invalid manifest makes discovery skip the plugin.
- **Start:** the script is launched by the hub and reads `stdin`.
- **Initialization:** the plugin answers `initialize`.
- **Tools schema:** the plugin reports its tools (JSON Schema) via `tools/list`.
  Example:
  ```json
  {
    "name": "wg_interface_status",
    "description": "Show WireGuard status",
    "inputSchema": {"type": "object", "properties": {}, "required": []}
  }
  ```
- **Call:** the hub writes to `stdin`:
  `{"method": "tools/call", "params": {"name": "wg_interface_status"}}`.
- **Result:** the plugin writes to `stdout`:
  `{"result": {"content": [{"type": "text", "text": "Peers: ..."}]}}`.
- **Risk declarations:** every advertised tool declares `risk`
  (`safe`/`moderate`/`dangerous`). The core uses it verbatim to decide whether a
  step needs approval; a declared risk overrides the core's name heuristic.
  Core-only tools are marked `internal: true` and are never shown to the LLM.
  The bundled plugins must declare risk for **every** tool with no stale entries.
- **Least privilege:** a plugin must not have root on the host or broad
  capabilities unless strictly necessary. Prefer a regular user or a container
  with only the required mounts. `SYS_PTRACE` and `DAC_READ_SEARCH` are not
  requested by design.
- **Adding a plugin:** copy `docs/architecture/plugin-template/` into
  `plugins/<Id>/`, set `id` to the folder name, implement `TOOLS_SCHEMA` +
  `HANDLERS`, declare permissions/env, add tests under `tests/`, and update
  `docs/plugins/<Id>.md`. See `docs/architecture/PLUGIN_MANIFEST.md`.

---

## 8. Dependencies and environment

- The project uses **`uv`** with `pyproject.toml` + `uv.lock`. Install with
  `uv sync` (or `uv sync --extra dev`).
- Python **≥ 3.11**.
- Dependencies are declared in `pyproject.toml`; do not install packages ad hoc
  or bypass the lockfile. Do not create extra virtual environments.
- The Docker image installs dependencies in isolation.
- Secrets never live in code or commits.

---

## 9. Tests are mandatory

- Linter: `ruff check .` must be clean.
- Fast unit run (no DB, no WireGuard):
  `pytest -m "not integration and not e2e"`.
- Full run: `pytest` (markers: `integration` needs Postgres; `e2e` needs Linux +
  WireGuard). Integration smoke:
  `LOOMCORE_INTEGRATION=1 pytest tests/test_smoke_integration.py`.
- A single test: `pytest tests/test_rate_limit.py::test_rate_limit_429_response_body`.
- Frontend: `npm ci`, then `npx tsc --noEmit` and `npm run build` (the build
  ignores TS errors, so type-check separately).
- Tests are code and live in the repository (`tests/`, plugin tests as needed).
  Do not leave unverified code: the server must start and chat/plugins must work.
- CI is `.github/workflows/ci.yml` (`ruff` + `pytest` + frontend). Do not leave
  the pipeline red.
- Large test output goes to `tmp/tests_*.log`; report only a summary and error
  lines in chat.

---

## 10. One file = one responsibility

- `src/api/` — routes.
- `src/core/` — logic (agent, contracts, security, audit, rate limit).
- `src/llm/` — LLM providers (Hetzner) and prompts.
- `src/plugins/` — plugin process management, manifests, state, health.
- `src/interfaces/` — WebSocket and event definitions.
- `src/db/` — models and session helpers.
- `main.py` contains no logic except the create-app/uvicorn launcher.
- No 1000-line monoliths. Duplicated code goes into a shared core module.

---

## 11. Frontend (Next.js)

`frontend/` is a Next.js **16** standalone client. Rules:

- Read `frontend/AGENTS.md` before touching `frontend/`. This version differs
  from your training data; consult `frontend/node_modules/next/dist/docs/` before
  writing code. Do not delete the `<!-- BEGIN:nextjs-agent-rules -->` block.
- Use `npm` (matching the Dockerfile): `npm install`, `npm run dev` (port
  `3000`), `npm run build`.
- `frontend/next.config.mjs` sets `typescript.ignoreBuildErrors: true` — TS errors
  do not break `next build`; always type-check separately with
  `npx tsc --noEmit`.
- Use the existing UI stack (shadcn components, Tailwind, `@tanstack/react-query`)
  and the existing `frontend/lib/*` API helpers; do not invent parallel patterns.
- UX: every action gives feedback (success/error); destructive actions ask for
  confirmation; no dead buttons; mobile and desktop share one codebase with
  responsive layouts.
- Frontend work is not done until `npx tsc --noEmit` and `npm run build` pass.

---

## 12. Security and AI guardrails (critical)

This is the most important part of LoomCore. The AI has access to server
management.

- **Approvals:** "read" operations (status, logs) run directly; "write"
  operations (reboot, config changes, command execution) run only after the user
  approves. See §13 for the plan-level invariant.
- **Rate limiting:** REST limits are enforced with slowapi (chat 30/min, auth
  5/min, etc.); plugin calls are bounded by timeouts. Do not remove limits to
  make a test pass.
- **API keys:** keys for Hetzner/LLM and the Telegram bot live only in `.env`
  (or Docker secrets), never in code or commits.
- **TLS:** `REQUIRE_TLS=true` refuses every admin auth path over plain HTTP;
  `SECURE_COOKIES=true` marks the session cookie Secure. Do not weaken these on
  a deployment.
- **Auth modes:** token (`ADMIN_TOKEN`), JWT (when `ADMIN_PASSWORD_HASH` +
  `ADMIN_TOTP_SECRET` + `SESSION_SECRET` are set), or both. An empty
  `SESSION_SECRET` with JWT enabled must fail fast.
- **Audit:** every mutating agent action is appended to the hash-chained
  `audit_log`; do not add mutation paths that skip it.
- **Isolation:** plugin capabilities are declared in `manifest.json` and kept to
  the minimum. Do not add `privileged`, the Docker socket, `SYS_PTRACE` or
  `DAC_READ_SEARCH`.

---

## 13. Agent and LLM invariants

The assistant is a conversational-first plan-and-execute agent. These are
structural guarantees, not prompt behaviour (see `docs/architecture/AGENT.md`):

- The planner LLM is offered exactly **one** callable tool, `agent_plan`; the
  real tool catalog is passed as context only.
- The deterministic executor in `src/core/agent.py` is the sole dispatcher. It
  clamps the mode (`plan` when declared or more than one step, else `direct`),
  classifies each step's risk, and replays approved steps **verbatim**.
- **Safety invariant:** a non-safe step runs only when the plan status is
  `approved`.
- `src/llm/SOUL.md` is **voice only** (identity, tone, format). It must not
  carry behaviour, safety, tool or approval rules.
- Direct actions that are not safe are confirmed in plain text
  (`src/core/confirmations.py`, phrases in `src/core/confirmations.json`).
- Plans persist in `messages.tool_calls` (JSONB, `role="plan"`); a changed goal
  is a new plan.

---

## 14. Changelog — keep it short

`CHANGELOG.md` — date + summary, 1–2 lines per entry. Details live in the docs
(or the task report). Do not write long walls of text in the changelog.

---

## 15. Documentation

The project root is closed: only `README.md`, `RULES.md`, `AGENTS.md`,
`CHANGELOG.md`, `docker-compose.yml`, `Dockerfile`, `pyproject.toml`, `uv.lock`,
`alembic.ini`, `.env.example`, `.gitignore` (plus the standard project folders
`src/`, `plugins/`, `frontend/`, `docs/`, `deploy/`, `tests/`, `scripts/`,
`alembic/`, and the coordinator workspace `Agents/`). Do not create new files or
folders in the root.

Documentation lives in `docs/` and follows the map in `docs/README.md`:

```
docs/
  README.md                 map: what goes where
  architecture/             MCP protocol, plugin manifest, agent loop, templates
  plugins/                  per-plugin docs (LoomWG.md, LoomOps.md, ...)
  setup/                    deployment, VDS runbook, testing
  tasks/<name>/             task specs and reports, paired (rule 16)
  tasks/_template/          blank TZ and REPORT templates
```

- A new `docs/` section is created only for a new **kind** of activity (a new
  plugin, new infrastructure), not for every task.
- No dumps, no duplicates, no `-2`/`final`/`new` copies, no empty placeholder
  folders. Unsure where something goes → read `docs/README.md`; still unsure →
  ask the owner.
- Drafts and throwaway notes go in `tmp/`, not in `docs/`.
- Changed behaviour → update the affected doc in the same commit.

---

## 16. Tasks: specs and reports

- Large tasks (a new plugin, a migration, a behaviour rework) start with a spec
  **before** work begins: copy `docs/tasks/_template/TZ_RU.md` to
  `docs/tasks/<name>/TZ_RU.md` and fill it in.
- Folder name is kebab-case by meaning (`agent-loop`, `plugin-security`). No
  dates or numbers — chronology lives in git.
- On completion, put the report `REPORT_RU.md` (template
  `docs/tasks/_template/REPORT_RU.md`) in the **same** folder. No report means
  the task is not accepted.
- The report contains: what was done / what was verified / changed files / test
  results / risks. Secrets (passwords, tokens, credentials) — never.
- Development order: (1) prototype/mock to validate the UI, (2) LLM
  integration, (3) plugin connection (real logic).
- Small fixes (typo, one-line fix) need no spec — a commit and a
  `CHANGELOG.md` line are enough.

---

*Last updated: 2026-09-19 (LoomCore architecture: FastAPI + Postgres + MCP
plugins + plan-and-execute agent)*
*Keep in the project root.*
