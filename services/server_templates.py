"""Customer templates over the existing Host structure and managed-message operations."""
from dataclasses import dataclass
import json
import time
from types import SimpleNamespace

from database import db
from services import managed_message_service as managed, server_operations as operations
from services.operation_context import read_scope
from services.server_service import ServerMessageError


@dataclass(frozen=True)
class ServerTemplate:
    key: str
    name: str
    entry_points: tuple[str, ...]


GAMERHQ = ServerTemplate('gamerhq-gaming-community', 'GamerHQ Gaming Community', (
    'welcome', 'guide', 'choose-your-roles', 'choose-your-games', 'looking-for-group',
    'community-events', 'suggestions', 'need-support',
))
TEMPLATES = (GAMERHQ,)


def progress_key(guild, template):
    return f'server_template:{guild.id}:{template.key}'


def progress_record(guild, template):
    try:
        record = json.loads(db.get_setting(progress_key(guild, template)) or '{}')
        return record if isinstance(record, dict) else {}
    except ValueError:
        return {}


def progress(guild, template):
    return progress_record(guild, template).get('state')


def reviewed_destinations(structure, template, *, linked_only=False):
    return {row['name']: row['bulk_id'] for row in structure['rows']
            if row['kind'] == 'text' and row['name'] in template.entry_points
            and row['bulk_action'] == 'reuse' and not row.get('private')
            and (not linked_only or row['status'] == 'EXACT_MATCH')}


def entry_message_keys(guild, destinations):
    from cogs.server import setting_key_for
    from services import game_service, role_panel_service

    by_mapping = {f'managed_channel:{guild.id}:{name}': name for name in destinations}
    if 'welcome' in destinations:
        by_mapping[f'onboarding:{guild.id}:welcome'] = 'welcome'
    keys = {key: by_mapping[mapping] for key, (_, mapping, _) in managed.specs(guild).items() if mapping in by_mapping}
    if 'welcome' in destinations:
        keys[f"server_pinned_message_{destinations['welcome']}"] = 'welcome'
    if 'choose-your-games' in destinations:
        keys['choose_games_message_id'] = 'choose-your-games'
        for title, _ in game_service.build_choose_games_sections():
            keys[operations.messages.SECTION_PREFIX + game_service._choose_games_section_key(title)] = 'choose-your-games'
    if 'choose-your-roles' in destinations:
        board = SimpleNamespace(id=destinations['choose-your-roles'])
        keys.update(dict.fromkeys(role_panel_service.message_keys(guild, board).values(), 'choose-your-roles'))
    if 'looking-for-group' in destinations:
        keys[setting_key_for(SimpleNamespace(id=destinations['looking-for-group']))] = 'looking-for-group'
    return keys


async def preview_entry_points(guild, owner, bot, destinations, pending):
    with db.read_only():
        keys = entry_message_keys(guild, destinations)
    boards = await operations.preview(guild, owner, 'setup', bot, scope='messages', message_keys=set(keys))
    boards['rows'] = [row for row in boards['rows'] if row['key'] in keys
                      and row.get('channel') and row['channel'].id == destinations[keys[row['key']]]]
    with read_scope():
        for row in boards['rows']:
            if row['status'] == 'MISSING':
                if row['key'] in pending:
                    row.update(status='AMBIGUOUS', reason='Earlier publication was not confirmed. Verify the outcome in Advanced Settings.')
                elif not await operations.public_message_channel(row['guild'], row['channel'], publish=True):
                    row.update(status='AMBIGUOUS', reason='Host cannot safely publish and pin in this destination.')
            if row['status'] == 'EXACT_MATCH' and (not row.get('resource') or row['resource'].id not in row['safe']
                                                   or not row['resource'].pinned):
                row.update(status='AMBIGUOUS', reason='Existing managed message needs review. Its content and controls are preserved.')
    return boards, {keys[row['key']] for row in boards['rows']}


async def preview(guild, owner, template, bot=None):
    if template not in TEMPLATES:
        raise ServerMessageError('Unknown server template.')
    structure = await operations.preview(guild, owner, 'setup', bot, scope='bulk')
    pending = set(progress_record(guild, template).get('pending_creates', ()))
    held = set()
    for row in structure['rows']:
        if row['bulk_action'] == 'create' and row['key'] in pending:
            row.update(bulk_action='skip', reason='Earlier creation was not confirmed. Verify the outcome in Advanced Settings.')
            held.add(row['key'])
        elif row['bulk_action'] == 'create' and row.get('parent') in held:
            row.update(bulk_action='skip', bulk_blocked=True, reason='Parent creation needs review')
    choices = {row['key']: row['bulk_id'] for row in structure['rows'] if row['bulk_action'] == 'choose'}
    boards, inspected = await preview_entry_points(guild, owner, bot, reviewed_destinations(structure, template), pending)
    new_boards = tuple(row['name'] for row in structure['rows'] if row['kind'] == 'text' and not row.get('private')
                       and row['name'] in template.entry_points and row['bulk_action'] == 'create')
    return dict(template=template, structure=structure, choices=choices, boards=boards,
                new_boards=new_boards,
                deferred=[name for name in template.entry_points if name not in inspected and name not in new_boards])


