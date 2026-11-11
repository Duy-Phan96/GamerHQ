"""Separate operational intents over disposable SQLite and Discord fakes."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import discord
from database import db
from services import server_operations as ops, message_reconciliation as duplicates, health_service
from services.server_service import ServerMessageError
from tests import test_onboarding as fixtures


class DefaultVoice(fixtures.FakeChannel):
    __class__ = discord.VoiceChannel


class OperationTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.OnboardingTests.setUp
    add_message = fixtures.OnboardingTests.add_message

    def owner(self):
        self.guild.owner_id = 42
        self.guild.me.guild_permissions = discord.Permissions.all()
        actor = SimpleNamespace(id=42, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members = [actor]
        return actor

    async def test_bulk_setup_owner_sees_one_confirmation_and_manual_actions(self):
        from cogs.server import OperationsView
        from cogs.server_management import StructureView

        actor = self.owner()
        owner_menu = StructureView(self.guild, actor.id)
        admin_menu = StructureView(self.guild, actor.id + 1)
        labels = [button.label for button in owner_menu.children]
        self.assertEqual(labels[0], 'Setup All Missing')
        self.assertIn(ops.PHASE_TITLES['foundations'], labels)
        self.assertIn(ops.PHASE_TITLES['channels'], labels)
        self.assertNotIn('Setup All Missing', [button.label for button in admin_menu.children])
        draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
        view = OperationsView(self.guild, draft, self.bot, friendly=True)
        self.assertEqual(view.confirm_preview.label, 'Confirm Setup')
        self.assertIn('Create by type', view.text())

    async def test_bulk_setup_reuses_local_defaults_only_with_explicit_choice(self):
        actor = self.owner()
        default_text = self.guild.add_category('Textkanäle')
        default_voice = self.guild.add_category('Sprachkanäle')
        text = self.guild.add_channel('Allgemein', default_text)
        voice = DefaultVoice(self.guild, 'Allgemein', default_voice, 7001)
        self.guild.fetch_channels = AsyncMock(return_value=[*self.guild.channels, voice])
        message = self.add_message(text, 'Existing conversation')
        voice.overwrites[self.guild.custom] = discord.PermissionOverwrite(speak=False)
        with patch.object(ops, 'inspect_message', AsyncMock(side_effect=AssertionError('message scan'))):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            draft['rows'] = [r for r in draft['rows'] if r['name'] in ops.REUSE_ALIASES]
            self.assertEqual({row['bulk_action'] for row in draft['rows']}, {'choose'})
            unselected = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
            self.assertEqual((unselected['reused'], len(unselected['needs_review'])), ([], 2))
            choices = {row['key']: row['bulk_id'] for row in draft['rows']}
            result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices=choices)
            self.assertEqual((len(result['reused']), result['created'], result['remaining']), (2, [], []))
        self.assertEqual(db.get_setting('managed_channel:1:general'), str(text.id))
        self.assertEqual(db.get_setting('managed_channel:1:chill-lounge'), str(voice.id))
        self.assertEqual((text.category, voice.category), (default_text, default_voice))
        self.assertEqual(text.messages[message.id], message)
        self.assertFalse(voice.overwrites[self.guild.custom].speak)
        self.assertEqual(text.edits + voice.edits, [])

    async def test_bulk_setup_orders_dependencies_and_serializes_confirmations(self):
        actor = self.owner()
        original = ops.definitions
        names = {'pc', 'gaming', 'events', 'community-events', 'voice-channels', 'afk'}
        voice_channels = []
        calls = []

        async def create_voice(parent, name, **kwargs):
            channel = DefaultVoice(self.guild, name, parent, 7900)
            channel.overwrites = kwargs['overwrites']
            voice_channels.append(channel)
            return channel

        async def inventory():
            return [*self.guild.channels, *voice_channels]

        create_category = self.guild.create_category
        create_role = self.guild.create_role
        async def category(*args, **kwargs):
            calls.append('category')
            return await create_category(*args, **kwargs)
        async def role(*args, **kwargs):
            calls.append('role')
            return await create_role(*args, **kwargs)

        self.guild.fetch_channels = inventory
        self.guild.create_category = category
        self.guild.create_role = role
        with (patch.object(ops, 'definitions', lambda guild: [row for row in original(guild) if row['name'] in names and not (row['name'] == 'community-events' and row['kind'] == 'role')]),
              patch.object(fixtures.FakeCategory, 'create_voice_channel', create_voice, create=True),
              patch.object(ops, 'inspect_message', AsyncMock(side_effect=AssertionError('message scan')))):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            with self.assertRaises(ServerMessageError):
                await ops.apply(self.guild, actor, draft, self.bot)
            first, concurrent = await asyncio.gather(*(
                ops.apply(self.guild, actor, draft, self.bot, confirmed=True) for _ in range(2)))
            self.assertEqual(calls[:2], ['role', 'role'])
            self.assertEqual(calls.count('category'), 1)
            self.assertEqual(len(first['created']), 5)
            self.assertEqual(concurrent['created'], [])
            fresh = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            again = await ops.apply(self.guild, actor, fresh, self.bot, confirmed=True)
            self.assertEqual(again['created'], [])
            self.assertEqual(len(again['already_configured']), 6)
            self.assertEqual(len(voice_channels), 1)
            self.assertTrue(db.get_setting('managed_channel:1:community-events'))

    async def test_bulk_setup_keeps_independent_work_after_category_failure(self):
        actor = self.owner()
        original = ops.definitions
        names = {'events', 'community-events', 'support-tickets'}
        create_category = self.guild.create_category

        async def sometimes_fail(name, **kwargs):
            if ops.alias(name) == 'events':
                raise TimeoutError
            return await create_category(name, **kwargs)

        with (patch.object(ops, 'definitions', lambda guild: [row for row in original(guild)
                    if row['name'] in names and row['kind'] not in ('role', 'bot-role')]),
              patch.object(self.guild, 'create_category', sometimes_fail)):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual((len(result['created']), len(result['failed']), len(result['remaining'])), (1, 1, 2))
        self.assertIsNone(db.get_setting('managed_channel:1:community-events'))
        tickets = self.guild.get_channel(int(db.get_setting('managed_category:1:support-tickets')))
        self.assertIs(tickets.overwrites_for(self.guild.default_role).view_channel, False)

    async def test_bulk_setup_checks_explicit_allow_and_deny_against_host_permissions(self):
        actor = self.owner()
        permissions = discord.Permissions.all()
        permissions.administrator = permissions.manage_messages = permissions.create_private_threads = False
        self.guild.me.guild_permissions = permissions
        original = ops.definitions
        self.guild.categories.remove(self.staff)
        names = {'start-here', 'guide', 'staff', 'ticket-logs'}
        with patch.object(ops, 'definitions', lambda guild: [r for r in original(guild) if r['name'] in names]):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            rows = {r['name']: r for r in draft['rows']}
            self.assertEqual(rows['start-here']['bulk_action'], 'reuse')
            self.assertEqual(rows['guide']['bulk_action'], 'skip')
            self.assertIn('Create Private Threads', rows['guide']['reason'])
            self.assertIn('Manage Messages', rows['staff']['reason'])
            self.assertIn('STAFF', rows['ticket-logs']['reason'])
            result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual((len(result['reused']), len(result['needs_review']), len(result['blocked'])), (1, 2, 1))
        self.assertEqual(result['failed'], [])
        self.assertIsNone(db.get_setting('managed_category:1:staff'))

    async def test_bulk_setup_requires_verified_private_principals(self):
        actor = self.owner()
        self.guild.roles.remove(self.guild.mod)
        self.guild.categories.remove(self.staff)
        original = ops.definitions
        names = {'staff', 'affiliate-stats'}
        with patch.object(ops, 'definitions', lambda guild: [r for r in original(guild) if r['name'] in names]):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            self.assertTrue(all('verified Staff role' in r['reason'] for r in draft['rows']))
            self.guild.roles.append(self.guild.mod)
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            rows = {r['name']: r for r in draft['rows']}
            self.assertEqual(rows['staff']['bulk_action'], 'create')
            self.assertIn('Verify the installed Instant Gaming bot', rows['affiliate-stats']['reason'])

    async def test_bulk_setup_reports_sanitized_api_failures_and_resumes_from_fresh_inventory(self):
        actor = self.owner()
        original = ops.definitions
        names = {'events', 'community-events', 'start-here', 'newbies', 'guide'}
        create_category = self.guild.create_category
        rejected = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'),
                                     {'code': 50013, 'message': 'private API detail'})
        throttled = discord.HTTPException(SimpleNamespace(status=429, reason='Too Many Requests'),
                                          {'code': 0, 'message': 'private API detail'})

        async def deny_events(name, **kwargs):
            if ops.alias(name) == 'events':
                raise rejected
            return await create_category(name, **kwargs)

        create_channel = fixtures.FakeCategory.create_text_channel

        async def rate_limit_guide(parent, name, **kwargs):
            if ops.alias(name) == 'guide':
                raise throttled
            return await create_channel(parent, name, **kwargs)

        with patch.object(ops, 'definitions', lambda guild: [r for r in original(guild)
                          if r['name'] in names and r['kind'] not in ('role', 'bot-role')]):
            with (patch.object(self.guild, 'create_category', deny_events),
                  patch.object(fixtures.FakeCategory, 'create_text_channel', rate_limit_guide),
                  self.assertLogs(ops.log, level='WARNING') as logs):
                draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
                first = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
            self.assertEqual((len(first['created']), len(first['failed']), len(first['blocked']),
                              len(first['remaining'])), (1, 2, 1, 3))
            self.assertTrue(any('HTTP 403, code 50013' in line for line in first['failed']))
            self.assertTrue(any('HTTP 429' in line for line in first['failed']))
            self.assertIn('EVENTS', first['blocked'][0])
            self.assertNotIn('private API detail', str(first) + str(logs.output))
            keys = ['managed_category:1:start-here', 'managed_channel:1:newbies']
            retained = {key: db.get_setting(key) for key in keys}
            fresh = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            second = await ops.apply(self.guild, actor, fresh, self.bot, confirmed=True)
            self.assertEqual((len(second['created']), len(second['already_configured']), second['remaining']), (3, 2, []))
            self.assertEqual({key: db.get_setting(key) for key in keys}, retained)
            self.assertEqual(sum(ops.alias(c.name) == 'events' for c in self.guild.categories), 1)
            self.assertEqual(sum(ops.alias(c.name) == 'newbies' for c in self.guild.text_channels), 1)

    async def test_bulk_setup_does_not_create_duplicate_after_mapping_write_failure(self):
        actor = self.owner()
        original = ops.definitions
        persist = ops.persist

        def fail_mapping(guild, row, resource):
            if row['name'] == 'events':
                raise ops.sqlite3.OperationalError('private storage detail')
            return persist(guild, row, resource)

        with patch.object(ops, 'definitions', lambda guild: [r for r in original(guild)
                          if r['kind'] == 'category' and r['name'] == 'events']):
            with patch.object(ops, 'persist', fail_mapping):
                draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
                first = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
            self.assertIn('save mapping after Discord creation', first['failed'][0])
            existing = next(c for c in self.guild.categories if ops.alias(c.name) == 'events')
            fresh = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            second = await ops.apply(self.guild, actor, fresh, self.bot, confirmed=True)
            self.assertEqual((len(second['reused']), second['created'], second['remaining']), (1, [], []))
            self.assertEqual(db.get_setting('managed_category:1:events'), str(existing.id))

    async def test_bulk_setup_skips_changed_private_permissions_and_protected_matches(self):
        actor = self.owner()
        self.staff.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        db.set_setting('managed_category:1:staff', self.staff.id)
        event_area = self.guild.add_category('Events')
        protected = self.guild.add_channel('community-events', event_area)
        protected.topic = 'skill-resource:event-manager'
        original = ops.definitions
        names = {'staff', 'staff-chat', 'events', 'community-events', 'gaming-chat'}
        with patch.object(ops, 'definitions', lambda guild: [row for row in original(guild) if row['name'] in names]):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            rows = {row['name']: row for row in draft['rows']}
            self.assertEqual(rows['staff-chat']['bulk_action'], 'create')
            self.assertEqual(rows['community-events']['bulk_action'], 'skip')
            self.assertEqual(rows['gaming-chat']['bulk_action'], 'skip')
            self.staff.overwrites[self.guild.custom] = discord.PermissionOverwrite(view_channel=True)
            result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertIsNone(db.get_setting('managed_channel:1:staff-chat'))
        self.assertIn(rows['staff-chat']['label'], result['remaining'])
        self.assertIsNone(db.get_setting('managed_channel:1:community-events'))
        self.assertEqual(protected.edits, [])

    async def test_structure_phases_link_localized_channels_without_inspecting_messages(self):
        actor = self.owner()
        default_text = self.guild.add_category('Textkanäle')
        default_voice = self.guild.add_category('Sprachkanäle')
        text = self.guild.add_channel('allgemein', default_text)
        voice = DefaultVoice(self.guild, 'Allgemein', default_voice, 7001)
        self.guild.fetch_channels = AsyncMock(return_value=[*self.guild.channels, voice])
        history = self.add_message(text, 'Existing conversation')
        voice.overwrites[self.guild.custom] = discord.PermissionOverwrite(speak=False)
        existing = {channel.id for channel in [text, voice]}

        with patch.object(ops, 'inspect_message', AsyncMock(side_effect=AssertionError('message scan'))):
            draft = await ops.preview(self.guild, actor, 'reconcile', self.bot, scope='structure')
            rows = [r for r in draft['rows'] if r['name'] in ops.REUSE_ALIASES]
            self.assertEqual({r['status'] for r in rows}, {'AMBIGUOUS'})
            self.assertEqual({r['safe'][0] for r in rows}, existing)
            done, _ = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
            self.assertEqual(done, [])
            choices = {r['key']: r['safe'][0] for r in rows}
            done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices=choices)
            self.assertEqual((len(done), skipped), (2, []))
            self.assertEqual(text.edits + voice.edits, [])
            db.set_setting('managed_category:1:community', self.community.id)
            db.set_setting('managed_category:1:voice-channels', self.voice.id)
            plan = await ops.preview(self.guild, actor, 'repair', self.bot, scope='placement')
            db.set_setting('managed_channel:1:conflicting', text.id)
            plan['rows'] = [r for r in plan['rows'] if r['name'] == 'general']
            done, skipped = await ops.apply(self.guild, actor, plan, self.bot, confirmed=True)
            self.assertEqual(done, [])
            self.assertEqual(len(skipped), 1)
            self.assertEqual(text.edits, [])
            db.set_setting('managed_channel:1:conflicting', '')
            plan = await ops.preview(self.guild, actor, 'repair', self.bot, scope='placement')
            done, skipped = await ops.apply(self.guild, actor, plan, self.bot, confirmed=True)
            self.assertEqual((len(done), skipped), (2, []))
        self.assertEqual({text.id, voice.id}, existing)
        self.assertEqual(text.name, '💬・general')
        self.assertEqual(voice.name, '🔊 Chill Lounge')
        self.assertEqual((text.category, voice.category), (self.community, self.voice))
        self.assertEqual(text.messages[history.id], history)
        self.assertTrue(voice.overwrites[self.guild.custom].speak is False)
        self.assertTrue(all(c.edits[0]['sync_permissions'] is False and 'overwrites' not in c.edits[0]
                            for c in (text, voice)))

    async def test_phases_hold_owned_defaults_and_never_create_optional_chat(self):
        actor = self.owner()
        default = self.guild.add_channel('allgemein', self.guild.add_category('Textkanäle'))
        db.set_setting('managed_channel:1:custom', default.id)
        with patch.object(ops, 'inspect_message', AsyncMock(side_effect=AssertionError('message scan'))):
            plan = await ops.preview(self.guild, actor, 'setup', self.bot, scope='channels')
            general = next(r for r in plan['rows'] if r['name'] == 'general')
            optional = next(r for r in plan['rows'] if r['name'] == 'gaming-chat')
            self.assertEqual((general['status'], general['safe']), ('AMBIGUOUS', []))
            self.assertTrue(optional['optional'])
            self.assertTrue(all(r['kind'] in ('text', 'voice', 'warning') for r in plan['rows']))
            plan['rows'] = [general, optional]
            self.assertEqual(await ops.apply(self.guild, actor, plan, self.bot, confirmed=True), ([], []))
            foundations = await ops.preview(self.guild, actor, 'setup', self.bot, scope='foundations')
            self.assertTrue(all(r['kind'] in ('category', 'role', 'bot-role', 'warning') for r in foundations['rows']))
            foundations['rows'] = [r for r in foundations['rows'] if r['name'] in ('events', 'pc')]
            done, skipped = await ops.apply(self.guild, actor, foundations, self.bot, confirmed=True)
            self.assertEqual((len(done), skipped), (2, []))
            self.assertTrue(db.get_setting('managed_category:1:events'))
            self.assertIsNotNone(db.get_managed_role_by_key('base', 'pc'))
        self.assertEqual(default.edits, [])
        self.assertIsNone(db.get_setting('managed_channel:1:general'))

    async def test_fresh_database_links_existing_structure_without_discord_writes(self):
        actor = self.owner()
        before = {c.id for c in self.guild.channels}
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        row = next(r for r in draft['rows'] if r['name'] == 'looking-for-group')
        self.assertEqual(row['status'], 'AMBIGUOUS')  # Existing LFG is in COMMUNITY, not START HERE.
        channel = row['candidates'][0]
        done, _ = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices={row['key']: channel.id})
        self.assertTrue(done)
        self.assertEqual(db.get_setting(row['key']), str(channel.id))
        self.assertEqual(before, {c.id for c in self.guild.channels})
        self.assertFalse(any(c.edits or c.sends for c in self.guild.text_channels))
        again = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        done, _ = await ops.apply(self.guild, actor, again, self.bot, confirmed=True)
        self.assertEqual(done, [])

    async def test_ambiguous_channel_selection_persists_only_chosen_id(self):
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        other = self.guild.add_category('KAMEX')
        first = self.guild.add_channel('community-events', events)
        second = self.guild.add_channel('community-events', other)
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        row = next(r for r in draft['rows'] if r['name'] == 'community-events')
        self.assertEqual(row['status'], 'AMBIGUOUS')
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices={row['key']: second.id})
        self.assertEqual(db.get_setting(row['key']), str(second.id))
        self.assertEqual(first.edits + second.edits, [])

    async def test_duplicate_scan_survives_unrelated_structure_warning(self):
        self.guild.add_channel('community-events', self.staff)
        from cogs.server import future_community_copies
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        for _ in range(2): self.add_message(channel, future_community_copies()['giveaways'])
        rows = await duplicates.audit(self.guild, self.owner(), bot=self.bot)
        self.assertEqual(sum(r['status'] == 'DUPLICATE' for r in rows), 1)
        self.assertTrue(any(r['key'] == 'structure' for r in rows))
        draft = await duplicates.preview(self.guild, self.owner(), 'server_future_giveaways_message_id', bot=self.bot)
        self.assertEqual(len(draft['ids']), 2)

    async def test_reconcile_messages_never_deletes_and_links_explicit_choice(self):
        actor = self.owner()
        from cogs.server import future_community_copies
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        first, second = [self.add_message(channel, future_community_copies()['giveaways']) for _ in range(2)]
        key = 'server_future_giveaways_message_id'
        admin = SimpleNamespace(id=43, guild_permissions=discord.Permissions(administrator=True))
        self.guild.members.append(admin)
        draft = await ops.preview(self.guild, admin, 'reconcile', self.bot)
        row = next(r for r in draft['rows'] if r['key'] == key)
        self.assertEqual((row['status'], row['safe']), ('AMBIGUOUS', []))
        await ops.apply(self.guild, admin, dict(draft, rows=[row]), self.bot,
                        confirmed=True, choices={key: first.id})
        self.assertIsNone(db.get_setting(key))

        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices={key: first.id})
        self.assertEqual(db.get_setting(key), str(first.id))
        self.assertFalse(first.deleted or second.deleted)
        self.assertEqual(first.edits + second.edits, 0)

    async def test_repair_only_known_resources_and_repeated_repair_idempotent(self):
        actor = self.owner()
        guide = self.guild.add_channel('guide', self.community)
        unknown = self.guild.add_channel('manual', self.community)
        db.set_setting('managed_channel:1:guide', guide.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        before = {c.id for c in self.guild.channels}
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(guide.category, self.start)
        self.assertFalse(guide.overwrites_for(self.guild.default_role).send_messages)
        self.assertFalse(unknown.edits)
        self.assertEqual(before, {c.id for c in self.guild.channels})
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        done, _ = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(done, [])

    async def test_setup_does_not_duplicate_legacy_games_category(self):
        actor = self.owner()
        legacy = self.guild.add_category('GAMING')
        key = 'managed_category:1:games'
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        row = next(r for r in draft['rows'] if r['key'] == key)
        self.assertEqual(row['status'], 'SAFE_ADOPTION')
        self.assertEqual(row['safe'], [legacy.id])
        db.set_setting('managed_category:1:gaming', legacy.id)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot)
        draft['rows'] = [r for r in draft['rows'] if r['key'] == key]
        self.assertEqual(draft['rows'][0]['status'], 'EXACT_MATCH')
        self.assertEqual(await ops.apply(self.guild, actor, draft, self.bot, confirmed=True), ([], []))
        self.assertEqual([category.id for category in self.guild.categories if ops.alias(category.name) in {'games', 'gaming'}],
                         [legacy.id])
        canonical = self.guild.add_category('GAMES')
        db.set_setting(key, canonical.id)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot)
        draft['rows'] = [r for r in draft['rows'] if r['key'] == key]
        self.assertEqual(draft['rows'][0]['status'], 'AMBIGUOUS')
        self.assertEqual(await ops.apply(self.guild, actor, draft, self.bot, confirmed=True), ([], []))
        self.assertEqual(db.get_setting(key), str(canonical.id))
        self.assertEqual(db.get_setting('managed_category:1:gaming'), str(legacy.id))

    async def test_setup_skips_existing_ambiguous_resource(self):
        actor = self.owner()
        self.guild.add_channel('community-events', self.staff)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot)
        # Isolate this reviewed resource so the test specifically checks ambiguity.
        draft['rows'] = [r for r in draft['rows'] if r['name'] == 'community-events']
        before = {c.id for c in self.guild.channels}
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(before, {c.id for c in self.guild.channels})

    async def test_setup_creates_missing_category_channel_role_and_message_once(self):
        actor = self.owner()
        draft = await ops.preview(self.guild, actor, 'setup', self.bot)
        draft['rows'] = [r for r in draft['rows'] if r['name'] in ('events', 'community-events', 'english')]
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(db.get_setting('managed_channel:1:community-events'))
        again = await ops.preview(self.guild, actor, 'setup', self.bot)
        again['rows'] = [r for r in again['rows'] if r['key'] == 'community_events:1']
        await asyncio.gather(
            ops.apply(self.guild, actor, again, self.bot, confirmed=True),
            ops.apply(self.guild, actor, again, self.bot, confirmed=True),
        )
        cid = int(db.get_setting('managed_channel:1:community-events'))
        channel = self.guild.get_channel(cid)
        self.assertEqual(channel.sends, 1)
        again = await ops.preview(self.guild, actor, 'setup', self.bot)
        again['rows'] = [r for r in again['rows'] if r['key'] == 'community_events:1']
        await ops.apply(self.guild, actor, again, self.bot, confirmed=True)
        self.assertEqual(channel.sends, 1)

    async def test_unlinked_board_with_discord_assigned_component_ids_is_linked_in_place(self):
        from services.community_structure_service import EVENTS_INTRO
        from discord.components import _component_factory

        actor = self.owner()
        key = 'community_events:1'
        channel = self.guild.add_channel('community-events', self.guild.add_category('EVENTS'))
        message = self.add_message(channel, EVENTS_INTRO, pinned=True)
        review = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        row = next(r for r in review['rows'] if r['key'] == key)
        self.assertEqual((row['status'], row['safe']), ('AMBIGUOUS', []))
        rows = ops.message_view(self.guild, key).to_components()
        for index, component in enumerate(rows, 1):
            component['id'] = index * 10
            for offset, child in enumerate(component['components'], 1):
                child['id'] = index * 10 + offset
        message.components = [_component_factory(row) for row in rows]
        setup = await ops.preview(self.guild, actor, 'setup', self.bot)
        setup['rows'] = [r for r in setup['rows'] if r['key'] == key]
        self.assertEqual(setup['rows'][0]['status'], 'SAFE_ADOPTION')
        self.assertEqual(await ops.apply(self.guild, actor, setup, self.bot, confirmed=True), ([], []))

        reconcile = await ops.preview(self.guild, actor, 'reconcile', self.bot, scope='messages')
        self.assertTrue(all(r['kind'] in ('message', 'warning') for r in reconcile['rows']))
        reconcile['rows'] = [r for r in reconcile['rows'] if r['key'] == key]
        done, skipped = await ops.apply(self.guild, actor, reconcile, self.bot, confirmed=True)
        self.assertEqual((len(done), skipped), (1, []))
        self.assertEqual(db.get_setting(key), str(message.id))
        self.assertTrue(message.pinned)
        self.assertFalse(message.deleted)
        self.assertEqual((channel.sends, message.edits), (0, 0))

    async def test_exact_match_with_a_competing_legacy_heading_needs_owner_choice(self):
        from cogs.server import future_community_copies

        actor = self.owner()
        key = 'server_future_giveaways_message_id'
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        content = future_community_copies()['giveaways']
        exact = self.add_message(channel, content, pinned=True)
        legacy = self.add_message(channel, content.split('\n', 1)[0] + '\nLegacy body', pinned=True)
        reconcile = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        row = next(r for r in reconcile['rows'] if r['key'] == key)
        self.assertEqual((row['status'], row['safe']), ('AMBIGUOUS', [exact.id]))
        for mode in ('reconcile', 'setup'):
            await ops.apply(self.guild, actor, dict(reconcile, mode=mode, rows=[row]), self.bot, confirmed=True)
        self.assertIsNone(db.get_setting(key))
        self.assertEqual((channel.sends, exact.edits, legacy.edits), (0, 0, 0))
        self.assertTrue(exact.pinned and legacy.pinned)

    async def test_message_created_after_preview_blocks_confirmed_setup(self):
        from cogs.server import future_community_copies
        actor = self.owner()
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        key = 'server_future_giveaways_message_id'
        setup = await ops.preview(self.guild, actor, 'setup', self.bot)
        setup['rows'] = [r for r in setup['rows'] if r['key'] == key]
        self.assertEqual(setup['rows'][0]['status'], 'MISSING')
        self.add_message(channel, future_community_copies()['giveaways'] + '\nPrevious wording', pinned=True)
        done, skipped = await ops.apply(self.guild, actor, setup, self.bot, confirmed=True)
        self.assertEqual((len(done), len(skipped)), (0, 1))
        self.assertIsNone(db.get_setting(key))
        self.assertEqual(channel.sends, 0)

    async def test_incomplete_history_and_saved_state_cannot_authorize_creation(self):
        from cogs.server import future_community_copies
        from services import managed_message_service as managed
        actor = self.owner()
        key = 'server_future_giveaways_message_id'
        channel = next(c for c in self.guild.text_channels if c.name == 'giveaways')
        for _ in range(101):
            self.add_message(channel, 'Community conversation', author=20)
        plan = await ops.preview(self.guild, actor, 'setup', self.bot)
        self.assertEqual(next(r for r in plan['rows'] if r['key'] == key)['status'], 'AMBIGUOUS')

        channel.messages.clear()
        content = future_community_copies()['giveaways']
        managed.store(dict(key=key, guild_id=self.guild.id, channel_id=channel.id, message_id=9001,
                           content=content, content_hash=managed.digest(content), pending=True))
        plan = await ops.preview(self.guild, actor, 'setup', self.bot)
        row = next(r for r in plan['rows'] if r['key'] == key)
        self.assertEqual(row['status'], 'AMBIGUOUS')
        self.assertEqual(await ops.apply(self.guild, actor, dict(plan, rows=[row]), self.bot, confirmed=True), ([], []))
        self.assertEqual(channel.sends, 0)

    async def test_health_and_scan_are_database_read_only(self):
        self.owner()
        before = db.DB_PATH.read_bytes()
        await health_service.scan(self.guild, self.bot)
        await ops.scan(self.guild, self.bot)
        self.assertEqual(before, db.DB_PATH.read_bytes())
        with db.read_only(), self.assertRaises(Exception): db.set_setting('forbidden', 'change')

    async def test_confirmation_auth_and_changed_candidate_are_rechecked(self):
        actor = self.owner()
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        with self.assertRaises(ServerMessageError):
            await ops.apply(self.guild, actor, draft, self.bot)
        self.start.name = 'Changed while preview was open'
        done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(skipped)
        self.assertIsNone(db.get_setting('managed_category:1:start-here'))
        self.guild.members = []
        with self.assertRaises(ServerMessageError):
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)

    async def test_private_category_cannot_be_made_public_by_generic_repair(self):
        actor = self.owner()
        market = self.guild.add_category('MARKETPLACE')
        market.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        db.set_setting('managed_category:1:partners-benefits', market.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        _, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(any('private category' in s for s in skipped))
        self.assertIs(market.overwrites_for(self.guild.default_role).view_channel, False)

    async def test_ambiguous_choice_ui_cancel_and_auth_do_not_write(self):
        from cogs.server import OperationsView, MappingChoiceView, ResourceChoice
        actor = self.owner()
        draft = await ops.preview(self.guild, actor, 'reconcile', self.bot)
        parent = OperationsView(self.guild, draft, self.bot)
        row = next(r for r in draft['rows'] if r['status'] == 'AMBIGUOUS' and r['safe'])
        parent.page = draft['rows'].index(row) // 8
        parent.refresh_choices()
        self.assertTrue(any(isinstance(c, ResourceChoice) for c in parent.children))
        choice = MappingChoiceView(parent, row)
        interaction = SimpleNamespace(guild=self.guild, user=SimpleNamespace(id=99), response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock(), edit_message=AsyncMock()))
        self.assertFalse(await choice.interaction_check(interaction))
        interaction.user = actor
        self.assertTrue(await choice.interaction_check(interaction))
        await choice.cancel.callback(interaction)
        self.assertIsNone(db.get_setting(row['key']))
        self.assertFalse(await choice.interaction_check(interaction))

    async def test_repair_pins_same_message_and_preserves_customized_body(self):
        from services import managed_message_service as managed
        from services.community_structure_service import EVENTS_INTRO
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        channel = self.guild.add_channel('community-events', events)
        db.set_setting('managed_channel:1:community-events', channel.id)
        db.set_setting('managed_category:1:events', events.id)
        message = self.add_message(channel, EVENTS_INTRO)
        message.components = [SimpleNamespace(to_dict=lambda row=row: row)
                              for row in ops.message_view(self.guild, 'community_events:1').to_components()]
        db.set_setting('community_events:1', message.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(message.pinned)
        state = managed.load('community_events:1')
        self.assertIsNotNone(state)
        state.update(content='Custom owner text', customized=True, content_hash=managed.digest('Custom owner text'))
        managed.store(state)
        message.content, message.pinned = state['content'], False
        message.components = [SimpleNamespace(to_dict=lambda row=row: row)
                              for row in ops.message_view(self.guild, 'community_events:1').to_components()]
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertTrue(message.pinned)
        self.assertEqual(message.content, 'Custom owner text')
        self.assertEqual(channel.sends, 0)

    async def test_destination_changes_after_preview_block_repair(self):
        actor = self.owner()
        channel = self.guild.add_channel('guide', self.community)
        db.set_setting('managed_channel:1:guide', channel.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        self.start.overwrites[self.guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(channel.category_id, self.community.id)

    async def test_configured_selector_id_prevents_duplicate_after_rename(self):
        import config
        actor = self.owner()
        channel = next(c for c in self.guild.text_channels if c.name == 'choose-your-games')
        channel.name = 'My renamed selector'
        with patch.object(config, 'CHOOSE_GAMES_CHANNEL_ID', channel.id):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot)
            row = next(r for r in draft['rows'] if r['name'] == 'choose-your-games')
            self.assertEqual([c.id for c in row['candidates']], [channel.id])
            draft['rows'] = [row]
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertFalse(channel.edits)
        self.assertIsNone(db.get_setting(row['key']))

    async def test_repair_event_order_only_moves_mapped_channels_and_is_repeatable(self):
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        names = ['giveaways', 'manual-event', 'tournaments', 'community-events']
        for pos, name in enumerate(names):
            channel = self.guild.add_channel(name, events)
            channel.position = pos
            if name != 'manual-event': db.set_setting(f'managed_channel:1:{name}', channel.id)
        db.set_setting('managed_category:1:events', events.id)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        rows = [r for r in draft['rows'] if r['kind'] == 'order']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['changed'])
        unknown = next(c for c in self.guild.channels if c.name == 'manual-event')
        self.assertNotIn(unknown.id, [r['id'] for r in rows[0]['positions']])
        draft['rows'] = rows
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        again = await ops.preview(self.guild, actor, 'repair', self.bot)
        again['rows'] = [r for r in again['rows'] if r['kind'] == 'order']
        done, _ = await ops.apply(self.guild, actor, again, self.bot, confirmed=True)
        self.assertEqual(done, [])

    async def test_repair_rechecks_later_board_after_an_earlier_pin(self):
        from services import managed_message_service as managed

        actor = self.owner()
        first = self.add_message(self.intro, '# First board')
        second = self.add_message(self.intro, '# Second board')
        boards = {'first-board': (self.intro, first.content), 'second-board': (self.intro, second.content)}
        db.set_setting('first-board', first.id)
        db.set_setting('second-board', second.id)

        async def pin_and_change_later_board(**kwargs):
            first.pinned = True
            second.content = '# Changed while repair was applying'

        first.pin = pin_and_change_later_board
        with patch.object(managed, 'canonical_boards', return_value=boards):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            draft['rows'] = [r for r in draft['rows'] if r['key'] in boards]
            done, skipped = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual((len(done), len(skipped)), (1, 1))
        self.assertTrue(first.pinned)
        self.assertFalse(second.pinned)
        self.assertEqual(second.content, '# Changed while repair was applying')
        self.assertEqual(db.get_setting('second-board'), str(second.id))

    async def test_unavailable_generated_content_never_clears_existing_message(self):
        from services import managed_message_service as managed
        actor = self.owner()
        message = self.add_message(self.intro, 'Known body')
        db.set_setting('test-board', message.id)
        def boards(*args, defaults=False, **kwargs):
            return {'test-board': (self.intro, None if defaults else 'Known body')}
        with patch.object(managed, 'canonical_boards', side_effect=boards):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            self.assertTrue(any(r.get('reason', '').startswith('Default content unavailable') for r in draft['rows']))
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(message.content, 'Known body')
        self.assertEqual(message.edits, 0)

    async def test_fresh_rest_category_privacy_wins_over_stale_gateway_cache(self):
        import copy
        actor = self.owner()
        guide = self.guild.add_channel('guide', self.community)
        db.set_setting('managed_channel:1:guide', guide.id)
        db.set_setting('managed_category:1:start-here', self.start.id)
        rest_category = copy.copy(self.start)
        rest_category.overwrites = {self.guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        snapshot = [rest_category if c.id == self.start.id else c for c in self.guild.channels]
        with patch.object(self.guild, 'fetch_channels', AsyncMock(return_value=snapshot)):
            draft = await ops.preview(self.guild, actor, 'repair', self.bot)
            row = next(r for r in draft['rows'] if r['name'] == 'guide')
            self.assertEqual(row['status'], 'AMBIGUOUS')
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertFalse(guide.edits)

    async def test_message_repair_registers_rest_result_before_gateway_cache_updates(self):
        import copy
        from services import managed_message_service as managed
        from services.community_structure_service import EVENTS_INTRO
        actor = self.owner()
        events = self.guild.add_category('EVENTS')
        channel = self.guild.add_channel('community-events', events)
        db.set_setting('managed_channel:1:community-events', channel.id)
        db.set_setting('managed_category:1:events', events.id)
        message = self.add_message(channel, EVENTS_INTRO.split('\n')[0] + '\nOld generated text')
        message.components = [SimpleNamespace(to_dict=lambda row=row: row)
                              for row in ops.message_view(self.guild, 'community_events:1').to_components()]
        db.set_setting('community_events:1', message.id)
        returned = copy.copy(message)
        returned.content = EVENTS_INTRO
        message.edit = AsyncMock(return_value=returned)
        draft = await ops.preview(self.guild, actor, 'repair', self.bot)
        draft['rows'] = [r for r in draft['rows'] if r['key'] == 'community_events:1']
        await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        state = managed.load('community_events:1')
        self.assertEqual(state['content'], EVENTS_INTRO)
        self.assertEqual(state['message_id'], message.id)
        self.assertTrue(returned.pinned)
        self.assertEqual(channel.sends, 0)
