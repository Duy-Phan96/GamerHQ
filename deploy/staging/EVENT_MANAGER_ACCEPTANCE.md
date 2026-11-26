# Event Manager staging acceptance

## Event Manager 0.1.3 readiness and owner gates

The latest reviewed [staging smoke](https://github.com/Duy-Phan96/GamerHQ/actions/runs/42205661949)
reports the existing bot on Host `29ecf575f3d865b0f32a9d82adf1b06d7969cdc9`
with Skill **0.1.2** installed. The Host development branch and workflow
revision are separate from that deployed application pin. Keep using the
existing staging guild, bot, controller and resource policy.

[Skill #13](https://github.com/Duy-Phan96/gamerhq-skill-event-manager/pull/13)
merged release source `1e7192d33084709548defe93e1a3b07d0c138630`. Its
[release-source CI](https://github.com/Duy-Phan96/gamerhq-skill-event-manager/actions/runs/42213208808)
passed Python 3.12/3.14 and the installed-wheel, isolated Host probe on the
deployed pin. Runtime API 1 and the published SDK 0.3.0 wheel remain required.
The SDK checksum is
`1a2136365f7a75449871423d4e57c91a421c4684dafbe067e900d11b60354399`.
The compact Discord builder and its v1 management calls can be reused on the
existing Host pin. Expiry is optional per Event via v1 and the current compact
editor uses the default when creating an Event. An expired or revoked response
may show “Invitation resolved.” Verify the inbox and ACL rather than interpreting
that message as acceptance.

### Exact artifact intake

1. **Owner approval to publish.** Authorize the Skill maintainer to publish the
   private `v0.1.3` tag and prerelease at the exact Skill source above. The
   expected assets are `gamerhq_skill_event_manager-0.1.3-py3-none-any.whl`,
   `gamerhq_skill_event_manager-0.1.3.tar.gz`, and `SHA256SUMS`.
2. After publication, verify the tag resolves to the reviewed commit, the tag
   workflow succeeded, and both downloaded assets match the publisher manifest.
   Record the release and asset URLs, tag workflow and digests in the private
   intake record. The controller independently verifies the tag, manifest and
   wheel bytes. Test artifacts do not supply the published release checksum.
3. Use this descriptor only after inserting the verified **published wheel**
   SHA-256 and recording source review. Retain the approved SDK and existing
   capabilities. See the [Skill handoff](https://github.com/Duy-Phan96/gamerhq-skill-event-manager/blob/e5e9c6f98283b78cd6c39297b292f24d668a1d1d/DEVELOPMENT_HANDOFF.md).

~~~json
{
  "repository": "Duy-Phan96/gamerhq-skill-event-manager",
  "distribution": "gamerhq-skill-event-manager",
  "version": "0.1.3",
  "tag": "v0.1.3",
  "reviewedCommit": "1e7192d33084709548defe93e1a3b07d0c138630",
  "sha256": "<verified published wheel SHA-256>",
  "sourceReviewed": true,
  "allowPrerelease": true,
  "skillId": "event-manager",
  "runtimeApi": "1",
  "migrationMode": "backward-compatible",
  "capabilities": ["storage.skill", "storage.atomic", "scheduler.jobs", "discord.resources.manage"]
}
~~~

### Backup, apply and post-restart verification

1. Before deployment, use `status`, `health`, `smoke` and `history` on
   [Policy-bound staging automation](https://github.com/Duy-Phan96/GamerHQ/actions/workflows/staging-automation.yml)
   on Host `main`. Require the enrolled policy, `HOST_READY`, the exact
   0.1.2/29ecf575 baseline and no recovery block. Record the prior image and
   deployment ID. Seed the pre-upgrade test fixtures and record the Event,
   occurrence and channel/card references and expected ACLs.
2. The existing authorized staging operator takes and rehearses a consistent
   SQLite backup in a private staging-only directory. The scoped workflow
   does not expose arbitrary shell or backups. Use the existing backup tool
   from the reviewed Host checkout with explicitly verified staging paths and
   two new files. The second copy simulates restoration away from the live DB.

   ~~~sh
   python -m tools.backup_database "$STAGING_BACKUP" --source "$STAGING_DATABASE"
   python -m tools.backup_database "$STAGING_RESTORE_PROBE" --source "$STAGING_BACKUP"
   python -m tools.backup_database --verify "$STAGING_RESTORE_PROBE"
   ~~~

   Record the backup SHA-256, restore verification, application fingerprint,
   and preserved staging Event authority, fixture records and resource mappings
   on the isolated copy. Compare the logical fingerprints of backup and restore
   with `hosts.gamerhq.skill_update_controller.application_fingerprint`.
   Keep paths and row contents private. Require a fresh backup rehearsal receipt
   before proceeding. If the operator or verified staging paths are unavailable,
   stop. The routine controller also makes its own consistent backup after
   stopping the previous image and records its hash and logical fingerprint.
   The routine Docker backend does not run the separate protected Host promotion
   restore probe, so a routine `APPLIED` receipt cannot replace this rehearsal.
3. **Owner approval to prepare and upgrade staging.** Authorize the exact
   Skill commit and published wheel SHA-256 for the existing staged
   Host/SDK pair, with the verified backup receipt. On Host `main`, run
   `prepare` with the release descriptor as the `release` input. Require
   `PASS / PREPARED` for 0.1.3. Run `update` with no release input only after
   that result. The controller creates and validates `READY` inside
   `update` before applying it. Record the returned `APPLIED` deployment
   ID and matching `history` result. Do not use the protected Host overlay
   promotion for this Skill-only upgrade.
4. After the policy cooldown, run `restart`, `health`, `smoke` and
   `history`. Require a successful restart, `HOST_READY` with the Gateway
   checked, `SMOKE_PASS` identifying installed 0.1.3 and the exact Host pin,
   and the same `APPLIED` deployment. Compare scheduler state (one Event
   Manager job), Event and occurrence IDs, existing members, opt-outs and
   history, mapped channel/card IDs, and fixture ACLs. Reopen the legacy invite
   after restart and compare its stored deadline to the first migrated value.
   Record run links, audit/deployment IDs, and per-case outcomes in
   [Host #208](https://github.com/Duy-Phan96/GamerHQ/issues/208).
5. If an operation fails, stop further mutations and retain the backup, prior
   image and receipts. The rollback guard checks the full application
   fingerprint, including Skill storage. Once expiry metadata or any other
   application state has changed, 0.1.2 must not be run against that database.
   Treat `RECOVERY_REQUIRED` as a stop. Prefer a reviewed fix forward, or
   restore the matched pre-upgrade image and verified backup only through
   approved recovery after accounting for intervening writes. No automatic
   restoration is part of this runbook.

The first successful refresh of an older pending invite sets a durable grace
deadline (168 hours by default) in the existing Event row. The migration does
not change the revision when it only adds metadata. It preserves guild scope,
membership, existing IDs, occurrence history and managed resource bindings. A
later expiry prune advances the revision. Restarts retain the original deadline.

### Manual Discord acceptance checklist

Use the existing isolated staging guild and the existing bot. Use **O** as the
organizer, **A** as a member with DMs enabled, **B** as a member with DMs
disabled, and **C** as an unrelated non-admin member. Use labeled disposable
Events. Before upgrading, O prepares a private recurring 0.1.2 Event with an
untouched invitation to B and records its mapping. Keep a separate mapped
imported legacy Event for case 9 if available. For each case record UTC time,
exact deployed versions, actor role, Event/occurrence reference, redacted
screenshots or scoped state evidence, and **Pass**, **Fail** or **Blocked**.
Pass only when every listed result is observed. Log any missing fixture or
policy prerequisite as blocked. Use no unrelated messages or personal data.

| Case | Accounts, setup and actions | Pass criteria | Evidence |
| --- | --- | --- | --- |
| 1. Public/private creation | O uses `/lfg create` → preview → confirm for a public Event and a private Event. C uses `/lfg join` for the public Event and opens the private channel link. O reopens `/lfg edit`, changes the title, previews and saves. Use chat disabled on the public fixture if the enrolled policy only permits private areas. | The previews match saved settings, C can join the public Event subject to capacity and cannot access the private area, and edits retain the Event and existing channel/card mapping. The public channel/card assertion is blocked under a private-only policy. | Previews, saved roster and settings, denied link, before/after Event and resource references. |
| 2. Consent and decline | O opens `/lfg participants` and adds A and B to a private Event. Verify both are pending. A accepts and B declines from `/lfg invitations`. On a separate invitation, O removes the pending player before they press Accept. | A receives the DM, B can use the private inbox with DMs blocked, and pending status grants neither access nor membership. Only A gains access after consent. Decline and revocation grant neither. | Redacted DM and inbox, consent/revoke responses, private ACLs and roster before/after. |
| 3. Expiry | O sets `invitationTtlHours=1` using an already authorized staging v1 path before inviting B, or uses the seven-day default. B opens two inbox views two minutes before the recorded deadline and preserves one for case 4. At the deadline or later, B presses Accept on the other view and reloads the inbox. | Invite is visible before the deadline and absent after. Stale acceptance cannot grant membership or private access. | TTL and deadline from scoped inspection, action timestamps, inbox before/after, response and denied access. |
| 4. Reissue | O refreshes `/lfg participants` and explicitly re-adds B after expiry. B tries the untouched old view within ten minutes of opening it and accepts via a freshly loaded inbox. | Old revision cannot accept the replacement invite. New consent adds one membership and permits access only after acceptance. | Old/new deadline and revision, stale outcome, fresh outcome and one roster entry. |
| 5. Grace across restart | B opens the untouched 0.1.2 pending invite after upgrade. O or the authorized operator reads the stored deadline before and after the scheduled restart. B reopens the inbox without responding. | The same grace deadline survives restart and B remains pending without access. Prior members, history and mappings are preserved. | Pre-upgrade invite, both deadline observations, restart/smoke receipts, before/after ACL and mappings. |
| 6. Leave private Event | A opens `/lfg manage` for the accepted private Event. Choose permanent leave, click **Keep Membership**, then repeat and confirm **Leave Event Permanently**. Reopen the original channel link and check future registration. | Cancel retains access. Confirm removes private access and future enrollment after reconciliation while retaining the Event and past attendance. | Both outcomes, denied channel link, past attendance and future roster. |
| 7. Attendance and opt-outs | A uses `/lfg attendance` on a recurring Event. Check default 2 weeks and **Show 4 Weeks**. Toggle two sessions and cancel. Reopen, toggle again, **Save Attendance**, and inspect both sessions and a future untouched session after restart. | Cancel changes nothing. Both edits save together and persist. Untouched future sessions retain enrollment and A remains a member. | Both views, Cancel/Save responses, session counts before/after and restart evidence. |
| 8. Recurring sessions | O creates an Event with a first date on a weekday outside the two selected recurrence weekdays. Inspect the preview and session list, cancel one session, then pause and resume. | First session retains its date, later sessions follow selected weekdays at local time, and only the chosen occurrence is cancelled. Pause/resume preserves history and the area. | Preview, session dates and cancellation state, same card/chat references. |
| 9. Legacy cards/threads | O captures an existing legacy card and chat/thread link before upgrade. After restart, open the original link and use its manage/navigation action. Allow a scheduler tick and compare with a new Event. | Same Event and resource mappings resolve. No duplicate channel or card, stale status or raw resource marker appears. | Before/after links and IDs, navigation result, resource counts and any failure screenshot. |
| 10. Temporary voice | O creates a short private one-time Event with temporary voice on, 10-minute opening lead and reminders off. A accepts. After the opening threshold allow one five-minute scheduler interval. A joins, C tries to join, and A leaves before the first tick after the Event ends. | Exactly one temporary channel opens for the occurrence. C has no access, the empty ended channel is removed, and the persistent chat/card stays. | Preview, creation/access/cleanup times and channel counts, denied access and retained chat/card link. |

---

## Historical 0.1.0 acceptance record, 4 November 2026


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
