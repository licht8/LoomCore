# ⚖️ Strict Rules — LoomCore

The central set of project rules. Any violation means changes are **not
accepted** until fixed. Read before making any edits.

> ⚠️ **Before editing: make sure you understand the difference between the Core (Hub) and Plugins.**
> The core is the "brain", plugins are the "hands". Do not mix their logic.

---

## 0. AI communicates with the developer in English

All AI chat messages (explanations, reports, questions, errors) are **strictly in English**.
Code identifiers are in English. Comments and project documentation are in
English. The UI is ru/en via dictionaries (item 12).
Commit messages are **in English**.

---

## 0a. Coordinator vs. worker (pair work)

- **Coordinator** sets tasks (per the spec), reviews the result, and **commits**.
- **Worker** executes the task per the spec and **does not commit** and does not
  go beyond the task; the coordinator commits after acceptance.
- **All task messages and all worker reports are only in fenced code
  blocks** (``` ```) so they can be copied without losing structure.
- The worker report (in a code block) contains: what was done, what was verified,
  changed files, notes/risks, and the phrase "Коммит не создавал".
- The worker does not take a new task until the report is submitted and the
  coordinator has confirmed acceptance.
- The worker does not silently make disputed/ambiguous decisions — they are recorded
  in the report and the coordinator decides.
- The coordinator's workspace is `Agents/README.md`
  (coordinator instructions).

---

## 0c. Interface architecture (Web & Telegram)

**LoomCore** is a server with a centralized API. Interfaces are clients.

**Mapping table: interface ↔ port/protocol (important for AI!):**

| Interface | Protocol | Port (Dev) | What it is |
|---|---|---|---|
| **Web UI** | WebSocket + REST | `8000` | Main dashboard. Chat, monitoring, plugin management. |
| **Telegram** | Bot API (Polling/Long Poll) | Internal | Bot assistant. Management "on the go". |
| **API** | HTTP / REST | `8000` | Internal core API for the Web UI and plugins. |

**Rules:**
- There is one codebase (the LoomCore). Interfaces are clients only.
- The Web UI and Telegram work with the same hub API.
- Do not write logic tied to an interface type (except for output format).

## 0d. Business logic and the role of AI

**LoomCore** is an infrastructure orchestrator.
- **Hub (Core)**: orchestration, history storage, routing requests to the LLM, session management.
- **Plugins**: actual work with hardware (WireGuard, SSH, Nginx).
- **AI (LLM)**: the "brain". Translates human language into JSON tool calls (MCP).
- A plugin **must not know** who called it (Web or Telegram). It receives JSON and returns a result.

---

## 0e. We work only inside the project

- The bot works ONLY in the project folder `LoomCore/` (where this `RULES.md` lives)
  and its subfolders. Outside the project (desktop, home folder, other projects) do
  not read, create, or modify files.
- All bot files (configs, docs, reports) go ONLY into the internal `tmp/` folder (in `.gitignore`).
- Acting outside the project is only allowed on the owner's explicit request.
- Switching to another project is only allowed with the owner's explicit permission.

---

## 0f. Deleting files — only to tmp/_trash

- Do NOT delete anything without the owner's explicit permission: even if a file
  looks like "garbage" — check the documentation first. When in doubt, ask.
- If the owner authorized deletion, the file is NOT physically erased but moved
  to the project trash `tmp/_trash/`.
- All of `tmp/` is in `.gitignore`.

---

## 1. LoomCore is a broker and orchestrator: the core is thin, everything else is plugins

> **Important:** The LoomCore (in `src/`) is transport and the "brain" only.
> All system logic (network, file, service management) lives in **Plugins**.

LoomCore is a control center (Hub).
- **Core (`src/`)**: web interface, API, Postgres, LLM Router, Plugin Manager.
- **Plugins (`plugins/`)**: separate processes (subprocess) communicating via **MCP (Model Context Protocol)**.

- New functionality = a new plugin (or an edit to an existing one), not core bloat.
- Only what all plugins need goes into the core: auth, sessions, plugin registry, logging.
- Root `main.py` is only the uvicorn launcher.

## 2. Plugin isolation: a plugin is a subprocess

- If a plugin crashes, **the core keeps running**. A plugin crash (segfault, infinite loop)
  must not crash FastAPI.
- A plugin runs as a separate process (subprocess) with limited resources (memory limit in Docker).
- Communication: **MCP over stdio**. The core writes a JSON request to the plugin's
  `stdin` and reads `stdout`.
- **MCP contract rules:**
    - A plugin must handle `tools/call` (tool invocation) and `initialize`.
    - A plugin must return a JSON-RPC response.
    - Call timeout: 30 seconds. If the plugin does not respond — kill the process and log it.
- Plugin routes (if any) are always protected by core middleware (authorization). A plugin does not "expose" itself.
- If a plugin crashes, the Manager must restart it automatically (reboot logic).

## 3. Ports and services are fixed

- **API / Web — always `8000`** (`APP_PORT`).
- **Postgres — `5432`** (inside the Docker container). It is not published directly
  outside (on the VDS), only via the Docker network.
- **Nginx (reverse proxy) — `80`/`443`** (for public access).
- New services (Redis, queues) — only with the owner's permission. Pick a free port.
- Do not touch other people's ports.

## 4. Docker discipline: other projects live on the machine

