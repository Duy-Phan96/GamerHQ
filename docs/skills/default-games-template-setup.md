# Default games in Core server setup (RC5)

The same owner-confirmed GamerHQ template now includes default game role
initialization as a final phase. Core structure and managed entry points must
converge first; a fresh preview then lists the exact role create/link decisions,
with no game text channels. This intentionally separates the role-inventory
mutation from the Core confirmation, which relies on stable role evidence.
The owner uses the same Apply Template flow, not a separate Games command.

The existing default-role service remains responsible for catalog eligibility,
role safety, stale-plan rejection, uncertain-creation checkpoints and
preserving intentionally hidden games. The selector reads the existing game
database dynamically. Core totals stay separate from the game role decisions;
remaining work and completion include the game phase.

Offline integration tests exercise fresh Core setup, the role-only final
phase, hidden-game retention, no extra channels and repeat convergence.
Live validation remains required before production deployment.
