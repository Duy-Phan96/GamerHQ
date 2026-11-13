"""Customer templates over the existing Host structure and managed-message operations."""
from dataclasses import dataclass
from contextlib import contextmanager
import json
import logging
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
    staff_access: bool = False


GAMERHQ = ServerTemplate('gamerhq-gaming-community', 'GamerHQ Gaming Community', (
    'welcome', 'guide', 'choose-your-roles', 'choose-your-games', 'looking-for-group',
    'community-events', 'suggestions', 'need-support',
), staff_access=True)
TEMPLATES = (GAMERHQ,)
log = logging.getLogger(__name__)


class TemplateApplyError(ServerMessageError):
    pass


@contextmanager
def apply_stage(guild, template, stage):
    try:
        yield
    except Exception as error:
        reason = operations.bulk_error_reason(error)
        log.error('Server template guild=%s template=%s stage=%s error=%s reason=%s',
                  guild.id, template.key, stage, type(error).__name__, reason)
        raise TemplateApplyError(f'Setup paused while {stage}. {reason}. Open a fresh preview to resume.') from None


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
                elif issue := await operations.public_message_issue(row['guild'], row['channel'], publish=True):
                    row.update(status='AMBIGUOUS', reason=issue)
            if row['status'] == 'EXACT_MATCH' and (not row.get('resource') or row['resource'].id not in row['safe']
                                                   or not row['resource'].pinned):
                row.update(status='AMBIGUOUS', reason='Existing managed message needs review. Its content and controls are preserved.')
    return boards, {keys[row['key']] for row in boards['rows']}


async def preview(guild, owner, template, bot=None):
    if template not in TEMPLATES:
        raise ServerMessageError('Unknown server template.')
    structure = await operations.preview(guild, owner, 'setup', bot, scope='bulk', template_staff=template.staff_access)
    record = progress_record(guild, template)
    pending = set(record.get('pending_creates', ()))
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
    return dict(template=template, structure=structure, choices=choices, boards=boards, progress=record,
                new_boards=new_boards,
                deferred=[name for name in template.entry_points if name not in inspected and name not in new_boards])


def counts(draft):
    decisions = [operations.bulk_decision(row, draft['choices']) for row in draft['structure']['rows']]
    board_states = [row['status'] for row in draft['boards']['rows']]
    return {'Already configured': decisions.count('configured') + board_states.count('EXACT_MATCH'),
            'Will reuse': decisions.count('reuse') + board_states.count('SAFE_ADOPTION'),
            'Will create': decisions.count('create') + board_states.count('MISSING'),
            'Needs review': decisions.count('review') + board_states.count('AMBIGUOUS'),
            'Blocked': decisions.count('blocked') + len(draft['deferred']),
            'Optional/Skipped': decisions.count('skip')}


def report_summary(draft):
    from collections import Counter

    structure = draft['structure']['rows']
    lines = [draft['template'].name, *(f'{label}: {count}' for label, count in counts(draft).items()), '']
    for row in structure:
        decision = operations.bulk_decision(row, draft['choices'])
        if row['kind'] == 'staff-role' or row.get('private') and row['kind'] == 'category' or row['name'] == 'gaming-deals':
            if row['kind'] == 'staff-role' or decision not in ('configured', 'skip'):
                lines.append(f"{row['label']} — {operations.BULK_DECISIONS[decision]}. {row.get('reason', '')}".rstrip())
    blocked = Counter(row.get('reason') or 'Needs verification' for row in draft['boards']['rows']
                      if row['status'] == 'AMBIGUOUS')
    lines.extend(f'{count} managed entry points — {reason}' for reason, count in blocked.items())
    staff = next((row for row in structure if row['kind'] == 'staff-role'), None)
    if staff and staff['status'] != 'EXACT_MATCH':
        next_action = ('Confirm the reviewed Staff role, then reopen setup for the private areas.'
                       if staff['bulk_action'] in ('create', 'reuse') else 'Review the Staff role finding before creating private areas.')
    elif counts(draft)['Will create'] or counts(draft)['Will reuse']:
        next_action = 'Review the plan, then select Apply Template to perform the listed safe actions.'
    else:
        next_action = 'Review the remaining findings in Full Report before opening a fresh preview.'
    return '\n'.join([*lines, '', 'Next: ' + next_action])


