# Shared EVENTS category boundary (RC5 / #358)

A verified Core EVENTS mapping and the Event Manager's reviewed text category
may refer to the same public category. Core can reuse the parent, but never
repair its permission overwrites or claim individual Skill-owned channels.
Only the five named Core event boards may use the shared destination.
Other Skill parent bindings, private parents, mismatched identities and
resource-marker collisions fail closed. Unknown children are not reordered
explicitly by Core. Managed entry point writes require an exact Core mapping
when the destination is in a protected parent.

No policy is enabled, no category is moved, and no existing deployment is
changed by this source update. Staging must exercise shared-parent setup,
private and public event creation, restart, board repair and retention of
existing channel/message IDs.
