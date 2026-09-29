# Private lobby administration

Use `/server manage` → **Lobby Admin** → **Confirm Admin Channel** to create or
update **📋・lobby-admin** in the existing private STAFF category. This is an
explicit setup action: startup never creates channels or replacement boards.
The normal setup/repair inventory is unchanged; this optional admin channel is
owned and maintained by the Lobby Admin entry in Server Management.

Only the guild owner, members with the Discord **Administrator** permission,
and GamerHQ may read the channel. A generic staff/moderator role is not sufficient.
Setup keeps stored channel IDs, never adopts an unknown channel by name alone,
and stops on ambiguity or changed resources. An uncertain creation is reserved
in SQLite for manual review instead of creating a duplicate on retry.

The pinned board displays up to six events and opens a paginated private list
for all remaining events. It shows event title, host, start, public/private flag,
participant count and cached voice occupancy. Both public and private events
are included, scoped to this guild. Invite codes, tokens, notes and chat content
are never copied to the overview or operational log. The board checks for changes
every 60 seconds; unchanged state does not trigger another Discord edit. All
member-facing role selectors and event builders keep their current behavior.

Select an open event, review the effects, then use **Confirm Close Event**.
Membership and administrator rights are rechecked before execution. Confirmations
are bound to the actor, guild, event fingerprint and a two-minute timeout. Changed
or already closed events are rejected. Closure uses the existing `lfg_events`
state and is an explicit administrative exception; normal `/lfg manage` remains
creator-only. Admins do not impersonate the host.

Closure marks the event cancelled, disables its event invitation and expires
pending time proposals atomically. An audit record containing administrator ID,
event ID and timestamp is stored in SQLite in the same transaction. A compact
Server Log entry records the action without copying private event details.

## Safe cleanup

An occupied event voice is retained, together with its private event chat. The
closed event remains listed as awaiting cleanup. Once the voice is empty, the
existing event cleanup handles its tracked resources. Unknown/unavailable voice
state or a Discord error must not be treated as an empty room. Shared LFG/game
channels are not deleted. Public event cards retain the existing 24-hour terminal
history window. Event records and the administrative audit remain in SQLite.

No deployment, bot login, existing lobby closure or live Discord cleanup is part
of implementing or testing this feature. Test via pytest with temporary databases
as described in [Development workflow](DEVELOPMENT_WORKFLOW.md).
