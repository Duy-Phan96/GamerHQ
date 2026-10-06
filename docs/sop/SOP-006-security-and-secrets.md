# SOP-006: Security and Secrets

Canonical sources:
- ../../SECURITY.md
- ../SECURITY_MODEL.md
- ../development/SECURITY.md

## Routine rules

1. Keep real credentials/private runtime data outside Git.
2. Use environment/configuration surfaces already defined by the project.
3. Keep normal CI credential-free where possible.
4. Redact logs/screenshots/issues before sharing.
5. Recheck authorization at mutation time.
6. Use minimum Skill/Discord capabilities.
7. Run repository/history audit before publishing security-sensitive changes.

## If a secret leaks

1. Stop using it.
2. Revoke it at the provider.
3. Rotate/issue a replacement.
4. Update private deployment configuration.
5. Verify the old credential no longer works where practical.
6. Audit Git history, CI artifacts, logs and screenshots for exposure.
7. Decide explicitly whether history rewriting is required.
8. Record the incident without copying the leaked secret.

Deleting a file or adding .gitignore is not sufficient remediation.