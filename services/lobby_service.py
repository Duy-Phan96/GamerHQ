"""Transactional lobby rules. Discord messages are views, never authority."""
import time
import logging

from database import db


def _event(conn, event_id, guild_id):
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM lfg_events WHERE id=? AND guild_id=?", (event_id, guild_id)).fetchone()
    if not row or row['status'] != 'scheduled':
        raise ValueError('This lobby is no longer active.')
    return dict(row)


def _host(event, actor_id):
    if event['host_id'] != actor_id:
        raise ValueError('Only the host can do this.')


def _member(conn, event_id, actor_id):
    row = conn.execute("SELECT status FROM lfg_event_members WHERE event_id=? AND user_id=?", (event_id, actor_id)).fetchone()
    if not row or row['status'] != 'joined':
        raise ValueError('Only joined lobby members can do this.')


def _future(start_at):
    if not isinstance(start_at, int) or start_at <= int(time.time()):
        raise ValueError('Choose a valid future date and time.')


def _reschedule(conn, event, start_at):
    _future(start_at)
    if event.get('voice_channel_id'):
        raise ValueError('Voice is already open. Close this lobby and create another to reschedule safely.')
    conn.execute('UPDATE lfg_events SET start_at=?, voice_invite_sent=0 WHERE id=?', (start_at, event['id']))
    conn.execute('DELETE FROM lfg_voice_notifications WHERE event_id=?', (event['id'],))
    conn.execute("UPDATE lfg_time_proposals SET status='EXPIRED' WHERE event_id=? AND status='PENDING'", (event['id'],))


def edit(event_id, guild_id, actor_id, **changes):
    if not changes or set(changes) - {'title', 'note', 'start_at', 'max_players', 'invite_lead_minutes'}:
        raise ValueError('Unsupported lobby change.')
    with db.connect() as conn:
        event = _event(conn, event_id, guild_id)
        _host(event, actor_id)
        if 'title' in changes:
            changes['title'] = changes['title'].strip()
            if not 1 <= len(changes['title']) <= 100:
                raise ValueError('Title must contain 1–100 characters.')
        if 'note' in changes and len(changes['note']) > 500:
            raise ValueError('Note must be at most 500 characters.')
        if 'max_players' in changes:
            count = conn.execute("SELECT count(*) FROM lfg_event_members WHERE event_id=? AND status='joined'", (event_id,)).fetchone()[0]
            if not max(1, count) <= changes['max_players'] <= 99:
                raise ValueError(f'Seats must be between {max(1, count)} and 99 (including the host).')
        if 'invite_lead_minutes' in changes and not 0 <= changes['invite_lead_minutes'] <= 1440:
            raise ValueError('Voice reminder must be 0–1440 minutes before start.')
        if event.get('voice_channel_id') and changes.get('invite_lead_minutes', event['invite_lead_minutes']) != event['invite_lead_minutes']:
            raise ValueError('The voice reminder has already fired; voice is open.')
        if 'start_at' in changes and changes['start_at'] != event['start_at']:
            _reschedule(conn, event, changes['start_at'])
        conn.execute('UPDATE lfg_events SET ' + ','.join(f'{key}=?' for key in changes) + ' WHERE id=?', (*changes.values(), event_id))
    return db.get_lfg_event(event_id)


def invite(event_id, guild_id, actor_id, user_id):
    with db.connect() as conn:
        event = _event(conn, event_id, guild_id)
        _host(event, actor_id)
        row = conn.execute('SELECT status FROM lfg_event_members WHERE event_id=? AND user_id=?', (event_id, user_id)).fetchone()
        if row and row['status'] in {'joined', 'invited'}:
            return False
        conn.execute("INSERT INTO lfg_event_members VALUES(?,?,'invited') ON CONFLICT(event_id,user_id) DO UPDATE SET status='invited'", (event_id, user_id))
    return True


def join(event_id, guild_id, user_id, share_token=None):
    with db.connect() as conn:
        event = _event(conn, event_id, guild_id)
        if event['start_at'] + 6 * 3600 <= int(time.time()):
            raise ValueError('This lobby has expired.')
        rows = conn.execute('SELECT * FROM lfg_event_members WHERE event_id=?', (event_id,)).fetchall()
        state = next((r['status'] for r in rows if r['user_id'] == user_id), None)
        if state == 'excluded':
            raise ValueError('The host removed you from this lobby. Ask for a new invitation.')
        if state == 'joined':
            return 'already'
        token_ok = share_token and event['share_enabled'] and event['share_token'] == share_token
        if event['visibility'] == 'private' and state != 'invited' and not token_ok:
            raise ValueError('A current private invitation is required.')
        if sum(r['status'] == 'joined' for r in rows) >= event['max_players']:
            return 'full'
        conn.execute("INSERT INTO lfg_event_members VALUES(?,?,'joined') ON CONFLICT(event_id,user_id) DO UPDATE SET status='joined'", (event_id, user_id))
    return 'joined'


