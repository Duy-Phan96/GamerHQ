# RC5 public LFG cards (Issue #358)

The Host projects the installed Event Manager's list API into one unpinned
message per public active event in the mapped central LFG board. The next open
occurrence is shown; a recurring event reuses the same message. No additional
event authority or Skill package change is introduced. Events without chat are
supported. The Host refreshes at a 30-second cadence; Discord/rate-limit or
Skill outages can delay synchronization. Buttons revalidate the exact card,
current public event and displayed occurrence; Share returns a public message
jump link, never an invitation token.

Publication requires the existing owner-approved allowPublic policy, running
Skill authority, verified mapped public board and Host permissions. Private
updates through the shared management API must retire any existing public card
before the write. If removal fails, that visibility change is rejected for
manual review. Uncertain sends are recovered only by unique Host marker; if no
unambiguous match exists, retry does not send a duplicate. Only Host-owned
projection messages may be retired; chats and attendance are not affected.

## Acceptance (not yet performed live)

- Create one public and one private event, with chat disabled and enabled.
- Confirm only public cards appear; join/leave and compare counts after refresh.
- Edit title, capacity and date; ensure the same message ID is retained.
- Cancel the first recurring session and verify the next replaces it.
- Switch public to private; verify the card is removed before saving.
- Restart and confirm no duplicate card. Simulate uncertain delivery in tests.
- Pause, resume, delete and disable the Skill; verify projection cleanup.
- Verify old controls cannot join a different occurrence or reveal a private event.
- Confirm no unrelated messages or event channels are changed.

This change is source-only until CI and explicit staging acceptance.
