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

Current verified entries retain their existing actions: general support opens
its form; Electricity creates the existing typed private human-assisted tariff
request. No external tariff-search API was introduced.

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
python -m pytest tests/test_ticket_entry_repair.py tests/test_tickets.py tests/test_energy_offers.py tests/test_managed_messages.py tests/test_support.py tests/test_reconciliation.py tests/test_global_message_duplicates.py tests/test_production_management.py
python -m pytest
```

After owner-controlled deployment, check both entries, confirm only a verified
binding repair, and reopen the original button. With synthetic content, submit
one general ticket and one Electricity request. Check intended creator/staff
access, denial for another normal member, repeated-click deduplication, Take /
Waiting / confirmed Close, retained read-only history and behavior after restart.
Record the actual running commit and results privately. Offline tests are not
live acceptance. STAB-02's complete VPS lifecycle/recovery audit remains the next
gate; in particular, do not assume missing member cache means departed users.

See [managed messages](MANAGED_MESSAGES.md), [operations](PRODUCTION_OPERATIONS.md)
and [rollback](../ROLLBACK.md). No new configuration values or secrets are needed.