def counts(draft):
    decisions = [operations.bulk_decision(row, draft['choices']) for row in draft['structure']['rows']]
    board_states = [row['status'] for row in draft['boards']['rows']]
    return {'Already configured': decisions.count('configured') + board_states.count('EXACT_MATCH'),
            'Will reuse': decisions.count('reuse') + board_states.count('SAFE_ADOPTION'),
            'Will create': decisions.count('create') + board_states.count('MISSING'),
            'Needs review': decisions.count('review') + board_states.count('AMBIGUOUS'),
            'Blocked': decisions.count('blocked') + len(draft['deferred'])}


async def apply(guild, owner, draft, bot=None):
    template = draft['template']
    structure = draft['structure']
    managed.require_admin(guild, owner)
    if (template not in TEMPLATES or owner.id != guild.owner_id or structure['actor_id'] != owner.id
            or structure['guild_id'] != guild.id):
        raise ServerMessageError('Only the server owner can apply this template preview.')
    key = progress_key(guild, template)
    async with managed.lock(key):
        if time.time() - structure['created'] > 240:
            raise ServerMessageError('Open a fresh template preview before applying.')
        pending = set(progress_record(guild, template).get('pending_creates', ()))

        def save(state, remaining=None):
            db.set_setting(key, json.dumps({'state': state, 'remaining': remaining, 'pending_creates': sorted(pending)}))

        def checkpoint(resource_key, started):
            if started and resource_key in pending:
                raise ServerMessageError('An earlier creation is unresolved. Open a fresh template preview.')
            if started:
                pending.add(resource_key)
            else:
                pending.discard(resource_key)
            save('applying')

        save('applying')
        created = {}

        def record_created(row, resource):
            if row['kind'] == 'text' and row['name'] in draft['new_boards']:
                created[row['name']] = resource.id

        result = await operations.apply(guild, owner, structure, bot, confirmed=True, choices=draft['choices'],
                                        create_checkpoint=checkpoint, on_created=record_created)

        current = await operations.preview(guild, owner, 'setup', bot, scope='bulk')
        linked = reviewed_destinations(current, template, linked_only=True)
        approved_ids = {channel_id for name, channel_id in reviewed_destinations(structure, template).items()
                        if linked.get(name) == channel_id}
        reviewed_boards = {**draft['boards'], 'rows': [row for row in draft['boards']['rows']
                           if row['channel'].id in approved_ids]}

        async def apply_boards(boards, status, mode, bucket):
            rows = [row for row in boards['rows'] if row['status'] == status]
            if rows:
                done, skipped = await operations.apply(guild, owner, {**boards, 'mode': mode, 'rows': rows},
                                                       bot, confirmed=True, create_checkpoint=checkpoint)
                result[bucket].extend(done)
                result['needs_review'].extend(skipped)
        await apply_boards(reviewed_boards, 'SAFE_ADOPTION', 'reconcile', 'reused')
        await apply_boards(reviewed_boards, 'MISSING', 'setup', 'created')
        created = {name: channel_id for name, channel_id in created.items() if linked.get(name) == channel_id}
        if created:
            boards, _ = await preview_entry_points(guild, owner, bot, created, pending)
            await apply_boards({**boards, 'created': structure['created']}, 'MISSING', 'setup', 'created')
        refreshed = await preview(guild, owner, template, bot)
        final = counts(refreshed)
        remaining = sum(final[key] for key in ('Will reuse', 'Will create', 'Needs review', 'Blocked'))
        result['remaining'] = [f"{row['label']} · {row['reason']}" for row in refreshed['structure']['rows']
                               if operations.bulk_decision(row, refreshed['choices']) not in ('configured', 'skip')]
        result['remaining'].extend(f"{row['label']} · {row['reason']}" for row in refreshed['boards']['rows']
                                   if row['status'] != 'EXACT_MATCH')
        result['remaining'].extend(f'{name} · Verify the destination for the managed entry point.'
                                   for name in refreshed['deferred'])
        save('remaining' if remaining else 'complete', remaining)
        return result, final, remaining
