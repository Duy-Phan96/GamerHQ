"""Private persisted support tickets; channels/history are never automatically deleted."""
import asyncio
import logging
import time
from types import SimpleNamespace

import discord
from database import db
from services.onboarding_service import alias, unique, is_staff, guide_overwrites, set_read_only
from services.server_service import ServerMessageError, upsert_fixed_message
from services.community_structure_service import resource_key
from cogs.suggestions import private_overwrites, STAFF_ALIASES

log = logging.getLogger(__name__)
ENTRY_NAME = '🆘・need-support'
CATEGORY_NAME = '🎫 SUPPORT TICKETS'
ENTRY_TEXT = ('# 🆘 Need Support?\n\nNeed help with GamerHQ?\n\n'
    'Create a private support ticket and a member of the team will help you.\n\n'
    'Use a ticket for things like:\n- bot problems\n- role or permission issues\n- LFG problems\n'
    '- voice-channel problems\n- account/server questions\n- reporting a technical issue\n\n'
    'Please do not include passwords, payment details or other sensitive information.')
TICKET_TYPES = {
    'GENERAL_SUPPORT': 'General Support',
    'ENERGY_SUPPORT': 'Energy Support',
    'ENERGY_COURSE_REQUEST': 'Energy Course Request',
}
ENERGY_COPY = {
    'ENERGY_SUPPORT': ('⚡ Strom & Gas Support',
        'Deine private Support-Anfrage wurde erstellt.\n\nBeschreibe hier kurz, wobei du Unterstützung brauchst oder welche Fragen du hast.'),
    'ENERGY_COURSE_REQUEST': ('🎓 Kursanfrage – Strom & Gas Vertrieb',
        'Deine Anfrage wurde erstellt.\n\nHier erhältst du die nächsten Schritte und den Zugang zum Kurs.'),
}
FEATURES = {'Bot','LFG','Voice','Roles','Game Areas','Other'}
_user_locks, _ticket_locks, _recovery_locks, _entry_locks = {}, {}, {}, {}


def staff(member):
    return member.id == member.guild.owner_id or any(is_staff(role) for role in member.roles)


def get(ticket_id):
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM support_tickets WHERE id=?',(ticket_id,)).fetchone()
        return dict(row) if row else None


def list_tickets(guild_id):
    with db.connect() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM support_tickets WHERE guild_id=? ORDER BY id',(guild_id,))]


def limit(guild):
    raw = db.get_setting(f'ticket_open_limit:{guild.id}')
    return max(1,min(5,int(raw))) if raw and str(raw).isdigit() else 1


def mapped(guild, name, category=False):
    key = f'managed_category:{guild.id}:{name}' if category else resource_key(guild,name)
    raw = db.get_setting(key)
    collection = guild.categories if category else guild.text_channels
    stored = guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    named = unique(collection,name)
    if stored in collection:
        if named and named.id != stored.id:
            raise ServerMessageError('Conflicting support mappings. Ask an admin to review setup.')
        return stored
    return named


def private(guild, channel=None, creator=None, closed=False):
    result = private_overwrites(guild,channel)
    if guild.me:
        result[guild.me].manage_channels = True
    if creator:
        result[creator] = discord.PermissionOverwrite(view_channel=True,read_message_history=True,
            send_messages=not closed,attach_files=not closed,embed_links=not closed,
            send_messages_in_threads=False,create_public_threads=False,create_private_threads=False,
            manage_channels=False,manage_roles=False,manage_messages=False)
    return result


def creator_target(guild, item, channel):
    return guild.get_member(item['creator_discord_id']) or next((target for target in channel.overwrites if target.id==item['creator_discord_id'] and target not in guild.roles),None)


def check_private(channel, creator_id=None):
    guild = channel.guild
    if channel.overwrites_for(guild.default_role).view_channel is not False:
        raise ServerMessageError('Ticket privacy needs repair. Please contact staff.')
    for target, overwrite in channel.overwrites.items():
        if overwrite.view_channel is True and target != guild.me and target.id != creator_id and not (target in guild.roles and is_staff(target)):
            raise ServerMessageError('Ticket privacy needs staff review.')


def marker(guild_id,ticket_id): return f'gamerhq:ticket:{guild_id}:{ticket_id}'


def ticket_title(item):
    kind = item.get('ticket_type', 'GENERAL_SUPPORT')
    return '# ' + ENERGY_COPY[kind][0] if kind in ENERGY_COPY else f'# 🎫 Support Ticket #{item["id"]:04d}'


