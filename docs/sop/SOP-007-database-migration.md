# SOP-007: Database Migration

Canonical source: ../DATABASE_MIGRATION.md

## Procedure

1. Identify the authoritative table/state owner and avoid parallel persistence.
2. Prefer additive, idempotent migrations.
3. Define backward-compatibility semantics for existing rows.
4. Add migration coverage using a database shaped like the previous production schema.
5. Test fresh-database initialization separately.
6. Verify existing data is preserved and repeated startup is safe.
7. Review concurrency/transaction implications.
8. Document backup and rollback implications.
9. Run the full integration suite for shared schema changes.
10. Before production, create/verify a backup and run the existing migration/preflight rehearsal.
11. Deploy using the canonical owner-controlled procedure.
12. Verify schema/data health after deployment.

Do not use production data for normal tests and do not perform an unreviewed
manual schema edit on the live database.
