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
            streamers = [dict(r) for r in conn.execute('SELECT * FROM streamer_profiles WHERE guild_id=?',(guild.id,))]
        games = db.get_all_games()
        add('Database','PASS','Required tables readable.')
    except sqlite3.Error:
        add('Database','CRITICAL','Storage unavailable or schema incomplete; check runtime logs before repair.')
        return findings
    if bot:
        inventory = command_inventory(bot,guild)
        missing = {'server health','server setup','area manage','voice manage','lfg create','lfg manage','lfg join-code'} - {name for name,_ in inventory}
        add('Runtime / commands','WARN' if missing else 'PASS','Missing registrations: '+', '.join(sorted(missing)) if missing else f'{len(inventory)} supported commands registered.')
        views = list(getattr(bot,'persistent_views',[]))
        ids = {getattr(child,'custom_id',None) for view in views for child in view.children}
        required = {'gamerhq:suggestions:submit','gamerhq:suggestions:ACCEPTED','gamerhq:tickets:create','gamerhq:tickets:take','gamerhq:tickets:wait','gamerhq:tickets:close','gamerhq:offers:energy-support','gamerhq:offers:energy-course'}
        add('Persistent controls','WARN' if not required <= ids else 'PASS','Restart/cog registration needs review.' if not required <= ids else f'{len(views)} persistent views registered; suggestion entry/review available.')
    groups = {
        'start-here': ['welcome','rules','announcements','choose-your-games','choose-your-roles','looking-for-group','guide','need-support'],
        'community': ['newbies','general','introductions','suggestions','bot-commands'],
        'events': ['tournaments','giveaways'],
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
                add(name,'REPAIRABLE' if name in {'guide','suggestions','bot-commands','welcome','newbies'} else 'MANUAL_REVIEW','Expected channel is missing.'); continue
            issue = channel.category is None or alias(channel.category.name)!=group or (name=='guide' and channel.name!='📘・guide')
            add(name,'REPAIRABLE' if issue else 'PASS','Managed name/location needs setup repair.' if issue else f'Channel {channel.id}.')
            if group=='start-here' or name=='suggestions':
                everyone=channel.overwrites_for(guild.default_role)
                posting = everyone.send_messages is not False or any(o.send_messages is True and t!=guild.me and not (t in guild.roles and (t.permissions.administrator or t.permissions.manage_messages or t.permissions.manage_guild or t.permissions.moderate_members)) for t,o in channel.overwrites.items())
                if posting: add(f'{name} permissions','REPAIRABLE','Normal posting is not fully disabled.')
                if everyone.use_application_commands is False: add(f'{name} interactions','MANUAL_REVIEW','Application commands explicitly denied; review custom policy.')
    from services.support_service import resolve as support_resource, CHANNEL_NAME
    try:
        category = next((c for c in guild.categories if alias(c.name)=='start-here'),None)
        support = support_resource(guild, 'channel')
        channels['support-gamerhq'] = support
        valid = category and support and support.name == CHANNEL_NAME and support.category_id == category.id
        add('Support GamerHQ', 'PASS' if valid else 'REPAIRABLE', 'Affiliate board structure checked; owner setup creates/repairs it.')
        if support:
            overwrite = support.overwrites_for(guild.default_role)
            if overwrite.send_messages is not False or overwrite.view_channel is not True or overwrite.read_message_history is not True:
                add('Support permissions','REPAIRABLE','Support board needs public read-only permissions.')
    except ServerMessageError:
        add('Support GamerHQ','MANUAL_REVIEW','Ambiguous support resources; no automatic merge.')
    from services.support_service import resource, PARTNER_CHANNELS, PARTNER_CATEGORY, legacy_review_channel
    try:
        legacy = legacy_review_channel(guild)
        if legacy:
            add('Legacy germany-services', 'MANUAL_REVIEW', 'Old channel/history retained; inspect before manual removal. Never delete unknown content.')
        if db.get_setting(f'partner_split:{guild.id}'):
            add('Partner migration', 'REPAIRABLE', 'Split migration pending; repair permissions and rerun setup/sync.')
        partners = resource(guild, 'partners-benefits', True)
        add('PARTNERS & BENEFITS', 'PASS' if partners and partners.name == PARTNER_CATEGORY else 'REPAIRABLE', 'Owner setup creates/reuses the partner category.')
        for name, display in PARTNER_CHANNELS.items():
            ch = resource(guild, name)
            channels[name] = ch
            rights = ch.overwrites_for(guild.default_role) if ch else None
            mapped_id = db.get_setting(f'managed_channel:{guild.id}:{name}')
            valid = ch and str(ch.id) == mapped_id and partners and ch.category_id == partners.id and ch.name == display
            valid = valid and rights.view_channel is True and rights.read_message_history is True and rights.send_messages is False
            add(name, 'PASS' if valid else 'REPAIRABLE', 'Managed channel ID, partner placement and read-only permissions checked; owner setup repairs missing mappings.')
    except ServerMessageError:
        add('PARTNERS & BENEFITS', 'MANUAL_REVIEW', 'Conflicting partner mappings; no automatic merge.')
    if bot is not None:
        registered = {item.custom_id for view in getattr(bot, 'persistent_views', []) for item in view.children if getattr(item, 'custom_id', None)}
        expected = {'gamerhq:offers:energy-support', 'gamerhq:offers:energy-course', 'gamerhq:offers:finance'}
        add('Partner ticket handlers', 'PASS' if expected <= registered else 'WARN', 'Persistent energy, course and finance handlers checked; restart after updating if missing.')
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
    for group in ('streamers','voice-channels'):
        present=[c for c in guild.categories if alias(c.name) in {group,'gamerhq-streamers' if group=='streamers' else group}]
        add(group.upper(),'PASS' if len(present)==1 else 'MANUAL_REVIEW',f'{len(present)} category matches.')
    for name in ('streamer-guide','streamer-commands','stream-updates','choose-streamers'):
        found=[c for c in guild.text_channels if alias(c.name)==name]
        add(name,'PASS' if len(found)==1 else 'MANUAL_REVIEW',f'{len(found)} channels found; Streamer structure is not rebuilt by health.')
    for profile in streamers:
        if profile.get('follower_role_id') and not guild.get_role(profile['follower_role_id']):
            add('Streamer role','MANUAL_REVIEW',f'Profile {profile["user_id"]}: follower role mapping missing.')
        for field in ('category_id','create_voice_channel_id'):
            if profile.get(field) and not guild.get_channel(profile[field]):
                add('Streamer resource','MANUAL_REVIEW',f'Profile {profile["user_id"]}: {field} missing; review Streamer tools.')
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
    known_categories={'start-here','community','events','staff','staff-area','moderators','moderator','mods','mod','team','streamers','gamerhq-streamers','voice-channels','support-gamerhq','support-tickets','partners-benefits'}
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
    return findings


def summary(findings):
    counts={s:sum(f.state==s for f in findings) for s in ('PASS','WARN','REPAIRABLE','MANUAL_REVIEW','CRITICAL')}
    lines=['# GamerHQ Health',f'✅ {counts["PASS"]} passed · 🔧 {counts["REPAIRABLE"]} repairable · ⚠️ {counts["WARN"]+counts["MANUAL_REVIEW"]} warnings/review · ❌ {counts["CRITICAL"]} critical']
    for f in [f for f in findings if f.state!='PASS'][:9]: lines.append(f'• {f.name}: {f.detail[:120]}')
    lines.append('Health is read-only. Owner `/server setup` previews known repairs; unknown resources require review.')
    return '\n'.join(lines)[:1900]