def opening_text(item):
    assigned = f'<@{item["assigned_staff_id"]}>' if item['assigned_staff_id'] else 'Not assigned'
    unavailable = ' (left/unavailable)' if item['creator_left'] else ''
    kind = item.get('ticket_type', 'GENERAL_SUPPORT')
    if kind in ENERGY_COPY:
        intro = ENERGY_COPY[kind][1] if item['status'] != 'CLOSED' else 'Diese Anfrage ist geschlossen. Der Verlauf bleibt für dich und das GamerHQ-Team lesbar.'
        return (f'{ticket_title(item)}\n\n{intro}\n\nTicket: #{item["id"]:04d}\n'
                f'Type: {TICKET_TYPES[kind]}\nErstellt von: <@{item["creator_discord_id"]}>{unavailable}\n'
                f'Zugewiesen an: {assigned}\n\nStatus: {item["status"]}')
    return (f'# 🎫 Support Ticket #{item["id"]:04d}\n\nOpened by: <@{item["creator_discord_id"]}>{unavailable}\n'
            f'Assigned to: {assigned}\nType: General Support\n\n**Subject:** {discord.utils.escape_mentions(item["subject"])}\n'
            f'**Feature:** {item["category"]}\n\n**Description:**\n{discord.utils.escape_mentions(item["description"])}\n\n'
            f'**Status:** {item["status"]}\n\n'
            + ('This ticket is closed. Its history remains available to you and the team.' if item['status']=='CLOSED' else 'A member of the GamerHQ team will help you here.'))


async def publish(guild,item):
    from cogs.tickets import TicketActions
    channel = guild.get_channel(item['channel_id']) if item['channel_id'] else None
    if not channel:
        raise ServerMessageError('Ticket channel is unavailable. Staff can review the saved ticket.')
    # Privacy is repaired before posting submitted content or closed-ticket state.
    creator = creator_target(guild,item,channel)
    await channel.edit(overwrites=private(guild,channel,creator,item['status']=='CLOSED'),reason='GamerHQ ticket access')
    title = ticket_title(item)
    message = await upsert_fixed_message(channel,setting_key=f'ticket_opening:{guild.id}:{item["id"]}',
        content=opening_text(item),allowed_mentions=discord.AllowedMentions.none(),view=TicketActions(),pin=True,recover_match=lambda msg:(msg.content or '').startswith(title+'\n'))
    with db.connect() as conn:
        conn.execute('UPDATE support_tickets SET opening_message_id=? WHERE id=?',(message.id,item['id']))
    return channel


async def audit(guild,item,actor_id,action):
    now = int(time.time())
    with db.connect() as conn:
        conn.execute('INSERT INTO ticket_audit(ticket_id,actor_id,action,created_at) VALUES(?,?,?,?)',(item['id'],actor_id,action,now))
    log.warning('ticket timestamp=%s actor=%s ticket=%s action=%s status=%s type=%s',now,actor_id,item['id'],action,item['status'],item['ticket_type'])
    try:
        channel = mapped(guild,'ticket-logs')
        if channel:
            check_private(channel)
            await channel.send(content=f'🎫 Ticket #{item["id"]:04d} · {TICKET_TYPES[item["ticket_type"]]} · {action} · actor {actor_id} · {item["status"]}\nCreator {item["creator_discord_id"]} · assigned {item["assigned_staff_id"] or "—"}\nOpened <t:{item["created_at"]}:f>'+(f' · closed <t:{item["closed_at"]}:f>' if item['closed_at'] else '')+f' · <#{item["channel_id"]}>',allowed_mentions=discord.AllowedMentions.none())
    except (discord.HTTPException,ServerMessageError):
        log.exception('Ticket log delivery failed ticket=%s; DB audit retained.',item['id'])


