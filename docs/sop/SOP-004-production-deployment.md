# SOP-004: Production Deployment

Production deployment is an owner-controlled operation.

Canonical sources:
- ../../DEPLOY.md
- ../../ROLLBACK.md
- ../../PRODUCTION_RULES.md
- ../PRODUCTION_OPERATIONS.md

## Procedure

1. Confirm the intended reviewed release is on main.
2. Confirm the VPS checkout is clean and production paths/configuration match the canonical guide.
3. Acquire/use the existing maintenance lock through repository scripts.
4. Back up the SQLite database before source/image/schema changes.
5. Fast-forward only from the reviewed main branch.
6. Validate Docker Compose and build the production image.
7. Run production doctor/preflight and migration rehearsal where applicable.
8. Start/update the service through the canonical update script.
9. Verify container health, deployed commit/release and private server-log.
10. Run only the relevant live Discord acceptance checks.

## Rollback

If deployment verification fails, follow ROLLBACK.md. Do not improvise a
database restore or Git history reset.

Never deploy develop, force-reset production, expose .env contents or run a
second bot process against the live guild/database.