"""Durable at-most-once delivery claims, using the existing SQLite connection."""
import time
from database import db


def processed(source_id):
    with db.connect() as conn:
        return conn.execute('SELECT 1 FROM processed_affiliate_deals WHERE source_message_id=?', (source_id,)).fetchone() is not None


def record(source_id):
    with db.connect() as conn:
        row = conn.execute('SELECT * FROM processed_affiliate_deals WHERE source_message_id=?', (source_id,)).fetchone()
        return dict(row) if row else None


def claim(message, url, title=None, source=None):
    with db.connect() as conn:
        return conn.execute(
            'INSERT OR IGNORE INTO processed_affiliate_deals '
            '(source_message_id,guild_id,channel_id,gocdkeys_url,processed_at,normalized_game,source_key) VALUES (?,?,?,?,?,?,?)',
            (message.id, message.guild.id, message.channel.id, url, int(time.time()), title, source)).rowcount == 1


def finish(source_id, status, response_id=None):
    with db.connect() as conn:
        conn.execute('UPDATE processed_affiliate_deals SET status=?, response_message_id=COALESCE(?,response_message_id) WHERE source_message_id=?',
                     (status, response_id, source_id))