async def open_ticket(guild,member,subject,description,feature='Other', *, ticket_type='GENERAL_SUPPORT'):
    if ticket_type not in TICKET_TYPES:
        raise ValueError('Unknown ticket type.')
    subject,description,feature=subject.strip(),description.strip(),feature.strip() or 'Other'
    feature = next((value for value in FEATURES if value.casefold()==feature.casefold()),feature)
    if not 1<=len(subject)<=100 or not 1<=len(description)<=900 or feature not in FEATURES:
        raise ValueError('Enter a subject (1–100 characters), description (1–900) and feature: Bot, LFG, Voice, Roles, Game Areas or Other.')
    if member.guild.id!=guild.id or member.bot:
        raise ValueError('Tickets are available to server members.')
    async with _user_locks.setdefault((guild.id,member.id),asyncio.Lock()):
        category=mapped(guild,'support-tickets',True)
        if not category:
            raise ServerMessageError('Tickets are not set up yet. Please contact an admin.')
        check_private(category)
        now=int(time.time())
        with db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            rows=conn.execute("SELECT * FROM support_tickets WHERE guild_id=? AND creator_discord_id=? AND ticket_type=? AND status!='CLOSED' ORDER BY id",(guild.id,member.id,ticket_type)).fetchall()
            if len(rows)>=limit(guild):
                return dict(rows[0]),False
            same=next((r for r in rows if r['subject']==subject and r['description']==description and r['created_at']>=now-30),None)
            if same:return dict(same),False
            if len(category.text_channels)>=50:
                raise ServerMessageError('The ticket area is full. Please contact staff to review retained tickets.')
            cursor=conn.execute('INSERT INTO support_tickets(guild_id,creator_discord_id,subject,description,category,created_at,updated_at,ticket_type) VALUES(?,?,?,?,?,?,?,?)',(guild.id,member.id,subject,description,feature,now,now,ticket_type))
            ticket_id=cursor.lastrowid
        # Reservation remains on uncertain API failure; retry/recovery must not
        # create a second channel after a send/create response was lost.
        item=get(ticket_id)
        async with _ticket_locks.setdefault(ticket_id,asyncio.Lock()):
            try:
                channel=await category.create_text_channel(f'ticket-{ticket_id:04d}',topic=marker(guild.id,ticket_id),overwrites=private(guild,creator=member),reason=f'GamerHQ ticket #{ticket_id:04d}')
                with db.connect() as conn:
                    conn.execute('UPDATE support_tickets SET channel_id=? WHERE id=?',(channel.id,ticket_id))
                item=get(ticket_id)
                await publish(guild,item)
                await audit(guild,get(ticket_id),member.id,'created')
            except Exception:
                log.exception('Ticket creation incomplete guild=%s ticket=%s; reservation retained.',guild.id,ticket_id)
                raise
        return get(ticket_id),True


def authorize(guild,actor,item,staff_only=False):
    if not item or item['guild_id']!=guild.id or actor.guild.id!=guild.id:
        raise ValueError('This ticket is unavailable.')
    if not staff(actor) and (staff_only or item['creator_discord_id']!=actor.id):
        raise ValueError('Only the ticket creator or authorized staff can use this action.' if not staff_only else 'Staff only.')


async def change(guild,actor,ticket_id,action):
    async with _ticket_locks.setdefault(ticket_id,asyncio.Lock()):
        item=get(ticket_id)
        authorize(guild,actor,item,staff_only=action!='close')
        if item['status']=='CLOSED':
            if action=='close':
                await publish(guild,item)
                return item
            raise ValueError('This ticket is closed.')
        assigned=item['assigned_staff_id']
        if action=='take':
            previous=guild.get_member(assigned) if assigned else None
            if previous and staff(previous) and assigned!=actor.id:
                raise ValueError('Another staff member already took this ticket. Staff may still help in the chat.')
            status='IN_PROGRESS';assigned=actor.id
        elif action=='wait' and item['status']=='IN_PROGRESS':status='WAITING_FOR_USER'
        elif action=='close':status='CLOSED'
        else:raise ValueError('That ticket status change is not available.')
        if item['status']==status and assigned==item['assigned_staff_id']:
            return item
        now=int(time.time())
        with db.connect() as conn:
            conn.execute('UPDATE support_tickets SET status=?,assigned_staff_id=?,updated_at=?,closed_at=? WHERE id=?',(status,assigned,now,now if status=='CLOSED' else None,ticket_id))
        item=get(ticket_id)
        await audit(guild,item,actor.id,action)
        await publish(guild,item)
        return item


async def recover(guild, member_id=None):
    async with _recovery_locks.setdefault(guild.id,asyncio.Lock()):
        await _recover(guild,member_id)


