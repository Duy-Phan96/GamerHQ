# Platform customer readiness

The platform workstream owns the Host, public SDK and Runtime, Web integration,
installation lifecycle and controlled operations. Each Skill repository owns its
domain, versioned domain contracts and release decisions. Platform changes use
public APIs and reviewed artifacts.

## Tenant boundary

The Discord guild snowflake is the customer identifier. Existing Host records
scope Skill installation, configuration, storage, jobs, role mappings and health
to that guild. GamerHQ Staging uses the same public install and enable lifecycle.
It currently has Event Manager 0.1.2 on Host `29ecf575` with passing automated
smoke and restart. Manual Discord acceptance remains pending. This audit makes
no claims about which Skills are installed for the production guild.

Package bytes and Docker updates are Host-wide. Installation and configuration
are per guild. Web exposes successful shared package history only for installed
Skills. Operator recovery history and diagnostics stay in Host operations.
Installing through Web adds an already reviewed and loaded package to the guild.
Publishing, downloading, updating and removing packages remain reviewed Host
operations. Customer self-service removal requires a retained-data lifecycle
contract and recovery policy.

## Skill repository handoffs

| Skill | Owning repository and release | Host contract and next evidence |
| --- | --- | --- |
| Event Manager | [gamerhq-skill-event-manager 0.1.2](https://github.com/Duy-Phan96/gamerhq-skill-event-manager/releases/tag/v0.1.2), preview | Runtime API 1, SDK 0.3.x, versioned `event-manager.*.v1` management, Host-managed resources and atomic storage. Staging installed and healthy. Owner Discord acceptance is tracked in [#208](https://github.com/Duy-Phan96/GamerHQ/issues/208). |
| Progression | [gamerhq-skill-xp-progression 0.2.0](https://github.com/Duy-Phan96/gamerhq-skill-xp-progression/releases/tag/v0.2.0) | Runtime API 1, versioned `progression.*.v1` management, storage, audited Discord operations. Host source lock pins `b8226977`. Active feature work stays with the owner of [#215](https://github.com/Duy-Phan96/GamerHQ/pull/215). Verify each tenant's install and role policy before release acceptance. |
| Recurring Posts | [gamerhq-skill-recurring-posts 1.4.0](https://github.com/Duy-Phan96/gamerhq-skill-recurring-posts/releases/tag/v1.4.0) | Runtime API 1, versioned `recurring-posts.*` management, scheduler and scoped Discord message delivery. The current Host source lock stays on 1.2.3 at `7656616a`. Promoting a newer artifact needs reviewed compatibility, capability and schedule-preservation checks. Tenant installation and live acceptance remain to be confirmed. |

The published Progression 0.2.0 wheel declares SDK `>=0.1,<0.2` in release
source. Recurring Posts 1.4.0 declares `>=0.2,<0.3`. Both declare Runtime API 1.
The published Event Manager 0.1.2 release declares SDK `>=0.3,<0.4` and has a
verified 0.3.0 staging installation. Existing source pins are a separate Host
integration path. The Skill owners must review and publish compatible wheel
metadata before adopting either newer distribution through the SDK 0.3.0
release lifecycle.

## Web staging boundary

[Private Web connectivity #166](https://github.com/Duy-Phan96/GamerHQ/issues/166)
tracks the live path. The existing rootless staging Host already binds the API
inside the private network and publishes it only on VPS loopback. The separate
Web staging service uses that same private Docker network and a separate loopback
port. Live activation requires a reviewed deployment identity and an approved
HTTPS origin with dedicated staging OAuth credentials. Keep production separate.
