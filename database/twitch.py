"""Private OAuth storage and durable live-session claims in the existing SQLite DB."""
import json
import time
from database import db


def connection(guild_id, user_id):
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM twitch_connections WHERE guild_id=? AND discord_user_id=?', (guild_id, user_id)).fetchone()
        return dict(row) if row else None


def connections():
    with db.connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM twitch_connections')]


def save(guild_id, user_id, account, tokens):
    # No reassignment or silent replacement of another Discord/Twitch identity.
    now = int(time.time())
    with db.connect() as conn:
        conn.execute('INSERT INTO twitch_connections(guild_id,discord_user_id,twitch_user_id,twitch_login,twitch_display_name,connected_at,updated_at,access_token,refresh_token,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (guild_id, user_id, account['id'], account['login'], account['display_name'], now, now,
                      tokens['access_token'], tokens['refresh_token'], now + int(tokens['expires_in'])))


def tokens(row, token):
    with db.connect() as conn:
        conn.execute('UPDATE twitch_connections SET access_token=?,refresh_token=?,expires_at=?,updated_at=? WHERE guild_id=? AND discord_user_id=? AND twitch_user_id=?',
                     (token['access_token'], token['refresh_token'], int(time.time()) + int(token['expires_in']), int(time.time()), row['guild_id'], row['discord_user_id'], row['twitch_user_id']))


def subscriptions(row, ids):
    with db.connect() as conn:
        conn.execute('UPDATE twitch_connections SET subscription_ids=? WHERE twitch_user_id=?', (json.dumps(ids), row['twitch_user_id']))


def disable(row):
    with db.connect() as conn:
        conn.execute('UPDATE twitch_connections SET notifications_enabled=0 WHERE twitch_user_id=?', (row['twitch_user_id'],))


def disconnect(guild_id, user_id):
    with db.connect() as conn:
        conn.execute('DELETE FROM twitch_connections WHERE guild_id=? AND discord_user_id=?', (guild_id, user_id))
    # Delivery claims survive disconnect/reconnect; the same session never reposts.


def live(row, stream):
    with db.connect() as conn:
        conn.execute('UPDATE twitch_connections SET is_live=1,last_live_started_at=? WHERE twitch_user_id=?', (stream['started_at'], row['twitch_user_id']))


def offline(row):
    with db.connect() as conn:
        conn.execute('UPDATE twitch_connections SET is_live=0 WHERE twitch_user_id=?', (row['twitch_user_id'],))


def claim(row, channel_id, stream):
    with db.connect() as conn:
        return conn.execute('INSERT OR IGNORE INTO twitch_live_deliveries(twitch_user_id,stream_session_id,guild_id,channel_id,started_at) VALUES(?,?,?,?,?)',
                            (row['twitch_user_id'], stream['id'], row['guild_id'], channel_id, stream['started_at'])).rowcount == 1


def finish(row, stream_id, status, message_id=None):
    with db.connect() as conn:
        conn.execute('UPDATE twitch_live_deliveries SET status=?,discord_notification_message_id=COALESCE(?,discord_notification_message_id) WHERE twitch_user_id=? AND stream_session_id=?',
                     (status, message_id, row['twitch_user_id'], stream_id))


def identity(row, account):
    with db.connect() as conn:
        conn.execute('UPDATE twitch_connections SET twitch_login=?,twitch_display_name=?,updated_at=? WHERE twitch_user_id=?',
                     (account['login'], account['display_name'], int(time.time()), row['twitch_user_id']))
