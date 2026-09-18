"""Single-process resource coordination during owner-confirmed area cleanup."""
import asyncio
import functools

_locks = {}
cleaning = set()


def area_lock(game_id):
    return _locks.setdefault(int(game_id), asyncio.Lock())


def coordinated_game_creation(function):
    @functools.wraps(function)
    async def wrapped(guild, game, *args, **kwargs):
        async with area_lock(game['id']):
            return await function(guild, game, *args, **kwargs)
    return wrapped


def require_available(game_id):
    if int(game_id) in cleaning:
        raise ValueError('This game area is being cleaned up. Please try again afterward.')
