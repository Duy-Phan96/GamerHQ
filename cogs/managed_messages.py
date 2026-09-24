"""Ephemeral owner/admin UI for the existing /server pinned-messages command."""
import copy
import sqlite3
from dataclasses import dataclass

import discord
from services import managed_message_service as service
from services.server_service import ServerMessageError


async def error(interaction, message):
    send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@dataclass
class Session:
    guild: object
    actor_id: int
    generation: int = 0
    finished: bool = False


class EditorView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=600)
        self.session = session
        session.generation += 1
        self.generation = session.generation

    async def interaction_check(self, interaction):
        if (self.session.finished or self.generation != self.session.generation or
                interaction.user.id != self.session.actor_id or not interaction.guild or
                interaction.guild.id != self.session.guild.id or
                not service.authorized(interaction.guild, interaction.user)):
            await error(interaction, 'This editor expired, belongs to another administrator, or you no longer have access. Reopen /server pinned-messages.')
            return False
        return True

    async def on_error(self, interaction, exception, item):
        # Never echo Discord payloads, database values or custom URLs in errors.
        text = str(exception) if isinstance(exception, ServerMessageError) else 'Could not complete this action. Check /server health and reopen the editor.'
        await error(interaction, text)

    def button(self, label, callback, *, disabled=False, style=discord.ButtonStyle.secondary):
        item = discord.ui.Button(label=label, style=style, disabled=disabled)
        item.callback = callback
        self.add_item(item)

    def select(self, placeholder, options, callback):
        item = discord.ui.Select(placeholder=placeholder, options=options)
        async def selected(interaction):
            await callback(interaction, item.values[0])
        item.callback = selected
        self.add_item(item)

    async def cancel(self, interaction):
        self.session.finished = True
        await interaction.response.edit_message(content='Cancelled. No draft changes saved.', view=None)
        self.stop()


