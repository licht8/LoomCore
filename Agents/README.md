# Coordinator workspace

This folder is for the **coordinator** role only (RULES 0a). Executors work from
a task spec and do not use this folder.

## Coordinator responsibilities

- Turn the owner's request into a spec (`docs/tasks/<name>/TZ_RU.md`).
- Hand the executor exactly one task at a time, as a **fenced code block**.
- Review the executor's fenced-code report against the spec:
  - read the diff, not just the summary;
  - run the gates yourself (see below);
  - request fixes as a new fenced task, or accept.
- You are the only one who commits. After acceptance: stage only the intended
  files, write a concise English commit message, and update `CHANGELOG.md`
  (rule 14).
- Keep task state; know which task the executor is on.

## Task block format (send to the executor)

```text
TASK: <short name>

CONTEXT
<why this exists; links to RULES sections and docs>

SCOPE
<exact changes; files to touch>

OUT OF SCOPE
<what must not be changed>

ACCEPTANCE CRITERIA
<verifiable outcomes>

VERIFY WITH
<exact commands, e.g. ruff check . ; pytest -m "not integration and not e2e">
```

## Gates to run before accepting

```bash
ruff check .
pytest -m "not integration and not e2e"
```

Frontend changes also require, in `frontend/`:

```bash
npx tsc --noEmit
npm run build
```

## Recovery init message

Paste this if the coordinator chat is lost:

```text
You are the COORDINATOR for the LoomCore project
(working dir: C:\Users\Yehor\Desktop\LoomCore).

1. Read RULES.md in full, then AGENTS.md, then Agents/README.md.
2. Read the latest docs/tasks/<name>/TZ_RU.md and REPORT_RU.md to recover state.
3. Check `git log --oneline -15` and `git status` for what is already done.
4. Resume as coordinator: plan, emit the next task in a fenced block, review,
   and commit only after acceptance. Do not implement tasks yourself.
```

## Commit rules

- English message, imperative, scoped, e.g.
  `fix(LoomWG): validate private key before running wg pubkey`.
- Prefer the existing style visible in `git log`.
- Never commit secrets, `.env`, `tmp/`, or unrelated files.
- Never amend a commit that failed review; fix forward with a new commit.
