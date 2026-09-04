from services.server_service import pin_managed_message, cleanup_pin_system_messages
import json
import re
from difflib import SequenceMatcher

import discord

from config import CHOOSE_GAMES_CHANNEL_ID, DISPLAY_GROUP_ORDER
from database import db


class GameStructureError(Exception):
    def __init__(self, stage: str, original: Exception):
        self.stage = stage
        self.original = original
        super().__init__(f"{stage}: {type(original).__name__}: {original}")


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def search_games(query: str, selectable_only=True, limit=25):
    games = db.get_selectable_games() if selectable_only else db.get_all_games(active_only=False)
    q = query.strip().lower()
    ranked = []

    for game in games:
        aliases = json.loads(game.get("aliases_json") or "[]")
        values = [game["name"], *aliases]
        best = 0.0

        for value in values:
            v = value.lower()
            if q == v:
                score = 1.0
            elif q in v:
                score = 0.9 + min(len(q) / max(len(v), 1), 0.09)
            else:
                score = SequenceMatcher(None, q, v).ratio()
            best = max(best, score)

        if not q or best >= 0.35:
            ranked.append((best, game))

    ranked.sort(key=lambda x: (-x[0], x[1]["name"].lower()))
    return [g for _, g in ranked[:limit]]


def _find_named(items, name):
    return [item for item in items if item.name == name]


def inspect_game_structure(guild: discord.Guild, game: dict) -> dict:
    """Read-only inspection. NEVER creates/changes anything."""
    role_name = f"{game['emoji']} {game['name']}"
    category_name = f"{game['emoji']} {game['name'].upper()}"

    roles = _find_named(guild.roles, role_name)
    categories = _find_named(guild.categories, category_name)

    plan = {
        "role_name": role_name,
        "category_name": category_name,
        "role": roles[0] if len(roles) == 1 else None,
        "category": categories[0] if len(categories) == 1 else None,
        "role_count": len(roles),
        "category_count": len(categories),
        "channels": {},
        "conflicts": [],
    }

    if len(roles) > 1:
        plan["conflicts"].append(f"Multiple roles named `{role_name}` exist.")
    if len(categories) > 1:
        plan["conflicts"].append(f"Multiple categories named `{category_name}` exist.")

    expected = [
        ("chat", "💬・chat", discord.TextChannel),
    ]
    if game.get("area_has_lfg", 1):
        expected.append(("lfg", "🎯・looking-for-group", discord.TextChannel))
    expected.append(("create_voice", "➕・create-voice", discord.VoiceChannel))

    category = plan["category"]
    for key, channel_name, expected_type in expected:
        matches = []
        if category:
            matches = [
                c for c in category.channels
                if c.name == channel_name and isinstance(c, expected_type)
            ]
        plan["channels"][key] = {
            "name": channel_name,
            "existing": matches[0] if len(matches) == 1 else None,
            "count": len(matches),
        }
        if len(matches) > 1:
            plan["conflicts"].append(
                f"Multiple `{channel_name}` channels exist in `{category_name}`."
            )

    return plan


def format_plan(plan: dict) -> str:
    def mark(existing, count=0):
        if count > 1:
            return "⚠️ CONFLICT"
        return "♻️ EXISTING" if existing else "➕ CREATE"

    lines = [
        f"**Role:** {mark(plan['role'], plan['role_count'])} `{plan['role_name']}`",
        f"**Category:** {mark(plan['category'], plan['category_count'])} `{plan['category_name']}`",
    ]

    for key in ("chat", "lfg", "create_voice"):
        if key not in plan["channels"]:
            continue
        item = plan["channels"][key]
        lines.append(
            f"**{item['name']}:** {mark(item['existing'], item['count'])}"
        )

    if plan["conflicts"]:
        lines.append("")
        lines.append("**Conflicts:**")
        lines.extend(f"• {c}" for c in plan["conflicts"])

    return "\n".join(lines)


