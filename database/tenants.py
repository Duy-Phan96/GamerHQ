"""Explicit storage admission for guilds in an owner-reviewed cohort."""
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat


@dataclass(frozen=True, slots=True)
class GuildTenant:
    guild_id: int
    database: Path


@dataclass(slots=True)
class _Binding:
    tenant: GuildTenant
    active: bool = True


_current = ContextVar("gamerhq_guild_tenant", default=None)


def current():
    binding = _current.get()
    if binding is None:
        return None
    if not binding.active:
        raise PermissionError("Guild operation has expired.")
    return binding.tenant


def require_guild(guild_id):
    tenant = current()
    if tenant is not None and tenant.guild_id != guild_id:
        raise PermissionError("Operation belongs to another guild.")


@contextmanager
def _bind(tenant):
    from database import db
    from services.operation_context import read_scope

    previous = current()
    if previous is not None and previous != tenant:
        raise PermissionError("Cannot switch guilds within a tenant operation.")
    if previous is None and db.in_read_only_scope():
        raise PermissionError("Bind the guild before opening a database snapshot.")
    if previous == tenant:
        yield tenant
        return
    binding = _Binding(tenant)
    token = _current.set(binding)
    try:
        with read_scope():
            yield tenant
    finally:
        binding.active = False
        _current.reset(token)


def _guild_id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError("Invalid guild identity.")
    return value


class GuildTenantStore:
    """Routes an explicit cohort to disjoint files. Membership is checked by callers."""

    def __init__(self, *, primary_guild, primary_database, tenant_directory, additional_guilds=()):
        self.primary_guild = _guild_id(primary_guild)
        self.primary_database = Path(primary_database).resolve()
        self.tenant_directory = Path(tenant_directory).resolve()
        self.additional_guilds = frozenset(_guild_id(value) for value in additional_guilds)
        if self.primary_guild in self.additional_guilds:
            raise ValueError("The primary guild already has its database binding.")

    def _tenant(self, guild_id):
        _guild_id(guild_id)
        if guild_id == self.primary_guild:
            return GuildTenant(guild_id, self.primary_database)
        if guild_id not in self.additional_guilds:
            raise PermissionError("Guild is outside the admitted cohort.")
        path = self.tenant_directory / f"{guild_id}.sqlite"
        if path.is_symlink() or path.resolve() == self.primary_database:
            raise PermissionError("Tenant database path is unsafe.")
        return GuildTenant(guild_id, path)

    @staticmethod
    def _verify(tenant, *, primary):
        try:
            info = tenant.database.lstat()
        except FileNotFoundError:
            raise PermissionError("Guild database has not been provisioned.") from None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("Tenant database path is unsafe.")
        if primary:
            return
        if os.name == "posix" and info.st_mode & 0o077:
            raise PermissionError("Tenant database must be private.")
        try:
            with closing(sqlite3.connect(tenant.database.as_uri() + "?mode=ro", uri=True)) as conn:
                identity = conn.execute("SELECT guild_id, ready FROM gamerhq_tenant_identity").fetchall()
        except sqlite3.Error:
            raise PermissionError("Guild database ownership could not be verified.") from None
        if identity != [(tenant.guild_id, 1)]:
            raise PermissionError("Guild database ownership could not be verified.")

    def provision(self, guild_id):
        """Create a new empty tenant once. Existing files must prove ownership."""
        from database import db

        if current() is not None or db.in_read_only_scope():
            raise PermissionError("Provisioning requires a separate operation.")
        tenant = self._tenant(guild_id)
        if self.primary_guild == guild_id:
            self._verify(tenant, primary=True)
            return False
        self.tenant_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            handle = os.open(tenant.database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            self._verify(tenant, primary=False)
            return False
        os.close(handle)
        with _bind(tenant):
            db.init_db()
            db.seed_catalog()
            with db.connect() as conn:
                conn.execute("CREATE TABLE gamerhq_tenant_identity (guild_id INTEGER PRIMARY KEY, ready INTEGER NOT NULL)")
                conn.execute("INSERT INTO gamerhq_tenant_identity VALUES (?, 1)", (guild_id,))
        return True

    @contextmanager
    def scope(self, guild_id):
        require_guild(guild_id)
        tenant = self._tenant(guild_id)
        with _bind(tenant):
            self._verify(tenant, primary=guild_id == self.primary_guild)
            yield tenant