The host (VDS) may run other containers.

- **Never** stop, restart, or remove other people's containers.
- All LoomCore resources use the `loom_` / `loom-` prefix:
    - Containers: `loom_core`, `loom_worker_...`, `loom_db`.
    - Volumes: `loom_db_data`.
    - Network: `loom_default`.
- On the VDS, ports are published only through Nginx (reverse proxy); never expose port 8000 directly.
- Before creating anything new: check `docker ps` for conflicts.

## 5. One project — one folder — one database

- The single working copy is this folder. Do not run code from copies (e.g. `LoomCore_backup/`).
- Launch: via `docker compose up -d` (the main method for Dev and Prod).
- Data lives in Postgres (`loom_db` container). Code reaches the DB via `DATABASE_URL`.
- `tmp/` — drafts, not tracked by git.

## 6. Database (PostgreSQL)

- The primary DBMS is **PostgreSQL in Docker**.
- The schema is managed via **SQLAlchemy** + **Alembic** (migrations).
- **`plugins` table**: stores status (running/stopped), versions, logs.
- **`sessions` table**: stores chat context (session ID, user ID).
- **`messages` table**: conversation history (role: user/ai/tool).
- **`tool_logs` table**: (optional) log of MCP tool calls (who, what, result).
- All dates are UTC.

## 7. Plugin contract (MCP)

Each plugin is a separate project in `plugins/<id>/`.
Main file: `main.py`.

- **Start:** the script is launched by the hub -> reads `stdin`.
- **Initialization:** it sends JSON `{"method": "initialize", ...}`.
- **Tools Schema:** the plugin reports its tools (JSON Schema) to the hub via `tools/list`.
  Example schema:
  ```json
  {
    "name": "wg_status",
    "description": "Show WireGuard status",
    "inputSchema": {"type": "object", "properties": {}, "required": []}
  }
  ```
- **Call:** the hub writes to `stdin`: `{"method": "tools/call", "params": {"name": "wg_status"}}`.
- **Result:** the plugin writes to `stdout`: `{"result": {"content": [{"type": "text", "text": "Peers: ..."}]}}`.
- **Security rule:** a plugin must **not** have root on the host unless strictly
  necessary. Prefer running the plugin as a regular user (or in a container with the required mounts).

## 8. Virtual environment

- Only `.venv` in the root.
- All dependencies in `requirements.txt` (or `pyproject.toml`).
- The Docker image (`Dockerfile`) must install dependencies in isolation.

## 9. Tests are mandatory

- **Smoke test:** a script that checks: "port 8000 responds" and "Postgres is reachable".
- **Unit tests:** tests for `src/core/router.py` (LLM logic).
- **Plugin test:** a minimal test for each plugin: "start the process -> send JSON -> get a response".
- Test results in `tmp/tests_*.log`.

## 10. One file = one responsibility

- `src/api/` — routes.
- `src/core/` — logic.
- `src/llm/` — providers (Hetzner, OpenRouter).
- `src/plugins/` — process management.
- No code in `main.py` except `app = FastAPI(...)`.

## 11. Interface (Web UI & Telegram)

- **UI:** modern, dark theme (admin panel), chat-focused.
- **Mobile-first:** the web UI must work well on a phone (like Telegram Web).
- **No "dead zones":** if a command cannot be executed (no permissions, plugin asleep) — show an explicit error.

## 12. i18n (Localization)

- All interface strings — ru/en.
- In the database (`messages` table), messages are stored exactly as the user wrote them.
- LLM responses are also stored as-is.
- Localization applies to UI components ("Start", "Status" buttons).

## 13. Security and AI Guardrails (Critical!)

This is the most important part of LoomCore. AI has access to server management.

- **Approvals:**
    - "Read" operations (status, logs) -> AI performs them itself.
    - "Write" operations (reboot, config changes) -> AI forms the request but **waits for
      the user's confirmation** ("Approve" button in chat/Web).
- **Rate Limiting:**
    - Limit the frequency of requests to plugins (so AI does not loop).
    - Limit requests to the LLM API (Hetzner/OpenRouter).
- **API Keys:**
    - Keys for Hetzner/OpenRouter — only in `.env` (docker secrets). Not in code.
    - Telegram Bot keys — only in config.
- **Network Isolation:**
    - The LoomWG plugin (if it manages the network) may require privileges. Other plugins get minimum rights.

## 14. Changelog — keep it short

`CHANGELOG.md` — date + summary. Details — in the docs.

## 15. Documentation

- The root is closed (`README.md`, `RULES.md`, `docker-compose.yml`, `Dockerfile`).
- Documentation lives in `docs/`:
    - `docs/architecture/` — diagrams, MCP protocol description.
    - `docs/plugins/` — plugin specs.
    - `docs/setup/` — VDS deployment instructions.

## 16. Tasks: specs and reports

- Large tasks start with a spec (`docs/tasks/<name>/TZ_RU.md`).
- On completion — a report (`docs/tasks/<name>/REPORT_RU.md`).
- **Development order:**
    1. Prototype (Mock API) — to validate the UI.
    2. LLM integration.
    3. Plugin connection (real logic).

---

*Last updated: 2026-09-12 (LoomCore Architecture: FastAPI + Postgres + MCP Plugins + Hetzner LLM)*
*Keep in the project root.*
