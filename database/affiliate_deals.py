"""Durable at-most-once delivery claims, using the existing SQLite connection."""
import time
import json
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


def update_target(source_id, url, title):
    with db.connect() as conn:
        conn.execute("UPDATE processed_affiliate_deals SET status='posted', gocdkeys_url=?, normalized_game=? WHERE source_message_id=?",
                     (url, title, source_id))


def claim_curated(draft_id, guild_id, channel_id, actor_id, data):
    with db.connect() as conn:
        return conn.execute('INSERT OR IGNORE INTO curated_deals '
            '(id,guild_id,channel_id,created_by,created_at,data_json) VALUES (?,?,?,?,?,?)',
            (draft_id, guild_id, channel_id, actor_id, int(time.time()), json.dumps(data))).rowcount == 1


def finish_curated(draft_id, status, message_id=None):
    with db.connect() as conn:
        conn.execute('UPDATE curated_deals SET status=?, discord_message_id=? WHERE id=?',
                     (status, message_id, draft_id))
