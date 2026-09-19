# Changelog

Short entries only: date + one or two lines. Details live in `docs/` or in the
task report (RULES 14).

## 2026-09-19

- Rewrote `RULES.md` for the current LoomCore architecture (thin core + MCP
  plugins + plan-and-execute agent).
- Added coordinator workspace (`Agents/README.md`), docs map (`docs/README.md`)
  and task templates (`docs/tasks/_template/`).
- Fixed admin credential precedence: a stale/invalid session cookie no longer
  shadows a valid `Authorization: Bearer` token (`extract_admin_token`,
  `/api/auth/session`).
