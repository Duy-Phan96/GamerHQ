"""Multi-area operations reuse the existing creation and conservative cleanup paths."""
import logging
import time
from database import db
from services import game_area_cleanup as cleanup
from services.game_service import create_game_structure_confirmed, GameStructureError

log = logging.getLogger(__name__)


def candidates(mode):
    games = db.get_all_games()
    if mode == 'add':
        return [g for g in games if g.get('selectable') and not g.get('category_id') and not g.get('area_enabled')]
    return [g for g in games if g.get('category_id')]


def preview(guild, actor, ids):
    if not cleanup.authorized(guild, actor):
        raise ValueError('Owner or administrator required.')
    rows = []
    for gid in sorted(ids):
        game = db.get_game_by_id(gid)
        if game and game.get('category_id'):
            rows.append(cleanup.inspect_area(guild, game, manual=True))
    return rows


async def create_many(guild, actor, ids):
    if not cleanup.authorized(guild, actor):
        raise ValueError('Owner or administrator required.')
    result = []
    for gid in sorted(set(ids)):
        if not cleanup.authorized(guild, actor):
            result.append('Stopped: admin permission was removed.'); break
        game = db.get_game_by_id(gid)
        if not game or not game.get('selectable'):
            outcome = 'Skipped: game is no longer eligible.'
        elif game.get('category_id') or game.get('area_enabled'):
            outcome = 'Already existed; skipped.'
        else:
            try:
                await create_game_structure_confirmed(guild, game, {}, fresh=True)
                outcome = 'Created.'
            except GameStructureError as exc:
                outcome = 'Already existed; review mapping.' if exc.stage == 'ALREADY EXISTS' else f'Error at {exc.stage}; review bot permissions/conflicts.'
                log.exception('Area creation failed actor=%s game=%s', actor.id, gid)
        name = game['name'] if game else str(gid)
        result.append(f'{name}: {outcome}')
        log.warning('Area timestamp=%s actor=%s action=create game=%s result=%s', int(time.time()), actor.id, gid, outcome)
    return result


async def remove_many(guild, actor, snapshots):
    if not cleanup.authorized(guild, actor):
        raise ValueError('Owner or administrator required.')
    result = []
    for row in snapshots:
        if not cleanup.authorized(guild, actor):
            result.append('Stopped: admin permission was removed.'); break
        outcome = await cleanup.delete_confirmed_area(guild, actor, row) if row['safe'] else 'Skipped: ' + '; '.join(row['reasons'])
        result.append(f'{row["game"]["name"]}: {outcome}')
        log.warning('Area timestamp=%s actor=%s action=remove game=%s result=%s', int(time.time()), actor.id, row['game']['id'], outcome)
    return result
