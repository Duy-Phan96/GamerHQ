# Skill Management UI Schema V1

## Goal

The Management UI Schema is a portable, declarative description of how a Skill
may be configured.

It exists so multiple clients can render the same Skill configuration:

```text
Skill Manifest
    ↓
Management UI Schema
    ↓
┌───────────────┬───────────────┐
│ Discord Admin │ Web Dashboard │
└───────────────┴───────────────┘
    ↓
Versioned Management APIs
    ↓
Skill-owned configuration
```

A Skill does not ship executable dashboard JavaScript through this contract.

## Manifest integration

A Skill may optionally declare:

```python
management_ui=ManagementUiSchema(...)
```

Older Skills without a Management UI Schema remain valid.

A declared schema must reference management API contracts that are also declared
by the same Skill manifest.

## Schema structure

V1 contains:

- schema version;
- read management contract;
- write management contract;
- sections;
- fields.

Example:

```python
ManagementUiSchema(
    version="1",
    read_contract="example.get-config.v1",
    write_contract="example.update-config.v1",
    sections=(
        ManagementSection(
            id="general",
            title="General",
            fields=(
                ManagementField(
                    key="enabled",
                    label="Enabled",
                    type="boolean",
                    config_path="settings.enabled",
                ),
            ),
        ),
    ),
)
```

## Field types

Supported V1 field types:

- `boolean`
- `integer`
- `string`
- `long_text`
- `select`
- `multi_select`
- `discord_channel`
- `discord_role`
- `timezone`
- `schedule`
- `collection`

Hosts decide how these are rendered.

For example:

```text
boolean
→ checkbox / toggle

integer
→ number input

discord_channel
→ Discord channel picker

collection
→ host-specific collection/list editor
```

The portable contract describes meaning and validation, not CSS, React
components or Discord UI classes.

## Config paths

Each field points to a dot-separated path inside the Skill's configuration
payload.

Example:

```text
xpSources.voice.enabled
xpSources.voice.xp
levelCurve.maxLevel
announcements.channelId
```

V1 requires unique config paths inside one schema.

## Validation

Fields may declare:

- required state;
- numeric minimum;
- numeric maximum;
- select options;
- help description.

Select and multi-select fields must declare options.

Arbitrary frontend code or arbitrary field types are rejected.

## Read / write contracts

The schema references two versioned Management APIs:

```text
read_contract
write_contract
```

The read contract returns the authoritative Skill configuration.

The write contract persists validated configuration.

Clients should preserve any revision or stale-state semantics exposed by the
Skill instead of implementing their own source of truth.

## Host access

The GamerHQ host Runtime may expose the schema for a registered Skill through
the registry/manifest boundary.

The host must not import the Skill's private implementation merely to render a
configuration screen.

## Progression reference implementation

Progression & Achievements is the first built-in reference provider.

Its schema currently describes:

- XP Sources;
- Level Curve;
- Achievements;
- Rewards;
- Announcements.

Simple values such as Voice XP, daily caps, level curve values and announcement
settings map directly to typed fields.

Achievements and Rewards are marked as `collection` fields in V1. A richer
generic collection-item schema can be added later once more than one real Skill
requires it.

This deliberately avoids designing an over-complex generic form language before
we have multiple concrete use cases.

## Web platform role

The future `gamerhq-web` dashboard should consume this schema to generate Skill
configuration pages.

Target behavior:

```text
Host discovers Skill
→ Manifest exposes Management UI Schema
→ Web dashboard renders sections/fields
→ User edits configuration
→ Client sends versioned Management API request
→ Skill validates and stores configuration
```

A newly installed external Skill should therefore be able to gain a useful web
configuration surface without requiring a hand-written page in the GamerHQ web
repository.

## Security boundary

The schema is data, not executable UI code.

Community Skills must not be allowed to inject arbitrary JavaScript, React
components or HTML into the main GamerHQ dashboard through this contract.

This protects:

- authenticated user sessions;
- other installed Skills;
- server configuration;
- dashboard consistency;
- future Marketplace security.

## Future extensions

Possible later additions include:

- nested collection item schemas;
- conditional visibility;
- read-only computed fields;
- preview operations;
- destructive-action metadata;
- richer validation;
- field grouping/layout hints;
- Marketplace-specific metadata.

These should be added only when real Skills require them.


## Collection schemas

A `collection` field may remain opaque/read-only to generic clients, or it may
declare a reusable CRUD description through `ManagementCollectionSchema`.

This extension exists for Skills such as Recurring Posts, where management is
not one configuration document but a list of independently managed resources.

A collection may declare:

- list contract;
- create contract;
- optional get contract;
- optional validate/preview contract;
- optional update contract;
- optional pause/resume (set-active) contract;
- optional delete-preview contract;
- optional delete contract;
- item fields;
- item ID path;
- title/status/summary display paths;
- maximum item count.

Example conceptually:

```python
ManagementField(
    key="posts",
    label="Recurring Posts",
    type="collection",
    config_path="posts",
    collection=ManagementCollectionSchema(
        operations=ManagementCollectionOperations(
            list_contract="recurring-posts.list.v1",
            create_contract="recurring-posts.create.v1",
            get_contract="recurring-posts.get.v1",
            validate_contract="recurring-posts.validate.v1",
            update_contract="recurring-posts.update.v1",
            set_active_contract="recurring-posts.set-active.v1",
            delete_preview_contract="recurring-posts.delete-preview.v1",
            delete_contract="recurring-posts.delete.v1",
        ),
        item_fields=(...),
        max_items=20,
    ),
)
```

Every referenced operation must also be declared by the same Skill manifest.
The host therefore never discovers hidden/private CRUD entry points through UI
metadata.

Opaque collections remain valid for backward compatibility. Progression's
Achievements/Rewards can continue to use an opaque collection until their
generic item editor contract is designed from real requirements.


For collection-driven screens, the schema's top-level `read_contract` and
`write_contract` may point to the collection's primary list/create operations.
More specific edit, validation, activation and deletion behavior belongs in the
collection operation metadata. This keeps one generic page entry contract while
still supporting safe resource-level CRUD.


### Collection item identity in operation payloads

Collections distinguish between where an item's ID is read from a response and
which payload key an operation expects.

- `item_id_path` locates the ID in the returned item, for example `id`.
- `item_id_payload_key` names the public Management API payload field used by
  get/update/pause/delete operations, for example `postId`.

Generic hosts must use this metadata instead of hard-coding Skill-specific ID
parameter names.
