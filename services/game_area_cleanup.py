"""Conservative preview and one-area-at-a-time confirmed deletion."""
import logging

import discord

from database import db
from services.game_area_safety import area_lock, cleaning
from services.music_bot_service import GAME_CHANNEL_FIELDS, blocked_name

log = logging.getLogger(__name__)


def authorized(guild, actor):
    return actor.id == guild.owner_id or bool(actor.guild_permissions.administrator)


def inspect_area(guild, game, *, channels=None, manual=False):
    objects = {c.id: c for c in (channels if channels is not None else guild.channels)}
    category = objects.get(game.get('category_id'))
    reasons = []
    from services.onboarding_service import alias
    protected = category and alias(category.name) in {'start-here', 'community', 'events', 'voice-channels', 'streamers', 'gamerhq-streamers', 'partners-benefits'}
    if not isinstance(category, discord.CategoryChannel) or blocked_name(category) or protected:
        reasons.append('Missing or protected category; cannot establish managed area')
    if not manual and (game.get('selectable') or game.get('active')):
        reasons.append('Game is visible or marked active')
    linked = {int(game[field]) for field in GAME_CHANNEL_FIELDS if game.get(field)}
    children = [c for c in objects.values() if getattr(c, 'category_id', None) == game.get('category_id')]
    if any(c.id not in linked for c in children):
        reasons.append('Untracked/user-created child channel exists')
    if any(cid in objects and getattr(objects[cid], 'category_id', None) != game.get('category_id') for cid in linked):
        reasons.append('A linked channel was moved outside this area')
    if any(isinstance(c, discord.VoiceChannel) and c.members for c in children):
        reasons.append('Voice is occupied')
    with db.connect() as conn:
        if conn.execute('SELECT 1 FROM games WHERE category_id=? AND id!=?', (game.get('category_id'), game['id'])).fetchone():
            reasons.append('Category is shared by multiple game records')
        if conn.execute("SELECT 1 FROM lfg_events WHERE game_id=? AND (status NOT IN ('completed','cancelled') OR voice_channel_id IS NOT NULL OR private_channel_id IS NOT NULL OR ended_at IS NOT NULL)", (game['id'],)).fetchone():
            reasons.append('Active/scheduled event or pending event resources')
        if conn.execute('SELECT 1 FROM temp_voice_channels WHERE game_id=?', (game['id'],)).fetchone():
            reasons.append('Tracked temporary voice resources exist')
        area_ids = linked | {game.get('category_id')}
        # Any other stored reference to a resource makes automatic cleanup unsafe.
        for other in conn.execute('SELECT * FROM games WHERE id!=?', (game['id'],)):
            if area_ids & {other[field] for field in GAME_CHANNEL_FIELDS}:
                reasons.append('A channel is shared with another game')
        refs = []
        for table, fields in (
            ('lfg_events', ('channel_id', 'dashboard_channel_id', 'private_channel_id', 'voice_channel_id')),
            ('lfg_event_messages', ('channel_id',)),
            ('temp_voice_channels', ('channel_id',)),
            ('streamer_profiles', ('category_id', 'create_voice_channel_id')),
            ('streamer_channels', ('channel_id',)),
        ):
            for row in conn.execute(f'SELECT * FROM {table}'):
                refs.extend(row[field] for field in fields if row[field] is not None)
        if area_ids & set(refs):
            reasons.append('Stored event/streamer/resource channel references exist')
        if any(str(row['value']).isdigit() and int(row['value']) in area_ids for row in conn.execute('SELECT value FROM settings')):
            reasons.append('Area resource is referenced by server settings')
    def signature(obj):
        permissions = tuple(sorted((target.id, *[p.value for p in overwrite.pair()]) for target, overwrite in obj.overwrites.items()))
        return (obj.id, obj.name, getattr(obj, 'category_id', None), getattr(obj, 'last_message_id', None), permissions)
    fingerprint = (tuple(sorted(game.items())), tuple(sorted(signature(c) for c in children)), signature(category) if category else None)
    return {'game': game, 'category': category, 'children': children, 'safe': not reasons,
            'reasons': reasons or ['No events, temporary resources or unrelated children'], 'fingerprint': fingerprint, 'manual': manual}


def scan_areas(guild):
    areas = [inspect_area(guild, game) for game in db.get_all_games() if game.get('category_id')]
    known = {row['game']['category_id'] for row in areas}
    unknown = [c for c in guild.categories if c.id not in known]
    return areas, unknown


async def delete_confirmed_area(guild, actor, preview):
    if not authorized(guild, actor):
        raise ValueError('Only the server owner or an administrator can confirm cleanup.')
    if not preview['safe']:
        raise ValueError('This area was not a safe candidate in the preview.')
    game_id = preview['game']['id']
    async with area_lock(game_id):
        cleaning.add(game_id)
        deleted = []
        try:
            current = db.get_game_by_id(game_id)
            if not current:
                return 'Skipped: game record changed.'
            fresh_channels = await guild.fetch_channels()
            fresh = inspect_area(guild, current, channels=fresh_channels, manual=preview.get('manual', False))
            if not fresh['safe'] or fresh['fingerprint'] != preview['fingerprint']:
                return 'Skipped: conditions changed since preview. ' + '; '.join(fresh['reasons'])
            expected = fresh
            for resource_id in [c.id for c in fresh['children']] + [fresh['category'].id]:
                if not authorized(guild, actor):
                    return 'Stopped: administrator permission was removed.'
                # Fresh Discord topology and current DB checks immediately precede each delete.
                current = db.get_game_by_id(game_id)
                if current is None:
                    return 'Stopped: game record changed; manual review required.'
                channels = await guild.fetch_channels()
                checked = inspect_area(guild, current, channels=channels, manual=preview.get('manual', False))
                if not checked['safe'] or checked['fingerprint'] != expected['fingerprint']:
                    return f'Stopped after {len(deleted)} resource(s): conditions changed; review a new preview.'
                resource = next((c for c in channels if c.id == resource_id), None)
                if resource is None:
                    return 'Stopped: a resource disappeared; review a new preview.'
                await resource.delete(reason=f'GamerHQ confirmed cleanup: game {game_id}, owner/admin {actor.id}')
                deleted.append(resource_id)
                log.warning('GamerHQ cleanup actor=%s game=%s deleted_resource=%s', actor.id, game_id, resource_id)
                if resource_id != fresh['category'].id:
                    with db.connect() as conn:
                        for field in GAME_CHANNEL_FIELDS:
                            conn.execute(f'UPDATE games SET {field}=NULL WHERE id=? AND {field}=?', (game_id, resource_id))
                    # Build expected next state from the just-validated topology, not a new unsafe snapshot.
                    expected = inspect_area(guild, db.get_game_by_id(game_id), channels=[c for c in channels if c.id != resource_id], manual=preview.get('manual', False))
            db.deactivate_game(game_id)
            return f"Removed area for {current['name']} ({len(deleted)} resources). Game record, role and visibility kept."
        except discord.HTTPException as exc:
            log.exception('GamerHQ cleanup interrupted game=%s after %s deletions', game_id, len(deleted))
            return f'Cleanup stopped after {len(deleted)} resources ({type(exc).__name__}). Review a fresh preview; game record kept.'
        finally:
            cleaning.discard(game_id)
