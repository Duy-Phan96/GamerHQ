# Awin Affiliate 0.9.3 — isolated GamerHQ acceptance plan

Status: **PREPARED — NOT RUN**. Owner: GamerHQ host repository. This is a staging/runbook document, **not** release approval.

## Evidence baseline

- Host integration: fresh branch `integration/awin-0.9.3-refresh` (target `develop`), superseding outdated PR #151.
- Awin Skill reviewed immutable source: `efdfc37256f69b1f868e6355c0d7906e0f85d078` (v0.9.3, Runtime API 1).
- Historical offline GitHub Actions run `39772806764`: Python 3.12 and 3.14 **PASS** on the earlier PR #151; **new refreshed-branch CI is required separately**.
- Awin live report: https://github.com/Duy-Phan96/gamerhq-skill-awin-affiliate/pull/40 remains **Draft / IN PROGRESS**.
- Awin host handoff: https://github.com/Duy-Phan96/gamerhq-skill-awin-affiliate/issues/41.

## Stage 0 — prerequisites (no live credentials needed)

1. Check Host release/version and compatible Runtime/SDK; verify the Skill pin with `python -m tools.skill_package_plan --json`.
2. Review `cogs/awin_management.py`, `tests/test_awin_skill_integration.py`, `docs/skills/external-packages.md`, `docs/SETUP.md`.
3. Confirm clean host tests, package discovery, Management APIs and `pip check` in disposable local environment.
4. Record exact tested host SHA and Awin SHA. Do not infer running VPS state from a Git branch.

## Stage 1 — isolated live host environment

Use a **separate Discord bot application/token, separate private test guild with an isolated channel, separate guild ID, separate fresh SQLite database and separate Skill encryption key**. Do not reuse live production environment, persistent volumes or config. Never commit or share `.env`, token values or any private log. Ensure only the isolated bot instance is using these credentials.

Source setup path: `docs/SETUP.md`; requirements installed locally into a dedicated virtual environment. To exercise external Skills, install packages only through reviewed immutable `requirements-skills.lock` as part of deliberate environment setup. Do not install packages dynamically from Discord commands.

Recommended *local* steps (Windows PowerShell from fresh GamerHQ checkout at the PR branch):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -r requirements-skills.lock
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m tools.skill_package_plan --json
.\.venv\Scripts\python.exe -m pytest tests/test_awin_skill_integration.py tests/test_skill_deployment.py
```

Create a **new local private** `.env` from `.env.example` only if absent; set independent `DISCORD_TOKEN`, `GUILD_ID`, `GAMERHQ_DB_PATH` and stable `GAMERHQ_SKILL_SECRET_KEY`. Generate the key privately following `.env.example`; never disclose it. Set other required local channel configuration per `docs/DISCORD_SETUP.md`.

**Only after confirming that the separate bot/test guild/DB are selected**, start the local instance with `.venv\Scripts\python.exe bot.py`. The documented startup syncs Discord application commands; it must never run with a production bot token during isolated acceptance.

## Stage 2 — live acceptance checks

In the isolated guild, open `/server manage → Skills → Awin Affiliate`. If missing or unavailable, record `HOST` and stop before entering an Awin token.

For available UI, follow the Skill's canonical `docs/live-acceptance.md`:
1. Confirm installed package version/Runtime API and safe status diagnostics.
2. Connect dedicated Awin publisher token privately in host UI; verify publisher selection.
3. Retrieve joined advertisers.
4. Import one supported banner/saved My Creative HTML; confirm HTTPS-only image and tracking URLs.
5. Validate Creative list/detail/preview, disable/enable and filtering.
6. Test Post Now preview without send side effects, then explicit confirm in the test Discord channel (affiliate disclosure visible).
7. Test campaign create (minimum 15 minutes), preview next, run now, pause/resume, history and delete.
8. Restart **only this isolated bot process**, check publisher/Creative/campaign persistence, scheduled jobs recovery, and no duplicate posts.
9. Verify aggregate diagnostics are correct and credential-free.

All observed results must be recorded as redacted `PASS`, `FAIL`, or `NOT RUN`; include safe categories (`SKILL`, `HOST`, `PROVIDER`, `ENVIRONMENT`, `UNKNOWN`) and no IDs/tokens/headers/raw private logs.

## Stage 3 — handoff / decision

- Skill defect → fix in Awin repo with regression tests; update the immutable pin via new reviewed host PR after acceptance.
- Host missing capability/UI → fix in GamerHQ on a separate reviewed branch/PR.
- Provider/environment failure → document without calling it a pass.
- Update Awin draft PR #40 with real evidence only after testing.
- The refreshed host integration PR requires current CI and review before develop merge; **CI success is not production deployment authorization**.
- Do not merge/promote a host release or update the healthy VPS as part of this test run.
