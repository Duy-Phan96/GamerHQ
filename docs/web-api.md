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

The browser must not call this listener directly.

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
