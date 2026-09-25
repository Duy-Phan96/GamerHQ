"""Read-only acceptance diagnostics over existing mappings; never creates or repairs."""
from dataclasses import dataclass
import sqlite3
import time
import discord
from database import db
from services.onboarding_service import alias
from services.community_structure_service import core_channel
from services.server_service import ServerMessageError
from services.music_bot_service import resolve_music_role, should_allow_music_bots, VOICE_RIGHTS, TEXT_RIGHTS, GAME_CHANNEL_FIELDS, blocked_name


@dataclass
class Finding:
    name: str
    state: str
    detail: str


def command_inventory(bot, guild):
    roots = bot.tree.get_commands(guild=guild) or bot.tree.get_commands()
    result = []
    def walk(command, prefix=''):
        name = (prefix + ' ' + command.name).strip()
        if isinstance(command, discord.app_commands.Group):
            for child in command.commands: walk(child, name)
        else:
            result.append((name, command.description))
    for root in roots: walk(root)
    return sorted(result)


async def scan(guild, bot=None, *, messages=True):
    findings = []
    def add(name, state, detail): findings.append(Finding(name,state,detail))
    try:
        with db.connect() as conn:
            conn.execute('SELECT ticket_type FROM support_tickets LIMIT 1').fetchone()
            for table in ('games','settings','lfg_events','lfg_event_members','temp_voice_channels','suggestions','lfg_time_proposals','support_tickets','ticket_audit','managed_message_content','managed_message_audit'):
                conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
            events = [dict(r) for r in conn.execute('SELECT * FROM lfg_events WHERE guild_id=?',(guild.id,))]
            temps = [dict(r) for r in conn.execute('SELECT * FROM temp_voice_channels')]
            suggestions = [dict(r) for r in conn.execute('SELECT * FROM suggestions WHERE guild_id=?',(guild.id,))]
        games = db.get_all_games()
        add('Database','PASS','Required tables readable.')
    except sqlite3.Error:
        add('Database','CRITICAL','Storage unavailable or schema incomplete; check runtime logs before repair.')
        return findings
    from services.instant_gaming_service import diagnostics
    findings.extend(Finding(*row) for row in await diagnostics(guild, messages=messages))
    if bot:
        inventory = command_inventory(bot,guild)
        missing = {'server health','server setup','area manage','voice manage','lfg create','lfg manage','lfg join-code'} - {name for name,_ in inventory}
        add('Runtime / commands','WARN' if missing else 'PASS','Missing registrations: '+', '.join(sorted(missing)) if missing else f'{len(inventory)} supported commands registered.')
        views = list(getattr(bot,'persistent_views',[]))
        ids = {getattr(child,'custom_id',None) for view in views for child in view.children}
        required = {'gamerhq:roles:select','gamerhq:roles:suggest','gamerhq:suggestions:submit','gamerhq:suggestions:ACCEPTED','gamerhq:tickets:create','gamerhq:tickets:take','gamerhq:tickets:wait','gamerhq:tickets:close','gamerhq:offers:electricity'}
        add('Persistent controls','WARN' if not required <= ids else 'PASS','Restart/cog registration needs review.' if not required <= ids else f'{len(views)} persistent views registered; suggestion entry/review available.')
    groups = {
        'start-here': ['welcome','rules','announcements','choose-your-games','choose-your-roles','looking-for-group','guide','need-support'],
        'community': ['newbies','general','introductions','suggestions','bot-commands'],
        'events': ['community-events','tournaments','giveaways'],
    }
    channels = {}
    for group,names in groups.items():
        categories = [c for c in guild.categories if alias(c.name)==group]
        add(group.upper(),'PASS' if len(categories)==1 else 'MANUAL_REVIEW' if len(categories)>1 else 'REPAIRABLE' if group=='events' else 'CRITICAL',f'{len(categories)} category matches.')
        for name in names:
            matches=[c for c in guild.text_channels if alias(c.name)==name and c.category and alias(c.category.name) in groups]
            if len(matches)>1: add(f'{name} duplicates','MANUAL_REVIEW','Multiple core channel names; mapped resource retained, others require review.')
            try: channel=core_channel(guild,name)
            except ServerMessageError:
                add(name,'MANUAL_REVIEW','Multiple name matches; no automatic merge.'); continue
            channels[name]=channel
            if not channel:
                add(name,'REPAIRABLE' if name in {'guide','suggestions','bot-commands','welcome','newbies','community-events'} else 'MANUAL_REVIEW','Expected channel is missing.'); continue
            issue = channel.category is None or alias(channel.category.name)!=group or (name=='guide' and channel.name!='📘・guide')
            if name == 'community-events':
                issue = issue or channel.name != '🎉・community-events' or db.get_setting(f'managed_channel:{guild.id}:{name}') != str(channel.id)
            add(name,'REPAIRABLE' if issue else 'PASS','Managed name/location needs setup repair.' if issue else f'Channel {channel.id}.')
            if group=='start-here' or name=='suggestions':
                everyone=channel.overwrites_for(guild.default_role)
                posting = everyone.send_messages is not False or any(o.send_messages is True and t!=guild.me and not (t in guild.roles and (t.permissions.administrator or t.permissions.manage_messages or t.permissions.manage_guild or t.permissions.moderate_members)) for t,o in channel.overwrites.items())
                if posting: add(f'{name} permissions','REPAIRABLE','Normal posting is not fully disabled.')
    from services.community_structure_service import event_order
    try:
        current, ordered = event_order(guild, await guild.fetch_channels())
        correct = [c.id for c in current] == [c.id for c in ordered]
        add('EVENTS order', 'PASS' if correct else 'REPAIRABLE',
            'Community Events, Tournaments, Giveaways order OK.' if correct else 'Owner Repair restores managed event order.')
    except ServerMessageError:
        add('EVENTS order', 'MANUAL_REVIEW', 'Ambiguous event channels; no automatic merge.')
    from services.support_service import resolve as support_resource, CHANNEL_NAME
    try:
        category = next((c for c in guild.categories if alias(c.name)=='start-here'),None)
        support = support_resource(guild, 'channel')
        channels['support-gamerhq'] = support
        from services.channel_adoption_service import placement as adopted_placement
        display, category = adopted_placement(guild, 'support-gamerhq', CHANNEL_NAME, category)
        valid = category and support and support.name == display and support.category_id == category.id
        from services.channel_change_service import removed
        add('Support GamerHQ', 'PASS' if valid or removed(guild, 'support-gamerhq') else 'REPAIRABLE', 'Affiliate board structure checked; owner setup creates/repairs it.')
        if support:
            overwrite = support.overwrites_for(guild.default_role)
            from services.channel_adoption_service import stored
            expected_send = stored(guild, 'support-gamerhq').get('send_messages', False)
            if overwrite.send_messages is not expected_send or overwrite.view_channel is not True or overwrite.read_message_history is not True:
                add('Support permissions','REPAIRABLE','Support board needs public read-only permissions.')
    except ServerMessageError:
        add('Support GamerHQ','MANUAL_REVIEW','Ambiguous support resources; no automatic merge.')
    from services.channel_adoption_service import stored as adoption_stored
    from services.support_service import resource, PARTNER_CHANNELS, PARTNER_CATEGORY
    try:
        from services import legacy_finance_service as finance
        from services.support_service import legacy_review_channels
        finance_channels = finance.candidates(guild)
        for channel in finance_channels:
            reason = await finance.inspect(guild, channel)
            add('Legacy partner channels', 'MANUAL_REVIEW' if reason else 'REPAIRABLE',
                f'finanzberatung — {reason}' if reason else 'finanzberatung — safe retirement available through owner setup Repair.')
        if any(c not in finance_channels for c in legacy_review_channels(guild)):
            add('Legacy partner channels', 'MANUAL_REVIEW', 'Old channel/history retained; inspect before manual removal. Never delete unknown content.')
        if any(db.get_setting(f'{key}:{guild.id}') for key in ('partner_split', 'partner_reorder', 'household_migration')):
            add('Partner migration', 'REPAIRABLE', 'Partner migration pending; repair permissions and rerun setup/sync.')
        partners = resource(guild, 'partners-benefits', True)
        from services.support_service import direct_support_retirement_reason, channel_key
        direct_id = db.get_setting(channel_key(guild, 'direct-support'))
        direct = guild.get_channel(int(direct_id)) if direct_id and direct_id.isdigit() else None
        if direct_id:
            try:
                reason = await direct_support_retirement_reason(guild, direct) if direct else None
            except discord.HTTPException as exc:
                reason = f'Cannot inspect direct-support: {type(exc).__name__}'
            add('Retired direct-support', 'MANUAL_REVIEW' if reason else 'REPAIRABLE',
                reason or 'Owner setup Repair removes the obsolete managed channel/mappings.')
        from services.channel_adoption_service import order_plans, diagnostics as adoption_diagnostics
        snapshot = await guild.fetch_channels()
        for row in adoption_diagnostics(guild, snapshot):
            add(*row)
        plans = order_plans(guild, snapshot)
        correct = all([c.id for c in current] == [c.id for c in ordered] for current, ordered in plans)
        add('Partner channel order', 'PASS' if correct else 'REPAIRABLE',
            'Compared to default/adopted order; explicit sync restores desired state.')
        add('MARKETPLACE', 'PASS' if partners and partners.name == '🛒 MARKETPLACE' else 'REPAIRABLE', 'Owner setup creates/reuses the mapped partner category.')
        for name, display in PARTNER_CHANNELS.items():
            if removed(guild, name):
                continue
            ch = resource(guild, name)
            channels[name] = ch
            rights = ch.overwrites_for(guild.default_role) if ch else None
            mapped_id = db.get_setting(f'managed_channel:{guild.id}:{name}')
            display, placement = adopted_placement(guild, name, display, partners)
            valid = ch and str(ch.id) == mapped_id and placement and ch.category_id == placement.id and ch.name == display
            valid = valid and rights.view_channel is True and rights.read_message_history is True and rights.send_messages is adoption_stored(guild, name).get('send_messages', False)
            add(name, 'PASS' if valid else 'REPAIRABLE', 'Managed channel ID, partner placement and read-only permissions checked; owner setup repairs missing mappings.')
    except ServerMessageError:
        add('MARKETPLACE', 'MANUAL_REVIEW', 'Conflicting partner mappings; no automatic merge.')
    from services.onboarding_service import read_only_mode, READ_ONLY_INTERACTIVE, guide_overwrites, is_staff, INTERACTIVE_BOARDS, STATIC_BOARDS
    from services.channel_change_service import public_policy
    for name, channel in channels.items():
        if not channel or name not in INTERACTIVE_BOARDS | STATIC_BOARDS:
            continue
        try:
            mode = read_only_mode(channel)
            expected = public_policy(guild, name, guide_overwrites(channel, mode=mode))
        except ServerMessageError:
            add(f'{name} read-only', 'MANUAL_REVIEW', 'Conflicting desired state; review before permission repair.')
            continue
        fields = ('send_messages', 'send_messages_in_threads', 'create_public_threads', 'create_private_threads', 'add_reactions')
        if mode == READ_ONLY_INTERACTIVE:
            fields += ('use_application_commands',)
        missing = set()
        for target, rights in expected.items():
            if target == guild.me or (target in guild.roles and is_staff(target)) or (
                isinstance(target, discord.Member) and (target.bot or any(is_staff(r) for r in target.roles))
            ):
                continue
            # Integration roles are governed by their owning feed helper.
            if name in {'gaming-news', 'gaming-deals', 'free-games'} and target != guild.default_role:
                continue
            actual = channel.overwrites_for(target)
            checks = fields + (('view_channel', 'read_message_history') if target == guild.default_role else ())
            missing.update(bit for bit in checks if getattr(actual, bit) != getattr(rights, bit))
        mode = 'Interactive' if mode == READ_ONLY_INTERACTIVE else 'Static'
        add(f'{name} read-only', 'REPAIRABLE' if missing else 'PASS',
            'Repair available: ' + ', '.join(sorted(missing)) if missing else f'{mode} read-only permissions OK.')
    if bot is not None:
        registered = {item.custom_id for view in getattr(bot, 'persistent_views', []) for item in view.children if getattr(item, 'custom_id', None)}
        expected = {'gamerhq:offers:electricity'}
        add('Partner ticket handlers', 'PASS' if expected <= registered else 'WARN', 'Persistent ELECTRICITY_REQUEST handler checked; restart after updating if missing.')
    add('Instant Gaming integration', 'INFO', 'Optional external configuration; see docs/INSTANT_GAMING.md. No external bot is required for GamerHQ health.')
    from services.gocdkeys_service import status as comparison_status
    try:
        add(*comparison_status(guild, bot))
    except ServerMessageError:
        add('GoCDKeys', 'WARN', 'Ambiguous managed deals channel; owner review required.')
    from services.bot_group_service import diagnostics as bot_groups_health, member as bot_member
    for row in bot_groups_health(guild):
        add(*row)
    from services.instant_gaming_service import affiliate_category, overwrites as ig_overwrites, BOT_RIGHTS
    try:
        stats = affiliate_category(guild)
        valid = stats and stats.overwrites == ig_overwrites(guild, 'ig-purchases', stats.overwrites)
        add('Affiliate Stats', 'PASS' if valid else 'REPAIRABLE', 'Private category and explicit Instant Gaming discovery access checked.')
        free = resource(guild, 'free-games')
        dealgecko = bot_member(guild, 'dealgecko')
        access = free and dealgecko and all(getattr(free.overwrites_for(dealgecko), bit) is True and getattr(free.permissions_for(dealgecko), bit) for bit in BOT_RIGHTS)
        add('DealGecko free-games access', 'PASS' if access else 'WARN', 'Optional bot posting access checked; configure verified DEALGECKO_BOT_ID and owner Repair if missing.')
        from services.bot_group_service import resolve as group_role
        deals = resource(guild, 'gaming-deals')
        gaming_role = group_role(guild, 'gaming')
        hq_access = deals and guild.me and all(getattr(deals.permissions_for(guild.me), bit)
            for bit in ('view_channel', 'read_message_history', 'send_messages', 'embed_links'))
        add('GamerHQ gaming-deals access', 'PASS' if hq_access else 'WARN',
            'Effective read/reply/link permissions checked; owner Repair can restore access.')
        for label, target in [('DealGecko', dealgecko), ('Gaming Bots', gaming_role),
                              ('Instant Gaming', bot_member(guild, 'instant-gaming'))]:
            access = deals and target and all(getattr(deals.overwrites_for(target), bit) is True and getattr(deals.permissions_for(target), bit) for bit in BOT_RIGHTS)
            parent_ok = deals and target and deals.category and deals.category.overwrites_for(target).view_channel is not False
            add(label + ' gaming-deals access', 'PASS' if access and parent_ok else 'WARN',
                'Public posting/embed access and parent visibility checked; owner Repair can restore access. External dashboard routing is optional.')
    except ServerMessageError as exc:
        add('Affiliate/free-games resources', 'MANUAL_REVIEW', str(exc))
    from services import ticket_service as tickets
    from services.onboarding_service import is_staff
    add('Ticket Staff access','PASS' if any(is_staff(r) for r in guild.roles) else 'WARN','Uses current moderation roles and server owner; review role policy.')
    try:
        category=tickets.mapped(guild,'support-tickets',True)
        if not category: raise ServerMessageError('Missing private ticket category.')
        tickets.check_private(category)
        logs=tickets.mapped(guild,'ticket-logs')
        if not logs: raise ServerMessageError('Missing private ticket logs.')
        tickets.check_private(logs)
        records=tickets.list_tickets(guild.id)
        issues=0
        for item in records:
            channel=guild.get_channel(item['channel_id']) if item['channel_id'] else None
            if not channel:
                issues+=1
                continue
            try:
                tickets.check_private(channel,item['creator_discord_id'])
                if channel.category_id!=category.id: issues+=1
                creator=tickets.creator_target(guild,item,channel)
                if item['status']=='CLOSED' and creator and channel.overwrites_for(creator).send_messages is not False: issues+=1
            except ServerMessageError: issues+=1
        add('Ticket System','MANUAL_REVIEW' if issues else 'PASS',f'{len(records)} saved tickets; {issues} channel/privacy issues. History retained.')
        if len(category.text_channels)>=45: add('Ticket capacity','WARN','Ticket category nearly full; review retained history before manual archival. No automatic deletion.')
    except ServerMessageError as exc:
        add('Ticket System','MANUAL_REVIEW',str(exc))
    from services.streamer_hub_service import health as streamer_health
    add(*streamer_health(guild, bot))
    present = [c for c in guild.categories if alias(c.name) == 'voice-channels']
    add('VOICE-CHANNELS', 'PASS' if len(present) == 1 else 'MANUAL_REVIEW', f'{len(present)} category matches.')
    try:
        from cogs.suggestions import inbox
        inbox(guild)
        add('Staff suggestions privacy','PASS','Private inbox checks passed.')
    except ServerMessageError:
        add('Staff suggestions privacy','MANUAL_REVIEW','Missing, ambiguous or unsafe inbox; review STAFF and run setup.')
    if messages:
        from services.support_service import support_sections, message_key, section_channel
        from services import managed_message_service as managed
        support_checks = [(section_channel(section), message_key(guild, section), content.split('\n', 1)[0])
                          for section, content, _ in support_sections()]
        for name,key,prefix in [('guide',f'central_guide:{guild.id}','# 📘 GamerHQ Guide'),('suggestions',f'suggestions_entry:{guild.id}','💡 Suggestions'),('bot-commands','server_community_commands_message_1_id','🤖 Bot Commands'),('need-support',f'ticket_entry:{guild.id}','# 🆘 Need Support?')] + support_checks:
            channel=channels.get(name); raw=db.get_setting(key)
            if not channel or not raw or not str(raw).isdigit():
                add(f'{name} pin','REPAIRABLE','Canonical mapping missing; setup can recover/create the managed message.'); continue
            try:
                message=await channel.fetch_message(int(raw))
                try:
                    state = managed.load(key)
                except (ValueError, TypeError):
                    add(f'{name} pin', 'MANUAL_REVIEW', 'Malformed managed configuration; manual review required.')
                    continue
                if name == 'electricity' and state and (any(b.get('target') == 'HOUSEHOLD_CHECK_REQUEST' for b in state['buttons']) or 'haushaltscheck' in (message.content or '').lower()):
                    add('Electricity legacy customization', 'MANUAL_REVIEW', 'Preserved custom pin needs owner editor review or Reset to Default; legacy action is disabled.')
                valid=guild.me and message.author.id==guild.me.id and (managed.owns(state, channel, message) if state else (message.content or '').startswith(prefix))
                add(f'{name} pin','PASS' if valid and message.pinned else 'REPAIRABLE' if valid else 'MANUAL_REVIEW','Canonical author/content/pin checked.')
            except discord.NotFound: add(f'{name} pin','REPAIRABLE','Managed message deleted; setup can recreate it.')
            except discord.HTTPException: add(f'{name} pin','WARN','Could not inspect message; check permissions and retry.')
    from services.managed_message_service import health as managed_health
    registry_issues = await managed_health(guild, messages=messages)
    add('Managed message registry', 'MANUAL_REVIEW' if registry_issues else 'PASS',
        f'{len(registry_issues)} board issues; check mappings/configuration and pending delivery.' if registry_issues else 'Managed identity, custom configuration and allowlisted actions checked.')
    role,error=resolve_music_role(guild,persist=False)
    if error: add('Music Bots','MANUAL_REVIEW',error)
    else:
        bad=[]; exposed=[]
        for channel in guild.channels:
            if should_allow_music_bots(channel):
                rights=VOICE_RIGHTS if isinstance(channel,discord.VoiceChannel) else TEXT_RIGHTS
                if any(getattr(channel.overwrites_for(role),r) is not True for r in rights): bad.append(channel.id)
            if (blocked_name(channel) or (getattr(channel,'category',None) and blocked_name(channel.category))) and channel.overwrites_for(role).view_channel is True:
                exposed.append(channel.id)
        add('Music Bots permissions','REPAIRABLE' if bad else 'PASS',f'{len(bad)} managed resources missing explicit grants.')
        if exposed: add('Music Bots privacy','MANUAL_REVIEW',f'{len(exposed)} protected resources explicitly allow Music Bots; review overrides.')
        members=getattr(guild,'members',[])
        bots=[m for m in members if getattr(m,'bot',False) and role in m.roles]
        add('Music bot membership','PASS' if bots else 'WARN',f'{len(bots)} cached bot members carry the role; third-party playback is not tested.')
    mapped=set()
    for game in games:
        if game.get('selectable') and not game.get('role_id'): add('Game role','MANUAL_REVIEW',f'Game {game["id"]}: visible game has no role mapping.')
        if game.get('role_id') and not guild.get_role(game['role_id']): add('Game role','MANUAL_REVIEW',f'Game {game["id"]}: mapped role missing.')
        cid=game.get('category_id')
        if cid:
            if cid in mapped: add('Game Area duplicate','MANUAL_REVIEW',f'Category {cid} is mapped to multiple games.')
            mapped.add(cid); category=guild.get_channel(cid)
            if not isinstance(category,discord.CategoryChannel): add('Game Area','MANUAL_REVIEW',f'Game {game["id"]}: category {cid} missing; never infer a replacement from its name.')
            for field in GAME_CHANNEL_FIELDS:
                ch=guild.get_channel(game[field]) if game.get(field) else None
                if game.get(field) and (not ch or ch.category_id!=cid): add('Game Area mapping','MANUAL_REVIEW',f'Game {game["id"]}: {field} missing/moved.')
        elif game.get('area_enabled'): add('Game Area','MANUAL_REVIEW',f'Game {game["id"]}: enabled area lacks category ID.')
    if not any(f.name.startswith('Game') and f.state!='PASS' for f in findings): add('Game Areas','PASS',f'{len(mapped)} area mappings checked.')
    known_categories={'start-here','community','events','staff','staff-area','moderators','moderator','mods','mod','team','streamers','gamerhq-streamers','voice-channels','support-gamerhq','support-tickets','partners-benefits','marketplace'}
    unknown=[c for c in guild.categories if c.id not in mapped and alias(c.name) not in known_categories]
    if unknown: add('Unknown categories','MANUAL_REVIEW',f'{len(unknown)} unmapped categories retained; may include legitimate custom/Streamer areas.')
    for row in temps:
        ch=guild.get_channel(row['channel_id'])
        if not ch: add('Temporary Voice record','MANUAL_REVIEW',f'Channel {row["channel_id"]} absent from this guild cache; verify guild/owner before cleanup.')
        elif not guild.get_member(row['host_id']): add('Temporary Voice owner','WARN',f'Room {ch.id}: owner not cached/present; staff can manage it, occupied room retained.')
    generators=[c for c in guild.channels if isinstance(c,discord.VoiceChannel) and alias(c.name)=='create-voice']
    add('Create Voice','PASS' if generators else 'MANUAL_REVIEW',f'{len(generators)} entry points detected; {len(temps)} tracked room records.')
    for event in events:
        if event['status']=='scheduled':
            if not guild.get_member(event['host_id']): add('LFG host','WARN',f'Lobby {event["id"]}: host absent/not cached; admin should review.')
            if event['start_at']+21600<=int(time.time()): add('LFG expiry','WARN',f'Lobby {event["id"]}: overdue; scheduler recovery required.')
            if not event.get('dashboard_message_id') or not guild.get_channel(event.get('dashboard_channel_id')): add('LFG dashboard','WARN',f'Lobby {event["id"]}: dashboard mapping missing; periodic recovery should restore it.')
    if messages:
        # Cap message fetches for large servers; complete records remain available
        # to the existing periodic recovery jobs, without a burst of API calls.
        active = [e for e in events if e['status']=='scheduled' and e.get('dashboard_message_id') and e.get('dashboard_channel_id')]
        for event in active[-10:]:
            channel=guild.get_channel(event['dashboard_channel_id'])
            if not channel: continue
            try:
                message=await channel.fetch_message(event['dashboard_message_id'])
                if not guild.me or message.author.id!=guild.me.id:
                    add('LFG dashboard author','MANUAL_REVIEW',f'Lobby {event["id"]}: mapped message belongs to another author; preserve it.')
            except discord.NotFound:
                add('LFG dashboard message','WARN',f'Lobby {event["id"]}: card deleted; scheduler will recover it.')
            except discord.HTTPException:
                add('LFG dashboard message','WARN',f'Lobby {event["id"]}: inspection failed; retry after permissions recover.')
        if len(active)>10: add('LFG diagnostic limit','WARN','Newest 10 dashboard messages inspected; all DB mappings checked.')
    add('LFG persistence','PASS',f'{len(events)} stored lobbies readable; history retained.')
    missing=sum(not r['staff_message_id'] for r in suggestions)
    add('Suggestion persistence','WARN' if missing else 'PASS',f'{len(suggestions)} records; {missing} deliveries not confirmed. Recovery does not blindly repost.')
    from services.channel_change_service import records as change_records
    pending = change_records(guild.id)
    for status in ('pending', 'ignored', 'repair_failed', 'auto_repaired', 'expired'):
        count = sum(r['status'] == status for r in pending)
        if count:
            add('Managed changes: ' + status, 'CRITICAL' if status == 'repair_failed' else 'INFO' if status == 'auto_repaired' else 'WARN', f'{count} change(s); review the private bot-log notification.')
    if any(not r.get('message_id') for r in pending):
        add('Managed change delivery', 'WARN', 'Undelivered changes; configure an existing private bot-log and verify bot access.')
    from services.role_panel_service import diagnostics as role_diagnostics
    role_issues = await role_diagnostics(guild, messages=messages)
    add('Role settings', 'WARN' if role_issues else 'PASS', '; '.join(role_issues) if role_issues else 'Managed role panels and notification mappings valid.')
    return findings


def summary(findings):
    counts={s:sum(f.state==s for f in findings) for s in ('PASS','WARN','REPAIRABLE','MANUAL_REVIEW','CRITICAL')}
    lines=['# GamerHQ Health',f'✅ {counts["PASS"]} passed · 🔧 {counts["REPAIRABLE"]} repairable · ⚠️ {counts["WARN"]+counts["MANUAL_REVIEW"]} warnings/review · ❌ {counts["CRITICAL"]} critical']
    for f in [f for f in findings if f.state!='PASS'][:9]: lines.append(f'• {f.name}: {f.detail[:120]}')
    lines.append('Health is read-only. `/server setup` previews repairs; `/server adopt` keeps intentional supported changes.')
    return '\n'.join(lines)[:1900]
