# Progressive enhancement

Preserve working behavior and established UX by default. Extend existing systems
incrementally rather than replacing them wholesale.

For Host, Skills, Web and future platform components:

1. Inspect current functionality, interaction handlers, presentation and user flows.
2. Identify the smallest compatible extension; reuse existing controls and services.
3. Preserve established interaction patterns and terminology.
4. Avoid broad redesigns unless explicitly requested.
5. Document unavoidable breaking UX changes and their compatibility impact.
6. Explain alternatives before implementing major changes requiring product-owner
   decisions; obtain that direction before committing to a new product flow.
7. Add regression tests for previously supported workflows, including snapshots
   when useful, authorization, retries, persistence and resource identity.

An internal architectural improvement is not permission to replace the user
experience. Preserve repository ownership and public contracts: the Host renders
Discord interactions and manages trusted resources; the installed Skill validates
and owns Event state, recurrence, participants, persistence and lifecycle.

The Event Manager compatibility slice reuses the existing compact builder's four
dropdowns and five buttons. Recurrence opens seven weekday toggles. Preview opens
the existing Edit / Create Event (or Save changes) / Cancel confirmation pattern;
players and event settings are secondary controls on that page because Discord
allows only five component rows. No raw draft/configuration dump is shown.
Existing event dates, minute values, timezones, memberships and capacities remain
intact even when outside the default dropdown shortcuts. Existing imported finite
schedules are preserved unless the explicit replacement setting is confirmed.

Record automated component snapshots separately from actual Discord acceptance.
A green offline test or staging infrastructure health check does not prove live
event creation, permissions or restart recovery with real Event data.
