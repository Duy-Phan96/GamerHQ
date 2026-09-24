import json
import sqlite3
from contextlib import contextmanager

from config import DB_PATH, SEED_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS twitch_connections (
    guild_id INTEGER NOT NULL,
    discord_user_id INTEGER NOT NULL,
    twitch_user_id TEXT NOT NULL UNIQUE,
    twitch_login TEXT NOT NULL,
    twitch_display_name TEXT NOT NULL,
    connected_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    notifications_enabled INTEGER NOT NULL DEFAULT 1,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    is_live INTEGER NOT NULL DEFAULT 0,
    last_live_started_at TEXT,
    subscription_ids TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (guild_id, discord_user_id)
);
CREATE TABLE IF NOT EXISTS twitch_live_deliveries (
    twitch_user_id TEXT NOT NULL,
    stream_session_id TEXT NOT NULL,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    discord_notification_message_id INTEGER,
    PRIMARY KEY (twitch_user_id, stream_session_id)
);

CREATE TABLE IF NOT EXISTS processed_affiliate_deals (
    source_message_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    gocdkeys_url TEXT NOT NULL,
    processed_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'reserved',
    response_message_id INTEGER
);

CREATE TABLE IF NOT EXISTS managed_message_content (
    setting_key TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    state_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS managed_message_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    setting_key TEXT NOT NULL,
    content_changed INTEGER NOT NULL,
    buttons_changed INTEGER NOT NULL,
    before_hash TEXT NOT NULL,
    after_hash TEXT NOT NULL,
    action TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_type TEXT NOT NULL DEFAULT 'GENERAL_SUPPORT',
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER UNIQUE,
    opening_message_id INTEGER,
    creator_discord_id INTEGER NOT NULL,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN','IN_PROGRESS','WAITING_FOR_USER','CLOSED')),
    assigned_staff_id INTEGER,
    creator_left INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    closed_at INTEGER
);
CREATE INDEX IF NOT EXISTS ticket_owner_status ON support_tickets(guild_id,creator_discord_id,status);
CREATE TABLE IF NOT EXISTS ticket_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    actor_id INTEGER,
    action TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    author_discord_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'NEW',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    staff_channel_id INTEGER NOT NULL,
    staff_message_id INTEGER UNIQUE
);
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    emoji TEXT NOT NULL DEFAULT '🎮',
    display_group TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 0,
    selectable INTEGER NOT NULL DEFAULT 0,
    area_enabled INTEGER NOT NULL DEFAULT 0,
    area_has_lfg INTEGER NOT NULL DEFAULT 1,
    role_id INTEGER,
    category_id INTEGER,
    chat_channel_id INTEGER,
    memes_channel_id INTEGER,
    clips_channel_id INTEGER,
    lfg_channel_id INTEGER,
    create_voice_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS deleted_games (
    name_key TEXT PRIMARY KEY,
    deleted_at INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS temp_voice_channels (
    channel_id INTEGER PRIMARY KEY,
    host_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS managed_roles (
    role_id INTEGER PRIMARY KEY,
    role_kind TEXT NOT NULL,
    role_key TEXT NOT NULL,
    role_group TEXT,
    UNIQUE(role_kind, role_key)
);

CREATE TABLE IF NOT EXISTS lfg_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    game_id INTEGER NOT NULL,
    host_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    start_at INTEGER NOT NULL,
    max_players INTEGER NOT NULL,
    invite_lead_minutes INTEGER NOT NULL DEFAULT 15,
    channel_id INTEGER,
    message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'scheduled',
    voice_channel_id INTEGER,
    voice_invite_sent INTEGER NOT NULL DEFAULT 0,
    visibility TEXT NOT NULL DEFAULT 'public',
    private_channel_id INTEGER,
    share_token TEXT,
    share_enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lfg_event_members (
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY(event_id, user_id)
);

CREATE TABLE IF NOT EXISTS lfg_event_messages (
    event_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY(event_id, channel_id)
);

CREATE TABLE IF NOT EXISTS lfg_voice_notifications (
    event_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    sent_at INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(event_id, user_id)
);

CREATE TABLE IF NOT EXISTS lfg_time_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    proposer_id INTEGER NOT NULL,
    start_at INTEGER NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS lfg_unique_pending_time
ON lfg_time_proposals(event_id, start_at) WHERE status='PENDING';


CREATE TABLE IF NOT EXISTS streamer_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT 'Twitch',
    channel TEXT NOT NULL,
    main_game_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL DEFAULT 0,
    reviewed_by INTEGER
);

CREATE TABLE IF NOT EXISTS streamer_profiles (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL DEFAULT 'Twitch',
    channel TEXT NOT NULL,
    main_game_id INTEGER NOT NULL,
    approved_at INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    twitch_connected INTEGER NOT NULL DEFAULT 0,
    follower_role_id INTEGER,
    category_id INTEGER,
    create_voice_channel_id INTEGER,
    PRIMARY KEY(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS streamer_channels (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    channel_id INTEGER PRIMARY KEY,
    channel_kind TEXT NOT NULL DEFAULT 'text'
);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)
        affiliate_cols = {row['name'] for row in conn.execute('PRAGMA table_info(processed_affiliate_deals)')}
        for name in ('normalized_game', 'source_key'):
            if name not in affiliate_cols:
                conn.execute(f'ALTER TABLE processed_affiliate_deals ADD COLUMN {name} TEXT')
        # Existing tickets keep their identity/state and become general support.
        ticket_cols = {row['name'] for row in conn.execute('PRAGMA table_info(support_tickets)')}
        if 'ticket_type' not in ticket_cols:
            conn.execute("ALTER TABLE support_tickets ADD COLUMN ticket_type TEXT NOT NULL DEFAULT 'GENERAL_SUPPORT'")
        conn.execute('CREATE INDEX IF NOT EXISTS ticket_owner_type_status ON support_tickets(guild_id,creator_discord_id,ticket_type,status)')
        # Lightweight migrations for existing GamerHQ databases.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(lfg_events)").fetchall()}
        for name, definition in {
            "note": "TEXT NOT NULL DEFAULT ''",
            "dashboard_channel_id": "INTEGER",
            "dashboard_message_id": "INTEGER",
            "ended_at": "INTEGER",
        }.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE lfg_events ADD COLUMN {name} {definition}")
        if "voice_channel_id" not in cols:
            conn.execute("ALTER TABLE lfg_events ADD COLUMN voice_channel_id INTEGER")
        if "voice_invite_sent" not in cols:
            conn.execute("ALTER TABLE lfg_events ADD COLUMN voice_invite_sent INTEGER NOT NULL DEFAULT 0")
        if "visibility" not in cols:
            conn.execute("ALTER TABLE lfg_events ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'")
        if "private_channel_id" not in cols:
            conn.execute("ALTER TABLE lfg_events ADD COLUMN private_channel_id INTEGER")
        if "share_token" not in cols:
            conn.execute("ALTER TABLE lfg_events ADD COLUMN share_token TEXT")
        if "share_enabled" not in cols:
            conn.execute("ALTER TABLE lfg_events ADD COLUMN share_enabled INTEGER NOT NULL DEFAULT 1")

        streamer_cols = {row["name"] for row in conn.execute("PRAGMA table_info(streamer_profiles)").fetchall()}
        if "description" not in streamer_cols:
            conn.execute("ALTER TABLE streamer_profiles ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "twitch_connected" not in streamer_cols:
            conn.execute("ALTER TABLE streamer_profiles ADD COLUMN twitch_connected INTEGER NOT NULL DEFAULT 0")
        if "follower_role_id" not in streamer_cols:
            conn.execute("ALTER TABLE streamer_profiles ADD COLUMN follower_role_id INTEGER")
        if "category_id" not in streamer_cols:
            conn.execute("ALTER TABLE streamer_profiles ADD COLUMN category_id INTEGER")
        if "create_voice_channel_id" not in streamer_cols:
            conn.execute("ALTER TABLE streamer_profiles ADD COLUMN create_voice_channel_id INTEGER")

        # V1 game model: library, selectable role and Discord area are independent.
        game_cols = {row["name"] for row in conn.execute("PRAGMA table_info(games)").fetchall()}
        if "selectable" not in game_cols:
            conn.execute("ALTER TABLE games ADD COLUMN selectable INTEGER NOT NULL DEFAULT 0")
        if "area_enabled" not in game_cols:
            conn.execute("ALTER TABLE games ADD COLUMN area_enabled INTEGER NOT NULL DEFAULT 0")
        if "area_has_lfg" not in game_cols:
            conn.execute("ALTER TABLE games ADD COLUMN area_has_lfg INTEGER NOT NULL DEFAULT 1")
        if "lfg_channel_id" not in game_cols:
            conn.execute("ALTER TABLE games ADD COLUMN lfg_channel_id INTEGER")

        # Preserve every existing Discord object. Only derive the new state flags/alias column.
        conn.execute("UPDATE games SET area_enabled=1 WHERE category_id IS NOT NULL")
        conn.execute("UPDATE games SET lfg_channel_id=clips_channel_id WHERE lfg_channel_id IS NULL AND clips_channel_id IS NOT NULL")

        # IMPORTANT: `selectable` is persistent admin-owned state.
        # Startup/migrations must never rewrite the curated Beta selection.
        # Visibility changes happen only through explicit admin actions such as
        # `/game-admin set-visible`, `/game-admin create`, or permanent delete.


def seed_catalog():
    with SEED_PATH.open("r", encoding="utf-8") as f:
        games = json.load(f)["games"]

    with connect() as conn:
        deleted = {
            row["name_key"]
            for row in conn.execute("SELECT name_key FROM deleted_games").fetchall()
        }
        for game in games:
            if game["name"].strip().lower() in deleted:
                continue
            conn.execute(
                """
                INSERT INTO games (name, emoji, display_group, tags_json, aliases_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    emoji=excluded.emoji,
                    display_group=excluded.display_group,
                    tags_json=excluded.tags_json,
                    aliases_json=excluded.aliases_json
                """,
                (
                    game["name"],
                    game.get("emoji", "🎮"),
                    game["display_group"],
                    json.dumps(game.get("tags", []), ensure_ascii=False),
                    json.dumps(game.get("aliases", []), ensure_ascii=False),
                ),
            )


def get_all_games(active_only=False):
    with connect() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM games WHERE active=1 ORDER BY display_group, name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM games ORDER BY display_group, name"
            ).fetchall()
        return [dict(r) for r in rows]


def get_game_by_id(game_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
        return dict(row) if row else None


def get_game_by_name(name):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM games WHERE lower(name)=lower(?)", (name,)
        ).fetchone()
        return dict(row) if row else None


def upsert_custom_game(name, emoji, display_group):
    with connect() as conn:
        conn.execute("DELETE FROM deleted_games WHERE name_key=?", (name.strip().lower(),))
        conn.execute(
            """
            INSERT INTO games (name, emoji, display_group, tags_json, aliases_json)
            VALUES (?, ?, ?, '[]', '[]')
            ON CONFLICT(name) DO UPDATE SET
                emoji=excluded.emoji,
                display_group=excluded.display_group
            """,
            (name, emoji, display_group),
        )
    return get_game_by_name(name)


def get_game_delete_dependencies(game_id):
    """Return references that must be resolved before permanent deletion."""
    with connect() as conn:
        lfg_events = conn.execute(
            "SELECT COUNT(*) AS c FROM lfg_events WHERE game_id=?", (game_id,)
        ).fetchone()["c"]
        streamer_apps = conn.execute(
            "SELECT COUNT(*) AS c FROM streamer_applications WHERE main_game_id=?", (game_id,)
        ).fetchone()["c"]
        streamer_profiles = conn.execute(
            "SELECT COUNT(*) AS c FROM streamer_profiles WHERE main_game_id=?", (game_id,)
        ).fetchone()["c"]
    return {
        "lfg_events": int(lfg_events),
        "streamer_applications": int(streamer_apps),
        "streamer_profiles": int(streamer_profiles),
    }


def delete_game_permanently(game_id):
    """Delete a Game Library entry and prevent seed_catalog from restoring it."""
    game = get_game_by_id(game_id)
    if not game:
        return False

    deps = get_game_delete_dependencies(game_id)
    if any(deps.values()):
        raise RuntimeError("Game still has dependent LFG/streamer records.")

    with connect() as conn:
        conn.execute(
            "INSERT INTO deleted_games(name_key, deleted_at) VALUES(?, strftime('%s','now')) "
            "ON CONFLICT(name_key) DO UPDATE SET deleted_at=excluded.deleted_at",
            (game["name"].strip().lower(),),
        )
        conn.execute("DELETE FROM games WHERE id=?", (game_id,))
    return True


def get_selectable_games():
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM games WHERE selectable=1 ORDER BY display_group, name"
        ).fetchall()
        return [dict(r) for r in rows]


def get_area_games(*, lfg_only=False):
    with connect() as conn:
        sql = "SELECT * FROM games WHERE area_enabled=1"
        if lfg_only:
            sql += " AND area_has_lfg=1"
        sql += " ORDER BY display_group, name"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


def set_game_selectable(game_id, selectable: bool):
    with connect() as conn:
        conn.execute("UPDATE games SET selectable=? WHERE id=?", (int(selectable), game_id))


def set_game_role(game_id, role_id):
    with connect() as conn:
        conn.execute("UPDATE games SET role_id=? WHERE id=?", (role_id, game_id))


def set_game_area_options(game_id, *, enabled=None, has_lfg=None):
    fields, values = [], []
    if enabled is not None:
        fields.append("area_enabled=?")
        values.append(int(enabled))
    if has_lfg is not None:
        fields.append("area_has_lfg=?")
        values.append(int(has_lfg))
    if not fields:
        return
    values.append(game_id)
    with connect() as conn:
        conn.execute(f"UPDATE games SET {', '.join(fields)} WHERE id=?", values)


def set_game_structure(game_id, *, role_id, category_id, chat_id, memes_id, lfg_id, create_voice_id, has_lfg=True):
    with connect() as conn:
        conn.execute(
            """
            UPDATE games SET
                area_enabled=1,
                area_has_lfg=?,
                role_id=?,
                category_id=?,
                chat_channel_id=?,
                memes_channel_id=?,
                clips_channel_id=?,
                lfg_channel_id=?,
                create_voice_channel_id=?
            WHERE id=?
            """,
            (int(has_lfg), role_id, category_id, chat_id, memes_id, lfg_id, lfg_id, create_voice_id, game_id),
        )


def deactivate_game(game_id):
    """Legacy name: disable only the Discord area; keep library entry and role."""
    with connect() as conn:
        conn.execute(
            """
            UPDATE games SET
                area_enabled=0,
                category_id=NULL,
                chat_channel_id=NULL,
                memes_channel_id=NULL,
                clips_channel_id=NULL,
                lfg_channel_id=NULL,
                create_voice_channel_id=NULL
            WHERE id=?
            """,
            (game_id,),
        )

def set_setting(key, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def get_setting(key):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def add_temp_voice(channel_id, host_id, game_id):
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO temp_voice_channels(channel_id,host_id,game_id) VALUES(?,?,?)",
            (channel_id, host_id, game_id),
        )


def remove_temp_voice(channel_id):
    with connect() as conn:
        conn.execute("DELETE FROM temp_voice_channels WHERE channel_id=?", (channel_id,))


def get_temp_voice_ids():
    with connect() as conn:
        return [r["channel_id"] for r in conn.execute("SELECT channel_id FROM temp_voice_channels").fetchall()]


def upsert_managed_role(*, role_id, role_kind, role_key, role_group=None):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO managed_roles(role_id, role_kind, role_key, role_group)
            VALUES(?,?,?,?)
            ON CONFLICT(role_kind, role_key) DO UPDATE SET
                role_id=excluded.role_id,
                role_group=excluded.role_group
            """,
            (role_id, role_kind, role_key, role_group),
        )


def get_managed_role_by_key(role_kind, role_key):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM managed_roles WHERE role_kind=? AND role_key=?",
            (role_kind, role_key),
        ).fetchone()
        return dict(row) if row else None


def get_managed_roles(role_kind=None):
    with connect() as conn:
        if role_kind is None:
            rows = conn.execute("SELECT * FROM managed_roles ORDER BY role_kind, role_group, role_key").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM managed_roles WHERE role_kind=? ORDER BY role_group, role_key",
                (role_kind,),
            ).fetchall()
        return [dict(row) for row in rows]


def delete_managed_role(role_id):
    with connect() as conn:
        conn.execute("DELETE FROM managed_roles WHERE role_id=?", (role_id,))


def create_lfg_event(*, guild_id, game_id, host_id, title, start_at, max_players, invite_lead_minutes, visibility="public", share_token=None):
    from services.game_area_safety import require_available
    require_available(game_id)
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO lfg_events(guild_id,game_id,host_id,title,start_at,max_players,invite_lead_minutes,visibility,share_token)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (guild_id, game_id, host_id, title, start_at, max_players, invite_lead_minutes, visibility, share_token),
        )
        event_id = cur.lastrowid
        conn.execute(
            "INSERT OR REPLACE INTO lfg_event_members(event_id,user_id,status) VALUES(?,?,?)",
            (event_id, host_id, "joined"),
        )
    return get_lfg_event(event_id)


def get_lfg_event(event_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM lfg_events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None


def get_active_lfg_events(guild_id=None):
    with connect() as conn:
        if guild_id is None:
            rows = conn.execute("SELECT * FROM lfg_events WHERE status='scheduled' ORDER BY start_at").fetchall()
        else:
            rows = conn.execute("SELECT * FROM lfg_events WHERE status='scheduled' AND guild_id=? ORDER BY start_at", (guild_id,)).fetchall()
        return [dict(row) for row in rows]


def set_lfg_event_message(event_id, *, channel_id, message_id):
    with connect() as conn:
        conn.execute("UPDATE lfg_events SET channel_id=?, message_id=? WHERE id=?", (channel_id, message_id, event_id))
        conn.execute(
            "INSERT INTO lfg_event_messages(event_id,channel_id,message_id) VALUES(?,?,?) "
            "ON CONFLICT(event_id,channel_id) DO UPDATE SET message_id=excluded.message_id",
            (event_id, channel_id, message_id),
        )


def add_lfg_event_message(event_id, *, channel_id, message_id):
    with connect() as conn:
        conn.execute(
            "INSERT INTO lfg_event_messages(event_id,channel_id,message_id) VALUES(?,?,?) "
            "ON CONFLICT(event_id,channel_id) DO UPDATE SET message_id=excluded.message_id",
            (event_id, channel_id, message_id),
        )


def get_lfg_event_messages(event_id):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM lfg_event_messages WHERE event_id=? ORDER BY channel_id", (event_id,)
        ).fetchall()
        return [dict(row) for row in rows]


def set_lfg_event_member(event_id, user_id, status):
    with connect() as conn:
        conn.execute(
            "INSERT INTO lfg_event_members(event_id,user_id,status) VALUES(?,?,?) "
            "ON CONFLICT(event_id,user_id) DO UPDATE SET status=excluded.status",
            (event_id, user_id, status),
        )


def remove_lfg_event_member(event_id, user_id):
    with connect() as conn:
        conn.execute("DELETE FROM lfg_event_members WHERE event_id=? AND user_id=?", (event_id, user_id))


def get_lfg_event_members(event_id):
    with connect() as conn:
        rows = conn.execute("SELECT * FROM lfg_event_members WHERE event_id=? ORDER BY user_id", (event_id,)).fetchall()
        return [dict(row) for row in rows]




def set_lfg_event_private_channel(event_id, channel_id):
    with connect() as conn:
        conn.execute("UPDATE lfg_events SET private_channel_id=? WHERE id=?", (channel_id, event_id))


def get_lfg_event_by_share_token(token):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM lfg_events WHERE share_token=? AND share_enabled=1 AND status='scheduled'",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def rotate_lfg_share_token(event_id, token):
    with connect() as conn:
        conn.execute("UPDATE lfg_events SET share_token=?, share_enabled=1 WHERE id=?", (token, event_id))


def set_lfg_share_enabled(event_id, enabled):
    with connect() as conn:
        conn.execute("UPDATE lfg_events SET share_enabled=? WHERE id=?", (int(enabled), event_id))


def set_lfg_event_status(event_id, status):
    with connect() as conn:
        cur = conn.execute("UPDATE lfg_events SET status=? WHERE id=?", (status, event_id))
        return cur.rowcount > 0


def claim_lfg_event_voice(event_id, channel_id, *, expected_start=None):
    """Atomically attach one voice channel only while the event is still scheduled."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE lfg_events SET voice_channel_id=?, voice_invite_sent=1 "
            "WHERE id=? AND status='scheduled' AND voice_channel_id IS NULL "
            "AND (? IS NULL OR start_at=?)",
            (channel_id, event_id, expected_start, expected_start),
        )
        return cur.rowcount > 0


