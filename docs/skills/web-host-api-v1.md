# GamerHQ Host Web API Contract V1

## Ownership

GamerHQ is the authoritative platform/host.

`gamerhq-web` is a client.

The web application must not own or duplicate:

- guild Skill enablement;
- Skill runtime health;
- Skill configuration;
- Skill Management UI Schemas;
- Skill capabilities;
- Discord server state.

Those values are projected from GamerHQ.

## Transport-neutral host service

The GamerHQ repository exposes:

`hosts.gamerhq.web_platform.GamerHQWebPlatformService`

This service is intentionally transport-neutral. It can later be called by an
HTTP adapter without coupling the Discord bot core to FastAPI, Flask or another
web framework.

The service exposes JSON-safe projections for:

- authorized servers;
- server-scoped Skills;
- one Skill's status;
- one Skill's Management UI Schema;
- versioned Skill Management API calls;
- Skill enable / disable.

## Authentication boundary

Discord OAuth belongs to the web/session layer.

The browser must never be trusted to provide its own authorized guild list.

The server-side session layer must:

1. authenticate the Discord user;
2. obtain the guilds the user can manage;
3. intersect them with guilds available to the GamerHQ host;
4. pass only those trusted guild IDs into the host service.

Every server-scoped operation rechecks that the requested guild is in that
trusted set before touching Runtime state.

## Proposed HTTP mapping

A thin HTTP adapter should map V1 like this:

### My Servers

`GET /api/v1/me/servers`

Response:

```json
{
  "servers": [
    {
      "id": "123",
      "name": "GamerHQ",
      "memberCount": 128,
      "manageable": true
    }
  ]
}
```

### My Skills

`GET /api/v1/servers/{guildId}/skills`

Response:

```json
{
  "skills": [
    {
      "id": "progression",
      "name": "Progression & Achievements",
      "version": "0.1.0",
      "description": "...",
      "source": {
        "kind": "built-in",
        "distribution": null
      },
      "state": {
        "available": true,
        "installed": true,
        "enabled": true,
        "configured": true,
        "healthy": true
      },
      "health": {
        "state": "PASS",
        "detail": "..."
      },
      "capabilities": ["storage.skill"],
      "missingCapabilities": [],
      "managementAvailable": true,
      "managementSchemaAvailable": true
    }
  ]
}
```

V1 `installed=true` means the Skill package is present in the GamerHQ host
deployment/registry. Per-guild Marketplace install/uninstall state is a separate
future capability and must not be faked in V1.

### Skill detail

`GET /api/v1/servers/{guildId}/skills/{skillId}`

Returns the same Skill projection as one object.

### Management UI Schema

`GET /api/v1/servers/{guildId}/skills/{skillId}/management-schema`

Response:

```json
{
  "schema": {
    "version": "1",
    "readContract": "progression.get-config.v1",
    "writeContract": "progression.update-config.v1",
    "sections": []
  }
}
```

If a Skill has no schema, `schema` is `null`.

### Management call

`POST /api/v1/servers/{guildId}/skills/{skillId}/management/{contractId}`

Request:

```json
{
  "payload": {}
}
```

The host delegates only through the existing versioned
`SkillManagementRouter`.

The HTTP adapter must not import or call private Skill implementation methods.

### Enable / disable

`PUT /api/v1/servers/{guildId}/skills/{skillId}/enabled`

Request:

```json
{
  "enabled": true
}
```

The host delegates to the existing Runtime `enable_skill` /
`disable_skill` methods.

No web-only enablement store is allowed.

## Revision-safe writes

For Skills such as Progression, the web client reads the configuration revision
through the schema read contract and writes with the Skill-defined stale-state
payload.

Example:

```json
{
  "payload": {
    "config": {},
    "expectedRevision": 6
  }
}
```

If the Skill config was changed from Discord or another web session, the Skill
rejects the stale write.

## Marketplace boundary

The Runtime registry describes Skills currently available to this host
deployment.

A future Marketplace catalog is a separate product surface containing Skills
that may be installed into a deployment/server.

Do not conflate:

- Marketplace catalog availability;
- package installed in the host deployment;
- guild enablement;
- guild configuration.

## Error semantics

The HTTP adapter should translate host exceptions into stable API errors:

- unauthorized/forbidden guild → 403;
- unavailable guild/Skill → 404;
- stale or invalid Skill configuration → 409 or 422;
- missing host capability → 409;
- unexpected host failure → 500 without leaking private Discord/API details.

The JSON error body should use a stable machine code and a user-safe message.

## Future transport

The transport may later live:

- in a dedicated GamerHQ API process;
- in a small web gateway colocated with the host;
- behind a reverse proxy.

That deployment decision must not change the contracts consumed by
`gamerhq-web`.


## Discord resource options

The trusted web BFF can load read-only server resources for generic
`discord_channel` and `discord_role` Management UI fields:

- `GET /api/v1/servers/{guildId}/resources/channels`
- `GET /api/v1/servers/{guildId}/resources/roles`

These routes use the same server-side guild authorization as every other Host
Web API operation.

Channel items expose:

- `id`
- `name`
- `kind`
- `position`
- `categoryId`
- `categoryName`

Role items expose:

- `id`
- `name`
- `position`
- `managed`
- `isDefault`

These endpoints are read-only presentation resources. They do not replace
Skill-side validation. A Skill remains authoritative about whether a selected
channel or role is valid for its operation.


## Guild Skill installation

Host package presence and guild installation are separate lifecycle states.

A package may be present in the host Runtime registry but not yet added to a
specific Discord server.

Add a host-available Skill to one authorized guild with:

`POST /api/v1/servers/{guildId}/skills/{skillId}/install`

The operation is idempotent.

It creates/updates host-owned per-guild lifecycle state with:

- `installed=true`
- `enabled=false` for a new installation

Installation does not automatically enable the Skill and does not execute Skill
lifecycle code.

For backward compatibility, the existing enable path still marks a Skill as
installed when older Discord management flows enable it directly. This
compatibility behavior is transitional until every client has an explicit
review/add flow.

The host cannot dynamically download arbitrary Marketplace code through this
endpoint. The Skill package must already be installed and registered in the
host deployment. Marketplace package deployment/orchestration remains a
separate platform concern.
