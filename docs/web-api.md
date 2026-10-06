# GamerHQ Host Web API V1

The Host Web API is a thin authenticated transport over
`GamerHQWebPlatformService`. It does not own Skill state, configuration, or a
second database.

## Boundary

```text
gamerhq-web / trusted BFF
        ↓ Bearer service secret
GamerHQ Host Web API V1
        ↓
GamerHQWebPlatformService
        ↓
Skill Runtime / Management APIs
```

The browser must not call this listener directly. The gateway does not enable CORS; browser-facing access belongs in the trusted BFF.

## Safe defaults

The transport is disabled by default and binds to `127.0.0.1:8080` when
enabled. Keep the loopback binding when gamerhq-web/BFF is deployed on the same
host.

Required configuration when enabled:

```text
GAMERHQ_WEB_API_ENABLED=true
GAMERHQ_WEB_API_HOST=127.0.0.1
GAMERHQ_WEB_API_PORT=8080
GAMERHQ_WEB_API_SECRET=<long random server-to-server secret>
```

The shared secret must be at least 32 characters and must never be exposed to
browser code.

## Trusted request headers

Every request requires:

```http
Authorization: Bearer <GAMERHQ_WEB_API_SECRET>
```

The BFF supplies the Discord guild IDs already authorized for the signed-in
session:

```http
X-GamerHQ-Authorized-Guild-Ids: 123456789,987654321
```

This header is trusted only because the request itself is authenticated as the
server-side BFF. The browser must never be allowed to set or forward this value
directly to the Host API.

## V1 routes

- `GET /api/v1/me/servers`
- `GET /api/v1/servers/{guildId}/skills`
- `GET /api/v1/servers/{guildId}/skills/{skillId}`
- `GET /api/v1/servers/{guildId}/skills/{skillId}/management-schema`
- `POST /api/v1/servers/{guildId}/skills/{skillId}/management/{contractId}`
- `PUT /api/v1/servers/{guildId}/skills/{skillId}/enabled`

The response shapes mirror the existing transport-neutral web platform service.

## Error envelope

Transport errors use:

```json
{
  "code": "machine_readable_code",
  "message": "user-safe message"
}
```

The gateway does not expose private Skill exceptions.

## Deployment note

No public port is required for the first same-host integration. If the web BFF
is later deployed on a different machine, add a private network or TLS-authenticated
reverse proxy rather than exposing this administrative listener directly to the
internet.


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
