# Event Manager staging acceptance — 2026-11-04

The owner-approved restricted automation is active. Event Manager 0.1.0 is
installed through the normal Host API on the existing isolated staging database.
Production, staging credentials and existing data were not replaced. No bootstrap,
configuration reset, database initialization or production migration was repeated.

## Verified on the VPS

| Operation | Evidence | Result |
| --- | --- | --- |
| Protected activation | [41888781495](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41888781495) | Owner review and activation passed |
| Independent controller status | [41897183347](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41897183347) | ACTIVE, bootstrap verified, no recovery block |
| Normal Skill installation | [41897252409](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41897252409) | INSTALLED; controller takes a consistent SQLite backup before this operation |
| Installed package smoke | [41897582552](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41897582552) | Host ready; installed version 0.1.0 |
| Repeat installation | [41898174711](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41898174711) | ALREADY_INSTALLED |
| Artifact preparation | [41898452243](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41898452243) | Published source/checksums verified; candidate built and probed without a Gateway |
| Same approved image restart | [41898617303](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41898617303) | RESTARTED and Host health restored |
| Post-restart smoke | [41898922241](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41898922241) | Version 0.1.0 remains installed |
| Healthy recovery request | [41899299449](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41899299449) | ALREADY_HEALTHY; no second restart or forced outage |
| Same-version update | [41899033784](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41899033784) | NO_UPDATE; no fabricated release or rollout |
| Update history query | [41899515236](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41899515236) | HISTORY, no update deployments yet |

Routine operations used `staging-automation` and the scoped Host controller,
without repeated owner deployment reviews. The controller remains pinned to
a1b50573850921e478a2f838e72868853bde6dd7 and the installed Host worker to
92db47042d58055cb0debdfd83f11f8508e19809. The old staging-update gate and the
production deployment gate remain false. Enrollment readiness and active Host
automation are separate from those legacy gates.

The install backup path is enforced by the reviewed controller. No live database
restore, backup-content inspection, failed-image rollout or data-changing rollback
has been performed. The restart proves installation-state persistence, not Event
or participant persistence: no live Event fixture has been created yet.

## Offline integration evidence

The installed public SDK/released package boundary was exercised with synthetic
data: 42 portable Skill tests; 21 Host installation, authority/cutover, Discord
editor and API tests; 22 Host update-controller and managed-resource tests; and
60 Web authorization, schema, revision and transport tests passed. These cover
recurrence, one stable logical area/chat, revision conflicts, privacy, retry,
six-table reconciliation, restart and rollback safeguards. Offline fixtures are
not live Discord acceptance.

## Remaining live application acceptance

Computer Use was stopped because the native Brave window URL could not be
determined confidently enough for policy enforcement. Discord was minimized;
no staging editor interaction, message, Event or Discord resource was created.
Do not work around this stop with another UI automation technology.

The scoped deployment protocol intentionally has no arbitrary shell, Docker exec,
Host HTTP proxy or Event CRUD operation. Do not weaken it to substitute for a
verified application session. Resume through the normal Discord/Host management
UI when the dedicated staging guild is open and its target can be verified.

Use a clearly named private staging fixture through the public Skill lifecycle.
Verify configuration and enablement, title/game/description, independent weekdays,
timezone and preview, membership versus occurrence participation, limits, private
visibility, voice choices and revision-safe editing. Observe actual managed
channel/card IDs across multiple occurrences and repeated reconciliation. Preserve
the fixture, chat, history and unrelated resources; pausing retains the area.
Do not silently delete channels or restore a database to tidy up acceptance data.

The staging resource policy allows private areas in its configured categories;
it does not authorize widening public visibility. Actual authority ownership,
Event CRUD, persistent chat identity, live editing and fault-injected recovery
remain unverified. Rootless healthy recovery is not an outage or rollback test.
No new approved release has been applied, so live version-upgrade/rollback remains
pending an appropriate candidate and the established data-safety rules.

Live Web acceptance also requires the private Host/BFF connectivity tracked in
[Host handoff #166](https://github.com/Duy-Phan96/GamerHQ/issues/166). Do not expose
the loopback API or copy production OAuth/session credentials to bypass it.
Production rollout, destructive migration, credential/security changes and
operations beyond the reviewed limits still require their existing approvals.