def report_details(draft):
    record = draft.get('progress', {})
    pending = set(record.get('pending_creates', ()))
    lines = [report_summary(draft), '', 'Setup journal',
             'State: ' + (record['state'] if record.get('state') in ('applying', 'remaining', 'complete') else 'No recorded apply'),
             f'Unresolved creation checkpoints: {len(pending)}',
             operations.bulk_preview_report(draft['structure'], draft['choices']), 'Managed entry points']
    for row in draft['structure']['rows'] + draft['boards']['rows']:
        if row['key'] in pending:
            lines.append(f"Unresolved checkpoint: {row['label']} — {row['status']}. Review current inventory before retrying.")
    lines.extend(f"{row['label']} · {row['status']} · {row.get('reason', '')}" for row in draft['boards']['rows'])
    pin_only = {row['channel'].id: row['channel'] for row in draft['boards']['rows']
                if row['status'] == 'AMBIGUOUS' and row.get('reason', '').startswith('Host lacks Pin Messages here.')}
    if pin_only:
        lines.extend(('', 'Permission review — proposed, no changes',
                      'Review a Pin Messages allow for the Host bot member only in these specific channels. '
                      'Keep public access and all other overwrites. If a channel is synced with its category, '
                      'a channel-specific edit will stop permission syncing for that channel.'))
        for channel_id, channel in sorted(pin_only.items()):
            host = channel.guild.me
            channel_pin = channel.overwrites_for(host).pin_messages
            lines.append(f'  {channel.name} (ID {channel_id}) · Host member {host.id} · explicit Pin Messages {channel_pin} → allow')
    lines.extend(f'{name} · Review on a fresh preview once its destination is verified.' for name in draft['deferred'])
    lines.extend(f'{name} · Add missing default entry points after creation and verification.' for name in draft['new_boards'])
    return '\n'.join(lines)


def remaining_structure(draft):
    defaults = {'reuse': 'Verified resource can be linked on a fresh preview.',
                'create': 'Eligible for creation on a fresh preview.'}
    for row in draft['structure']['rows']:
        decision = operations.bulk_decision(row, draft['choices'])
        if decision not in ('configured', 'skip'):
            reason = row.get('reason') or defaults.get(decision, 'Review the current resource evidence.')
            yield f"{row['label']} · {reason}"


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

        with apply_stage(guild, template, 'saving setup progress'):
            save('applying')
        created = {}

        def record_created(row, resource):
            if row['kind'] == 'text' and row['name'] in draft['new_boards']:
                created[row['name']] = resource.id

        with apply_stage(guild, template, 'applying the server structure'):
            result = await operations.apply(guild, owner, structure, bot, confirmed=True, choices=draft['choices'],
                                            create_checkpoint=checkpoint, on_created=record_created)

        with apply_stage(guild, template, 'verifying the server structure'):
            current = await operations.preview(guild, owner, 'setup', bot, scope='bulk', template_staff=template.staff_access)
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
        with apply_stage(guild, template, 'setting up managed entry points'):
            await apply_boards(reviewed_boards, 'SAFE_ADOPTION', 'reconcile', 'reused')
            await apply_boards(reviewed_boards, 'MISSING', 'setup', 'created')
            created = {name: channel_id for name, channel_id in created.items() if linked.get(name) == channel_id}
            if created:
                boards, _ = await preview_entry_points(guild, owner, bot, created, pending)
                await apply_boards({**boards, 'created': structure['created']}, 'MISSING', 'setup', 'created')
        with apply_stage(guild, template, 'verifying the result'):
            refreshed = await preview(guild, owner, template, bot)
            final = counts(refreshed)
            remaining = sum(final[key] for key in ('Will reuse', 'Will create', 'Needs review', 'Blocked'))
            result['remaining'] = list(remaining_structure(refreshed))
            result['remaining'].extend(f"{row['label']} · {row['reason']}" for row in refreshed['boards']['rows']
                                       if row['status'] != 'EXACT_MATCH')
            result['remaining'].extend(f'{name} · Verify the destination for the managed entry point.'
                                       for name in refreshed['deferred'])
        with apply_stage(guild, template, 'saving the verified result'):
            save('remaining' if remaining else 'complete', remaining)
        return result, final, remaining
