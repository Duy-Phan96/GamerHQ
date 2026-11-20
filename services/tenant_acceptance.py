"""Tenant-scoped acceptance projections over the existing setup services.

No setup/lifecycle apply path is invoked. SQL query_only and a restrictive
authorizer provide independent protection if a preview accidentally writes.
Discord transports supplied by the collector must independently reject writes.
"""
from contextlib import contextmanager, closing
import hashlib
import json
import re
import sqlite3
from types import SimpleNamespace

from database import db, tenants
from services import server_templates as templates, staff_access


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def logical(value):
    value = str(value)
    return value if re.fullmatch('[a-zA-Z0-9:_-]{1,120}', value) else 'hash:' + fingerprint(value)


def comparison_value(value):
    """Canonical current state; historical audit aggregates are diagnostic only.

    Keep every decision/reason fingerprint/ID/ACL/lifecycle field. Ownership is
    an unordered multiset: sort but never deduplicate conflicting references.
    """
    import copy
    value = copy.deepcopy(value)
    plan = value['preview']
    plan['ownership'] = sorted(plan['ownership'], key=lambda row:(row['kind'],row['key'],row['id']))
    plan['journal'].pop('audit', None)
    if isinstance(plan.get('messageChecks'),dict):
        plan['messageChecks'].pop('httpFailures',None)
        plan['messageChecks'].pop('rateLimits',None)
        plan['messageChecks'].pop('rateLimitCount',None)
        plan['messageChecks'].pop('rateLimitsTruncated',None)
    return value


def changed_paths(first, second, path=''):
    if type(first) is not type(second):
        return [path]
    if isinstance(first, dict):
        result=[]
        for key in sorted(first.keys() | second.keys()):
            current=path+'/'+str(key)
            result.extend([current] if key not in first or key not in second else changed_paths(first[key],second[key],current))
        return result
    if isinstance(first,list):
        if len(first)!=len(second):return [path+'/length']
        return [item for index,(a,b) in enumerate(zip(first,second)) for item in changed_paths(a,b,path+'/'+str(index))]
    return [] if first==second else [path]


def compare_passes(first, second):
    a,b=comparison_value(first),comparison_value(second)
    paths=changed_paths(a,b)
    raw=changed_paths(first,second)
    before={row['key']:row for row in first['preview']['rows']}
    after={row['key']:row for row in second['preview']['rows']}
    rows=[{'key':key,'fields':changed_paths(before.get(key),after.get(key))}
          for key in sorted(before.keys()|after.keys()) if before.get(key)!=after.get(key)]
    return {'scope':'CURRENT_STATE_V2_AUDIT_DIAGNOSTIC_OWNERSHIP_MULTISET',
            'semanticFingerprints':[fingerprint(a),fingerprint(b)],
            'rawFingerprints':[fingerprint(first),fingerprint(second)],
            'changedPaths':paths[:64], 'changesTruncated':len(paths)>64,
            'rawChangedPaths':raw[:64], 'rawChangesTruncated':len(raw)>64,
            'changedRows':rows[:32], 'rowChangesTruncated':len(rows)>32,
            'components':{key:[fingerprint(a[key]),fingerprint(b[key])] for key in sorted(a)}}


@contextmanager
def readonly_snapshot(denied=None):
    """One fresh bounded in-memory snapshot, reused by existing SQL helpers.

    The installed DB module's request-local read session is source-pinned by
    the controller. Seeding its connection does not patch any global helper or
    hold a live transaction while network reads run. No snapshot file is made.
    """
    with db.read_only():
        session = db._read_session.get()
        if session['connection'] is not None:
            raise ValueError('READ_SNAPSHOT_NESTING_REJECTED')
        connection = sqlite3.connect(':memory:')
        try:
            with closing(sqlite3.connect(db.current_path().resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as source:
                def budget(_, remaining, total):
                    if total * page_size > 32 * 1024**2:
                        raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
                page_size = source.execute('PRAGMA page_size').fetchone()[0]
                if source.execute('PRAGMA page_count').fetchone()[0] * page_size > 32 * 1024**2:
                    raise ValueError('ACCEPTANCE_BUDGET_EXCEEDED')
                source.backup(connection, pages=128, progress=budget)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            session['connection'] = connection
        except BaseException:
            connection.close()
            raise
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                   sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_RECURSIVE}
        def authorize(action, first, second, *_):
            if action == sqlite3.SQLITE_PRAGMA:
                permit = (first in {'integrity_check', 'user_version', 'query_only'} and second is None
                          or first in {'table_info', 'index_list', 'foreign_key_list'}
                          and isinstance(second, str) and re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]{0,63}', second))
            else:
                permit = action in allowed
            if not permit and denied is not None:
                denied.append(action)
            return sqlite3.SQLITE_OK if permit else sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize)
        connection.execute('BEGIN')
        try:
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('DATABASE_INTEGRITY_REJECTED')
            yield connection
        finally:
            connection.rollback()


