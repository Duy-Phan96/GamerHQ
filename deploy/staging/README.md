# Isolated staging bootstrap and Host-managed updates

Use [the verified one-time owner procedure](OWNER_SETUP.md). It replaces the earlier
manual-copy/root-dispatcher handoff. The accepted operator command retrieves its
installer from an immutable GitHub commit, verifies SHA-256, and creates its own
private temporary path. No script/public-key file must already exist on the VPS.

Staging uses the existing VPS/Docker binaries with a **separate rootless daemon**,
unprivileged forced-command identity, private configuration/data/backups and a
UID-specific systemd sandbox. No sudo or production Docker-group membership is
granted. The production daemon, bot and data remain untouched. The trusted Host
continues to own release compatibility, staging, health, history and rollback;
portable Skills do not receive deployment capabilities.

The profile/Compose/release files here remain unconfigured templates. Bootstrap
rewrites only their new staging configuration paths, generates the staging API
secret, and records the allocated UID/socket/daemon/source identity. Actual private
release trust and dedicated Discord credentials are still owner inputs. A read-only
watchdog starts, but no database, bot container or Gateway is created by bootstrap.

[Rootless Docker documentation](https://docs.docker.com/engine/security/rootless/)
explains the separate unprivileged daemon; [its systemd guidance](https://docs.docker.com/engine/security/rootless/tips/)
requires a user service and cgroup-v2 delegation for resource limits. Setup never
stops production Docker or weakens global kernel/SSH controls to bypass a failed
prerequisite. SSH restrictions follow [OpenSSH forced-command guidance](https://man.openbsd.org/sshd_config).

Protected configuration, candidate selection, build, installation and future updates
run through the prepared Host-owned workflows. Credentials travel on SSH stdin;
GitHub environment approval and explicit live/apply approvals stay separate.
See the owner procedure for the complete initial sequence and revocation/failure
handling. Do not use the obsolete placeholder command or create a general deployment
runner with production Docker/root authority.
