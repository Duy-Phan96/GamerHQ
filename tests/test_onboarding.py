"""Stateful Discord fakes exercise retries, identity and permission preservation."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import discord
from discord import app_commands

from database import db
from services import onboarding_service as onboarding
from services import command_guide_service as guides
from services import server_setup_service as setup
from services.server_service import ServerMessageError
from cogs import server


def missing():
    return discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'missing')


class FakeMessage:
    def __init__(self, channel, mid, content='', author=900, pinned=False, embeds=None):
        self.channel, self.guild, self.id = channel, channel.guild, mid
        self.content, self.author = content, SimpleNamespace(id=author)
        self.pinned, self.embeds = pinned, embeds or []
        self.type = discord.MessageType.default
        self.deleted = False
        self.edits = 0

    async def edit(self, **kwargs):
        self.content = kwargs.get('content', self.content)
        self.edits += 1
        if 'embed' in kwargs: self.embeds = []

    async def pin(self, **kwargs): self.pinned = True
    async def delete(self):
        self.deleted = True
        self.channel.messages.pop(self.id, None)


class FakeChannel:
    def __init__(self, guild, name, category, cid):
        self.guild, self.name, self.category, self.id = guild, name, category, cid
        self.topic = None
        self.overwrites = {}
        self.messages = {}
        self.edits = []
        self.sends = 0

    @property
    def category_id(self): return self.category.id
    @property
    def mention(self): return f'<#{self.id}>'
    def overwrites_for(self, target):
        original = self.overwrites.get(target, discord.PermissionOverwrite())
        return discord.PermissionOverwrite.from_pair(*original.pair())

    async def edit(self, **kwargs):
        self.edits.append(kwargs)
        for attr in ('name', 'category', 'overwrites', 'topic'):
            if attr in kwargs: setattr(self, attr, kwargs[attr])
        return self

    async def set_permissions(self, target, *, overwrite, **kwargs):
        self.overwrites[target] = overwrite

    async def send(self, *, content, **kwargs):
        self.sends += 1
        self.guild.sequence += 1
        message = FakeMessage(self, self.guild.sequence, content)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, mid):
        if mid not in self.messages: raise missing()
        return self.messages[mid]

    async def pins(self, **kwargs):
        for msg in list(self.messages.values()):
            if msg.pinned: yield msg

    async def history(self, **kwargs):
        for msg in list(self.messages.values()): yield msg


class FakeCategory:
    def __init__(self, guild, name, cid):
        self.guild, self.name, self.id = guild, name, cid
        self.overwrites = {}
    overwrites_for = FakeChannel.overwrites_for
    async def edit(self, **kwargs):
        if 'name' in kwargs: self.name = kwargs['name']
        if 'overwrites' in kwargs: self.overwrites = kwargs['overwrites']
        return self
    @property
    def text_channels(self): return [c for c in self.guild.text_channels if c.category is self]
    @property
    def voice_channels(self): return []
    async def create_text_channel(self, name, **kwargs):
        channel = self.guild.add_channel(name, self)
        channel.overwrites = kwargs.get('overwrites', {})
        channel.topic = kwargs.get('topic')
        return channel


class FakeGuild:
    def __init__(self):
        self.id, self.sequence = 1, 1000
        self.categories, self.text_channels = [], []
        self.default_role = self.role(1, default=True)
        self.mod = self.role(2, manage_messages=True)
        self.custom = self.role(3)
        self.roles = [self.default_role, self.mod, self.custom]
        self.me = MagicMock(spec=discord.Member); self.me.id = 900
    @staticmethod
    def role(rid, default=False, **permissions):
        result = MagicMock(spec=discord.Role)
        result.id, result.managed = rid, False
        result.is_default.return_value = default
        result.permissions = discord.Permissions(**permissions)
        return result
    @property
    def channels(self): return self.categories + self.text_channels
    def get_channel(self, cid): return next((c for c in self.channels if c.id == cid), None)
    async def create_category(self, name, **kwargs):
        category = self.add_category(name)
        category.overwrites = kwargs.get("overwrites", {})
        return category
    def add_category(self, name):
        self.sequence += 1
        category = FakeCategory(self, name, self.sequence)
        self.categories.append(category)
        return category
    def add_channel(self, name, category):
        self.sequence += 1
        channel = FakeChannel(self, name, category, self.sequence)
        self.text_channels.append(channel)
        return channel


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(db, 'DB_PATH', Path(temp.name) / 'test.db')
        patcher.start(); self.addCleanup(patcher.stop)
        db.init_db()
        onboarding._locks.clear()
        self.guild = FakeGuild()
        self.start = self.guild.add_category('👋 START HERE')
        self.community = self.guild.add_category('💬 COMMUNITY')
        self.voice = self.guild.add_category('🔊 VOICE CHANNELS')
        self.streamers = self.guild.add_category('🎥 STREAMERS')
        self.staff = self.guild.add_category('🔒 STAFF')
        self.old = self.guild.add_channel('👋・welcome', self.start)
        self.intro = self.guild.add_channel('👋・introductions', self.community)
        self.commands = self.guild.add_channel('📘・community-commands', self.community)
        for name in ('rules', 'announcements', 'choose-your-games', 'choose-your-roles'):
            self.guild.add_channel(name, self.start)
        self.guild.add_channel('looking-for-group', self.community)
        self.guild.add_channel('tournaments', self.community)
        self.guild.add_channel('giveaways', self.community)
        self.guild.add_channel('custom-voice-info', self.voice)
        self.guild.add_channel('streamer-commands', self.streamers)
        self.groups = []
        async def callback(interaction): pass
        for name, commands in [('game', ['select', 'suggest']), ('lfg', ['create', 'manage', 'join-code']), ('server', ['setup'])]:
            group = app_commands.Group(name=name, description='test')
            for command in commands:
                group.add_command(app_commands.Command(name=command, description='test', callback=callback))
            self.groups.append(group)
        self.bot = SimpleNamespace(tree=SimpleNamespace(get_commands=lambda **kwargs: self.groups))

    def add_message(self, channel, text, **kwargs):
        self.guild.sequence += 1
        message = FakeMessage(channel, self.guild.sequence, text, **kwargs)
        channel.messages[message.id] = message
        return message

    async def test_full_migration_and_repeated_update_preserve_identity_history_and_pins(self):
        welcome_pin = self.add_message(self.old, '# 👋 Welcome to GamerHQ!\nFind Games. Find Mates. Play Together.', pinned=True)
        introduction_pin = self.add_message(self.intro, '# Introduce yourself\nTell us what games you play on GamerHQ.', pinned=True)
        conversation = self.add_message(self.old, 'Hi friends', author=20)
        user_pin = self.add_message(self.intro, 'Introduce yourself to GamerHQ!', author=20, pinned=True)
        bot_pin = self.add_message(self.intro, 'A different staff notice', pinned=True)
        custom = discord.PermissionOverwrite(view_channel=False, attach_files=False, send_messages=True)
        game_channel = onboarding.unique(self.start.text_channels, 'choose-your-games')
        game_channel.overwrites[self.guild.custom] = custom
        game_channel.overwrites[self.guild.default_role] = discord.PermissionOverwrite(add_reactions=True, use_application_commands=True)
        changed, failed = await setup.repair_server(self.guild, self.bot)
        self.assertEqual(failed, [])
        self.assertTrue(changed)
        self.assertIs(onboarding.unique(self.community.text_channels, 'newbies'), self.old)
        self.assertIs(onboarding.unique(self.community.text_channels, 'bot-commands'), self.commands)
        new = onboarding.unique(self.start.text_channels, 'welcome')
        self.assertNotEqual(new.id, self.old.id)
        self.assertTrue(welcome_pin.deleted and introduction_pin.deleted)
        self.assertFalse(conversation.deleted or user_pin.deleted or bot_pin.deleted)
        self.assertFalse(new.overwrites[self.guild.default_role].send_messages)
        self.assertTrue(new.overwrites[self.guild.mod].send_messages)
        self.assertTrue(new.overwrites[self.guild.me].manage_messages)
        self.assertTrue(game_channel.overwrites[self.guild.default_role].add_reactions)
        self.assertTrue(game_channel.overwrites[self.guild.default_role].use_application_commands)
        self.assertFalse(game_channel.overwrites[self.guild.custom].view_channel)
        self.assertFalse(game_channel.overwrites[self.guild.custom].attach_files)
        self.assertFalse(game_channel.overwrites[self.guild.custom].send_messages)
        self.assertTrue(self.commands.overwrites[self.guild.default_role].send_messages)
        self.assertTrue(self.old.overwrites[self.guild.default_role].send_messages)
        channel_ids = [c.id for c in self.guild.channels]
        message_ids = list(new.messages)
        await setup.repair_server(self.guild, self.bot)
        self.assertEqual(channel_ids, [c.id for c in self.guild.channels])
        self.assertEqual(message_ids, list(new.messages))
        self.assertEqual(new.sends, 1)
        self.assertEqual(self.commands.sends, 1)
        self.assertEqual(self.voice.text_channels[0].edits, [])
        self.assertEqual(self.streamers.text_channels[0].edits, [])

    async def test_restart_does_not_recreate_intro_or_migrate_old_welcome(self):
        pin = self.add_message(self.intro, 'Introduce yourself to GamerHQ! Tell us what games you play.', pinned=True)
        await onboarding.refresh_onboarding(self.guild)
        self.assertTrue(pin.deleted)
        self.assertEqual(self.intro.sends, 0)
        self.assertEqual(self.old.name, '👋・welcome')
        await setup.repair_server(self.guild, self.bot)
        new = onboarding.unique(self.start.text_channels, 'welcome')
        await onboarding.refresh_onboarding(self.guild)
        self.assertEqual(new.sends, 1)
        self.assertEqual(self.intro.sends, 0)

    async def test_collision_preserves_channels_and_reports_manual_review(self):
        existing_newbies = self.guild.add_channel('newbies', self.community)
        existing_commands = self.guild.add_channel('bot-commands', self.community)
        ids = [c.id for c in self.guild.channels]
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertTrue(any('Both welcome and newbies' in failure for failure in failed))
        self.assertTrue(any('Both bot-commands' in failure for failure in failed))
        self.assertTrue(set(ids).issubset(c.id for c in self.guild.channels))
        self.assertEqual(self.old.name, '👋・welcome')
        self.assertIs(onboarding.unique(self.community.text_channels, 'newbies'), existing_newbies)
        self.assertEqual(existing_commands.sends, 1)

    async def test_duplicate_names_abort_before_mutation(self):
        self.guild.add_channel('welcome', self.community)
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertTrue(failed)
        self.assertEqual(self.old.edits, [])
        self.assertEqual(self.commands.edits, [])

    async def test_canonical_recovers_lost_setting_without_new_pin(self):
        await setup.repair_server(self.guild, self.bot)
        new = onboarding.unique(self.start.text_channels, 'welcome')
        db.set_setting(f'server_pinned_message_{new.id}', '')
        await onboarding.canonical_welcome(new)
        self.assertEqual(new.sends, 1)

    async def test_unrelated_bot_pin_at_stale_mapping_is_preserved(self):
        unrelated = self.add_message(self.commands, 'Staff announcement: maintenance tomorrow', pinned=True)
        db.set_setting(guides.COMMUNITY_GUIDE_MESSAGE_KEYS[0], unrelated.id)
        await setup.repair_server(self.guild, self.bot)
        self.assertEqual(unrelated.edits, 0)
        self.assertFalse(unrelated.deleted)
        self.assertEqual(self.commands.sends, 1)

    async def test_obsolete_guide_pages_removed_but_user_pin_preserved(self):
        first = self.add_message(self.commands, '# 🎮 GAME COMMANDS\nOld guide', pinned=True)
        second = self.add_message(self.commands, '# 🎯 LFG & EVENT COMMANDS\nOld guide', pinned=True)
        user = self.add_message(self.commands, '# 🎥 STREAMER COMMANDS', author=20, pinned=True)
        db.set_setting(guides.COMMUNITY_GUIDE_MESSAGE_KEYS[0], first.id)
        db.set_setting(guides.COMMUNITY_GUIDE_MESSAGE_KEYS[1], second.id)
        db.set_setting(guides.LEGACY_COMMUNITY_STREAMER_MESSAGE_KEY, user.id)
        await setup.repair_server(self.guild, self.bot)
        self.assertFalse(first.deleted)
        self.assertIn('see <#', first.content)
        self.assertNotIn('Music Bots', first.content)
        self.assertTrue(second.deleted)
        self.assertFalse(user.deleted)
        self.assertEqual(self.commands.sends, 0)

    async def test_pin_delete_failure_keeps_identity_for_retry(self):
        message = self.add_message(self.intro, 'Introduce yourself to GamerHQ! Tell us what games you play.', pinned=True)
        db.set_setting('server_introductions_message_id', message.id)
        async def fail(): raise discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        with patch.object(message, 'delete', fail):
            with self.assertRaises(discord.Forbidden): await onboarding.remove_obsolete_pins(self.intro)
        self.assertEqual(db.get_setting('server_introductions_message_id'), str(message.id))
        await onboarding.remove_obsolete_pins(self.intro)
        self.assertTrue(message.deleted)

    async def test_resume_after_rename_failure_and_missing_category(self):
        async def fail(**kwargs): raise discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'denied')
        with patch.object(self.old, 'edit', fail):
            _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertTrue(failed)
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertEqual(failed, [])
        self.assertEqual(sum(onboarding.alias(c.name) == 'welcome' for c in self.guild.text_channels), 1)
        self.guild.categories.remove(self.community)
        _, failed = await setup.repair_server(self.guild, self.bot)
        self.assertTrue(failed)
        self.assertEqual(len(self.guild.categories), 6)

    def test_guides_are_short_use_actual_commands_and_correct_music_disconnect(self):
        from services.community_structure_service import guide_text
        text = guide_text(self.guild)
        for command in ('/lfg create', '/lfg manage', '/lfg join-code'):
            self.assertIn(command, text)
        self.assertNotIn('/help', text)
        self.assertNotIn('/lobby', text)
        self.assertNotIn('/game suggest', text)
        self.assertIn('p!stop', text)
        self.assertNotIn('p!leave', text)
        self.assertIn('m!leave', text)
        self.assertLess(len(text), 2000)
        self.groups.clear()
        self.assertNotIn('/lfg create', guides.build_community_command_guide_pages(self.bot, self.guild)[0])
        welcome = onboarding.welcome_text(self.guild)
        self.assertNotIn('Find Games.', welcome)
        self.assertNotIn('New here?', welcome)
        self.assertIn('<#', welcome)
        self.assertEqual(server.default_copy_for(self.intro), '')


if __name__ == '__main__': unittest.main()