async def create_game_structure_confirmed(guild: discord.Guild, game: dict, plan: dict):
    """
    Executes ONLY after explicit admin confirmation.
    Existing matching objects are reused.
    On failure, resources created by THIS operation are rolled back.
    """
    if plan["conflicts"]:
        raise GameStructureError(
            "PRE-CHECK",
            RuntimeError("Conflicts exist. Resolve duplicate names before continuing.")
        )

    bot_member = guild.me
    if bot_member is None:
        raise GameStructureError("BOT MEMBER LOOKUP", RuntimeError("Bot member not found."))

    created = {
        "role": None,
        "category": None,
        "channels": [],
    }

    role = plan["role"]
    category = plan["category"]

    try:
        if role is None:
            try:
                role = await guild.create_role(
                    name=plan["role_name"],
                    reason=f"GamerHQ confirmed game setup: {game['name']}",
                )
                created["role"] = role
            except Exception as exc:
                raise GameStructureError("CREATE ROLE", exc) from exc

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                connect=True,
                speak=True,
            ),
            bot_member: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                connect=True,
                speak=True,
                move_members=True,
            ),
        }

        if category is None:
            try:
                category = await guild.create_category(
                    name=plan["category_name"],
                    overwrites=overwrites,
                    reason=f"GamerHQ confirmed game setup: {game['name']}",
                )
                created["category"] = category
            except Exception as exc:
                raise GameStructureError("CREATE CATEGORY", exc) from exc
        else:
            # Never silently change an existing category. We reuse it as-is.
            # The admin explicitly confirmed that existing resources may be reused.
            pass

        async def ensure_channel(key, creator, stage):
            existing = plan["channels"][key]["existing"]
            if existing is not None:
                return existing
            try:
                channel = await creator()
                created["channels"].append(channel)
                return channel
            except Exception as exc:
                raise GameStructureError(stage, exc) from exc

        chat = await ensure_channel(
            "chat",
            lambda: guild.create_text_channel(
                "💬・chat", category=category,
                reason=f"GamerHQ confirmed game setup: {game['name']}"
            ),
            "CREATE CHAT CHANNEL",
        )
        lfg = None
        if game.get("area_has_lfg", 1):
            lfg = await ensure_channel(
                "lfg",
                lambda: guild.create_text_channel(
                    "🎯・looking-for-group", category=category,
                    reason=f"GamerHQ confirmed game setup: {game['name']}"
                ),
                "CREATE GAME LFG CHANNEL",
            )
        create_voice = await ensure_channel(
            "create_voice",
            lambda: guild.create_voice_channel(
                "➕・create-voice", category=category,
                reason=f"GamerHQ confirmed voice generator: {game['name']}"
            ),
            "CREATE VOICE GENERATOR",
        )

        try:
            db.set_game_structure(
                game["id"],
                role_id=role.id,
                category_id=category.id,
                chat_id=chat.id,
                memes_id=None,
                lfg_id=lfg.id if lfg else None,
                create_voice_id=create_voice.id,
                has_lfg=bool(game.get("area_has_lfg", 1)),
            )
        except Exception as exc:
            raise GameStructureError("SAVE DATABASE", exc) from exc

        return db.get_game_by_id(game["id"])

    except Exception:
        # Roll back ONLY what this confirmed operation created.
        # Existing role/category/channels are never deleted.
        for channel in reversed(created["channels"]):
            try:
                await channel.delete(reason="Rollback failed GamerHQ game setup")
            except Exception:
                pass

        if created["category"] is not None:
            try:
                await created["category"].delete(reason="Rollback failed GamerHQ game setup")
            except Exception:
                pass

        if created["role"] is not None:
            try:
                await created["role"].delete(reason="Rollback failed GamerHQ game setup")
            except Exception:
                pass

        raise