def overwrites(resource, application_id):
    rows = []
    for principal, overwrite in resource.overwrites.items():
        allow, deny = overwrite.pair()
        role = hasattr(principal, 'permissions')
        public = role or principal.id == application_id
        rows.append({'principalKind': 'role' if role else 'member',
                     'principal': str(principal.id) if public else 'hash:' + fingerprint(str(principal.id)),
                     'allow': str(allow.value), 'deny': str(deny.value)})
    return sorted(rows, key=lambda row: (row['principalKind'], row['principal']))


def inventory(guild, application_id):
    channels = []
    for channel in guild.channels:
        if channel.guild.id != guild.id:
            raise ValueError('FOREIGN_RESOURCE_REJECTED')
        parent = getattr(channel, 'category_id', None)
        if parent is not None and guild.get_channel(parent) is None:
            raise ValueError('UNVERIFIED_PARENT_REJECTED')
        channel_type = getattr(channel.type, 'value', None)
        if type(channel_type) is not int:
            raise ValueError('DISCORD_CHANNEL_TYPE_REJECTED')
        channels.append({'id': str(channel.id), 'type': channel_type,
                         'nameFingerprint':fingerprint(channel.name), 'position':channel.position,
                         'parentId': str(parent) if parent else None,
                         'overwrites': overwrites(channel, application_id)})
    roles = []
    for role in guild.roles:
        if role.guild.id != guild.id:
            raise ValueError('FOREIGN_RESOURCE_REJECTED')
        roles.append({'id': str(role.id), 'permissions': str(role.permissions.value),
                      'nameFingerprint':fingerprint(role.name),
                      'position': role.position, 'managed': role.managed,
                      'staffNameCandidate': role.name == staff_access.ROLE_NAME})
    return {'channels': sorted(channels, key=lambda row: row['id']),
            'roles': sorted(roles, key=lambda row: row['id'])}


def skill_projection(raw):
    result = []
    for row in raw:
        state = row['state']
        if any(type(state.get(key)) is not bool for key in ('available', 'installed', 'configured', 'enabled', 'healthy')):
            raise ValueError('SKILL_RESPONSE_REJECTED')
        running = row.get('runtime', {}).get('running')
        result.append({'id': logical(row['id']), 'version': row['version'] if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.]+)?', str(row['version'])) else None,
                       'state': {key: state[key] for key in ('available', 'installed', 'configured', 'enabled', 'healthy')},
                       'runtime': {'status': 'PASS', 'running': running} if type(running) is bool else
                                  {'status': 'UNVERIFIED', 'reason': 'HOST_API_RUNTIME_FLAG_UNAVAILABLE'},
                       'managementAvailable': row.get('managementAvailable') is True,
                       'source': {key: value if isinstance(value, str) and re.fullmatch('[a-zA-Z0-9_.-]{1,100}', value) else None
                                  for key, value in row.get('source', {}).items() if key in {'kind', 'distribution'}}})
    return sorted(result, key=lambda row: row['id'])


def reason_code(row, staff):
    if row.get('template_boundary') == 'Optional module':
        return 'OPTIONAL_MODULE'
    if row.get('template_boundary') == 'Skill-owned':
        return 'SKILL_OWNED'
    if row.get('bulk_blocked'):
        return 'PARENT_BLOCKED'
    if row.get('private') and row['kind'] == 'category' and row.get('bulk_action') not in ('reuse', 'create'):
        return 'STAFF_MAPPING_ABSENT' if staff['mapping'] is None else 'PRIVATE_ACCESS_REVIEW'
    if row.get('bulk_action') == 'review' or row['status'] == 'AMBIGUOUS':
        return 'RESOURCE_REVIEW_REQUIRED'
    return 'NONE'


