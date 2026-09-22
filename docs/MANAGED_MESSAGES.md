# Managed pinned message editor

`/server pinned-messages` is available to the server owner and administrators. Moderator permissions alone do not grant access. All menus, modal submissions and saves recheck access. Menus expire after ten minutes; reopen the command after a restart or expiry.

## Supported boards

The editor uses existing GamerHQ message/channel mappings, bot ownership and a stored content fingerprint. It never lists arbitrary user/admin pins or private ticket messages. Owner setup Repair registers supported boards through the existing fixed-message helper.

| Channel | Managed messages |
| --- | --- |
| support-gamerhq | Support Overview |
| direct-support | Direct Support |
| amazon | Amazon |
| haushaltscheck | Haushaltscheck |
| gaming-deals | Gaming Deals |
| ai-tools | AI Tools |
| welcome | Welcome |
| guide | Guide |
| suggestions | Suggestions |
| need-support | Need Support |

Game/role selectors, generated command inventories, LFG dashboards and private tickets retain their existing feature-specific management flows. This command edits existing healthy pins; it does not create arbitrary channel messages.

## Edit and save

1. Open `/server pinned-messages` and select a channel.
2. Select a message if the channel has several. A single managed message opens automatically. Each board has its own stable key and draft; the UI also supports multiple registered boards sharing a channel.
3. Choose **Edit Content** for ordinary Discord Markdown. Newlines, headings, emphasis, lists, code, mentions and URLs are preserved verbatim. No placeholder markup is interpreted. The limit is 2000 Discord characters; emoji can consume two character units. Empty or oversized content is rejected before saving.
4. Choose **Manage Buttons** to add a Link or an allowlisted Action, select a button, edit its label/emoji, remove it, toggle enabled/disabled, or move it up/down. To change type, remove it and add the desired type. Button order is row-major, with at most 25 buttons (five per row). Labels have at most 80 Discord characters.
5. Choose **Preview** or **Save**. Both show the complete draft and inert buttons, followed by the target channel, board label and **Save Changes / Back / Cancel**. Preview buttons do not open URLs or trigger tickets. Link destinations can be reviewed in the button edit modal.
6. Choose **Save Changes** to edit the existing message in place. Its channel, message ID and current pin are preserved. Mentions render without notifying members. **Back** returns to editing; **Cancel** discards the unsaved session. Returning to channel selection also discards that draft.

Link buttons require a valid HTTPS URL, at most 512 characters, with no embedded login credentials or secret query/fragment parameters. Do not paste tokens or private data into public content or URLs. Buttons are separate structured configuration, never callback code embedded in Markdown. Use Unicode emoji or custom emoji from this server that the bot can use.

Action choices are limited to the board's existing registered handler:

| Board | Action |
| --- | --- |
| Haushaltscheck | `HOUSEHOLD_CHECK_REQUEST` |
| Need Support | `CREATE_SUPPORT_TICKET` |
| Suggestions | `SUBMIT_SUGGESTION` |

Each action can occur once on its board. Other boards support Link buttons. Existing ticket-source checks and persistent callback IDs continue to apply, including after restart. Removing/disabling an action removes/disables that public entry point until restored.

## Defaults, repair and recovery

Saving marks the complete body/button configuration customized in private SQLite. Startup, sync-support and normal owner setup Repair preserve it. Default boards continue to receive generated changes. Customized navigation links must be reviewed after channels change; automatic refresh intentionally preserves the saved body.

**Reset to Default** shows generated defaults and asks for **Confirm Reset**. Only confirmation replaces the customized body/buttons with the latest defaults and returns the board to normal generated updates. Cancel does not reset anything.

Concurrent edits use versions: when another admin or a default refresh changes the record, saving an older draft is rejected. Reopen the editor and apply the intended changes to the latest version. Earlier menus/previews in the same session stop accepting actions when you move to a newer screen.

If Discord delivery fails after confirmation, canonical changes and metadata audit are already saved; the error explicitly reports unconfirmed delivery. Check permissions, run `/server health`, then owner `/server setup` → Repair to deliver the saved configuration. Do not repeatedly confirm an old draft. Pending/invalid/unpinned boards are excluded from editing. Unknown fingerprints or conflicting mappings require manual review and are not overwritten by the editor. A pending partner migration must be completed before editing partner boards.

If Discord definitively rejects the payload as invalid (HTTP 400), the previous canonical configuration is restored and a rejection audit entry is recorded. Reopen the editor to correct the draft; the rejected version cannot be saved again from the old confirmation.

Health reads registry state without changing it. It checks missing channels/messages, duplicate mappings, ownership, fingerprints, button configuration/action IDs and pending delivery. Customized headings are healthy. Repair may recreate a genuinely deleted pin, but editor saves and resets never recreate messages.

The private audit table records actor, channel, stable key, content/buttons changed flags, time and before/after hashes. It does not store full edited content or URLs in audit entries. Keep the runtime database and backups out of Git; run only one bot instance against a guild/database.

Retired energy/course/finance boards are excluded from editing after migration. Historical custom content and audit records remain stored. Custom legacy messages are left untouched for MANUAL_REVIEW; their old ticket actions cannot create new requests. See [partner migration](PARTNERS.md).

Current default partner copy is English except for Haushaltscheck. Repair refreshes generated defaults in place and preserves customized text/buttons. To adopt the English wording on a customized board, preview and confirm Reset to Default; the existing message ID stays unchanged.