def claim_lfg_voice_notification(event_id, user_id, sent_at):
    """Reserve a Voice Ready DM once per event/user, only for a scheduled event."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO lfg_voice_notifications(event_id,user_id,sent_at) "
            "SELECT id, ?, ? FROM lfg_events WHERE id=? AND status='scheduled'",
            (user_id, sent_at, event_id),
        )
        return cur.rowcount > 0


def has_lfg_voice_notification(event_id, user_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM lfg_voice_notifications WHERE event_id=? AND user_id=?",
            (event_id, user_id),
        ).fetchone()
        return row is not None


def set_lfg_event_voice(event_id, channel_id, invite_sent=1):
    with connect() as conn:
        conn.execute(
            "UPDATE lfg_events SET voice_channel_id=?, voice_invite_sent=? WHERE id=?",
            (channel_id, int(invite_sent), event_id),
        )


def clear_lfg_event_voice(event_id):
    with connect() as conn:
        conn.execute(
            "UPDATE lfg_events SET voice_channel_id=NULL, voice_invite_sent=0 WHERE id=?",
            (event_id,),
        )

def delete_lfg_event(event_id):
    """Delete one LFG event and all of its local database relations."""
    with connect() as conn:
        conn.execute("DELETE FROM lfg_time_proposals WHERE event_id=?", (event_id,))
        conn.execute("DELETE FROM lfg_voice_notifications WHERE event_id=?", (event_id,))
        conn.execute("DELETE FROM lfg_event_members WHERE event_id=?", (event_id,))
        conn.execute("DELETE FROM lfg_event_messages WHERE event_id=?", (event_id,))
        conn.execute("DELETE FROM lfg_events WHERE id=?", (event_id,))


def create_streamer_application(guild_id, user_id, platform, channel, main_game_id, created_at):
    with connect() as conn:
        conn.execute("UPDATE streamer_applications SET status='superseded' WHERE guild_id=? AND user_id=? AND status='pending'", (guild_id,user_id))
        cur=conn.execute("INSERT INTO streamer_applications(guild_id,user_id,platform,channel,main_game_id,status,created_at) VALUES(?,?,?,?,?,'pending',?)", (guild_id,user_id,platform,channel,main_game_id,created_at))
        return cur.lastrowid

def get_streamer_application(app_id):
    with connect() as conn:
        r=conn.execute("SELECT * FROM streamer_applications WHERE id=?",(app_id,)).fetchone(); return dict(r) if r else None

def set_streamer_application_status(app_id,status,reviewed_by):
    with connect() as conn:
        cur=conn.execute("UPDATE streamer_applications SET status=?, reviewed_by=? WHERE id=? AND status='pending'",(status,reviewed_by,app_id)); return cur.rowcount>0

def upsert_streamer_profile(guild_id,user_id,platform,channel,main_game_id,approved_at):
    with connect() as conn:
        conn.execute("INSERT INTO streamer_profiles(guild_id,user_id,platform,channel,main_game_id,approved_at) VALUES(?,?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET platform=excluded.platform,channel=excluded.channel,main_game_id=excluded.main_game_id,approved_at=excluded.approved_at",(guild_id,user_id,platform,channel,main_game_id,approved_at))

def get_streamer_profile(guild_id,user_id):
    with connect() as conn:
        r=conn.execute("SELECT * FROM streamer_profiles WHERE guild_id=? AND user_id=?",(guild_id,user_id)).fetchone(); return dict(r) if r else None

def get_streamer_profiles(guild_id):
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM streamer_profiles WHERE guild_id=? ORDER BY user_id",(guild_id,)).fetchall()]


def upsert_streamer_profile_open(guild_id, user_id, platform, channel, main_game_id, description, created_at):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO streamer_profiles(
                guild_id,user_id,platform,channel,main_game_id,approved_at,description,twitch_connected
            ) VALUES(?,?,?,?,?,?,?,0)
            ON CONFLICT(guild_id,user_id) DO UPDATE SET
                platform=excluded.platform,
                channel=excluded.channel,
                main_game_id=excluded.main_game_id,
                approved_at=excluded.approved_at,
                description=excluded.description,
                twitch_connected=CASE
                    WHEN streamer_profiles.channel=excluded.channel THEN streamer_profiles.twitch_connected
                    ELSE 0
                END
            """,
            (guild_id,user_id,platform,channel,main_game_id,created_at,description),
        )


