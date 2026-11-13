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

    async def test_template_combines_verified_reuse_creation_and_publication(self):
        from discord.components import _component_factory
        from services import server_templates as templates

        actor = self.owner()
        existing = self.guild.add_channel('Allgemein', self.guild.add_category('Textkanäle'))
        history = self.add_message(existing, 'Existing conversation', author=42, pinned=True)
        help_channel = self.guild.add_channel('need-support', self.start)
        names = {'community', 'general', 'events', 'community-events', 'start-here', 'need-support'}
        definitions = ops.definitions
        send = fixtures.FakeChannel.send

        async def send_with_components(channel, **kwargs):
            message = await send(channel, **kwargs)
            message.components = [_component_factory(row) for row in message.view.to_components()] if message.view else []
            return message

        template = templates.ServerTemplate('test-community', 'Community', ('community-events', 'need-support'))
        with patch.object(templates, 'TEMPLATES', (template,)), patch.object(
            ops, 'definitions', lambda guild: [row for row in definitions(guild) if row['name'] in names]
        ), patch.object(fixtures.FakeChannel, 'send', send_with_components):
            draft = await templates.preview(self.guild, actor, template, self.bot)
            self.assertEqual(draft['choices'], {'managed_channel:1:general': existing.id})
            self.assertEqual(draft['new_boards'], ('community-events',))
            self.assertEqual([(row['channel'].id, row['status']) for row in draft['boards']['rows']],
                             [(help_channel.id, 'MISSING')])
            self.assertIsNone(db.get_setting(templates.progress_key(self.guild, template)))
            result, _, remaining = await templates.apply(self.guild, actor, draft, self.bot)
            self.assertEqual(remaining, 0, result)
            self.assertEqual(db.get_setting('managed_channel:1:general'), str(existing.id))
            self.assertEqual(existing.edits, [])
            self.assertIs(existing.messages[history.id], history)
            self.assertTrue(history.pinned)
            channel = self.guild.get_channel(int(db.get_setting('managed_channel:1:community-events')))
            self.assertEqual(channel.sends, 1)
            self.assertEqual(help_channel.sends, 1)
            again = await templates.preview(self.guild, actor, template, self.bot)
            await templates.apply(self.guild, actor, again, self.bot)
            self.assertEqual(channel.sends, 1)
            self.assertEqual(help_channel.sends, 1)
            self.assertEqual(templates.progress(self.guild, template), 'complete')

    async def test_template_resume_holds_unknown_create_and_keeps_independent_progress(self):
        from services import server_templates as templates

        actor = self.owner()
        names = {'events', 'community-events', 'partners-benefits'}
        definitions = ops.definitions
        template = templates.ServerTemplate('test-retry', 'Community', ())
        create = self.guild.create_category

        async def unreliable(name, **kwargs):
            if ops.alias(name) == 'events':
                raise TimeoutError()
            return await create(name, **kwargs)

        with patch.object(templates, 'TEMPLATES', (template,)), patch.object(
            ops, 'definitions', lambda guild: [row for row in definitions(guild) if row['name'] in names]
        ), patch.object(self.guild, 'create_category', AsyncMock(side_effect=unreliable)) as request:
            draft = await templates.preview(self.guild, actor, template, self.bot)
            result, _, _ = await templates.apply(self.guild, actor, draft, self.bot)
            self.assertTrue(result['failed'])
            self.assertIsNotNone(db.get_setting('managed_category:1:partners-benefits'))
            self.assertEqual(templates.progress(self.guild, template), 'remaining')
            resumed = await templates.preview(self.guild, actor, template, self.bot)
            rows = {row['name']: row for row in resumed['structure']['rows']}
            self.assertIn('Earlier creation was not confirmed', rows['events']['reason'])
            self.assertTrue(rows['community-events']['bulk_blocked'])
            await templates.apply(self.guild, actor, resumed, self.bot)
            self.assertEqual(request.await_count, 2)

    async def test_template_reports_unapplied_safe_create_without_a_reason_field(self):
        from services import server_templates as templates

        actor = self.owner()
        template = templates.ServerTemplate('test-feed', 'Community', ())
        definitions = ops.definitions
        marketplace = self.guild.add_category('MARKETPLACE')
        db.set_setting('managed_category:1:partners-benefits', marketplace.id)
        with patch.object(templates, 'TEMPLATES', (template,)), patch.object(
            ops, 'definitions', lambda guild: [row for row in definitions(guild)
                                               if row['name'] in {'partners-benefits', 'gaming-news'}]
        ), patch.object(marketplace, 'create_text_channel', AsyncMock(
            side_effect=discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), {'code': 50013}))):
            draft = await templates.preview(self.guild, actor, template, self.bot)
            news = next(row for row in draft['structure']['rows'] if row['name'] == 'gaming-news')
            self.assertEqual(news['bulk_action'], 'create')
            result, _, remaining = await templates.apply(self.guild, actor, draft, self.bot)
            self.assertEqual(remaining, 1)
            self.assertIn('HTTP 403, code 50013', result['failed'][0])
            self.assertTrue(any('gaming-news' in line and 'Host posting only' in line for line in result['remaining']))
            self.assertEqual(templates.progress_record(self.guild, template)['pending_creates'], [])
            self.assertIsNone(db.get_setting('managed_channel:1:gaming-news'))

    async def test_template_reports_post_apply_verification_failure_and_can_resume(self):
        from services import server_templates as templates

        actor = self.owner()
        template = templates.ServerTemplate('test-resume', 'Community', ())
        definitions = ops.definitions
        with patch.object(templates, 'TEMPLATES', (template,)), patch.object(
            ops, 'definitions', lambda guild: [row for row in definitions(guild) if row['name'] == 'events']
        ):
            draft = await templates.preview(self.guild, actor, template, self.bot)
            with patch.object(templates, 'preview', AsyncMock(side_effect=TimeoutError('private exception detail'))):
                with self.assertLogs(templates.log, level='ERROR') as logs:
                    with self.assertRaisesRegex(templates.TemplateApplyError, 'verifying the result'):
                        await templates.apply(self.guild, actor, draft, self.bot)
            self.assertNotIn('private exception detail', '\n'.join(logs.output))
            saved = db.get_setting('managed_category:1:events')
            self.assertIsNotNone(saved)
            self.assertEqual(templates.progress(self.guild, template), 'applying')
            refreshed = await templates.preview(self.guild, actor, template, self.bot)
            result, _, remaining = await templates.apply(self.guild, actor, refreshed, self.bot)
            self.assertEqual((result['created'], remaining), ([], 0))
            self.assertEqual(db.get_setting('managed_category:1:events'), saved)

    async def test_template_publish_reports_the_effective_missing_channel_permissions(self):
        self.owner()
        permissions = discord.Permissions.all()
        permissions.administrator = permissions.send_messages = permissions.pin_messages = False
        with patch.object(ops, 'snapshot_permissions', return_value=permissions):
            self.assertTrue(await ops.public_message_channel(self.guild, self.old))
            reason = await ops.public_message_issue(self.guild, self.old, publish=True)
        self.assertIn('Send Messages and Pin Messages', reason)

    async def test_expired_template_does_not_open_a_checkpoint_or_apply(self):
        from services import server_templates as templates

        actor = self.owner()
        template = templates.GAMERHQ
        draft = {'template': template, 'structure': {'actor_id': actor.id, 'guild_id': self.guild.id, 'created': 0}}
        with patch.object(ops, 'apply', AsyncMock()) as apply:
            with self.assertRaisesRegex(ServerMessageError, 'fresh template preview'):
                await templates.apply(self.guild, actor, draft, self.bot)
        apply.assert_not_awaited()
        self.assertIsNone(db.get_setting(templates.progress_key(self.guild, template)))

    async def test_template_staff_create_recovers_after_discord_response_is_lost(self):
        from cogs.suggestions import staff_member
        from services import server_templates as templates, staff_access, ticket_service
        from services.role_service import assignable

        owner = self.owner()
        self.guild.categories.remove(self.staff)
        definitions = ops.definitions
        template = templates.ServerTemplate('test-staff', 'Community', (), staff_access=True)
        create = self.guild.create_role

        async def lost_response(**kwargs):
            await create(**kwargs)
            raise TimeoutError()

        with patch.object(templates, 'TEMPLATES', (template,)), patch.object(
            ops, 'definitions', lambda guild: [row for row in definitions(guild) if row['name'] in {'staff', 'staff-chat'}]
        ), patch.object(self.guild, 'create_role', AsyncMock(side_effect=lost_response)) as request:
            draft = await templates.preview(self.guild, owner, template, self.bot)
            with self.assertRaises(ServerMessageError):
                await templates.apply(self.guild, SimpleNamespace(id=43, guild_permissions=discord.Permissions.all()), draft)
            await templates.apply(self.guild, owner, draft, self.bot)
            self.assertIsNone(staff_access.mapped_role(self.guild))
            self.assertIn(staff_access.key(self.guild), templates.progress_record(self.guild, template)['pending_creates'])
            role = next(role for role in self.guild.roles if role.name == staff_access.ROLE_NAME)
            member = SimpleNamespace(id=43, guild=self.guild, roles=[role])
            self.assertFalse(staff_member(member))
            self.assertFalse(ticket_service.staff(member))
            recovery = await templates.preview(self.guild, owner, template, self.bot)
            self.assertIn('Unresolved creation checkpoints: 1', templates.report_details(recovery))
            self.assertIn('Confirm the reviewed Staff role', templates.report_summary(recovery))
            self.assertIn('Guild-wide permissions: 0', templates.report_details(recovery))
            await templates.apply(self.guild, owner, recovery, self.bot)
            self.assertIs(staff_access.mapped_role(self.guild), role)
            self.assertEqual(role.permissions.value, 0)
            self.assertEqual(role.members, [])
            self.assertFalse(assignable(role, self.guild))
            self.assertFalse(staff_member(member))
            self.assertFalse(ticket_service.staff(member))
            self.assertFalse(any(ops.alias(category.name) == 'staff' for category in self.guild.categories))
            private = await templates.preview(self.guild, owner, template, self.bot)
            await templates.apply(self.guild, owner, private, self.bot)
            ids = {name: db.get_setting(f'managed_{kind}:1:{name}')
                   for name, kind in [('staff', 'category'), ('staff-chat', 'channel')]}
            for channel_id in ids.values():
                channel = self.guild.get_channel(int(channel_id))
                self.assertIs(channel.overwrites_for(self.guild.default_role).view_channel, False)
                self.assertIs(channel.overwrites_for(role).view_channel, True)
                self.assertIsNone(channel.overwrites_for(self.guild.mod).view_channel)
                self.assertIsNone(channel.overwrites_for(role).manage_messages)
            refreshed = await templates.preview(self.guild, owner, template, self.bot)
            result, _, remaining = await templates.apply(self.guild, owner, refreshed, self.bot)
            self.assertEqual((result['created'], remaining, request.await_count), ([], 0, 1))

    async def test_template_does_not_link_forged_privileged_duplicate_or_self_assignable_staff(self):
        from services import server_templates as templates, staff_access

        owner = self.owner()
        template = templates.ServerTemplate('test-forged-staff', 'Community', (), staff_access=True)
        role = self.guild.role(7000, manage_roles=True)
        role.name = staff_access.ROLE_NAME
        self.guild.roles.append(role)
        with patch.object(templates, 'TEMPLATES', (template,)), patch.object(ops, 'definitions', return_value=[]):
            async def action():
                draft = await templates.preview(self.guild, owner, template, self.bot)
                self.assertIsNone(staff_access.mapped_role(self.guild))
                return next(row['bulk_action'] for row in draft['structure']['rows'] if row['kind'] == 'staff-role')
            self.assertEqual(await action(), 'skip')
            role.permissions = discord.Permissions.none()
            self.assertEqual(await action(), 'reuse')
            db.upsert_managed_role(role_id=role.id, role_kind='base', role_key='english', role_group='Language')
            self.assertEqual(await action(), 'skip')
            second = self.guild.role(7001)
            second.name = staff_access.ROLE_NAME
            self.guild.roles.append(second)
            self.assertEqual(await action(), 'skip')

    def test_mapped_staff_is_guild_scoped_and_does_not_add_existing_overwrites(self):
        from cogs.suggestions import private_overwrites
        from services import staff_access, ticket_service

        role = self.guild.custom
        db.set_setting(staff_access.key(self.guild), role.id)
        self.assertIs(staff_access.mapped_role(self.guild), role)
        self.assertIsNone(staff_access.mapped_role(SimpleNamespace(id=99, get_role=self.guild.get_role)))
        self.assertNotIn(role, private_overwrites(self.guild, self.staff))
        self.assertNotIn(role, ticket_service.private(self.guild, self.staff))
        self.staff.overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        self.assertEqual(private_overwrites(self.guild, self.staff)[role], self.staff.overwrites[role])
        role.permissions = discord.Permissions(manage_channels=True)
        self.assertIsNone(staff_access.mapped_role(self.guild))
        self.assertEqual(staff_access.creation_roles(self.guild), [])

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

    async def test_bulk_setup_initial_response_attaches_complete_read_only_preview(self):
        from cogs import server

        actor = self.owner()
        interaction = SimpleNamespace(guild=self.guild, user=actor, client=self.bot,
                                      response=SimpleNamespace(defer=AsyncMock()),
                                      edit_original_response=AsyncMock())
        original = ops.definitions
        names = {'staff', 'staff-chat', 'affiliate-stats', 'ig-purchases', 'gaming-chat'}
        self.guild.roles.remove(self.guild.mod)
        self.guild.categories.remove(self.staff)
        with (patch.object(server, 'check_admin', AsyncMock(return_value=True)),
              patch.object(ops, 'definitions', lambda guild: [r for r in original(guild) if r['name'] in names]),
              patch.object(ops, 'apply', AsyncMock(side_effect=AssertionError('write operation'))),
              patch.object(ops, 'inspect_message', AsyncMock(side_effect=AssertionError('message inventory')))):
            await server.open_operation(interaction, 'setup', scope='bulk', friendly=True)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        payload = interaction.edit_original_response.await_args.kwargs
        self.assertIsNone(payload['view'].page)
        self.assertTrue(payload['view'].confirm_preview.disabled)
        self.assertIn('Needs review 2', payload['content'])
        self.assertIn('Blocked by parent 2', payload['content'])
        self.assertNotIn('Page 1/', payload['content'])
        (file,) = payload['attachments']
        self.assertEqual(file.filename, 'bulk-setup-preview.txt')
        report = file.fp.read().decode()
        self.assertIn('5 resource decisions', report)
        self.assertIn('No verified Staff role', report)
        self.assertIn('Parent: 🔒 STAFF [Needs review]', report)
        self.assertIn('Parent: 🔒 AFFILIATE STATS [Needs review]', report)
        self.assertIn('[Skip by policy] 💬・gaming-chat', report)
        self.assertIsNone(db.get_setting('managed_category:1:staff'))

    async def test_bulk_setup_optional_pages_include_every_resource_and_return_to_summary(self):
        from cogs.server import OperationsView

        actor = self.owner()
        draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
        view = OperationsView(self.guild, draft, self.bot)
        interaction = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        self.assertEqual(view.next_page.label, 'Browse details')
        visited = []
        for page in range(view.page_count):
            await view.next_page.callback(interaction)
            self.assertEqual(view.page, page)
            self.assertIn(f'Page {page + 1}/{view.page_count}', view.text())
            visited.extend(view.page_rows())
        self.assertEqual(visited, draft['rows'])
        self.assertEqual(view.next_page.label, 'Back to summary')
        await view.next_page.callback(interaction)
        self.assertIsNone(view.page)

    async def test_bulk_setup_attachment_tracks_explicit_localized_choice_and_preserves_ids(self):
        from cogs.server import OperationsView, ResourceChoice

        actor = self.owner()
        category = self.guild.add_category('Textkanäle')
        text = self.guild.add_channel('Allgemein', category)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
        row = next(r for r in draft['rows'] if r['name'] == 'general')
        draft['rows'] = [row]
        view = OperationsView(self.guild, draft, self.bot)
        self.assertTrue(any(isinstance(item, ResourceChoice) for item in view.children))
        first = view.preview_message(include_report=True)['attachments'][0].fp.read().decode()
        self.assertIn('[Choose reuse]', first)
        self.assertIn(f'Candidate: Allgemein · ID {text.id} · verified for reuse', first)
        self.assertTrue(view.confirm_preview.disabled)
        view.choices[row['key']] = text.id
        second = view.preview_message(include_report=True)['attachments'][0].fp.read().decode()
        self.assertIn('[Link existing]', second)
        self.assertIn(f'Verified resource ID: {text.id}', second)
        self.assertFalse(view.confirm_preview.disabled)
        self.assertEqual(text.edits, [])
        self.assertIsNone(db.get_setting(row['key']))

    async def test_bulk_setup_reports_competing_default_mappings_without_reassigning_ids(self):
        actor = self.owner()
        category = self.guild.add_category('Textkanäle')
        text = self.guild.add_channel('Allgemein', category)
        voice = DefaultVoice(self.guild, 'Allgemein', category, 7100)
        self.guild.fetch_channels = AsyncMock(return_value=[*self.guild.channels, voice])
        keys = {'managed_channel:1:legacy-general': text.id,
                'managed_channel:1:legacy-voice': voice.id}
        for key, channel_id in keys.items():
            db.set_setting(key, channel_id)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
        draft['rows'] = [row for row in draft['rows'] if row['name'] in ops.REUSE_ALIASES]
        report = ops.bulk_preview_report(draft, {})
        self.assertIn('Existing channel has saved references that need ownership review', report)
        for key in keys:
            self.assertIn(f'Saved reference: settings.value ({key})', report)
        self.assertTrue(all(row['bulk_action'] == 'skip' for row in draft['rows']))
        result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual(result['created'] + result['reused'], [])
        self.assertEqual({key: db.get_setting(key) for key in keys}, {key: str(value) for key, value in keys.items()})
        self.assertEqual(text.edits + voice.edits, [])

    async def test_bulk_setup_plans_public_structure_without_message_moderation(self):
        actor = self.owner()
        permissions = discord.Permissions.all()
        permissions.administrator = permissions.manage_messages = False
        self.guild.me.guild_permissions = permissions
        original = ops.definitions
        names = {'partners-benefits', 'ai-tools', 'start-here', 'guide'}
        with patch.object(ops, 'definitions', lambda guild: [r for r in original(guild) if r['name'] in names]):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
        rows = {row['name']: row for row in draft['rows']}
        for name in ('partners-benefits', 'ai-tools', 'guide'):
            self.assertEqual(rows[name]['bulk_action'], 'create')
            self.assertFalse(any(value.manage_messages is True for value in rows[name]['create_overwrites'].values()))
        self.assertIs(rows['guide']['create_overwrites'][self.guild.default_role].send_messages, False)

    async def test_bulk_setup_reuses_local_defaults_only_with_explicit_choice(self):
        from services import server_change_observer as observer

        actor = self.owner()
        default_text = self.guild.add_category('Textkanäle')
        default_voice = self.guild.add_category('Sprachkanäle')
        text = self.guild.add_channel('Allgemein', default_text)
        voice = DefaultVoice(self.guild, 'Allgemein', default_voice, 7001)
        self.guild.fetch_channels = AsyncMock(return_value=[*self.guild.channels, voice])
        message = self.add_message(text, 'Existing conversation')
        voice.overwrites[self.guild.custom] = discord.PermissionOverwrite(speak=False)
        with db.connect() as conn:
            for channel in (text, voice):
                observed = observer.snapshot(channel, 'channel')
                observer._record(conn, self.guild, 'channel', channel.id, dict(observed, name='previous'), observed)
            observer._store(conn, observer.key(self.guild.id, 'guild', self.guild.id), {'system_channel_id': text.id})
            history = [tuple(row) for row in conn.execute('SELECT * FROM structure_change_log ORDER BY id')]
            observations = [tuple(row) for row in conn.execute(
                "SELECT * FROM settings WHERE key LIKE 'owner_observation:%' ORDER BY key")]
        self.assertEqual((len(history), len(observations)), (2, 3))
        self.assertTrue(duplicates.runtime_reference_locations(text.id, 'managed_channel:1:general'))
        with patch.object(ops, 'inspect_message', AsyncMock(side_effect=AssertionError('message scan'))):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            draft['rows'] = [r for r in draft['rows'] if r['name'] in ops.REUSE_ALIASES]
            self.assertEqual({row['bulk_action'] for row in draft['rows']}, {'choose'})
            unselected = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
            self.assertEqual((unselected['reused'], len(unselected['needs_review'])), ([], 2))
            choices = {row['key']: row['bulk_id'] for row in draft['rows']}
            result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True, choices=choices)
            self.assertEqual((len(result['reused']), result['created'], result['remaining']), (2, [], []))
            repeat = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            self.assertTrue(all(row['status'] == 'EXACT_MATCH' for row in repeat['rows'] if row['name'] in ops.REUSE_ALIASES))
        with db.connect() as conn:
            self.assertEqual(history, [tuple(row) for row in conn.execute('SELECT * FROM structure_change_log ORDER BY id')])
            self.assertEqual(observations, [tuple(row) for row in conn.execute(
                "SELECT * FROM settings WHERE key LIKE 'owner_observation:%' ORDER BY key")])
        self.assertEqual(db.get_setting('managed_channel:1:general'), str(text.id))
        self.assertEqual(db.get_setting('managed_channel:1:chill-lounge'), str(voice.id))
        self.assertEqual((text.category, voice.category), (default_text, default_voice))
        self.assertEqual(text.messages[message.id], message)
        self.assertFalse(voice.overwrites[self.guild.custom].speak)
        self.assertEqual(text.edits + voice.edits, [])

    async def test_bulk_setup_revalidates_localized_channel_ownership_before_linking(self):
        actor = self.owner()
        category = self.guild.add_category('Textkanäle')
        text = self.guild.add_channel('Allgemein', category)
        draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
        row = next(row for row in draft['rows'] if row['name'] == 'general')
        self.assertEqual(row['bulk_action'], 'choose')
        draft['rows'] = [row]
        db.set_setting('managed_channel:1:legacy-general', text.id)
        result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True,
                                 choices={row['key']: text.id})
        self.assertEqual((result['reused'], result['created'], result['remaining']), ([], [], [row['label']]))
        self.assertIsNone(db.get_setting(row['key']))
        self.assertEqual(text.edits, [])

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
        names = {'start-here', 'guide', 'staff', 'ticket-logs', 'partners-benefits'}
        with patch.object(ops, 'definitions', lambda guild: [r for r in original(guild) if r['name'] in names]):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            rows = {r['name']: r for r in draft['rows']}
            self.assertEqual(rows['start-here']['bulk_action'], 'reuse')
            self.assertEqual(rows['guide']['bulk_action'], 'skip')
            self.assertIn('Create Private Threads', rows['guide']['reason'])
            for name in ('staff', 'ticket-logs'):
                self.assertEqual(rows[name]['bulk_action'], 'create')
                self.assertIs(rows[name]['create_overwrites'][self.guild.default_role].view_channel, False)
                self.assertIsNone(rows[name]['create_overwrites'][self.guild.me].manage_messages)
            self.assertIn('Create Private Threads', rows['partners-benefits']['reason'])
            result = await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
        self.assertEqual((len(result['created']), len(result['reused']), len(result['needs_review'])), (2, 1, 2))
        self.assertEqual(result['failed'], [])
        self.assertTrue(db.get_setting('managed_category:1:staff'))

    async def test_bulk_setup_requires_verified_private_principals(self):
        actor = self.owner()
        self.guild.roles.remove(self.guild.mod)
        self.guild.custom.name = 'Staff'
        self.guild.categories.remove(self.staff)
        original = ops.definitions
        names = {'staff', 'affiliate-stats'}
        with patch.object(ops, 'definitions', lambda guild: [r for r in original(guild) if r['name'] in names]):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            self.assertTrue(all('verified Staff role' in r['reason'] for r in draft['rows']))
            report = ops.bulk_preview_report(draft, {})
            self.assertIn('Staff roles verified by the current policy: none', report)
            self.assertIn(f'Staff-named role without verified authority: Staff (ID {self.guild.custom.id})', report)
            self.guild.roles.append(self.guild.mod)
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            rows = {r['name']: r for r in draft['rows']}
            self.assertEqual(rows['staff']['bulk_action'], 'create')
            self.assertIn('Verify the installed Instant Gaming bot', rows['affiliate-stats']['reason'])

    async def test_bulk_setup_reports_sanitized_api_failures_and_resumes_from_fresh_inventory(self):
        actor = self.owner()
        original = ops.definitions
        names = {'events', 'community-events', 'start-here', 'community', 'suggestions', 'guide'}
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
            keys = ['managed_category:1:start-here', 'managed_category:1:community', 'managed_channel:1:suggestions']
            retained = {key: db.get_setting(key) for key in keys}
            fresh = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            second = await ops.apply(self.guild, actor, fresh, self.bot, confirmed=True)
            self.assertEqual((len(second['created']), len(second['already_configured']), second['remaining']), (3, 3, []))
            self.assertEqual({key: db.get_setting(key) for key in keys}, retained)
            self.assertEqual(sum(ops.alias(c.name) == 'events' for c in self.guild.categories), 1)
            self.assertEqual(sum(ops.alias(c.name) == 'suggestions' for c in self.guild.text_channels), 1)

    async def test_bulk_feed_reports_overwrite_targets_and_checks_destination_access(self):
        actor = self.owner()
        self.guild.me.guild_permissions.administrator = False
        self.guild.me.top_role = self.guild.role(20)
        marketplace = self.guild.add_category('MARKETPLACE')
        db.set_setting('managed_category:1:partners-benefits', marketplace.id)
        feed_role = self.guild.role(15)
        self.guild.roles.append(feed_role)
        from services.bot_group_service import key
        db.set_setting(key(self.guild, 'gaming'), feed_role.id)
        names = {'partners-benefits', 'gaming-news', 'gaming-deals'}
        original = ops.definitions
        category_permissions = discord.Permissions(view_channel=False, manage_channels=True)
        rejected = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'),
                                     {'code': 50013, 'message': 'private API detail'})
        with (patch.object(ops, 'definitions', lambda guild: [r for r in original(guild)
                          if r['name'] in names and r['kind'] in ('category', 'text')]),
              patch.object(marketplace, 'permissions_for', lambda member: category_permissions, create=True),
              patch.object(marketplace, 'create_text_channel', AsyncMock(side_effect=rejected)) as create):
            blocked = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            report = ops.bulk_preview_report(blocked, {})
            self.assertIn('Host needs effective View Channel and Manage Channels', report)
            self.assertIn(f'Destination ID {marketplace.id} (View Channel=False, Manage Channels=True)', report)
            create.assert_not_awaited()

            category_permissions.view_channel = True
            ready = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            report = ops.bulk_preview_report(ready, {})
            self.assertNotIn('role 15 at position 15', report)
            self.assertEqual(sum(row['bulk_action'] == 'create' for row in ready['rows']), 2)
            with self.assertLogs(ops.log, level='WARNING') as logs:
                result = await ops.apply(self.guild, actor, ready, self.bot, confirmed=True)

            self.assertEqual({ops.alias(call.args[0]) for call in create.await_args_list}, {'gaming-news', 'gaming-deals'})
            self.assertIn(f'Destination ID {marketplace.id}', result['failed'][0])
            self.assertIn('Host role 20 at position 20', result['failed'][0])
            self.assertIn('HTTP 403, code 50013', result['failed'][0])
            self.assertIn('Parent overwrites:', result['failed'][0])
            self.assertIn('does not identify a rejected overwrite', result['failed'][0])
            self.assertNotIn('private API detail', str(result) + str(logs.output))
            self.assertNotIn('requested overwrites', str(logs.output))
            self.assertIsNone(db.get_setting('managed_channel:1:gaming-news'))

    async def test_empty_marketplace_and_feeds_ignore_publisher_policy(self):
        from services import instant_gaming_service as integration

        actor = self.owner()
        self.guild.me.guild_permissions.administrator = False
        names = {'partners-benefits', 'gaming-news', 'gaming-deals'}
        original = ops.definitions
        with (patch.object(ops, 'definitions', lambda guild: [r for r in original(guild)
                                                            if r['name'] in names and r['kind'] in ('category', 'text')]),
              patch.object(integration, 'configured_bot', side_effect=AssertionError('publisher lookup during setup'))):
            draft = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            self.assertEqual({r['name'] for r in draft['rows'] if r['bulk_action'] == 'create'}, names)
            for row in draft['rows']:
                if row['bulk_action'] != 'create':
                    continue
                policy = row['create_overwrites']
                self.assertEqual(set(policy), {self.guild.default_role, self.guild.me})
                self.assertTrue(policy[self.guild.me].send_messages)
                self.assertTrue(policy[self.guild.me].read_message_history)
                self.assertTrue(all(getattr(policy[self.guild.default_role], flag) is False for flag in ops.FEED_POSTING))
                self.assertTrue(all(value.manage_roles is None and value.manage_channels is None for value in policy.values()))
            await ops.apply(self.guild, actor, draft, self.bot, confirmed=True)
            before = {name: db.get_setting(f'managed_channel:1:{name}') for name in ops.PUBLIC_FEEDS}
            self.assertTrue(all(before.values()))
            fresh = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            result = await ops.apply(self.guild, actor, fresh, self.bot, confirmed=True)
            self.assertFalse(result['created'])
        publisher = self.guild.role(7520)
        with patch.object(integration, 'configured_bot', return_value=publisher):
            for name, channel_id in before.items():
                channel = integration.resolve(self.guild, name, mapped_only=True)
                self.assertEqual(channel.id, int(channel_id))
                acl = integration.overwrites(self.guild, name, channel.overwrites, channel.category)
                self.assertTrue(acl[publisher].send_messages)
                self.assertNotIn(publisher, channel.overwrites)

    def test_template_uses_existing_feed_board_keys_and_counts_affiliate_separately(self):
        from services import instant_gaming_service as integration, server_templates as templates

        keys = templates.entry_message_keys(self.guild, {'gaming-news': 4210, 'gaming-deals': 4211})
        self.assertEqual(keys, {integration.message_key(self.guild, name): name for name in ops.PUBLIC_FEEDS})
        draft = {'structure': {'rows': [{'name': 'affiliate-stats', 'bulk_action': 'skip'},
                                       {'name': 'ig-purchases', 'bulk_action': 'skip', 'bulk_blocked': True}]},
                 'choices': {}, 'boards': {'rows': []}, 'deferred': []}
        counts = templates.counts(draft)
        self.assertEqual((counts['Needs review'], counts['Blocked'], counts['Optional integration review']), (0, 0, 2))

    async def test_gaming_news_inherits_only_an_identical_read_only_policy(self):
        actor = self.owner()
        self.guild.me.guild_permissions.administrator = False
        marketplace = self.guild.add_category('MARKETPLACE')
        db.set_setting('managed_category:1:partners-benefits', marketplace.id)
        names = {'partners-benefits', 'gaming-news'}
        original = ops.definitions
        created = []

        async def inherit(name, **kwargs):
            self.assertNotIn('overwrites', kwargs)
            channel = self.guild.add_channel(name, marketplace)
            channel.overwrites = dict(marketplace.overwrites)
            created.append(channel)
            return channel

        with (patch.object(ops, 'definitions', lambda guild: [r for r in original(guild)
                                                            if r['name'] in names and r['kind'] in ('category', 'text')]),
              patch.object(marketplace, 'create_text_channel', AsyncMock(side_effect=inherit)) as create):
            first = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            news = next(row for row in first['rows'] if row['name'] == 'gaming-news')
            self.assertFalse(news['inherit_parent'])
            marketplace.overwrites = dict(news['create_overwrites'])
            reviewed = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            news = next(row for row in reviewed['rows'] if row['name'] == 'gaming-news')
            self.assertTrue(news['inherit_parent'])
            self.assertIs(news['create_overwrites'][self.guild.default_role].send_messages, False)
            self.assertIs(news['create_overwrites'][self.guild.me].send_messages, True)
            marketplace.overwrites[self.guild.custom] = discord.PermissionOverwrite(send_messages=True)
            result = await ops.apply(self.guild, actor, reviewed, self.bot, confirmed=True)
            self.assertTrue(result['needs_review'])
            create.assert_not_awaited()
            del marketplace.overwrites[self.guild.custom]
            reviewed = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            await ops.apply(self.guild, actor, reviewed, self.bot, confirmed=True)
            self.assertEqual(db.get_setting('managed_channel:1:gaming-news'), str(created[0].id))
            fresh = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            await ops.apply(self.guild, actor, fresh, self.bot, confirmed=True)
            create.assert_awaited_once()
            # Simulate a lost mapping after creation and an unexpected write grant.
            db.set_setting('managed_channel:1:gaming-news', '')
            created[0].overwrites[self.guild.custom] = discord.PermissionOverwrite(send_messages=True)
            fresh = await ops.preview(self.guild, actor, 'setup', self.bot, scope='bulk')
            news = next(row for row in fresh['rows'] if row['name'] == 'gaming-news')
            self.assertEqual(news['bulk_action'], 'skip')
            self.assertIn('verified feed policy', news['reason'])
            await ops.apply(self.guild, actor, fresh, self.bot, confirmed=True)
            create.assert_awaited_once()

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
