# Isolated Web staging rollout

Tracked in [Host handoff #166](https://github.com/Duy-Phan96/GamerHQ/issues/166).
Web owns the application and its reviewed build. Host owns installation on the
existing isolated staging daemon. The VPS edge terminates public HTTPS and
forwards only the selected hostname to `127.0.0.1:13000`. The Web container
connects to `http://gamerhq-staging:8080` over the existing private network. The
Host's existing staging API credential is injected into the Web server at
container creation. The browser uses only the Web origin and its session cookie.

The existing forced command, authenticated key routing, production path
exclusions, rootless Docker and global operation lock still apply. GitHub
workload identity binds each request to the exact Host workflow bytes on
`main`. The Web health job uses `staging-automation`. Applying a Web image
or rolling back uses `deployment-security` and a verified owner review.
The controller accepts no caller-supplied Docker args, hostname, image, port,
network, secrets or command. The owner installs the root-owned policy once
through the existing checksum-verified controller extension. An image pin
binds a successful Web `main` push run, artifact ID, ZIP SHA-256, source commit
and Docker image ID. Update the reviewed candidate descriptor before enrollment
if the short-retention artifact has expired.

## Owner configuration

1. Choose a dedicated `staging` subdomain of a DNS zone you control. Confirm
   the exact hostname and intended edge. Do not use the SSH IP as a Web origin.
2. Add a DNS-only `A` record for that subdomain to the confirmed public IPv4
   of the staging edge. Omit `AAAA` until an IPv6 listener and route have both
   been verified. Keep the production records and virtual hosts in place.
3. In the Discord Developer Portal, create or select a dedicated staging
   application. Under OAuth2 → Redirects, add the verified HTTPS hostname with
   the exact path `/api/auth/discord/callback`, without a trailing slash.
   The existing flow requests `identify` and `guilds`. Enter the application ID
   and OAuth client secret in the checksum-verified VPS installer’s prompts.
   It generates the Web session secret locally. The staging Gateway token is
   a separate credential and is never used for Web OAuth.
4. In Host → Settings → Environments → deployment-security → Environment
   secrets, add `GAMERHQ_WEB_ARTIFACT_READ_TOKEN`. Use a fine-grained credential
   scoped only to `gamerhq-web` with Actions read access. The runner uses it
   only against the GitHub API and sends a one-minute signed download URL to
   the controller over the pinned SSH channel. The controller verifies the
   downloaded bytes before Docker sees them.
5. Provision the exact HTTPS virtual host on the approved existing edge. Park
   it with a static 503 first and verify the DNS, public certificate and HTTP
   redirect independently. Caddy can issue a certificate when public DNS and
   ports 80/443 are ready. Use the existing proxy if a service already owns
   those ports. The included Caddy site block gives the final routing contract.
   It proxies only loopback port 13000 and strips client-supplied internal
   authorization headers. Keep access logs off for the OAuth route so callback
   codes are not written to request logs.
6. Run the checksum-verified controller extension in a trusted root TTY with
   the reviewed controller commit, approved hostname, and the approval text
   `ENROLL ISOLATED STAGING WEB`. Expect `WEB_ENROLLED` with the exact Web
   commit and `containersChanged: false`. This does not create an edge listener.
7. Run `web-health` to measure actual VPS capacity. Require at least 384 MiB
   available RAM and 2 GiB free on both the staging work filesystem and the
   rootless Docker filesystem. Web is capped at 256 MiB without extra swap,
   half a CPU, 96 processes and two 5 MiB log files per slot. Require a free
   loopback port 13000. Deployment refuses to apply below the headroom limits.
8. Queue `web-deploy`, review it under `deployment-security`, and inspect
   the exact commit, image ID and `WEB_HEALTHY` result. Only then enable the
   reviewed reverse proxy route. Verify the public hostname, certificate,
   security headers, OAuth login and guild selection before using Marketplace
   or changing any staging Skill configuration.

## Recovery and evidence

Two fixed Web slots allow an image switch without mounting a database or a
volume. Only one slot can listen on port 13000. The controller keeps the previous
healthy Web container stopped and brings it back if the candidate fails the
loopback version check or the private Host authentication preflight. The same
controller supports owner-reviewed `web-rollback` to the retained slot. First
install has no prior Web image. Park the HTTPS route if no healthy Web slot
exists. If an interrupted switch leaves the last healthy slot stopped, rollback
resumes it. Host and Skill images, Gateway, channels, and database backups are
outside Web rollback.

Record the workflow run, audit ID, Web commit and image ID. Routine health
reports resource facts, running identity and rollback availability without
dumping the environment or raw container logs. Use the approved proxy and
controller audit paths for operational review. A successful deployment is
separate from real Discord OAuth and manual browser acceptance.