def remove(event_id, guild_id, actor_id, user_id):
    with db.connect() as conn:
        event = _event(conn, event_id, guild_id)
        if actor_id != user_id:
            _host(event, actor_id)
        if user_id == event['host_id']:
            raise ValueError('The host must close or cancel the lobby instead of leaving.')
        _member(conn, event_id, user_id)
        if actor_id == user_id:
            conn.execute('DELETE FROM lfg_event_members WHERE event_id=? AND user_id=?', (event_id, user_id))
        else:
            conn.execute("UPDATE lfg_event_members SET status='excluded' WHERE event_id=? AND user_id=?", (event_id, user_id))
        conn.execute("UPDATE lfg_time_proposals SET status='WITHDRAWN' WHERE event_id=? AND proposer_id=? AND status='PENDING'", (event_id, user_id))


def propose(event_id, guild_id, actor_id, start_at, reason=''):
    _future(start_at)
    if len(reason) > 300:
        raise ValueError('Reason must be at most 300 characters.')
    with db.connect() as conn:
        event = _event(conn, event_id, guild_id)
        _member(conn, event_id, actor_id)
        if event.get('voice_channel_id'):
            raise ValueError('Voice is already open; this lobby can no longer be rescheduled.')
        if start_at == event['start_at']:
            raise ValueError('That is already the current time.')
        conn.execute("UPDATE lfg_time_proposals SET status='EXPIRED' WHERE event_id=? AND status='PENDING' AND start_at<=?", (event_id, int(time.time())))
        if conn.execute("SELECT 1 FROM lfg_time_proposals WHERE event_id=? AND start_at=? AND status='PENDING'", (event_id, start_at)).fetchone():
            raise ValueError('An identical time proposal is already pending.')
        if conn.execute("SELECT count(*) FROM lfg_time_proposals WHERE event_id=? AND status='PENDING'", (event_id,)).fetchone()[0] >= 20:
            raise ValueError('Please resolve existing proposals first (20 pending maximum).')
        cur = conn.execute('INSERT INTO lfg_time_proposals(event_id,proposer_id,start_at,reason,created_at) VALUES(?,?,?,?,?)', (event_id, actor_id, start_at, reason.strip(), int(time.time())))
        return cur.lastrowid


def proposals(event_id):
    with db.connect() as conn:
        conn.execute("UPDATE lfg_time_proposals SET status='EXPIRED' WHERE event_id=? AND status='PENDING' AND start_at<=?", (event_id, int(time.time())))
        return [dict(r) for r in conn.execute("SELECT * FROM lfg_time_proposals WHERE event_id=? AND status='PENDING' ORDER BY id", (event_id,))]


def decide(event_id, guild_id, actor_id, proposal_id, decision):
    if decision not in {'ACCEPTED', 'DECLINED', 'WITHDRAWN'}:
        raise ValueError('Invalid decision.')
    with db.connect() as conn:
        event = _event(conn, event_id, guild_id)
        proposal = conn.execute("SELECT * FROM lfg_time_proposals WHERE id=? AND event_id=? AND status='PENDING'", (proposal_id, event_id)).fetchone()
        if not proposal:
            raise ValueError('This proposal is no longer pending.')
        if decision == 'WITHDRAWN':
            if proposal['proposer_id'] != actor_id:
                raise ValueError('Only the proposer can withdraw this suggestion.')
        else:
            _host(event, actor_id)
        if decision == 'ACCEPTED':
            if event['start_at'] + 6 * 3600 <= int(time.time()):
                raise ValueError('This lobby has expired.')
            _reschedule(conn, event, proposal['start_at'])
        conn.execute('UPDATE lfg_time_proposals SET status=? WHERE id=?', (decision, proposal_id))
    return db.get_lfg_event(event_id)


def end(event_id, guild_id, actor_id, status, *, administrator=False):
    if status not in {'cancelled', 'completed'}:
        raise ValueError('Invalid final state.')
    with db.connect() as conn:
        event = _event(conn, event_id, guild_id)
        # Preserve the pre-existing administrator cancellation override only.
        if not (administrator and status == 'cancelled'):
            _host(event, actor_id)
        conn.execute('UPDATE lfg_events SET status=?, ended_at=? WHERE id=?', (status, int(time.time()), event_id))
        conn.execute("UPDATE lfg_time_proposals SET status='EXPIRED' WHERE event_id=? AND status='PENDING'", (event_id,))
    logging.getLogger(__name__).warning('lobby timestamp=%s actor=%s target=%s result=%s', int(time.time()), actor_id, event_id, status)
    return db.get_lfg_event(event_id)
