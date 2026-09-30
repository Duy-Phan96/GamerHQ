# Owner Change Log

Open `/server manage` → **Owner Change Log** → **Confirm Owner Log** as the current guild owner. This explicitly creates or repairs `🕘・owner-changelog` inside the existing private STAFF category. Startup never creates it.

## Permissions and privacy

The feature uses at most three channel permission overwrites: `@everyone` denied, the cached owner allowed to read/use controls, and GamerHQ allowed to publish/manage the launcher. The owner has intrinsic guild access even when absent from the member cache. Do not add a deny for every game/server role: that exceeds Discord's 100-overwrite limit on larger servers.

Confirmed repair replaces only the mapped Owner Change Log channel's access list. Obsolete role/member grants are removed there, not from STAFF, other channels, guild roles, games or member selections. The child does not sync STAFF permissions. Background publication stops if an unrelated explicit view grant is present.

**Discord Administrator bypasses all channel denies. A guild channel cannot be hidden from members or bots with Administrator.** Therefore the pinned channel message contains only a non-sensitive **Review / Undo** launcher. It contains no change records, actor IDs, resource names or event details. The history, review details and Undo/restore controls are ephemeral responses available only to the current guild owner. Administrator alone does not authorize these application actions. The separate general server-log retains its existing staff-facing policy.

See [permission contracts](PERMISSIONS.md) and [Discord's permission reference](https://docs.discord.com/developers/topics/permissions).

## Safe retries

Setup confirmations expire after two minutes and are single-use. A guild-level lock and stored-ID recheck prevent concurrent panels creating duplicates. An unknown same-name channel is never adopted. A stale stored ID requires review rather than silent replacement.

After create/edit, the returned channel object is used to publish the launcher without waiting for the gateway cache. A definite Discord 400/403 creation rejection releases the creation reservation so a fresh setup attempt can work. An uncertain failure retains the reservation for review and never triggers an automatic second creation. A saved channel with failed board delivery is reused on a fresh confirmed setup.

## Verification and rollout

`tests/test_owner_changelog.py` covers 250 extra roles, pruning old grants, effective permission calculation (including Administrator bypass), private owner responses, non-owner rejection, gateway lag, repeated setup and failed creation retries. `tests/test_structure_adoption.py` continues covering existing adoption/Undo behavior.

This fix changes code and tests, not production data or guild-wide roles. After a reviewed deployment, reopen `/server manage` and confirm Owner Change Log again. Do not reuse an old interaction panel. A real Discord check is still required: normal members/staff must not see the channel, a non-owner Administrator must not open its history, and the owner must be able to open the private review.