async def show(interaction, text, view):
    if interaction.response.is_done():
        await interaction.edit_original_response(content=text, view=view, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.edit_message(content=text, view=view, allowed_mentions=discord.AllowedMentions.none())


class Channels(EditorView):
    def __init__(self, session, states):
        super().__init__(session)
        self.states = states
        channels = {s['channel_id']: session.guild.get_channel(s['channel_id']) for s in states}
        if channels:
            self.select('Select channel', [discord.SelectOption(label=c.name[:100], value=str(cid)) for cid, c in channels.items()], self.choose)
        self.button('Cancel', self.cancel)

    async def choose(self, interaction, channel_id):
        states = [s for s in self.states if s['channel_id'] == int(channel_id)]
        if len(states) == 1:
            await open_message(interaction, self.session, states[0]['key'])
        else:
            await show(interaction, 'Select the managed message to edit:', Messages(self.session, states))


async def channels(interaction, session):
    await interaction.response.defer()
    states = await service.available(interaction.guild)
    await show(interaction, 'Select a managed channel.' if states else 'No editable managed pins found. Run owner /server setup → Repair, then reopen.', Channels(session, states))


class Messages(EditorView):
    def __init__(self, session, states):
        super().__init__(session)
        self.select('Select managed message', [discord.SelectOption(label=s['label'], value=s['key']) for s in states], self.choose)
        self.button('Back', self.back)
        self.button('Cancel', self.cancel)

    async def choose(self, interaction, key):
        await open_message(interaction, self.session, key)

    async def back(self, interaction):
        await channels(interaction, self.session)


async def open_message(interaction, session, key):
    await interaction.response.defer()
    state = service.load(key)
    if not state:
        raise ServerMessageError('Managed message unavailable. Reopen the editor.')
    await service.inspect(interaction.guild, state)
    await show(interaction, heading(state), Main(session, copy.deepcopy(state)))


def heading(draft):
    return (f"**{draft['label']}** · <#{draft['channel_id']}>\n"
            f"Message ID: `{draft['message_id']}` · Version {draft['version']}\n"
            f"{'Customized' if draft['customized'] else 'Default'} content. Changes remain a draft until Preview → Save Changes.")


class Main(EditorView):
    def __init__(self, session, draft):
        super().__init__(session)
        self.draft = draft
        self.button('Edit Content', self.edit)
        self.button('Manage Buttons', self.buttons)
        self.button('Preview', self.preview)
        self.button('Save', self.preview, style=discord.ButtonStyle.primary)
        self.button('Reset to Default', self.reset)
        self.button('Back', self.back)
        self.button('Cancel', self.cancel)

    async def edit(self, interaction):
        await interaction.response.send_modal(ContentModal(self))

    async def buttons(self, interaction):
        await show(interaction, 'Buttons are ordered from top left to bottom right. Select a button to edit, remove, enable/disable or move it.', Buttons(self.session, self.draft))

    async def preview(self, interaction):
        draft = copy.deepcopy(self.draft)
        service.validate(interaction.guild, draft['key'], draft['content'], draft['buttons'])
        controls = Confirm(self.session, draft)
        await interaction.response.send_message(draft['content'], view=service.render(draft['buttons'], preview=True), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(heading(draft) + '\nPreview buttons are inert. Confirm to update this existing message.', view=controls, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def reset(self, interaction):
        # Reset is a separate confirmation, never an automatic setup operation.
        draft = copy.deepcopy(self.draft)
        draft['content'], draft['buttons'] = draft['default_content'], copy.deepcopy(draft['default_buttons'])
        await interaction.response.send_message(draft['content'], view=service.render(draft['buttons'], preview=True), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"Reset **{draft['label']}** in <#{draft['channel_id']}> to defaults? This overwrites custom text and buttons.", view=Confirm(self.session, self.draft, reset=True), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def back(self, interaction):
        await channels(interaction, self.session)


class ContentModal(discord.ui.Modal):
    def __init__(self, parent):
        super().__init__(title='Edit Markdown content', timeout=600)
        self.parent = parent
        self.body = discord.ui.TextInput(label='Discord Markdown (maximum 2000 characters)', style=discord.TextStyle.paragraph, default=parent.draft['content'], max_length=2000)
        self.add_item(self.body)

    async def on_submit(self, interaction):
        if not await self.parent.interaction_check(interaction):
            return
        try:
            service.validate(interaction.guild, self.parent.draft['key'], str(self.body), self.parent.draft['buttons'])
            self.parent.draft['content'] = str(self.body)
            await show(interaction, heading(self.parent.draft), Main(self.parent.session, self.parent.draft))
        except ServerMessageError as exc:
            await error(interaction, str(exc))

    async def on_error(self, interaction, exception):
        await error(interaction, 'Could not update the draft. Reopen the editor.')


class Confirm(EditorView):
    def __init__(self, session, draft, *, reset=False):
        super().__init__(session)
        self.draft, self.reset = copy.deepcopy(draft), reset
        self.button('Confirm Reset' if reset else 'Save Changes', self.save, style=discord.ButtonStyle.danger if reset else discord.ButtonStyle.success)
        self.button('Back', self.back)
        self.button('Cancel', self.cancel)

    async def save(self, interaction):
        await interaction.response.defer()
        try:
            updated = await service.save(interaction.guild, interaction.user, self.draft, reset=self.reset, confirmed=True)
        except ServerMessageError as exc:
            state = service.load(self.draft['key'])
            if state and state.get('pending') and state['version'] != self.draft['version']:
                self.session.finished = True
                await show(interaction, str(exc), None)
                self.stop()
                return
            raise
        self.session.finished = True
        await show(interaction, f"Saved **{updated['label']}** in <#{updated['channel_id']}>. Existing message ID `{updated['message_id']}` and pin preserved.", None)
        self.stop()

    async def back(self, interaction):
        await show(interaction, heading(self.draft), Main(self.session, self.draft))


class Buttons(EditorView):
    def __init__(self, session, draft, selected=None):
        super().__init__(session)
        self.draft, self.selected = draft, selected
        if draft['buttons']:
            options = [discord.SelectOption(label=f"{i+1}. {b['label']}"[:100], value=str(i),
                                            description=f"{b['type']} · {'enabled' if b['enabled'] else 'disabled'}", default=i == selected)
                       for i, b in enumerate(draft['buttons'])]
            self.select('Select button', options, self.choose)
        self.button('Add Link Button', self.add_link, disabled=len(draft['buttons']) >= 25)
        allowed = service.specs(session.guild)[draft['key']][2]
        if allowed:
            self.button('Add Action Button', self.add_action, disabled=len(draft['buttons']) >= 25)
        self.button('Edit Button', self.edit, disabled=selected is None)
        self.button('Remove Button', self.remove, disabled=selected is None)
        self.button('Enable / Disable', self.toggle, disabled=selected is None)
        self.button('Move Up', self.up, disabled=selected is None or selected == 0)
        self.button('Move Down', self.down, disabled=selected is None or selected == len(draft['buttons'])-1)
        self.button('Back', self.back)
        self.button('Cancel', self.cancel)

    async def refresh(self, interaction):
        await show(interaction, 'Manage buttons. Order is shown in the selection menu. Changes are still a draft.', Buttons(self.session, self.draft, self.selected))

    async def choose(self, interaction, selected):
        self.selected = int(selected)
        await self.refresh(interaction)

    async def add_link(self, interaction):
        await interaction.response.send_modal(ButtonModal(self, dict(label='Open link', emoji='', type='LINK', target='', enabled=True)))

    async def add_action(self, interaction):
        await show(interaction, 'Select an existing GamerHQ action. Actions are restricted to their canonical boards.', Actions(self.session, self.draft))

    async def edit(self, interaction):
        await interaction.response.send_modal(ButtonModal(self, self.draft['buttons'][self.selected], self.selected))

    async def remove(self, interaction):
        self.draft['buttons'].pop(self.selected)
        self.selected = None
        await self.refresh(interaction)

    async def toggle(self, interaction):
        b = self.draft['buttons'][self.selected]
        b['enabled'] = not b['enabled']
        await self.refresh(interaction)

    async def move(self, interaction, step):
        other = self.selected + step
        if not 0 <= other < len(self.draft['buttons']):
            raise ServerMessageError('Select a valid button position.')
        buttons = self.draft['buttons']
        buttons[other], buttons[self.selected] = buttons[self.selected], buttons[other]
        self.selected = other
        await self.refresh(interaction)

    async def up(self, interaction):
        await self.move(interaction, -1)

    async def down(self, interaction):
        await self.move(interaction, 1)

    async def back(self, interaction):
        await show(interaction, heading(self.draft), Main(self.session, self.draft))


class Actions(EditorView):
    def __init__(self, session, draft):
        super().__init__(session)
        self.draft = draft
        used = {b['target'] for b in draft['buttons'] if b['type'] == 'ACTION'}
        choices = [key for key in service.specs(session.guild)[draft['key']][2] if key not in used and key != 'HOUSEHOLD_CHECK_REQUEST']
        if choices:
            self.select('Choose action', [discord.SelectOption(label=service.ACTIONS[key][0], value=key) for key in choices], self.choose)
        self.button('Back', self.back)
        self.button('Cancel', self.cancel)

    async def choose(self, interaction, action):
        if action == 'HOUSEHOLD_CHECK_REQUEST':
            raise service.ServerMessageError('This legacy action is retired. Choose Electricity instead.')
        button = dict(label=service.ACTIONS[action][0], emoji='', type='ACTION', target=action, enabled=True)
        buttons = self.draft['buttons'] + [button]
        service.validate(interaction.guild, self.draft['key'], self.draft['content'], buttons)
        self.draft['buttons'] = buttons
        await self.back(interaction)

    async def back(self, interaction):
        await show(interaction, 'Select the action button to edit its label or emoji.', Buttons(self.session, self.draft))


class ButtonModal(discord.ui.Modal):
    def __init__(self, parent, config, index=None):
        super().__init__(title='Edit button', timeout=600)
        self.parent, self.config, self.index = parent, copy.deepcopy(config), index
        self.label_input = discord.ui.TextInput(label='Label', default=config['label'], max_length=80)
        self.emoji_input = discord.ui.TextInput(label='Emoji (optional)', default=config['emoji'], required=False, max_length=100)
        self.add_item(self.label_input)
        self.add_item(self.emoji_input)
        self.url_input = None
        if config['type'] == 'LINK':
            self.url_input = discord.ui.TextInput(label='Public HTTPS URL (no secrets)', default=config['target'], max_length=512)
            self.add_item(self.url_input)

    async def on_submit(self, interaction):
        if not await self.parent.interaction_check(interaction):
            return
        config = self.config
        config['label'], config['emoji'] = str(self.label_input), str(self.emoji_input)
        if self.url_input is not None:
            config['target'] = str(self.url_input)
        buttons = copy.deepcopy(self.parent.draft['buttons'])
        if self.index is None:
            buttons.append(config)
        else:
            buttons[self.index] = config
        try:
            service.validate(interaction.guild, self.parent.draft['key'], self.parent.draft['content'], buttons)
            self.parent.draft['buttons'] = buttons
            await self.parent.refresh(interaction)
        except ServerMessageError as exc:
            await error(interaction, str(exc))

    async def on_error(self, interaction, exception):
        await error(interaction, 'Could not update the button draft. Reopen the editor.')


async def open_editor(interaction):
    try:
        service.require_admin(interaction.guild, interaction.user)
        await interaction.response.defer(ephemeral=True)
        states = await service.available(interaction.guild)
        text = ('**Managed Pinned Messages**\nSelect a channel. Unknown/manual pins are excluded.' if states else
                'No editable managed pins found. Run owner /server setup → Repair, then reopen.')
        await interaction.followup.send(text, view=Channels(Session(interaction.guild, interaction.user.id), states), ephemeral=True)
    except (ServerMessageError, discord.HTTPException, sqlite3.Error, ValueError):
        await error(interaction, 'Owner/admin access and healthy managed pins are required. Check /server health and setup Repair.')