async def _recover(guild, member_id=None):
    raw=db.get_setting(f'managed_category:{guild.id}:support-tickets')
    category=guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if category not in guild.categories:return
    # Staff role changes must revoke access to old history in category/logs too.
    await category.edit(overwrites=private(guild,category),reason='GamerHQ ticket staff access')
    raw=db.get_setting(resource_key(guild,'ticket-logs'))
    logs=guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if logs:
        await logs.edit(overwrites=private(guild,logs),reason='GamerHQ ticket log staff access')
    for item in list_tickets(guild.id):
        if member_id is not None and member_id not in (item['creator_discord_id'],item['assigned_staff_id']):continue
        async with _ticket_locks.setdefault(item['id'],asyncio.Lock()):
            item=get(item['id'])
            try:
                if not item['channel_id']:
                    matches=[c for c in category.text_channels if getattr(c,'topic',None)==marker(guild.id,item['id'])]
                    if len(matches)!=1:
                        log.warning('Ticket %s requires creation-recovery review; no channels created.',item['id']);continue
                    check_private(matches[0],item['creator_discord_id'])
                    with db.connect() as conn:conn.execute('UPDATE support_tickets SET channel_id=? WHERE id=?',(matches[0].id,item['id']))
                assigned=guild.get_member(item['assigned_staff_id']) if item['assigned_staff_id'] else None
                with db.connect() as conn:
                    conn.execute('UPDATE support_tickets SET creator_left=?,assigned_staff_id=? WHERE id=?',(int(guild.get_member(item['creator_discord_id']) is None),item['assigned_staff_id'] if assigned and staff(assigned) else None,item['id']))
                updated=get(item['id'])
                if updated['creator_left']!=item['creator_left'] or updated['assigned_staff_id']!=item['assigned_staff_id']:
                    await audit(guild,updated,None,'membership/access updated')
                await publish(guild,updated)
            except (discord.HTTPException,ServerMessageError):
                log.exception('Ticket recovery failed ticket=%s; history retained.',item['id'])


async def refresh_entry(guild,channel=None):
    async with _entry_locks.setdefault(guild.id,asyncio.Lock()):
        await _refresh_entry(guild,channel)


async def _refresh_entry(guild,channel=None):
    from cogs.tickets import TicketEntry
    if channel is None:
        raw=db.get_setting(resource_key(guild,'need-support'))
        channel=guild.get_channel(int(raw)) if raw and str(raw).isdigit() else None
    if channel:
        await upsert_fixed_message(channel,setting_key=f'ticket_entry:{guild.id}',content=ENTRY_TEXT,pin=True,
            view=TicketEntry(),recover_match=lambda msg:(msg.content or '').startswith('# 🆘 Need Support?'))


async def repair(guild,changed,failed):
    start=unique(guild.categories,'start-here')
    if not start:raise ServerMessageError('START HERE must exist before support setup.')
    entry=mapped(guild,'need-support');category=mapped(guild,'support-tickets',True);logs=mapped(guild,'ticket-logs')
    staff_categories=[c for c in guild.categories if alias(c.name) in STAFF_ALIASES]
    if len(staff_categories)>1:raise ServerMessageError('Multiple STAFF categories; review ticket log placement manually.')
    if entry and entry.category and alias(entry.category.name) not in {'start-here','community'}:
        raise ServerMessageError('Need Support entry exists outside the public core. Review it before moving.')
    if category is None:category=await guild.create_category(CATEGORY_NAME,overwrites=private(guild),reason='GamerHQ private support tickets')
    else:category=await category.edit(name=CATEGORY_NAME,overwrites=private(guild,category),reason='GamerHQ ticket category privacy')
    db.set_setting(f'managed_category:{guild.id}:support-tickets',category.id)
    if entry is None:
        empty=SimpleNamespace(guild=guild,overwrites={},overwrites_for=lambda target:discord.PermissionOverwrite())
        entry=await start.create_text_channel(ENTRY_NAME,overwrites=guide_overwrites(empty),reason='GamerHQ technical support entry')
    else:entry=await entry.edit(name=ENTRY_NAME,category=start,sync_permissions=False,reason='GamerHQ support entry placement')
    db.set_setting(resource_key(guild,'need-support'),entry.id)
    await set_read_only(entry)
    await refresh_entry(guild,entry)
    if staff_categories:
        staff_category=staff_categories[0]
        if logs is None:logs=await staff_category.create_text_channel('🎫・ticket-logs',overwrites=private(guild),reason='GamerHQ private ticket audit')
        else:logs=await logs.edit(category=staff_category,sync_permissions=False,overwrites=private(guild,logs),reason='GamerHQ ticket audit privacy')
        db.set_setting(resource_key(guild,'ticket-logs'),logs.id)
    else:failed.append('No existing STAFF category for ticket-logs. Ticket metadata/audit remains in storage; review STAFF setup.')
    await recover(guild)
    changed.append('Updated Need Support entry, private SUPPORT TICKETS and Staff ticket logs; ticket history retained')


def guide_reference(guild):
    channel=mapped(guild,'need-support')
    link=channel.mention if channel else '#need-support'
    return f'## 🆘 Need Support?\nHaving a problem? Open **{link}** to create a private ticket. Only you and the GamerHQ team can see it.'