def set_streamer_follower_role(guild_id, user_id, role_id):
    with connect() as conn:
        conn.execute(
            "UPDATE streamer_profiles SET follower_role_id=? WHERE guild_id=? AND user_id=?",
            (role_id, guild_id, user_id),
        )

def get_streamer_profile_by_role(guild_id, role_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM streamer_profiles WHERE guild_id=? AND follower_role_id=?",
            (guild_id, role_id),
        ).fetchone()
        return dict(row) if row else None


def set_streamer_area(guild_id, user_id, category_id, create_voice_channel_id):
    with connect() as conn:
        conn.execute(
            "UPDATE streamer_profiles SET category_id=?, create_voice_channel_id=? WHERE guild_id=? AND user_id=?",
            (category_id, create_voice_channel_id, guild_id, user_id),
        )


def get_streamer_profile_by_create_voice(guild_id, channel_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM streamer_profiles WHERE guild_id=? AND create_voice_channel_id=?",
            (guild_id, channel_id),
        ).fetchone()
        return dict(row) if row else None


def add_streamer_channel(guild_id, user_id, channel_id, channel_kind='text'):
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO streamer_channels(guild_id,user_id,channel_id,channel_kind) VALUES(?,?,?,?)",
            (guild_id, user_id, channel_id, channel_kind),
        )


def get_streamer_channels(guild_id, user_id):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM streamer_channels WHERE guild_id=? AND user_id=? ORDER BY channel_id",
            (guild_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]


def get_streamer_channel(guild_id, user_id, channel_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM streamer_channels WHERE guild_id=? AND user_id=? AND channel_id=?",
            (guild_id, user_id, channel_id),
        ).fetchone()
        return dict(row) if row else None


def remove_streamer_channel(channel_id):
    with connect() as conn:
        conn.execute("DELETE FROM streamer_channels WHERE channel_id=?", (channel_id,))


def clear_game_memes_channel(game_id):
    """V27 migration: game areas no longer use a memes channel."""
    with connect() as conn:
        conn.execute("UPDATE games SET memes_channel_id=NULL WHERE id=?", (game_id,))


def get_temp_voice(channel_id):
    with connect() as conn:
        row = conn.execute('SELECT * FROM temp_voice_channels WHERE channel_id=?', (channel_id,)).fetchone()
        return dict(row) if row else None
