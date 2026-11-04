# Compact Event Manager Discord compatibility

Tracking: [Host #208](https://github.com/Duy-Phan96/GamerHQ/issues/208).
Branch: `fix/skill-event-compact-ux`, based on reviewed `develop`.

The installed Skill remains authoritative. The Host editor reuses V1 date, time,
player limit, type, weekday and title components, without its persistence or
resource handlers. Main view: four selects and five buttons. Preview: Edit,
Create Event / Save changes, Cancel, Players & invites, Event settings.
The secondary pages preserve all declared settings without raw IDs or boolean
storage values in the primary view. Existing finite imported schedules remain
protected by the Skill's explicit conversion requirement.

Offline evidence: `tests/fixtures/skill-event-compact-ui.json` freezes the compact
copy/control layout. `test_event_skill_discord.py` covers weekdays, input options,
authorization, preview invalidation, player pickers, safe confirmation and retries.
The optional installed-wheel `test_event_skill_end_to_end.py` drives the actual
Discord adapter through public Skill APIs and temporary SQLite/Discord fakes. A
lost create response is retried against the real Skill; exactly one Event,
persistent area/card and resource creation remain. No private Skill imports.

Companion policy changes: [Skill #6](https://github.com/Duy-Phan96/gamerhq-skill-event-manager/pull/6)
and [Web #67](https://github.com/Duy-Phan96/gamerhq-web/pull/67). They change only
development guidance, not artifacts or deployed application code.

## Staging boundary

The established scoped automation health run
[41906919163](https://github.com/Duy-Phan96/GamerHQ/actions/runs/41906919163)
passed with `HOST_READY`, `gatewayChecked=true`, `productionChanged=false` and
audit ID `0a51f503c2d949f8866acf1399299006`. This checks the currently approved
deployment, not this new UI source.

The enrolled controller's prepare/update operations accept reviewed Skill patch
artifacts, using the existing immutable Host base. They do not accept arbitrary
Host source commits. Its base is built from the root-owned worker checkout pinned
by `hostSourceCommit`; the automation policy additionally binds
`legacyWorkerCommit`. Do not place Discord UI code inside the portable Skill,
change trusted pins silently, enable the legacy gates, patch a running container,
or expand the restricted SSH controller to deploy this Host change.

After code review and green CI, the next deployment dependency is an explicitly
reviewed staging Host source/image promotion under the existing security boundary.
No production promotion, migration, credential update or bootstrap is included.
Do not claim this change is staged until an exact accepted source/image has been
prepared and deployed through its authorized release path.

## Live acceptance still required

Computer Use previously confirmed the dedicated guild but could not click or
capture Discord (`coordinate input geometry is unavailable`, capture timeout),
including after a tool-session reset. No event was submitted. These are tool
failures, not proof of an application defect. Component snapshots are automated
regression evidence, not live screenshots or live acceptance.

Once the exact Host candidate is deployed and Computer Use works, create a private
future recurring Event in the configured staging categories; select several
weekdays; preview and confirm; edit title, game, description, schedule, limit,
voice and visibility within policy; add/remove authorized test participants;
verify unchanged event/chat/card IDs; restart through the scoped staging workflow
within its rolling limits; verify persistence and the same resource IDs. Preserve
history and pause the fixture when finished. Do not delete existing channels.
Multi-user permission checks require actual authorized test accounts. Production
remains unchanged until separately approved.
