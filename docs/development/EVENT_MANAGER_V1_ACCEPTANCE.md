# Event Manager Recurring Events V1 — Live Acceptance

_Status: PENDING_

This checklist validates the recurring-event implementation added under Issue #153.

## Scope

Validate the existing Event Manager with:

- one-time events;
- daily recurrence;
- weekly recurrence;
- Mon–Fri recurrence;
- weekends recurrence;
- custom weekdays;
- occurrence-count limit;
- end-date limit;
- open-ended recurrence;
- recurring event publication/recovery;
- occurrence-vs-series management.

## Creation

- [ ] One-time event creation still works unchanged.
- [ ] Daily recurring event can be created.
- [ ] Weekly recurring event can be created.
- [ ] Mon–Fri recurring event can be created.
- [ ] Weekend recurring event can be created.
- [ ] Custom recurrence can use multiple weekdays such as Mon/Wed/Fri.
- [ ] Occurrence-count limit is shown in preview and persisted.
- [ ] End-date limit is shown in preview and persisted.
- [ ] Count and end date cannot be configured together.
- [ ] Closing Repeat Settings without saving keeps the previous draft recurrence.
- [ ] Public recurring event publishes its first event card.
- [ ] Private recurring event creates its private event channel.

## Future occurrences

- [ ] Future occurrences are materialized automatically.
- [ ] Materialization does not duplicate an existing occurrence.
- [ ] Restart does not duplicate occurrences.
- [ ] Missed past occurrences are not backfilled after downtime.
- [ ] Public future occurrences receive/recover their normal event cards.
- [ ] Private future occurrences receive/recover their private event channels.
- [ ] Existing event IDs remain stable during recovery.

## Participation and lifecycle

- [ ] Join/leave behavior works on a recurring occurrence.
- [ ] Private invite/join-code behavior works on a recurring occurrence.
- [ ] Seats remain occurrence-owned.
- [ ] Voice reminder and event voice work on a recurring occurrence.
- [ ] Event completion/cancellation retains normal cleanup behavior.

## Edit scope

For a recurring occurrence:

- [ ] Edit Details → Only This Event changes only that occurrence.
- [ ] Edit Details → This + Future Series updates current/future occurrences and future series defaults.
- [ ] Change Date / Time → Only This Event changes only that occurrence.
- [ ] Change Date / Time → This + Future Series performs a safe series split.
- [ ] Series split preserves earlier historical occurrences.
- [ ] Series split preserves concrete event IDs and participant state.
- [ ] Series split blocks when an affected occurrence already has voice open.

## End scope

- [ ] Cancel Event → Only This Event affects only that occurrence.
- [ ] Cancel Event → This + Future Series ends current/future occurrences.
- [ ] Close Event → Only This Event affects only that occurrence.
- [ ] Close Event → This + Future Series ends current/future occurrences.
- [ ] Historical occurrences remain unchanged.
- [ ] Terminal series is not rematerialized after restart.

## Timezone / DST

- [ ] Weekly events keep the intended local wall-clock time across DST.
- [ ] Spring-forward nonexistent local time resolves to the first valid local minute.
- [ ] Fall-back ambiguous local time uses the first occurrence consistently.
- [ ] Discord renders event timestamps correctly for members in another timezone.

## Restart / recovery

- [ ] Restart the bot once with active recurring events.
- [ ] Existing cards remain/recover without duplicates.
- [ ] Existing private event channels remain/relink without duplicates.
- [ ] No duplicate event rows are created.
- [ ] No duplicate voice reminder claims are created.

## Acceptance result

Record one final state:

- `BLOCKED`
- `READY FOR RELEASE`
- `ACCEPTED`

Record:
- exact Host commit/version;
- date/time;
- guild used;
- failed checks;
- screenshots or concise evidence for any repaired bug.
