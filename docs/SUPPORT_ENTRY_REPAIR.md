# Support and Electricity entry repair (STAB-01)

This is the first implementation slice of the server-stabilization roadmap.
The observed replies were produced by the entry-identity guards, before ticket
creation. The exact VPS mapping discrepancy is still a live diagnostic question;
this change does not claim to have inspected the production database.

## Owner workflow

After updating the reviewed code, open `/server manage` → **Support & Requests**.
Choose **Check Need Support** or **Check Electricity**. Checking is read-only.
Alternatively, click a failing original entry as owner; its private response now
includes the same scoped check, with the clicked channel/message as evidence.

The check compares the existing channel and message settings with the managed
message record, actual bot author, content fingerprint, configured action,
controls and pin. It searches at most the existing reconciliation scan allowance
in that one channel, not the whole server. A cache miss triggers an exact channel
read; denied/unavailable reads are not treated as proof of deletion.

When the evidence identifies one safe original entry, **Confirm Entry Repair**
restores only its saved bindings. The original content, custom labels/buttons,
message ID, pin, permissions and private-ticket histories remain unchanged.
The owner is rechecked, changed previews are refused, a normal managed-message
metadata audit is recorded and the editor version advances to invalidate old
drafts. Concurrent confirmation cannot apply the same stale repair twice.

A known default without a managed record can be registered only when the exact
same-bot body and controls are verified. Names, headings and action custom IDs
alone never establish ownership. Conflicting channels, duplicate candidates,
unknown/customized content without its record, changed controls, intentional
removals, unpinned entries and pending/retired/disabled actions require review;
they are not silently replaced, reenabled or adopted.

No message or channel is created, deleted, moved or permission-edited by this
repair. It does not run global setup, ticket recovery or partner retirement.
Electricity deliberately keeps `partner_message:<guild>:household` as its stable
message key; its channel key remains `managed_channel:<guild>:electricity`.

## Member experience and startup

Current verified entries open private chats: **Create Support Ticket** now
creates general support directly, without a mandatory form. Electricity creates
the existing typed private human-assisted tariff request. Members describe their
issue or comparison needs inside the private chat. No external tariff-search
API was introduced. See the direct-chat follow-up below.

An obsolete entry links to a verified current entry only when the member can
access that channel. Otherwise it gives a short staff reference such as
`ENTRY-SUPPORT-MESSAGE`. Owner repair controls are never offered to ordinary
members. The old public custom IDs remain registered across restarts.

Ticket recovery, Need Support refresh and Electricity refresh are now independent
startup phases. Failure in one is logged without suppressing the others.
Background entry refresh uses `create_missing=False`: absent/unregistered/mismatched
or pending bindings are not recreated or rebound automatically. Explicit setup
retains the existing default creation behavior. An incomplete partner inventory
can refresh its available boards but cannot retire an incomplete migration.

## Verification and remaining gate

Offline regression cases cover ready/missing/mismatched bindings, preserved
customization, duplicate/lookalike entries, wrong authors/guilds, read denial,
scan bounds, stale/concurrent confirmation, owner transfer, disabled/pending
state, member redirection, cancellation, startup isolation and restart reuse.
Run via the repository's pytest isolation, never a live bot:

```bash
python -m pytest tests/test_support_chat.py tests/test_ticket_entry_components.py tests/test_ticket_entry_repair.py tests/test_tickets.py tests/test_energy_offers.py tests/test_managed_messages.py tests/test_support.py tests/test_reconciliation.py tests/test_global_message_duplicates.py tests/test_production_management.py
python -m pytest
```

After owner-controlled deployment, check both entries, confirm only a verified
binding repair if needed, and reopen the original button. With synthetic content,
open one general ticket and one Electricity request. Check intended creator/staff
access, denial for another normal member, repeated-click deduplication, Take /
Waiting / confirmed Close, retained read-only history and behavior after restart.
Record the actual running commit and results privately. Offline tests are not
live acceptance. STAB-02's complete VPS lifecycle/recovery audit remains a live
gate; in particular, do not assume missing member cache means departed users.

See [managed messages](MANAGED_MESSAGES.md), [operations](PRODUCTION_OPERATIONS.md)
and [rollback](../ROLLBACK.md). No new configuration values or secrets are needed.

