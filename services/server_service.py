import discord

from database import db


class ServerMessageError(RuntimeError):
    pass


async def get_fixed_message(
    channel: discord.TextChannel,
    *,
    setting_key: str,
):
    """Return the currently stored managed message, or None if it no longer exists."""
    message_id = db.get_setting(setting_key)
    if not message_id:
        return None

    try:
        return await channel.fetch_message(int(message_id))
    except discord.NotFound:
        return None
    except (discord.Forbidden, discord.HTTPException) as exc:
        raise ServerMessageError(
            f"Could not fetch the existing managed message: {exc}"
        ) from exc


async def cleanup_pin_system_messages(
    channel: discord.TextChannel,
    *,
    bot_user_id: int | None = None,
    limit: int = 100,
):
    """Delete Discord 'pinned a message' system notices created by GamerHQ.

    Only MessageType.pins_add notices are touched. Normal user/bot messages are
    never considered. Failures are intentionally non-fatal because cosmetic
    cleanup must not break managed-message updates.
    """
    if bot_user_id is None:
        bot_user = channel.guild.me
        bot_user_id = bot_user.id if bot_user else None
    if bot_user_id is None:
        return 0

    deleted = 0
    try:
        async for message in channel.history(limit=limit):
            if (
                message.type == discord.MessageType.pins_add
                and message.author.id == bot_user_id
            ):
                try:
                    await message.delete()
                    deleted += 1
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
    except (discord.Forbidden, discord.HTTPException):
        pass
    return deleted


async def pin_managed_message(
    message: discord.Message,
    *,
    reason: str = "GamerHQ managed server information",
):
    """Pin a managed message and remove Discord's cosmetic pin notice."""
    if not message.pinned:
        await message.pin(reason=reason)
        await cleanup_pin_system_messages(
            message.channel,
            bot_user_id=message.guild.me.id if message.guild and message.guild.me else None,
            limit=20,
        )


async def upsert_fixed_message(channel, *, setting_key, content, pin=False, view=None,
                               recover_match=None, allowed_mentions=None):
    """Refresh canonical defaults while preserving explicitly customized boards."""
    from services import managed_message_service as managed
    spec = managed.specs(channel.guild).get(setting_key)
    if not spec:
        return await _upsert_fixed_message(channel, setting_key=setting_key, content=content,
                                          pin=pin, view=view, recover_match=recover_match,
                                          allowed_mentions=allowed_mentions)
    async with managed.lock(setting_key):
        state = managed.load(setting_key)
        defaults = managed.serialize_view(view)
        managed.validate(channel.guild, setting_key, content, defaults)
        default_content = content
        if state and state['customized']:
            managed.validate(channel.guild, setting_key, state['content'], state['buttons'])
            content, view = state['content'], managed.render(state['buttons'])
        original_match = recover_match

        def match(message):
            if state and state['channel_id'] == channel.id and state['message_id'] == message.id:
                # Accept only the last delivered body or an unconfirmed saved body.
                return (managed.owns(state, channel, message) or
                        (state.get('pending') and managed.digest(message.content) == managed.digest(state['content'])))
            return bool(original_match and original_match(message))

        current_id = db.get_setting(setting_key)
        if state and current_id and str(state['message_id']) == current_id:
            try:
                current = await channel.fetch_message(int(current_id))
            except discord.NotFound:
                current = None
            except discord.HTTPException:
                raise ServerMessageError('Could not inspect the managed message. Check permissions and retry.') from None
            if current and (not channel.guild.me or current.author.id != channel.guild.me.id or not match(current)):
                raise ServerMessageError('Managed message fingerprint changed; manual review required. Message preserved.')
        elif state and state['customized']:
            raise ServerMessageError('Customized message mapping changed; manual review required. Content retained.')
        message = await _upsert_fixed_message(channel, setting_key=setting_key, content=content,
                                             pin=pin, view=view, recover_match=match,
                                             allowed_mentions=discord.AllowedMentions.none())
        buttons = state['buttons'] if state and state['customized'] else defaults
        version = state['version'] if state else 0
        if state and any((state['content'] != content, state['buttons'] != buttons,
                          state['message_id'] != message.id, state['default_content'] != default_content,
                          state['default_buttons'] != defaults)):
            version += 1
        managed.store(dict(key=setting_key, guild_id=channel.guild.id, channel_id=channel.id,
                           message_id=message.id, label=spec[0], content=content, buttons=buttons,
                           default_content=default_content, default_buttons=defaults,
                           customized=bool(state and state['customized']), version=version,
                           content_hash=managed.digest(content), pending=False))
        return message


async def _upsert_fixed_message(
    channel: discord.TextChannel,
    *,
    setting_key: str,
    content: str,
    pin: bool = False,
    view: discord.ui.View | None = None,
    recover_match=None,
    allowed_mentions=None,
):
    """Create or edit one bot-managed fixed message and persist its message ID.

    The stored message is edited in place. If it was deleted, a new one is
    created and the stored ID is replaced.
    """
    message = None
    message_id = db.get_setting(setting_key)

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            message = None
        except (discord.Forbidden, discord.HTTPException) as exc:
            raise ServerMessageError(
                f"Could not fetch the existing managed message: {exc}"
            ) from exc

    try:
        # A bot can manage/delete other users' messages, but it cannot edit them.
        # Old manually-created GamerHQ guides may still have a stored message ID,
        # so only reuse a message when it was authored by this bot.
        bot_user = channel.guild.me
        if message is not None and (bot_user is None or message.author.id != bot_user.id):
            message = None
        if message is not None and recover_match is not None and not recover_match(message):
            # A stale mapping must not overwrite an unrelated bot/admin notice.
            message = None

        if message is None and recover_match is not None and bot_user:
            candidates = [item async for item in channel.pins(limit=None)]
            async for item in channel.history(limit=100):
                if all(item.id != existing.id for existing in candidates):
                    candidates.append(item)
            message = next((item for item in candidates if item.author.id == bot_user.id and recover_match(item)), None)
            if message:
                db.set_setting(setting_key, message.id)

        if message is None:
            message = await channel.send(content=content, view=view, allowed_mentions=allowed_mentions)
            db.set_setting(setting_key, message.id)
        else:
            await message.edit(content=content, embed=None, view=view, allowed_mentions=allowed_mentions)

        if pin:
            await pin_managed_message(
                message,
                reason="GamerHQ managed server information",
            )
            # Also clears older GamerHQ pin notices left behind by previous
            # versions of the bot.
            await cleanup_pin_system_messages(channel, limit=100)

        return message
    except (discord.Forbidden, discord.HTTPException) as exc:
        raise ServerMessageError(
            f"Could not update the managed server message: {exc}"
        ) from exc
