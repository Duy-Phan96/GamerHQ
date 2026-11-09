# Read-only staging catalog preview

The restricted staging controller can inspect the pinned 70-title catalog
against a private staging backup after owner enrollment. It reuses the
existing SSH identity, protected GitHub workflow identity and operation lock.

The controller reads recent Event Manager deployment backup records from the
staging database in read-only mode. It considers only existing private files
named by those records under the fixed staging backup root. It chooses the
newest candidate that matches the recorded SHA-256, timestamp bounds,
SQLite integrity and schema, and the isolated staging guild's Event Manager
authority. If none passes, the operation fails closed. The preview reads the
backup and does not create or restore a snapshot.

The Actions receipt includes timestamp, age, integrity, and aggregate catalog
and mapping counts, plus which titles from the public manifest matched or
need review. Database-only titles, bytes, internal IDs, paths and apply
tokens stay on the staging host. The immutable manifest comes from
the reviewed Host revision. The request accepts neither a path nor a manifest.

Owner enrollment uses the existing checksum-verified controller extension
with `--catalog-preview` and approval text `ENROLL STAGING CATALOG PREVIEW`.
The installer preserves the installed Host promotion pin, credentials,
container state and staging data. It verifies the currently installed
controller code before changing its root-owned policy. The workflow must be
registered on `main` with the same checksum the installer pins.

After enrollment, run **Read-only staging catalog preview** and approve the
`deployment-security` review. The workflow contains only the preview
operation. Check for `CATALOG_PREVIEW_VERIFIED` and review backup age and
counts. Catalog apply requires its own separate authorization.