## Follow-up: Discord component round-trip compatibility

The 2026-10-01 follow-up reproduced a bug in the repair checker: its raw
component-dictionary comparison rejected an unchanged Support or Electricity
button after Discord assigned numeric component `id` values. Previous fixtures
only echoed outgoing dictionaries and did not exercise that API round trip.
See the [official component reference](https://docs.discord.com/developers/components/reference).

The comparison now ignores only numeric `id` fields on action rows and buttons.
It still compares action `custom_id`, custom-emoji identity, labels, links,
style, disabled state, row order and all other fields. Message/channel IDs,
authorship, guild, content, pinning, uniqueness, owner confirmation, saved-state
checks and the exact stale-preview fingerprint are unchanged. Received objects
and stored customization are not rewritten by this comparison.

Diagnostics distinguish identity, text, extra content and control mismatches;
they no longer report one indistinguishable author/content/controls error.
A successful deployment does not reconstruct missing bindings by itself. When
they are absent, the owner still reconnects the existing verified entry once
through its original button and **Confirm Entry Repair**. Members then use the
normal entry button without repair dialogs; the direct-chat change below removes
the formerly required form.

`test_ticket_entry_components.py` parses received payloads through discord.py's
component factory, including assigned row/button IDs. It covers both entries,
missing-all-bindings recovery, saved custom buttons, genuine mismatches,
duplicate entries, stale confirmation and handler restart followed by direct
chat creation through the existing private ticket service. No production data
is used. The raw live payload was not captured, so additional live content or
configuration differences must not be claimed as resolved by this code fix.

## Follow-up: direct Support chat (STAB-02, first usability slice)

The owner reports that Electricity creation, taking and closing now work, while
Support reaches a subject/description/feature validation error. The screenshot
does not establish which submitted field failed. The requested product behavior
is now explicitly **button → private chat**, not a mandatory intake form.

The same bound `gamerhq:tickets:create` action calls `open_support_chat`, a small
wrapper around the existing durable `open_ticket` writer with valid internal
General Support defaults. It does not add another ticket table, channel registry,
permission policy or creation implementation. Public board copy and custom IDs
are unchanged, so already repaired entry bindings do not need another repair just
for this update. The old form class remains only for compatibility; it is not
opened by the public Support button. Submitted historical details remain readable.

General support shows a short welcome asking the member to explain the issue in
the chat, optionally adding a screenshot, without sharing sensitive information.
Both current ticket types show their creator and assigned staff member on the
existing opening message. Electricity retains its specific comparison guidance;
no mandatory postcode, account, contract or payment fields were added.

The current creator/staff permission policy, per-type open limit, locks,
reservations, typed audit and history-preserving Close remain in use. At the
default limit of one open ticket per type, another click returns the existing
chat. Requests of different types remain independent. Repeated/concurrent calls
still go through the same writer; uncertain creation is never retried blindly.

A shared response helper links the saved channel, using one exact lookup on a
cache miss. Missing opening-message state or unverified member access is reported
as not confirmed ready, not as successful creation. Discord navigation is offered
through **Open Ticket** / **Open Request**; this does not force the user's client
to switch channels automatically.

Closed ticket messages no longer offer Take / Waiting / Close controls. Existing
stale callbacks still reject closed state. Waiting is enabled only while the
stored ticket is in progress; Take remains available to resume a waiting ticket.
Existing staff can participate in the chat without claiming it, while assignment
remains exclusive under the existing service rule. This is not a staff dashboard.

Acceptance: click Support without entering anything; verify one private chat and
no modal, write a synthetic issue, have a moderator take and reply, then close
with confirmation. Confirm assignment text, creator read-only history and absent
closed controls. Repeat a click before closing (same ticket), compare Electricity,
and restart without losing IDs/assignment. Test actual ordinary-member access,
not only the owner's view. The deeper cache-membership audit and historical
metadata/duplicate-name review remain separate; no old ticket is deleted or
adopted based on its name in this slice.

### Later, not implemented: staff assignment overview

Provide a staff-only overview of open/unassigned, in-progress and waiting tickets,
showing the assigned moderator and an accessible link to each ticket. Reuse
`support_tickets` and the existing assignment/audit service, with current staff
checks and no public ticket content. This must be a separate reviewable feature,
not a prerequisite for opening or answering tickets.