async def remove_game_structure(guild: discord.Guild, game: dict):
    for key in (
        "chat_channel_id",
        "memes_channel_id",
        "lfg_channel_id",
        "clips_channel_id",
        "create_voice_channel_id",
    ):
        cid = game.get(key)
        if cid:
            channel = guild.get_channel(cid)
            if channel:
                await channel.delete(reason=f"GamerHQ remove game: {game['name']}")

    category_id = game.get("category_id")
    if category_id:
        category = guild.get_channel(category_id)
        if category:
            await category.delete(reason=f"GamerHQ remove game: {game['name']}")

    # V1: removing an area must never remove the game role or library entry.
    db.deactivate_game(game["id"])


def build_choose_games_message():
    return (
        "# 🎮 Choose Your Games\n\n"
        "Pick the games you play or are interested in. Your game roles are part of your "
        "GamerHQ profile and may also give you access to dedicated game areas.\n\n"
        "**Game Buttons**\n"
        "Browse the categories below and click a game to quickly add or remove it.\n\n"
        "**Select Games**\n"
        "Want to manage several games at once? Use the **Select Games** button below. "
        "You can also use `/game select` in chat for a quick single-game change.\n\n"
        "**Suggest Game**\n"
        "Can't find the game you're looking for? Use **Suggest Game** below or `/game suggest` in chat.\n\n"
        "You can change your games anytime."
    )


def build_choose_games_sections():
    games = db.get_selectable_games()
    grouped = {group: [] for group in DISPLAY_GROUP_ORDER}
    for game in games:
        grouped.setdefault(game["display_group"], []).append(game)

    sections = []
    for group in DISPLAY_GROUP_ORDER:
        entries = grouped.get(group, [])
        for index in range(0, len(entries), 25):
            chunk = entries[index:index + 25]
            page = index // 25 + 1
            pages = (len(entries) + 24) // 25
            title = f"## {group}"
            if pages > 1:
                title += f" · {page}/{pages}"
            sections.append((title, chunk))
    return sections


def _choose_games_section_key(title: str) -> str:
    """Stable key for one category/page message, independent of message IDs."""
    cleaned = title.removeprefix("## ").strip()
    match = re.match(r"^(.*?)(?: · (\d+)/(\d+))?$", cleaned)
    if not match:
        return cleaned
    group = match.group(1).strip()
    page = match.group(2) or "1"
    return f"{group}::{page}"


def _looks_like_choose_games_message(content: str) -> bool:
    if content.startswith("# 🎮 Choose Your Games"):
        return True
    if not content.startswith("## "):
        return False
    return any(content.startswith(f"## {group}") for group in DISPLAY_GROUP_ORDER)


async def _pin_managed_message(message: discord.Message):
    await pin_managed_message(
        message,
        reason="GamerHQ managed Choose Your Games message",
    )


def _managed_message_matches(message, content, view):
    # Discord assigns numeric component IDs on send; those are not custom_ids.
    def without_ids(value):
        if isinstance(value, dict):
            return {k: without_ids(v) for k, v in value.items() if k != "id"}
        if isinstance(value, list):
            return [without_ids(v) for v in value]
        return value

    actual = [component.to_dict() for component in message.components]
    desired = view.to_components() if view is not None else []
    return (message.content == content and not message.embeds
            and without_ids(actual) == without_ids(desired))


