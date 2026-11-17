"""Offline Servertest database preparation inside the reviewed Staging container."""
import json
import os
from pathlib import Path

from services.tenant_routing import PILOT_ENV, pilot_routes


def provision(pin, primary, database):
    """Initialize only the secondary binding using the normal tenant store."""
    routes = pilot_routes(pin, primary=primary, database=database, get_guild=lambda _: None)
    if routes is None:
        raise PermissionError('An explicit Staging tenant binding is required.')
    secondary, = routes.store.additional_guilds
    with routes.store.scope(primary):
        pass
    created = routes.store.provision(secondary)
    return {'state': 'PILOT_SECONDARY_DATABASE_READY', 'guildId': str(secondary), 'created': created,
            'primaryDatabaseChanged': False, 'gatewayStarted': False}


def main():
    from config import DB_PATH, GUILD_ID

    if os.geteuid() != 10001 or DB_PATH != Path('/app/runtime/data/gamerhq.db'):
        raise PermissionError('Use the verified Staging container identity and data mount.')
    print(json.dumps(provision(os.environ.get(PILOT_ENV), GUILD_ID, DB_PATH)))


if __name__ == '__main__':
    main()
