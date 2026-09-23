# Offline test requirements

- Read [validation commands](../docs/DEVELOPMENT_WORKFLOW.md); run from repository root.
- pytest enters tools.test.offline_environment through conftest.py before collection. It disables dotenv, substitutes temporary storage/config and rejects Discord HTTP requests.
- Do not bypass that setup with direct test-file execution or raw unittest discovery. The supported unittest entry point is python -m tools.test.
- Reuse stateful Discord fakes in test_onboarding.py and existing unittest/IsolatedAsyncioTestCase patterns. Most feature tests patch database.db.DB_PATH to their own temporary DB and call init_db().
- Use synthetic IDs/content only. Never read the local .env or production SQLite to build fixtures.
- Assert observable behavior: authorization, privacy, persisted IDs, repeat/concurrent execution, deleted-resource recovery and preserved unrelated content.
- Discord edit() can return a new object before the guild cache catches up; fakes must not conceal that failure mode.
- A mocked API test is not live Discord acceptance. State that distinction in the handoff.