def project_preview(guild, draft, application_id):
    # Never compare a command-generated guide against the collector's empty
    # metadata tree. Current Core entry points do not contain staff guides.
    if any(row['key'].startswith('server_staff_commands_message_id') for row in draft['boards']['rows']):
        raise ValueError('COMMAND_REGISTRY_UNAVAILABLE')
    raw = db.get_setting(staff_access.key(guild))
    mapped = staff_access.mapped_role(guild)
    staff = {'mapping': raw if raw and raw.isdecimal() else None,
             'verified': mapped is not None,
             'candidates': sorted(str(role.id) for role in guild.roles if role.name == staff_access.ROLE_NAME),
             'permissions': str(mapped.permissions.value) if mapped else None}
    rows = []
    ownership = []
    for row in draft['structure']['rows'] + draft['boards']['rows']:
        resource = row.get('resource')
        actual_guild = getattr(getattr(resource, 'guild', None), 'id', guild.id)
        channel = getattr(resource, 'channel', None)
        destination = row.get('channel')
        if (actual_guild != guild.id or channel is not None and channel.guild.id != guild.id
                or destination is not None and destination.guild.id != guild.id):
            raise ValueError('FOREIGN_RESOURCE_REJECTED')
        ident = str(resource.id) if resource is not None else None
        saved = row.get('raw')
        saved = str(saved) if saved is not None and str(saved).isdecimal() else None
        # Board scans also contain explicit warning rows. They are review
        # evidence, not bulk structure rows with a bulk_action contract.
        decision = row['status'] if row['kind'] in ('message','warning') else templates.template_decision(row, draft['choices'])
        safe = sorted(str(candidate.id) for candidate in row.get('candidates', ()) if getattr(getattr(candidate, 'guild', None), 'id', guild.id) == guild.id)
        rows.append({'key': logical(row['key']), 'kind': row['kind'], 'status': row['status'],
                     'decision': decision, 'savedId': saved, 'resourceId': ident, 'candidates': safe,
                     'parentKey': logical(row['parent']) if row.get('parent') else None,
                     'reason': reason_code(row, staff), 'reasonFingerprint': fingerprint(row.get('reason', '')),
                     'private': row.get('private') is True,
                     'channelId':str(channel.id) if channel is not None else None,
                     'destinationChannelId':str(destination.id) if destination is not None else None,
                     'proposedOverwrites':overwrites(SimpleNamespace(overwrites=row['create_overwrites']), application_id)
                          if 'create_overwrites' in row else None})
        if saved:
            ownership.append({'id': saved, 'kind': row['kind'], 'key': logical(row['key'])})
    record = draft['progress']
    pending = record.get('pending_creates', ())
    journal = {'state': record.get('state') if record.get('state') in ('applying', 'remaining', 'complete') else 'ABSENT',
               'remaining': record.get('remaining') if type(record.get('remaining')) is int else None,
               'pendingCreationCheckpoints': sorted(logical(key) for key in pending),
               'resultsInterpretation': 'CREATED_LABELS_ARE_APPLY_OUTCOMES_NOT_PREVIEW_ACTIONS'}
    # Audit references are history, not active resource ownership.
    with db.connect() as connection:
        audit = connection.execute('SELECT action,COUNT(*),MIN(created_at),MAX(created_at) FROM structure_change_log WHERE guild_id=? GROUP BY action', (guild.id,)).fetchall()
        journal['audit'] = [{'action': action if action in {'CREATE', 'LINK', 'APPLY', 'create', 'link', 'setup', 'created'} else 'OTHER',
                             'count': count, 'firstAt': first, 'lastAt': last} for action, count, first, last in audit]
        lifecycle = connection.execute('SELECT skill_id,installed,configured,enabled,version FROM skill_guild_state WHERE guild_id=? ORDER BY skill_id', (guild.id,)).fetchall()
        foreign_rows = connection.execute('SELECT COUNT(*) FROM skill_guild_state WHERE guild_id<>?', (guild.id,)).fetchone()[0]
        for (role_id,) in connection.execute('SELECT role_id FROM managed_roles'):
            ownership.append({'id': str(role_id), 'kind': 'role', 'key': 'role_mapping:' + str(role_id)})
        for game in db.get_all_games():
            for field in ('role_id','channel_id','category_id','chat_channel_id','memes_channel_id','clips_channel_id','lfg_channel_id','create_voice_channel_id'):
                if game.get(field):
                    ownership.append({'id': str(game[field]), 'kind': 'role' if field == 'role_id' else 'text',
                                      'key': 'game:' + str(game['id']) + ':' + field})
    return {'counts': templates.counts(draft), 'journal': journal, 'staff': staff,
            'rows': sorted(rows, key=lambda row: row['key']), 'ownership': ownership,
            'foreignLifecycleRows': foreign_rows,
            'lifecycle': [{'id': logical(skill), 'installed': bool(installed), 'configured': bool(configured),
                           'enabled': bool(enabled), 'storedVersion': version if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', str(version)) else None}
                          for skill, installed, configured, enabled, version in lifecycle]}


async def guard_probes(routes):
    """Existing ingress dispatch with in-memory envelopes/callbacks only.

    No Discord interaction response is sent and no real lifecycle/job executes.
    The caller has separately authenticated the live cohort and loaded its models.
    """
    import discord
    from services.tenant_routing import GuildAdmissionError, GuildCommandTree, GuildViewStore
    ids = sorted(routes.allowed)
    if len(ids) < 2:
        return {'status': 'UNVERIFIED', 'reason': 'TWO_TENANTS_REQUIRED'}
    first, second = ids[:2]
    guild = routes.get_guild(second)
    class Response:
        calls = 0
        def is_done(self): return False
        async def send_message(self, *args, **kwargs): self.calls += 1
    response = Response()
    event = SimpleNamespace(client=SimpleNamespace(guild_routes=routes), guild_id=second,
                            guild=guild, application_id=routes.application_id,
                            user=guild.get_member(guild.owner_id), response=response,
                            followup=SimpleNamespace(send=response.send_message))
    results = {}
    # A real command tree reaches its inherited command implementation only if
    # the shared envelope guard incorrectly accepts this forged binding.
    spoof = SimpleNamespace(**vars(event))
    spoof.guild = routes.get_guild(first)
    try:
        await object.__new__(GuildCommandTree)._call(spoof)
        results['command'] = response.calls == 1
    except Exception:
        results['command'] = False
    for label, view in (('component', discord.ui.View()), ('modal', discord.ui.Modal(title='Read-only guard probe'))):
        called = []
        async def callback(*args): called.append(True)
        view._scheduled_task = callback
        store = GuildViewStore(SimpleNamespace())
        with routes.scope(first): store.add_view(view)
        before = response.calls
        try:
            await view._scheduled_task(*(event,) if label == 'modal' else (None, event))
            results[label] = not called and response.calls in (before, before + 1)
        except Exception:
            results[label] = False
        finally:
            view.stop()
    for label in ('scheduler', 'skillLifecycle'):
        called = []
        async def callback(**kwargs): called.append(True)
        runtime = SimpleNamespace(restore_guild=callback, scheduler=SimpleNamespace(run_due=callback))
        try:
            with routes.scope(first):
                if label == 'scheduler': await routes.run_due(runtime, second)
                else: await routes.restore(runtime, second)
        except (PermissionError, GuildAdmissionError):
            results[label] = not called
        else:
            results[label] = False
    return {'status': 'PASS' if all(results.values()) else 'FAIL',
            'mode': 'IN_MEMORY_DISPATCH_WITH_VERIFIED_LIVE_BINDINGS', 'paths': results,
            'liveMutatorsInvoked': False}


async def collect(store, application_id, load_guild, load_skills, *, preview=templates.preview, inspect_messages=None):
    """Two independent REST inventories/SQL snapshots and ephemeral preview plans.

    Every callback is scoped to the current tenant. The live runner enforces GET
    transport allowlists. Unexpected errors return only a stable public code.
    """
    guild_ids = (store.primary_guild, *sorted(store.additional_guilds))
    receipts = []
    inventories = {}
    denied = []
    for guild_id in guild_ids:
        passes = []
        incomplete = None
        stage = 'DATABASE_SNAPSHOT'
        try:
            for _ in range(2):
                with store.scope(guild_id), readonly_snapshot(denied):
                    stage = 'DISCORD_INVENTORY'
                    guild, actor, bot = await load_guild(guild_id)
                    if guild.id != guild_id or actor.id != guild.owner_id or guild.me.id != application_id:
                        raise ValueError('GUILD_IDENTITY_REJECTED')
                    stage = 'INVENTORY_PROJECTION'
                    snapshot = inventory(guild, application_id)
                    stage = 'TEMPLATE_PREVIEW'
                    draft = await preview(guild, actor, templates.GAMERHQ, bot)
                    stage = 'TEMPLATE_PROJECTION'
                    projection = project_preview(guild, draft, application_id)
                    if inspect_messages is not None:
                        stage = 'MAPPED_MESSAGE_PROBES'
                        projection['messageChecks'] = await inspect_messages(guild,draft,application_id)
                    stage = 'HOST_SKILL_STATE'
                    skills = skill_projection(await load_skills(guild_id))
                    mismatches = []
                    for persisted in projection['lifecycle']:
                        observed = next((row for row in skills if row['id'] == persisted['id']), None)
                        if observed is None or any(observed['state'][key] != persisted[key] for key in ('installed', 'configured', 'enabled')):
                            mismatches.append(persisted['id'])
                    for observed in skills:
                        if any(observed['state'][key] for key in ('installed', 'configured', 'enabled')) and not any(row['id'] == observed['id'] for row in projection['lifecycle']):
                            mismatches.append(observed['id'])
                    current={'inventory':snapshot,'preview':projection,'skills':skills,'lifecycleMismatches':mismatches}
                    if projection.get('messageChecks',{}).get('unverifiedRows'):
                        stage='MAPPED_MESSAGE_VERIFICATION'
                        incomplete=current
                        raise ValueError('DISCORD_MESSAGE_READ_INCOMPLETE')
                    passes.append(current)
            comparison=compare_passes(passes[0],passes[1])
            stable = comparison['semanticFingerprints'][0] == comparison['semanticFingerprints'][1]
            result = passes[-1]
            inventories[guild_id] = {row['id'] for group in ('channels', 'roles') for row in result['inventory'][group]}
            inventories[guild_id].update(row['resourceId'] for row in result['preview']['rows'] if row['kind'] == 'message' and row['resourceId'])
            blockers = sum(result['preview']['counts'][key] for key in ('Needs review','Blocked','Will create','Will reuse'))
            selected = result['preview']['journal']['state'] != 'ABSENT'
            template_status = 'UNVERIFIED' if not selected else 'FAIL' if blockers else 'PASS'
            status = ('FAIL' if not stable or result['lifecycleMismatches'] or result['preview']['foreignLifecycleRows'] or template_status == 'FAIL'
                      else 'UNVERIFIED' if not selected or any(row['runtime']['status'] == 'UNVERIFIED' for row in result['skills']) else 'PASS')
            receipts.append({'guildId': str(guild_id), 'status': status, 'previewStable': stable,
                             'templateAcceptance': {'status': template_status, 'selected': selected},
                             'previewFingerprints': comparison['semanticFingerprints'],
                             'comparison':comparison,'firstPass':passes[0], **result})
        except Exception as error:
            known = {'FOREIGN_RESOURCE_REJECTED', 'UNVERIFIED_PARENT_REJECTED', 'GUILD_IDENTITY_REJECTED',
                     'DATABASE_INTEGRITY_REJECTED', 'SKILL_RESPONSE_REJECTED', 'READ_ONLY_REQUEST_REJECTED',
                     'DISCORD_RESPONSE_REJECTED', 'DISCORD_READ_REJECTED', 'ACCEPTANCE_BUDGET_EXCEEDED',
                     'DISCORD_CHANNEL_TYPE_REJECTED', 'DISCORD_RESPONSE_INVALID', 'DISCORD_MODEL_INVALID',
                     'DISCORD_RATE_LIMITED', 'DISCORD_TRANSPORT_FAILED', 'READ_TIMEOUT', 'COMMAND_REGISTRY_UNAVAILABLE',
                     'DISCORD_MESSAGE_BINDING_REJECTED','DISCORD_MESSAGE_READ_INCOMPLETE'}
            known.add('HOST_SKILL_READ_REJECTED')
            reason = error.args[0] if error.args and isinstance(error.args[0], str) and error.args[0] in known else 'COLLECTION_UNAVAILABLE'
            if reason == 'COLLECTION_UNAVAILABLE':
                import asyncio
                from services.tenant_routing import GuildAdmissionError, GuildCorePermissionsError
                if isinstance(error, GuildCorePermissionsError): reason = 'CORE_PERMISSION_REJECTED'
                elif isinstance(error, GuildAdmissionError): reason = 'GUILD_BINDING_REJECTED'
                elif isinstance(error, (TimeoutError, asyncio.TimeoutError)): reason = 'READ_TIMEOUT'
                elif isinstance(error, json.JSONDecodeError): reason = 'RESPONSE_JSON_INVALID'
                elif isinstance(error, TypeError): reason = 'COLLECTION_TYPE_INVALID'
                elif isinstance(error, (KeyError, AttributeError, ValueError)): reason = 'COLLECTION_SCHEMA_INVALID'
                else: reason = 'COLLECTION_INTERNAL_ERROR'
            status = getattr(error, 'status', None)
            if type(status) is int and 300 <= status <= 599:
                if reason not in {'HOST_SKILL_READ_REJECTED', 'DISCORD_RATE_LIMITED', 'DISCORD_RESPONSE_INVALID'}:
                    reason = 'DISCORD_READ_REJECTED'
            if reason == 'READ_ONLY_REQUEST_REJECTED':
                denied.append('HTTP')
            receipts.append({'guildId': str(guild_id), 'status': 'UNVERIFIED', 'reason': reason,
                             'stage': stage, 'passCount': len(passes), **({'lastVerifiedPass': passes[-1]} if passes else {})})
            if incomplete is not None:
                receipts[-1]['incompletePass']=incomplete
                if passes:receipts[-1]['incompleteComparison']=compare_passes(passes[-1],incomplete)
            if type(status) is int and 100 <= status <= 599:
                receipts[-1]['httpStatus'] = status
            operation = getattr(error, 'operation', None)
            if operation in {'BOT_IDENTITY','GUILD_MEMBERSHIP','GUILD_IDENTITY','GUILD_ROLES','GUILD_CHANNELS','HOST_MEMBER','OWNER_MEMBER','MANAGED_MESSAGE','MODEL_BUILD'}:
                receipts[-1]['operation'] = operation
            code = getattr(error, 'code', None)
            if type(code) is int and 0 <= code <= 999999:
                receipts[-1]['discordCode'] = code
    for receipt in receipts:
        if 'preview' not in receipt:
            continue
        foreign = set().union(*(values for guild, values in inventories.items() if str(guild) != receipt['guildId']))
        conflicts = [row for row in receipt['preview']['ownership'] if row['id'] in foreign]
        receipt['foreignMappings'] = conflicts
        own = inventories[int(receipt['guildId'])]
        receipt['unresolvedMappings'] = [row for row in receipt['preview']['ownership'] if row['id'] not in own]
        if conflicts or receipt['unresolvedMappings']:
            receipt['status'] = 'FAIL'
    return {'schemaVersion': 1, 'state': 'TENANT_ACCEPTANCE_COLLECTED',
            'status': 'FAIL' if denied or any(row['status'] == 'FAIL' for row in receipts) else 'UNVERIFIED',
            'guilds': receipts,
            'mutationsAttempted': bool(denied),
            'liveMutationPathTests': {'status': 'UNVERIFIED', 'reason': 'READ_ONLY_OPERATION_DOES_NOT_INVOKE_MUTATORS'},
            'gatewayRestarted': False, 'databaseChanged': False, 'discordResourcesChanged': False,
            'productionChanged': False}
