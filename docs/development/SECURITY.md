# Development Security

This guide adds a lightweight security review to normal development.

It supplements the canonical root [SECURITY.md](../../SECURITY.md) and
[Security Model](../SECURITY_MODEL.md). Do not duplicate their operational
details here.

## When a security review is required

Explicitly review security impact when a change touches:

- authentication;
- authorization;
- Discord permissions or role hierarchy;
- Skill capabilities;
- secrets or environment variables;
- web APIs or OAuth;
- external input;
- database access or migrations;
- file uploads/exports;
- external providers/webhooks;
- private Discord resources;
- logs/telemetry;
- PII/community data;
- dependencies.

## Review questions

Ask only the questions relevant to the change:

- Who is allowed to perform this action?
- Is authorization rechecked at mutation time?
- Is the guild/server scope trusted server-side?
- Does this add a new capability or widen an existing one?
- Can untrusted input select arbitrary IDs, URLs, paths, contracts or code?
- Can private server resources become visible?
- Can secrets/tokens appear in code, logs, exceptions or browser state?
- Is persisted data minimally scoped?
- Is destructive behavior previewed/confirmed where appropriate?
- Is retry/concurrency behavior safe?
- Does failure stay closed?

## Secrets

Never commit Discord tokens, OAuth/client secrets, API keys, webhook credentials,
private keys, production .env files, runtime databases, or private logs/backups/exports.

Use .env for local/private configuration and GitHub Secrets only for workflows
that genuinely require a secret. Normal offline CI should remain credential-free.

### Exposed secret response

If a real secret leaks:

1. stop using the exposed credential;
2. revoke it at the provider;
3. issue/rotate a replacement;
4. update the private deployment configuration;
5. verify the old credential no longer works where practical;
6. run repository/history audit;
7. assess where the value was copied (Git, logs, screenshots, CI artifacts);
8. decide deliberately whether a history rewrite is needed;
9. document the incident without publishing the secret.

Deleting the current file or adding .gitignore is not remediation.

## AI-assisted security

AI agents must not:

- invent real credentials;
- paste private production data into tests;
- disable authorization/tests to make a feature work;
- broaden Discord permissions as a shortcut;
- add arbitrary shell/runtime package execution without architecture review;
- expose raw provider/private exceptions to users.

When an AI change affects security, its review summary should state the changed
trust boundary, authorization/capability impact, secret/config impact, new
external input, negative tests, and unverified live assumptions.

## Dependency changes

Dependency upgrades/additions require concrete need, compatibility review,
locked versions according to repository policy, dependency validation, relevant
tests, and a production image build where applicable.

Do not add tooling/dependencies merely because another project uses them.

## Logging

Logs should make failures diagnosable without exposing access/refresh tokens,
.env values, ticket/support text, private invite codes, unredacted user data,
or full private payloads unless specifically designed and protected.

Prefer bounded identifiers, action names, status and correlation context.

## Security completion

Security-sensitive work is complete when relevant negative paths are tested and
remaining manual/live security checks are explicitly documented.
