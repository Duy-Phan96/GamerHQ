"""Member-bound, read-only GamerHQ server tour opened from the Welcome button."""
from __future__ import annotations

import time

import discord

from services.onboarding_service import alias
from services.server_service import ServerMessageError

TOUR_TIMEOUT = 600

STEPS = (
    {
        "title": "👋 Welcome to GamerHQ",
        "body": (
            "This quick tour shows you where to choose games, meet people, find sessions, "
            "use voice rooms and get help.\n\n"
            "Nothing is changed just by taking the tour. Personal profile details are optional."
        ),
        "channels": (),
    },
    {
        "title": "🎮 Choose your games",
        "body": (
            "Pick the games you actually play or follow. Your game choices control the game roles "
            "and the game channels you can see. You can change them again at any time."
        ),
        "channels": (("choose-your-games", "Open Choose Games"),),
        "games": True,
    },
    {
        "title": "💬 Meet the community",
        "body": (
            "Use General for everyday chat and Introductions when you want to say hello. "
            "These are the easiest places to meet people outside a specific game channel."
        ),
        "channels": (("general", "Open General"), ("introductions", "Open Introductions")),
    },
    {
        "title": "🎯 Find people to play with",
        "body": (
            "Looking for Group is the central place for gaming sessions. You can create or join "
            "scheduled sessions, and Community Events is used for broader GamerHQ game nights."
        ),
        "channels": (("looking-for-group", "Open LFG"), ("community-events", "Open Events")),
    },
    {
        "title": "🔊 Voice rooms",
        "body": (
            "Join Create Voice when you want your own temporary room. Empty temporary rooms are "
            "removed automatically. Use /voice manage inside your room for its controls."
        ),
        "channels": (),
        "voice": True,
    },
    {
        "title": "🆘 Help, commands & extras",
        "body": (
            "Need Support opens a private ticket with the GamerHQ team. Bot Commands keeps commands "
            "out of normal chat, and Support GamerHQ links to optional deals and partner offers.\n\n"
            "Your profile settings are optional and can be changed separately."
        ),
        "channels": (
            ("need-support", "Open Need Support"),
            ("bot-commands", "Open Bot Commands"),
            ("support-gamerhq", "Open Extras"),
        ),
        "profile": True,
    },
)


def _channel_url(channel):
    return f"https://discord.com/channels/{channel.guild.id}/{channel.id}"


def _viewable(channel, member):
    try:
        return channel.permissions_for(member).view_channel
    except Exception:
        # Test/simple fixture objects may not implement Discord's full resolver.
        everyone = channel.guild.default_role
        direct = channel.overwrites_for(everyone).view_channel
        parent = getattr(channel, "category", None)
        inherited = parent.overwrites_for(everyone).view_channel if parent else None
        return direct is not False and inherited is not False


def _core(guild, name):
    from services.community_structure_service import core_channel
    try:
        return core_channel(guild, name)
    except ServerMessageError:
        return None


def _voice(guild):
    matches = []
    for channel in getattr(guild, "voice_channels", []):
        if alias(channel.name) == "create-voice":
            matches.append(channel)
    return matches[0] if len(matches) == 1 else None


async def open_tour(interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Use Get Started inside GamerHQ.", ephemeral=True)
    view = ServerTour(interaction.guild, interaction.user.id)
    await interaction.response.send_message(view.content(interaction.user), view=view, ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())


class ServerTour(discord.ui.View):
    def __init__(self, guild, member_id, *, step=0):
        super().__init__(timeout=TOUR_TIMEOUT)
        self.guild = guild
        self.member_id = member_id
        self.step = max(0, min(step, len(STEPS) - 1))
        self.expires = time.monotonic() + TOUR_TIMEOUT
        self.closed = False
        self.rebuild()

    async def interaction_check(self, interaction):
        ok = (
            not self.closed
            and interaction.guild
            and interaction.guild.id == self.guild.id
            and interaction.user.id == self.member_id
            and time.monotonic() <= self.expires
        )
        if not ok:
            await interaction.response.send_message(
                "This tour expired or belongs to another member. Press Get Started again.",
                ephemeral=True,
            )
        return bool(ok)

    def content(self, member):
        row = STEPS[self.step]
        footer = (
            f"\n\n**Tour {self.step + 1}/{len(STEPS)}**"
            "\nYou can leave the tour at any time. Nothing here changes your settings automatically."
        )
        if self.step == len(STEPS) - 1:
            footer += "\n\n**You're ready. Have fun & see you in game! 🚀**"
        return f"# {row['title']}\n\n{row['body']}{footer}"

    def _link(self, label, channel, row):
        if channel is None:
            return
        self.add_item(discord.ui.Button(label=label[:80], style=discord.ButtonStyle.link,
                                        url=_channel_url(channel), row=row))

    def rebuild(self, member=None):
        self.clear_items()
        row = STEPS[self.step]

        # Context actions sit above navigation. Missing or inaccessible resources
        # are simply omitted; the tour never promises a broken destination.
        target_member = member or self.guild.get_member(self.member_id)
        if target_member:
            action_row = 0
            for name, label in row.get("channels", ()):
                channel = _core(self.guild, name)
                if channel and _viewable(channel, target_member):
                    self._link(label, channel, action_row)
            if row.get("voice"):
                channel = _voice(self.guild)
                if channel and _viewable(channel, target_member):
                    self._link("Open Create Voice", channel, action_row)

            if row.get("games"):
                button = discord.ui.Button(label="Choose Games", emoji="🎮",
                                           style=discord.ButtonStyle.primary, row=1)
                button.callback = self.choose_games
                self.add_item(button)
            if row.get("profile"):
                button = discord.ui.Button(label="Update Optional Profile", emoji="👤",
                                           style=discord.ButtonStyle.secondary, row=1)
                button.callback = self.profile
                self.add_item(button)

        back = discord.ui.Button(label="Back", disabled=self.step == 0, row=2)
        back.callback = self.back
        self.add_item(back)

        if self.step < len(STEPS) - 1:
            nxt = discord.ui.Button(label="Next", style=discord.ButtonStyle.primary, row=2)
            nxt.callback = self.next
            self.add_item(nxt)
            skip = discord.ui.Button(label="Skip to end", row=2)
            skip.callback = self.skip
            self.add_item(skip)
        else:
            done = discord.ui.Button(label="Finish Tour", style=discord.ButtonStyle.success, row=2)
            done.callback = self.finish
            self.add_item(done)

    async def repaint(self, interaction):
        member = interaction.guild.get_member(self.member_id) or interaction.user
        self.rebuild(member)
        await interaction.response.edit_message(content=self.content(member), view=self,
                                                allowed_mentions=discord.AllowedMentions.none())

    async def next(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.step = min(len(STEPS) - 1, self.step + 1)
        await self.repaint(interaction)

    async def back(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.step = max(0, self.step - 1)
        await self.repaint(interaction)

    async def skip(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.step = len(STEPS) - 1
        await self.repaint(interaction)

    async def finish(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.closed = True
        self.stop()
        await interaction.response.edit_message(
            content="# ✅ Tour complete\n\nYou can press **Get Started** again any time to reopen the tour.",
            view=None,
        )

    async def choose_games(self, interaction):
        if not await self.interaction_check(interaction):
            return
        from cogs.games import open_game_selector
        await open_game_selector(interaction)

    async def profile(self, interaction):
        if not await self.interaction_check(interaction):
            return
        from cogs.roles import open_profile
        await open_profile(interaction)

    async def on_timeout(self):
        self.closed = True
