"""Local gateway/event-loop heartbeat; no HTTP listener or Discord API call."""
import asyncio
import json
import os
import time
from pathlib import Path

HEARTBEAT = Path('/tmp/gamerhq-health.json')


async def heartbeat(bot):
    while True:
        payload = {'pid': os.getpid(), 'time': time.time(), 'ready': bot.is_ready() and not bot.is_closed()}
        temporary = HEARTBEAT.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload), encoding='utf-8')
        temporary.replace(HEARTBEAT)
        await asyncio.sleep(30)


def healthy(path=HEARTBEAT, *, now=None):
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(state, dict):
            return False
        age = (time.time() if now is None else now) - state['time']
        os.kill(int(state['pid']), 0)
        return state['ready'] is True and 0 <= age < 90
    except (OSError, ValueError, TypeError, KeyError):
        return False


if __name__ == '__main__':
    raise SystemExit(0 if healthy() else 1)
