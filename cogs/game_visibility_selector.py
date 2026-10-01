"""Admin-only multi-game visibility drafts; member selection remains role-only."""
from __future__ import annotations

import copy
import time

import discord
from database import db
from services.authorization_service import authorized
from services.game_catalog_service import member_counts, sections
from services.response_service import SafeView, check_admin
from services import game_visibility_service as visibility
from cogs.game_selector import LETTER_RANGES, POPULAR

PAGE_SIZE = 15  # Three game rows, navigation row, then draft actions.
DETAIL_PAGE_SIZE = 6


def text(value, limit=120):
    escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))
    return escaped.encode('utf-16-le')[:limit * 2].decode('utf-16-le', errors='ignore')


async def open_selector(interaction):
    if not await check_admin(interaction):
        return
    view = AdminGameSelection(interaction.guild, interaction.user.id, db.get_all_games(active_only=True))
    await interaction.response.send_message(view.content(), view=view, ephemeral=True,
                                           allowed_mentions=discord.AllowedMentions.none())


class AdminGameSelection(SafeView):
    def __init__(self, guild, actor_id, games):
        super().__init__(timeout=600)
        self.guild, self.actor_id = guild, actor_id
        self.games = {g['id']: copy.deepcopy(g) for g in games if g['active']}
        self.visible = {gid for gid, g in self.games.items() if g['selectable']}
        self.pending = {}
        ranked = sections(list(self.games.values()), member_counts(guild, list(self.games.values())))
        self.groups = {POPULAR: ranked[POPULAR]} if POPULAR in ranked else {}
        for label in (*LETTER_RANGES, '#'):
            rows = [g for letter, group in ranked.items() if letter != POPULAR
                    and (letter == '#' if label == '#' else label[0] <= letter <= label[-1]) for g in group]
            if rows:
                self.groups[label] = sorted(rows, key=lambda g: (g['name'].casefold(), g['id']))
        self.group = next(iter(self.groups), None)
        self.page, self.browsing = 0, False
        self.phase, self.expires = 'select', time.monotonic() + 600
        self.rebuild()

    async def allowed(self, interaction, *, phase=None):
        ok = (interaction.guild and interaction.guild.id == self.guild.id
              and interaction.user.id == self.actor_id and authorized(interaction.guild, interaction.user)
              and time.monotonic() <= self.expires and self.phase != 'closed'
              and (phase is None or self.phase == phase))
        if not ok:
            await interaction.response.send_message('This admin selection expired, is busy, or belongs to another administrator. Reopen Manage Visible Games.', ephemeral=True)
        return bool(ok)

    async def interaction_check(self, interaction):
        return await self.allowed(interaction, phase='select')

    def pages(self):
        return max(1, (len(self.groups.get(self.group, [])) + PAGE_SIZE - 1) // PAGE_SIZE)

    def page_games(self):
        if self.browsing:
            return []
        return self.groups.get(self.group, [])[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]

    def is_visible(self, gid):
        return self.pending.get(gid, gid in self.visible)

    def content(self):
        shows = sum(self.pending.values())
        heading = 'Browse A–Z' if self.browsing else f'{"0–9 / Other" if self.group == "#" else self.group or "No active games"} · {self.page + 1}/{self.pages()}'
        return ('# 🎮 Manage Visible Games\n'
                'Admin settings, not your personal game roles. Choose several games, then review and confirm.\n'
                '✅ Visible after saving · ➕ Hidden after saving · • Unsaved choice\n'
                'Green games may still need a chat. **Show page** also sets up missing chats.\n'
                'Hidden chats keep their messages; staff retains access.\n\n'
                f'**Pending: {shows} show / set up · {len(self.pending) - shows} hide**\n'
                f'## {heading}\nNothing changes until you confirm the review.')

    def button(self, label, callback, row, *, disabled=False, style=discord.ButtonStyle.secondary, custom_id=None):
        options = dict(label=label, row=row, disabled=disabled, style=style)
        if custom_id:
            options['custom_id'] = custom_id
        item = discord.ui.Button(**options)
        item.callback = callback
        self.add_item(item)

    def rebuild(self):
        self.clear_items()
        if self.browsing:
            for index, group in enumerate(g for g in self.groups if g != POPULAR):
                async def choose(i, group=group):
                    await self.navigate(i, group=group)
                self.button('0–9 / Other' if group == '#' else group, choose, index // 5)
        else:
            for index, game in enumerate(self.page_games()):
                gid, selected = game['id'], self.is_visible(game['id'])
                prefix = '✅ ' if selected else '➕ '
                label = (prefix + game['name'] + (' •' if gid in self.pending else '')).encode('utf-16-le')[:160].decode('utf-16-le', errors='ignore')
                async def toggle(i, gid=gid):
                    await self.toggle(i, gid)
                self.button(label, toggle, index // 5,
                            style=discord.ButtonStyle.success if selected else discord.ButtonStyle.secondary,
                            custom_id=f'gamerhq:admin_visibility:game:{gid}')
        async def prev(i): await self.navigate(i, delta=-1)
        async def next_page(i): await self.navigate(i, delta=1)
        async def popular(i): await self.navigate(i, group=POPULAR)
        async def browse(i): await self.navigate(i, browse=True)
        self.button('◀ Previous', prev, 3, disabled=self.browsing or self.page == 0)
        self.button('Next ▶', next_page, 3, disabled=self.browsing or self.page + 1 >= self.pages())
        self.button('Popular', popular, 3, disabled=POPULAR not in self.groups)
        self.button('Browse A–Z', browse, 3, disabled=not self.groups)
        async def show(i): await self.set_page(i, True)
        async def hide(i): await self.set_page(i, False)
        self.button('Show page', show, 4, disabled=not self.page_games())
        self.button('Hide page', hide, 4, disabled=not self.page_games())
        self.button('Review changes', self.review, 4, disabled=not self.pending, style=discord.ButtonStyle.primary)
        self.button('Clear choices', self.clear, 4, disabled=not self.pending)
        self.button('Cancel', self.cancel, 4)

    async def repaint(self, interaction):
        self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self,
                                                allowed_mentions=discord.AllowedMentions.none())

    async def navigate(self, interaction, *, group=None, delta=0, browse=False):
        if not await self.allowed(interaction, phase='select'):
            return
        if group is not None:
            if group not in self.groups:
                return await interaction.response.send_message('This range is unavailable.', ephemeral=True)
            self.group, self.page = group, 0
        self.browsing = browse
        self.page = min(max(0, self.page + delta), self.pages() - 1)
        await self.repaint(interaction)

    async def toggle(self, interaction, gid):
        if not await self.allowed(interaction, phase='select'):
            return
        if gid not in self.games:
            return await interaction.response.send_message('This game is no longer in this selection.', ephemeral=True)
        target = not self.is_visible(gid)
        if target == (gid in self.visible):
            self.pending.pop(gid, None)
        elif gid in self.pending or len(self.pending) < visibility.MAX_BATCH:
            self.pending[gid] = target
        else:
            return await interaction.response.send_message('Review these 50 choices before adding more.', ephemeral=True)
        await self.repaint(interaction)

    async def set_page(self, interaction, visible):
        if not await self.allowed(interaction, phase='select'):
            return
        ids = {g['id'] for g in self.page_games()}
        if len(set(self.pending) | ids) > visibility.MAX_BATCH:
            return await interaction.response.send_message('This page would exceed 50 choices. Apply the current review first.', ephemeral=True)
        # Explicit page actions include unchanged visible games needing channel setup.
        self.pending.update({gid: visible for gid in sorted(ids)})
        await self.repaint(interaction)

    async def clear(self, interaction):
        if not await self.allowed(interaction, phase='select'):
            return
        self.pending.clear()
        await self.repaint(interaction)

    async def cancel(self, interaction):
        if not await self.allowed(interaction, phase='select'):
            return
        self.phase = 'closed'
        self.stop()
        await interaction.response.edit_message(content='Cancelled. No game or channel was changed.', view=None)

    async def review(self, interaction):
        if not await self.allowed(interaction, phase='select'):
            return
        if not self.pending:
            return await interaction.response.send_message('Choose at least one game first.', ephemeral=True)
        self.phase = 'loading'
        await interaction.response.defer()
        try:
            draft = await visibility.preview_batch(self.guild, interaction.user, dict(self.pending), expected_games=self.games)
            if not authorized(interaction.guild, interaction.user):
                raise ValueError('Administrator access changed. Reopen the selector.')
            self.phase = 'review'
            panel = BatchReview(self, draft)
            await interaction.edit_original_response(content=panel.content(), view=panel,
                                                     allowed_mentions=discord.AllowedMentions.none())
        except (ValueError, discord.HTTPException) as exc:
            self.phase = 'select'
            self.rebuild()
            reason = str(exc) if isinstance(exc, ValueError) else 'Discord could not load the review. Try again later.'
            await interaction.edit_original_response(content=text(reason, 600), view=self,
                                                     allowed_mentions=discord.AllowedMentions.none())


class BatchReview(SafeView):
    def __init__(self, session, draft):
        super().__init__(timeout=240)
        self.session, self.draft = session, draft
        self.page, self.valid, self.results = 0, True, None
        self.rebuild()

    async def interaction_check(self, interaction):
        if not self.valid:
            await interaction.response.send_message('This review was replaced. Reopen Manage Visible Games.', ephemeral=True)
            return False
        return await self.session.allowed(interaction, phase='review' if self.results is None else 'result')

    def lines(self):
        if self.results is not None:
            return ([f'{"✅" if r["ok"] else "⚠️"} **{text(r["name"], 60)}** — {text(r["reason"], 150)}' for r in self.results]
                    + [f'⚠️ **{text(r["name"], 60)}** — Skipped: {text(r["reason"], 140)}' for r in self.draft['blocked']])
        rows = []
        for plan in self.draft['plans']:
            action = ('Show / set up existing chat' if plan['channel_id'] else 'Show / create game chat') if plan['visible'] else 'Hide; keep messages'
            rows.append(f'**{text(plan["game"]["name"], 60)}** — {action}')
        rows += [f'⚠️ **{text(r["name"], 60)}** — {text(r["reason"], 150)}' for r in self.draft['blocked']]
        return rows

    def content(self):
        rows = self.lines()
        pages = max(1, (len(rows) + DETAIL_PAGE_SIZE - 1) // DETAIL_PAGE_SIZE)
        header = '# 🎮 Review game visibility' if self.results is None else '# 🎮 Game visibility results'
        if self.results is None:
            shown = sum(p['visible'] for p in self.draft['plans'])
            summary = f'**{shown} show / set up · {len(self.draft["plans"]) - shown} hide · {len(self.draft["blocked"])} blocked**'
            parent = next((p for p in self.draft['plans'] if p['visible']), None)
            destination = ('\nCreate the shared 🎮 Games category.' if parent and parent['category_action'] == 'create'
                           else '\nReuse the reviewed Games category.' if parent else '')
            warning = '\n⚠️ This batch exceeds the configured channel soft limit; confirm the override explicitly.' if self.draft['override'] else ''
            footer = f'Confirm applies ALL {len(self.draft["plans"])} ready games across these pages. Blocked games are skipped. Nothing is deleted.'
        else:
            good = sum(r['ok'] for r in self.results)
            summary = f'**{good} completed · {len(self.results) - good} failed / not applied · {len(self.draft["blocked"])} blocked in preview**'
            destination = warning = ''
            footer = 'Some setup steps may have completed for failed games. Reopen and review current state before retrying. No automatic retries.' if good != len(self.results) else 'Game roles and message history are preserved. Reopen Manage Visible Games for a fresh selection.'
        return (f'{header}\n{summary}{destination}{warning}\n\n' + '\n'.join(rows[self.page * DETAIL_PAGE_SIZE:(self.page + 1) * DETAIL_PAGE_SIZE])
                + f'\n\nPage {self.page + 1}/{pages}\n{footer}')

    def add(self, label, callback, *, disabled=False, style=discord.ButtonStyle.secondary):
        item = discord.ui.Button(label=label, disabled=disabled, style=style)
        item.callback = callback
        self.add_item(item)

    def rebuild(self):
        self.clear_items()
        async def previous(i): await self.turn(i, -1)
        async def next_page(i): await self.turn(i, 1)
        self.add('◀ Previous', previous, disabled=self.page == 0)
        self.add('Next ▶', next_page, disabled=(self.page + 1) * DETAIL_PAGE_SIZE >= len(self.lines()))
        if self.results is None:
            self.add('Confirm & exceed soft limit' if self.draft['override'] else 'Confirm changes', self.confirm,
                     disabled=not self.draft['plans'], style=discord.ButtonStyle.danger if self.draft['override'] else discord.ButtonStyle.success)
            self.add('Back to selection', self.back)
        self.add('Close', self.close)

    async def turn(self, interaction, delta):
        if not await self.interaction_check(interaction):
            return
        self.page = min(max(0, self.page + delta), max(0, (len(self.lines()) - 1) // DETAIL_PAGE_SIZE))
        self.rebuild()
        await interaction.response.edit_message(content=self.content(), view=self, allowed_mentions=discord.AllowedMentions.none())

    async def back(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.valid = False
        self.stop()
        self.session.phase = 'select'
        await self.session.repaint(interaction)

    async def close(self, interaction):
        if not await self.interaction_check(interaction):
            return
        self.valid = False
        self.session.phase = 'closed'
        self.stop()
        self.session.stop()
        await interaction.response.edit_message(content='Closed. No changes applied.' if self.results is None else 'Closed. Saved results remain in game management.', view=None)

    async def confirm(self, interaction):
        if self.results is not None:
            return await interaction.response.send_message('This batch has already finished. Reopen Manage Visible Games.', ephemeral=True)
        if not await self.interaction_check(interaction):
            return
        self.session.phase = 'applying'
        await interaction.response.defer()
        await interaction.edit_original_response(content='Applying the reviewed game changes…', view=None)
        last = 0.0
        async def progress(done, total):
            nonlocal last
            if time.monotonic() - last >= 3 or done == total:
                last = time.monotonic()
                await interaction.edit_original_response(content=f'Applying game changes: {done}/{total}.', view=None)
        try:
            self.results = await visibility.apply_batch(self.session.guild, interaction.user, self.draft,
                                                         override=self.draft['override'], progress=progress)
        except ValueError as exc:
            self.valid = False
            self.session.phase = 'closed'
            return await interaction.edit_original_response(content=text(exc, 600), view=None)
        self.session.phase = 'result'
        self.page = 0
        self.rebuild()
        await interaction.edit_original_response(content=self.content(), view=self, allowed_mentions=discord.AllowedMentions.none())
