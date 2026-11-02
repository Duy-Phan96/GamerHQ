"""Trusted legacy compatibility boundary; no Event Manager V2 domain logic.

The installed Skill supplies an import plan through its versioned management
API. The Host verifies source freshness, backup and namespace constraints, then
commits the Skill data and the legacy write barrier in the same transaction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import discord

from database import db
from tools.event_manager_export import TABLES


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def snapshot_for_guild(conn, guild_id):
    result = {"schemaVersion": "event-manager-v1-export.1"}
    for key, table in TABLES.items():
        if key in ("series", "events"):
            query = f"SELECT * FROM {table} WHERE guild_id=? ORDER BY rowid"
        else:
            query = f"SELECT r.* FROM {table} r JOIN lfg_events e ON e.id=r.event_id WHERE e.guild_id=? ORDER BY r.rowid"
        result[key] = [dict(row) for row in conn.execute(query, (guild_id,))]
    for series in result["series"]:
        series["weekdays"] = json.loads(series["weekdays_json"])
    return result


def install_barriers(conn):
    for table in TABLES.values():
        direct = table in ("lfg_events", "lfg_event_series")
        for operation in ("INSERT", "UPDATE", "DELETE"):
            sides = ("NEW",) if operation == "INSERT" else ("OLD",) if operation == "DELETE" else ("NEW", "OLD")
            guards = []
            for side in sides:
                guild = f"{side}.guild_id" if direct else f"(SELECT guild_id FROM lfg_events WHERE id={side}.event_id)"
                guards.append(f"EXISTS(SELECT 1 FROM skill_domain_authority WHERE guild_id={guild} AND domain='event-manager')")
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS freeze_{table}_{operation.lower()} BEFORE {operation} ON {table} "
                         f"WHEN {' OR '.join(guards)} BEGIN SELECT RAISE(ABORT,'Event Manager authority belongs to installed Skill'); END")


def owns_events(guild_id):
    with db.connect() as conn:
        return conn.execute("SELECT 1 FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'", (guild_id,)).fetchone() is not None


async def prepare(runtime, *, guild_id, decisions=None):
    with db.connect() as conn:
        conn.execute("BEGIN")
        snapshot = snapshot_for_guild(conn, guild_id)
    return await runtime.call_management(guild_id=guild_id, skill_id="event-manager",
        contract_id="event-manager.migrate-plan.v1", payload={"snapshot": snapshot, "decisions": decisions or {}})


def claim_empty_tenant(guild_id):
    """Normal installation claims an empty domain without a legacy migration.

    Existing legacy tenants remain installable but disabled pending verified cutover.
    The lock serializes this decision with legacy writes and installs their barrier.
    """
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        installed = conn.execute("SELECT installed FROM skill_guild_state WHERE guild_id=? AND skill_id='event-manager'", (guild_id,)).fetchone()
        if not installed or not installed[0]:
            raise ValueError("Install Event Manager through the Skill lifecycle first.")
        if conn.execute("SELECT 1 FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'", (guild_id,)).fetchone():
            return True
        snapshot = snapshot_for_guild(conn, guild_id)
        if snapshot["events"] or snapshot["series"]:
            return False
        conn.execute("INSERT INTO skill_domain_authority VALUES(?,'event-manager','event-manager',?,?,?)",
            (guild_id, digest(snapshot), digest({}), int(time.time())))
        install_barriers(conn)
        return True


async def verify_legacy_resources(guild, plan):
    """Read-only ownership proof for selected canonical legacy private channels."""
    if str(guild.id) != plan["guildId"]:
        raise ValueError("Resource verification must match the migration tenant.")
    channels = {channel.id: channel for channel in await guild.fetch_channels()}
    rows = [value for key, value in plan["writes"].items() if key.startswith("legacy.v1:events:")]
    members = [value for key, value in plan["writes"].items() if key.startswith("legacy.v1:members:")]
    bindings = {}
    for key, event in plan["writes"].items():
        if not key.startswith("event.v2:") or not event.get("chat"):
            continue
        channel_id = int(event["chat"]["channelId"])
        channel = channels.get(channel_id)
        source = [row for row in rows if row["id"] in event["legacyEventIds"] and row.get("private_channel_id") == channel_id]
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id or len(source) != 1:
            raise ValueError("Selected legacy channel ownership is ambiguous or unavailable.")
        row = source[0]
        if channel.topic != f"Private GamerHQ event #{row['id']} · {row['title']}":
            raise ValueError("Legacy channel ownership marker changed; manual verification required.")
        if event["config"]["visibility"] == "private" and channel.overwrites_for(guild.default_role).view_channel is not False:
            raise ValueError("Private legacy channel permissions require reconciliation before cutover.")
        message_id = row.get("message_id") if row.get("channel_id") == channel_id else None
        if message_id:
            message = await channel.fetch_message(message_id)
            if message.author.id != guild.me.id:
                raise ValueError("Legacy overview is not owned by this Host bot.")
        state = {"marker": channel.topic, "channelId": channel_id,
            "members": {str(member["user_id"]): [None, None] for member in members if member["event_id"] == row["id"]}}
        if message_id:
            state["messageId"] = message_id
        bindings[event["resourceKey"]] = state
    claimed_voice = set()
    for key, occurrence in plan["writes"].items():
        if not key.startswith("occurrence.v2:") or not occurrence.get("voice"):
            continue
        channel_id = int(occurrence["voice"]["channelId"])
        source = [row for row in rows if row["id"] == occurrence.get("legacyEventId") and row.get("voice_channel_id") == channel_id]
        channel = channels.get(channel_id)
        if len(source) != 1 or channel_id in claimed_voice or not isinstance(channel, discord.VoiceChannel) or channel.guild.id != guild.id:
            raise ValueError("Legacy voice mapping is ambiguous or unavailable; reconcile before cutover.")
        if channel.name != f"📅・{source[0]['title']}"[:100]:
            raise ValueError("Legacy voice identity changed; manual verification required.")
        if source[0].get("visibility") == "private" and channel.overwrites_for(guild.default_role).view_channel is not False:
            raise ValueError("Legacy private voice permissions require reconciliation.")
        claimed_voice.add(channel_id)
        bindings["voice:" + occurrence["id"]] = {"kind": "voice", "channelId": channel_id, "retainLegacy": True,
            "members": {str(member["user_id"]): [None, None] for member in members if member["event_id"] == source[0]["id"]}}
    return bindings


def apply(plan, *, backup_path: Path, backup_sha256: str, verified_resources=None):
    guild_id = int(plan["guildId"])
    if guild_id <= 0 or plan.get("skillId") != "event-manager" or plan.get("schemaVersion") != "event-manager-import.1" or plan.get("deleteChannels"):
        raise ValueError("Invalid Event Manager import contract.")
    backup_path = backup_path.resolve(strict=True)
    if backup_path == db.DB_PATH.resolve() or hashlib.sha256(backup_path.read_bytes()).hexdigest() != backup_sha256:
        raise ValueError("A distinct verified matching database backup is required.")
    backup = sqlite3.connect(backup_path.as_uri() + "?mode=ro", uri=True)
    backup.row_factory = sqlite3.Row
    try:
        if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok" or digest(snapshot_for_guild(backup, guild_id)) != plan["sourceHash"]:
            raise ValueError("Backup does not match the reviewed source snapshot.")
    finally:
        backup.close()
    writes = plan["writes"]
    if not isinstance(writes, dict) or not writes or len(writes) > 10000 or digest(writes) != plan["targetHash"]:
        raise ValueError("Invalid or modified import data.")
    encoded = {}
    verified_resources = verified_resources or {}
    for key, event in writes.items():
        if key.startswith("event.v2:") and event.get("chat"):
            binding = verified_resources.get(event["resourceKey"])
            if not binding or str(binding.get("channelId")) != str(event["chat"]["channelId"]):
                raise ValueError("Verify selected Discord resources through the trusted Host before cutover.")
        if key.startswith("occurrence.v2:") and event.get("voice"):
            binding = verified_resources.get("voice:" + event["id"])
            if not binding or binding.get("kind") != "voice" or binding.get("retainLegacy") is not True or str(binding.get("channelId")) != str(event["voice"]["channelId"]):
                raise ValueError("Verify and retain legacy voice mappings before cutover.")
    for key, value in writes.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key) or value is None:
            raise ValueError("Invalid import storage key/value.")
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(body.encode()) > 65536:
            raise ValueError("Import row exceeds the Host storage limit.")
        encoded[key] = body
    if sum(len(body.encode()) for body in encoded.values()) > 16 * 1024 * 1024:
        raise ValueError("Import exceeds the bounded transaction limit.")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT * FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'", (guild_id,)).fetchone()
        if previous:
            if previous["source_hash"] == plan["sourceHash"] and previous["target_hash"] == plan["targetHash"]:
                return False
            raise ValueError("Another import already owns this domain.")
        installed = conn.execute("SELECT installed FROM skill_guild_state WHERE guild_id=? AND skill_id='event-manager'", (guild_id,)).fetchone()
        if not installed or not installed[0]:
            raise ValueError("Install the reviewed Event Manager through the normal lifecycle first.")
        if digest(snapshot_for_guild(conn, guild_id)) != plan["sourceHash"]:
            raise ValueError("Legacy state changed after preview; generate a fresh plan and backup.")
        if conn.execute("SELECT 1 FROM skill_storage WHERE guild_id=? AND skill_id='event-manager'", (guild_id,)).fetchone():
            raise ValueError("Target Skill namespace must be empty before initial migration.")
        for key, body in encoded.items():
            conn.execute("INSERT INTO skill_storage VALUES(?,?,?,?,?)", (guild_id, "event-manager", key, body, int(time.time())))
        for key, state in verified_resources.items():
            state = dict(state)
            kind = state.pop("kind", "text")
            if kind not in ("text", "voice"):
                raise ValueError("Invalid verified resource kind.")
            state = {**state, "importBaselineHash": digest(state)}
            conn.execute("INSERT INTO skill_managed_resources VALUES(?,?,?,?,?,NULL,0)",
                         (guild_id, "event-manager", key, kind, json.dumps(state)))
        install_barriers(conn)
        conn.execute("INSERT INTO skill_domain_authority VALUES(?,?,?,?,?,?)",
            (guild_id, "event-manager", "event-manager", plan["sourceHash"], plan["targetHash"], int(time.time())))
    return True


def rollback_unmodified(*, guild_id, resources_verified=False):
    """Reverse only a frozen, unmodified initial import. Never discard V2 writes."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        authority = conn.execute("SELECT * FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'", (guild_id,)).fetchone()
        if not authority:
            return False
        state = conn.execute("SELECT enabled FROM skill_guild_state WHERE guild_id=? AND skill_id='event-manager'", (guild_id,)).fetchone()
        if state and state[0]:
            raise ValueError("Disable/freeze the Skill before rollback.")
        values = {row["storage_key"]: json.loads(row["value_json"]) for row in conn.execute(
            "SELECT * FROM skill_storage WHERE guild_id=? AND skill_id='event-manager'", (guild_id,))}
        if digest(values) != authority["target_hash"]:
            raise ValueError("V2 state changed; a reviewed reverse migration is required.")
        bindings = conn.execute("SELECT state_json FROM skill_managed_resources WHERE guild_id=? AND skill_id='event-manager'", (guild_id,)).fetchall()
        for binding in bindings:
            value = json.loads(binding[0])
            baseline = value.pop("importBaselineHash", None)
            if not resources_verified or not baseline or digest(value) != baseline:
                raise ValueError("Discord resources changed or lack external verification; reviewed rollback required.")
        conn.execute("DELETE FROM skill_domain_authority WHERE guild_id=? AND domain='event-manager'", (guild_id,))
        conn.execute("DELETE FROM skill_storage WHERE guild_id=? AND skill_id='event-manager'", (guild_id,))
        conn.execute("DELETE FROM skill_managed_resources WHERE guild_id=? AND skill_id='event-manager'", (guild_id,))
    return True