async def refresh_choose_games_message(bot, view=None, intro_view=None):
    """
    Maintain pinned, bot-managed Choose Your Games messages.

    Existing GamerHQ Bot messages are rediscovered when stored IDs are missing,
    so refreshing the overview edits in place instead of reposting it. Duplicate
    bot-managed overview/category messages are removed, while messages from users
    or other bots are never touched.
    """
    if CHOOSE_GAMES_CHANNEL_ID == 0:
        return
    if view is None or intro_view is None:
        raise GameStructureError(
            "UPDATE CHOOSE-YOUR-GAMES",
            ValueError("Both category and intro views are required for a safe refresh."),
        )

    channel = bot.get_channel(CHOOSE_GAMES_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        raise GameStructureError(
            "UPDATE CHOOSE-YOUR-GAMES",
            RuntimeError("Configured choose-your-games channel was not found."),
        )

    try:
        bot_user = channel.guild.me
        bot_id = bot_user.id if bot_user else getattr(bot.user, "id", None)

        # Discover recent bot-owned managed messages. This repairs missing/stale DB
        # message IDs and prevents /game-admin overview from posting duplicates.
        discovered = []
        if bot_id is not None:
            async for candidate in channel.history(limit=100, oldest_first=False):
                if candidate.author.id == bot_id and _looks_like_choose_games_message(candidate.content or ""):
                    discovered.append(candidate)
        discovered_by_id = {message.id: message for message in discovered}

        intro_id = db.get_setting("choose_games_message_id")
        intro = None
        if intro_id:
            try:
                candidate = discovered_by_id.get(int(intro_id)) or await channel.fetch_message(int(intro_id))
                if bot_id is None or candidate.author.id == bot_id:
                    intro = candidate
            except discord.NotFound:
                pass

        discovered_intros = [m for m in discovered if (m.content or "").startswith("# 🎮 Choose Your Games")]
        if intro is None and discovered_intros:
            intro = discovered_intros[0]

        overview_view = intro_view() if callable(intro_view) else intro_view
        if intro is None:
            intro = await channel.send(build_choose_games_message(), view=overview_view)
        elif not _managed_message_matches(intro, build_choose_games_message(), overview_view):
            await intro.edit(content=build_choose_games_message(), embed=None, view=overview_view)
        await _pin_managed_message(intro)
        db.set_setting("choose_games_message_id", str(intro.id))

        # Remove only duplicate intros authored by this bot.
        for duplicate in discovered_intros:
            if duplicate.id != intro.id:
                await duplicate.delete()

        try:
            stored_raw = json.loads(db.get_setting("choose_games_section_message_ids") or "{}")
        except (TypeError, json.JSONDecodeError):
            stored_raw = {}

        # v2 stored a positional list. Keep it only as a fallback while moving to
        # stable section keys in v3.
        legacy_ids = stored_raw if isinstance(stored_raw, list) else []
        stored_ids = stored_raw if isinstance(stored_raw, dict) else {}

        discovered_sections = {}
        duplicate_sections = []
        for message in discovered:
            content = message.content or ""
            if not content.startswith("## "):
                continue
            key = _choose_games_section_key(content)
            if key not in discovered_sections:
                discovered_sections[key] = message
            else:
                duplicate_sections.append(message)

        sections = build_choose_games_sections()
        new_ids = {}
        used_ids = {intro.id}

        for idx, (title, games) in enumerate(sections):
            key = _choose_games_section_key(title)
            section_view = view(games) if callable(view) else view
            message = None

            stored_id = stored_ids.get(key)
            if stored_id:
                try:
                    candidate = discovered_by_id.get(int(stored_id)) or await channel.fetch_message(int(stored_id))
                    if bot_id is None or candidate.author.id == bot_id:
                        message = candidate
                except discord.NotFound:
                    pass

            if message is None:
                message = discovered_sections.get(key)

            if message is None and idx < len(legacy_ids):
                try:
                    candidate = await channel.fetch_message(int(legacy_ids[idx]))
                    if (bot_id is None or candidate.author.id == bot_id) and candidate.id not in used_ids:
                        message = candidate
                except discord.NotFound:
                    pass

            if message is None:
                message = await channel.send(title, view=section_view)
            elif not _managed_message_matches(message, title, section_view):
                await message.edit(content=title, embed=None, view=section_view)

            await _pin_managed_message(message)
            used_ids.add(message.id)
            new_ids[key] = message.id

        # Delete stale/duplicate category messages only when they are clearly
        # bot-owned Choose Your Games messages. This also cleans up the duplicate
        # messages created by older overview refreshes.
        stale_candidates = [m for m in discovered if (m.content or "").startswith("## ")]
        stale_candidates.extend(duplicate_sections)
        seen = set()
        for stale in stale_candidates:
            if stale.id in seen or stale.id in used_ids:
                continue
            seen.add(stale.id)
            await stale.delete()

        db.set_setting("choose_games_section_message_ids", json.dumps(new_ids))
        # Remove old pin notices created by previous managed-message versions.
        await cleanup_pin_system_messages(channel, bot_user_id=bot_id, limit=100)

    except Exception as exc:
        if isinstance(exc, GameStructureError):
            raise
        raise GameStructureError("UPDATE CHOOSE-YOUR-GAMES", exc) from exc



async def rebuild_choose_games_message(bot, view=None, intro_view=None):
    """
    Rebuild the complete bot-managed Choose Your Games UI from the current DB.

    This is intentionally a manual/admin recovery operation:
    - delete only GamerHQ Bot messages that clearly belong to Choose Your Games;
    - clear the stored managed message IDs;
    - create one fresh intro message and fresh category/page messages;
    - pin the fresh messages and persist their new IDs.

    User messages and messages from other bots are never deleted.
    """
    if CHOOSE_GAMES_CHANNEL_ID == 0:
        return {"deleted": 0, "created": 0}

    channel = bot.get_channel(CHOOSE_GAMES_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        raise GameStructureError(
            "REBUILD CHOOSE-YOUR-GAMES",
            RuntimeError("Configured choose-your-games channel was not found."),
        )

    try:
        bot_user = channel.guild.me
        bot_id = bot_user.id if bot_user else getattr(bot.user, "id", None)
        if bot_id is None:
            raise RuntimeError("Could not resolve the GamerHQ Bot user.")

        # Gather known managed IDs first, so stale messages can be removed even
        # when their content was edited by an older bot version.
        known_ids = set()

        intro_id = db.get_setting("choose_games_message_id")
        if intro_id:
            try:
                known_ids.add(int(intro_id))
            except (TypeError, ValueError):
                pass

        try:
            stored_raw = json.loads(db.get_setting("choose_games_section_message_ids") or "{}")
        except (TypeError, json.JSONDecodeError):
            stored_raw = {}

        if isinstance(stored_raw, dict):
            for value in stored_raw.values():
                try:
                    known_ids.add(int(value))
                except (TypeError, ValueError):
                    pass
        elif isinstance(stored_raw, list):
            for value in stored_raw:
                try:
                    known_ids.add(int(value))
                except (TypeError, ValueError):
                    pass

        # Manual overview rebuild is allowed to do a wider history scan than
        # normal user/admin actions. It is the single maintenance path that
        # deliberately repairs duplicate/stale managed messages.
        candidates = {}
        async for message in channel.history(limit=500, oldest_first=False):
            if message.author.id != bot_id:
                continue
            if message.id in known_ids or _looks_like_choose_games_message(message.content or ""):
                candidates[message.id] = message

        deleted = 0
        for message in candidates.values():
            try:
                await message.delete()
                deleted += 1
            except discord.NotFound:
                pass

        db.set_setting("choose_games_message_id", "")
        db.set_setting("choose_games_section_message_ids", "{}")

        overview_view = intro_view() if callable(intro_view) else intro_view
        intro = await channel.send(build_choose_games_message(), view=overview_view)
        await _pin_managed_message(intro)
        db.set_setting("choose_games_message_id", str(intro.id))

        new_ids = {}
        created = 1

        for title, games in build_choose_games_sections():
            section_view = view(games) if callable(view) else view
            message = await channel.send(title, view=section_view)
            await _pin_managed_message(message)
            new_ids[_choose_games_section_key(title)] = message.id
            created += 1

        db.set_setting("choose_games_section_message_ids", json.dumps(new_ids))
        await cleanup_pin_system_messages(channel, bot_user_id=bot_id, limit=100)

        return {"deleted": deleted, "created": created}

    except Exception as exc:
        if isinstance(exc, GameStructureError):
            raise
        raise GameStructureError("REBUILD CHOOSE-YOUR-GAMES", exc) from exc
