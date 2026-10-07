"""Host-side Discord management for the external Awin Affiliate Skill.

This module knows only stable public Management contract IDs and response shapes.
It must never import the external Skill package implementation.
"""
from __future__ import annotations

import discord

from cogs.server import RoleAdminSession


SKILL_ID = "awin-affiliate"

DIAGNOSTICS_API = "awin-affiliate.diagnostics.v1"
SETUP_STATUS_API = "awin-affiliate.setup.status.v1"
SETUP_CONNECT_API = "awin-affiliate.setup.connect.v1"
SETUP_ACCOUNTS_API = "awin-affiliate.setup.accounts.v1"
SETUP_SELECT_PUBLISHER_API = "awin-affiliate.setup.select-publisher.v1"
SETUP_DISCONNECT_API = "awin-affiliate.setup.disconnect.v1"
ADVERTISERS_LIST_API = "awin-affiliate.advertisers.list.v1"
CREATIVES_LIST_API = "awin-affiliate.creatives.list.v1"
CREATIVE_GET_API = "awin-affiliate.creatives.get.v1"
CREATIVE_PREVIEW_API = "awin-affiliate.creatives.preview.v1"
CREATIVES_SET_ENABLED_API = "awin-affiliate.creatives.set-enabled.v1"
CREATIVES_SET_ADVERTISER_ENABLED_API = "awin-affiliate.creatives.set-advertiser-enabled.v1"
IMPORT_SAVED_HTML_API = "awin-affiliate.creatives.import-saved-html.v1"


async def _call(interaction, guild, contract_id, payload):
    runtime = getattr(interaction.client, "skill_runtime", None)
    if runtime is None:
        raise RuntimeError("Skill Runtime is unavailable.")
    return await runtime.call_management(
        guild_id=guild.id,
        skill_id=SKILL_ID,
        contract_id=contract_id,
        payload=dict(payload),
    )


def _escape(value, limit=100):
    return discord.utils.escape_markdown(str(value or ""))[:limit]


def _overview_text(diagnostics):
    setup = dict(diagnostics.get("setup") or {})
    creatives = dict(diagnostics.get("creatives") or {})
    campaigns = dict(diagnostics.get("campaigns") or {})
    history = dict(diagnostics.get("deliveryHistory") or {})
    publisher = setup.get("publisher") if isinstance(setup.get("publisher"), dict) else None
    publisher_label = (
        f"{_escape(publisher.get('name') or 'Publisher', 80)} · ID {_escape(publisher.get('id'), 40)}"
        if publisher
        else "Not selected"
    )
    states = creatives.get("byState") if isinstance(creatives.get("byState"), dict) else {}
    return (
        "# 🔗 Awin Affiliate\n"
        "Manage the connected publisher, Creative Library and Awin-owned posting workflows.\n\n"
        f"**Connection:** {'Connected' if setup.get('connected') else 'Not connected'}\n"
        f"**Publisher:** {publisher_label}\n\n"
        "**Creative Library**\n"
        f"• Total: {int(creatives.get('total', 0))}\n"
        f"• Enabled: {int(creatives.get('enabled', 0))} · Disabled: {int(creatives.get('disabled', 0))}\n"
        f"• ACTIVE: {int(states.get('ACTIVE', 0))} · NEW: {int(states.get('NEW', 0))} · "
        f"MISSING: {int(states.get('MISSING', 0))} · INACTIVE: {int(states.get('INACTIVE', 0))}\n\n"
        "**Campaigns**\n"
        f"• Active: {int(campaigns.get('active', 0))} · Paused: {int(campaigns.get('paused', 0))} · "
        f"Blocked: {int(campaigns.get('blocked', 0))}\n\n"
        "**Recent delivery history**\n"
        f"• Sent: {int(history.get('sent', 0))} · Blocked: {int(history.get('blocked', 0))} · "
        f"Failed: {int(history.get('failed', 0))}"
    )[:1950]


async def open_awin(interaction, guild, actor_id):
    try:
        response = await _call(interaction, guild, DIAGNOSTICS_API, {})
        diagnostics = dict(response.get("diagnostics") or {})
    except Exception:
        return await interaction.response.edit_message(
            content="# 🔗 Awin Affiliate\nConfiguration is currently unavailable.",
            view=AwinBackOnlyView(guild, actor_id),
        )
    await interaction.response.edit_message(
        content=_overview_text(diagnostics),
        view=AwinOverviewView(guild, actor_id, diagnostics),
    )


class AwinMenu(RoleAdminSession):
    session_name = "Awin management"

    def __init__(self, guild, actor_id, *, timeout=240):
        super().__init__(timeout=timeout)
        self.guild = guild
        self.admin_id = actor_id

    def action(self, label, callback, *, style=discord.ButtonStyle.secondary, row=None):
        button = discord.ui.Button(label=label, style=style, row=row)
        button.callback = callback
        self.add_item(button)


class AwinBackOnlyView(AwinMenu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        self.action("Back to Skill", self.back)

    async def back(self, interaction):
        from cogs.server_management import open_skills
        await open_skills(interaction, self.guild, self.admin_id)


class AwinOverviewView(AwinMenu):
    def __init__(self, guild, actor_id, diagnostics):
        super().__init__(guild, actor_id)
        self.connected = bool((diagnostics.get("setup") or {}).get("connected"))
        if not self.connected:
            self.action("Connect Awin", self.connect, style=discord.ButtonStyle.primary)
        else:
            self.action("Publisher", self.publisher)
            self.action("Advertisers", self.advertisers)
            self.action("Creative Library", self.creatives)
            self.action("Disconnect", self.disconnect)
        self.action("Refresh", self.refresh)
        self.action("Back to Skill", self.back)

    async def connect(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can connect Awin credentials.",
                ephemeral=True,
            )
        await interaction.response.send_modal(AwinConnectModal(self.guild, self.admin_id))

    async def publisher(self, interaction):
        await open_publishers(interaction, self.guild, self.admin_id)

    async def advertisers(self, interaction):
        await open_advertisers(interaction, self.guild, self.admin_id)

    async def creatives(self, interaction):
        await open_creatives(interaction, self.guild, self.admin_id)

    async def disconnect(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can disconnect Awin.",
                ephemeral=True,
            )
        await interaction.response.edit_message(
            content=(
                "# Disconnect Awin?\n"
                "The encrypted Awin access token and publisher selection will be removed. "
                "Creative Library and campaign records are preserved.\n\n"
                "Nothing changes until you confirm."
            ),
            view=AwinDisconnectConfirmView(self.guild, self.admin_id),
        )

    async def refresh(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)

    async def back(self, interaction):
        from cogs.server_management import open_skills
        await open_skills(interaction, self.guild, self.admin_id)


class AwinConnectModal(discord.ui.Modal, title="Connect Awin"):
    access_token = discord.ui.TextInput(
        label="Awin access token",
        placeholder="Paste the token from your Awin account",
        required=True,
        max_length=4096,
    )

    def __init__(self, guild, actor_id):
        super().__init__(timeout=300)
        self.guild = guild
        self.actor_id = actor_id

    async def on_submit(self, interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message(
                "This setup form belongs to another admin session.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        try:
            response = await _call(
                interaction,
                self.guild,
                SETUP_CONNECT_API,
                {"accessToken": str(self.access_token.value)},
            )
            setup = dict(response.get("setup") or {})
        except Exception:
            return await interaction.edit_original_response(
                content=(
                    "❌ Awin connection could not be verified. "
                    "Check the token and account permissions, then try again."
                ),
                view=AwinBackOnlyView(self.guild, self.actor_id),
            )

        if setup.get("selectionRequired"):
            try:
                accounts_response = await _call(
                    interaction,
                    self.guild,
                    SETUP_ACCOUNTS_API,
                    {},
                )
                accounts = tuple(accounts_response.get("accounts") or ())
            except Exception:
                accounts = ()
            if accounts:
                return await interaction.edit_original_response(
                    content="# 🔗 Choose Awin Publisher\nSelect the publisher account this server should use.",
                    view=AwinPublisherSelectView(self.guild, self.actor_id, accounts),
                )

        diagnostics = await _call(interaction, self.guild, DIAGNOSTICS_API, {})
        await interaction.edit_original_response(
            content="✅ Awin connected.\n\n" + _overview_text(dict(diagnostics.get("diagnostics") or {})),
            view=AwinOverviewView(
                self.guild,
                self.actor_id,
                dict(diagnostics.get("diagnostics") or {}),
            ),
        )


class AwinDisconnectConfirmView(AwinMenu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        self.action("Confirm Disconnect", self.confirm, style=discord.ButtonStyle.danger)
        self.action("Cancel", self.cancel)

    async def confirm(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can disconnect Awin.",
                ephemeral=True,
            )
        try:
            await _call(interaction, self.guild, SETUP_DISCONNECT_API, {})
        except Exception:
            return await interaction.response.send_message(
                "Awin disconnect failed. Nothing was changed by the host.",
                ephemeral=True,
            )
        await open_awin(interaction, self.guild, self.admin_id)

    async def cancel(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)


async def open_publishers(interaction, guild, actor_id):
    try:
        response = await _call(interaction, guild, SETUP_ACCOUNTS_API, {})
        accounts = tuple(response.get("accounts") or ())
    except Exception:
        return await interaction.response.edit_message(
            content="# 🔗 Awin Publishers\nPublisher accounts are currently unavailable.",
            view=AwinBackToOverviewView(guild, actor_id),
        )
    if not accounts:
        return await interaction.response.edit_message(
            content="# 🔗 Awin Publishers\nNo publisher account is available to the connected Awin user.",
            view=AwinBackToOverviewView(guild, actor_id),
        )
    await interaction.response.edit_message(
        content="# 🔗 Choose Awin Publisher\nSelect the publisher account this server should use.",
        view=AwinPublisherSelectView(guild, actor_id, accounts),
    )


class AwinPublisherSelect(discord.ui.Select):
    def __init__(self, accounts):
        options = []
        for account in accounts[:25]:
            account_id = str(account.get("id", "")).strip()
            if not account_id:
                continue
            options.append(
                discord.SelectOption(
                    label=str(account.get("name") or f"Publisher {account_id}")[:100],
                    value=account_id,
                    description=(f"Publisher ID {account_id}")[:100],
                )
            )
        super().__init__(
            placeholder="Choose publisher",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        try:
            await _call(
                interaction,
                self.view.guild,
                SETUP_SELECT_PUBLISHER_API,
                {"publisherId": self.values[0]},
            )
        except Exception:
            return await interaction.response.send_message(
                "That publisher could not be selected. Reopen the list and try again.",
                ephemeral=True,
            )
        await open_awin(interaction, self.view.guild, self.view.admin_id)


class AwinPublisherSelectView(AwinMenu):
    def __init__(self, guild, actor_id, accounts):
        super().__init__(guild, actor_id)
        self.add_item(AwinPublisherSelect(accounts))
        self.action("Back", self.back)

    async def back(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)


class AwinBackToOverviewView(AwinMenu):
    def __init__(self, guild, actor_id):
        super().__init__(guild, actor_id)
        self.action("Back", self.back)

    async def back(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)


async def open_advertisers(interaction, guild, actor_id, relationship="joined"):
    try:
        response = await _call(
            interaction,
            guild,
            ADVERTISERS_LIST_API,
            {"relationship": relationship},
        )
        advertisers = tuple(response.get("advertisers") or ())
    except Exception:
        return await interaction.response.edit_message(
            content="# 🏷️ Awin Advertisers\nAdvertiser discovery is currently unavailable.",
            view=AwinBackToOverviewView(guild, actor_id),
        )

    lines = [
        "# 🏷️ Awin Advertisers",
        f"Relationship filter: **{_escape(relationship.title(), 40)}**",
        "",
    ]
    if advertisers:
        for advertiser in advertisers[:15]:
            name = _escape(advertiser.get("name") or "Advertiser", 80)
            advertiser_id = _escape(advertiser.get("id"), 30)
            status = _escape(advertiser.get("status") or advertiser.get("relationship") or "unknown", 30)
            lines.append(f"• **{name}** · ID {advertiser_id} · {status}")
        if len(advertisers) > 15:
            lines.append(f"• + {len(advertisers) - 15} more")
    else:
        lines.append("No advertisers matched this relationship filter.")

    await interaction.response.edit_message(
        content="\n".join(lines)[:1950],
        view=AwinAdvertisersView(guild, actor_id, relationship),
    )


class AwinAdvertiserRelationshipSelect(discord.ui.Select):
    def __init__(self, selected):
        values = ("joined", "pending", "suspended", "rejected", "notjoined")
        super().__init__(
            placeholder="Relationship filter",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=value.title(),
                    value=value,
                    default=value == selected,
                )
                for value in values
            ],
        )

    async def callback(self, interaction):
        await open_advertisers(
            interaction,
            self.view.guild,
            self.view.admin_id,
            relationship=self.values[0],
        )


class AwinAdvertisersView(AwinMenu):
    def __init__(self, guild, actor_id, relationship):
        super().__init__(guild, actor_id)
        self.add_item(AwinAdvertiserRelationshipSelect(relationship))
        self.action("Creative Library", self.creatives)
        self.action("Back", self.back)

    async def creatives(self, interaction):
        await open_creatives(interaction, self.guild, self.admin_id)

    async def back(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)


async def open_creatives(interaction, guild, actor_id, *, offset=0, advertiser_id=None):
    payload = {
        "offset": max(0, int(offset)),
        "limit": 10,
        "activeOnly": False,
        "enabledOnly": False,
    }
    if advertiser_id:
        payload["advertiserId"] = str(advertiser_id)
    try:
        response = await _call(interaction, guild, CREATIVES_LIST_API, payload)
        page = dict(response.get("creatives") or {})
    except Exception:
        return await interaction.response.edit_message(
            content="# 🖼️ Awin Creative Library\nThe Creative Library is currently unavailable.",
            view=AwinBackToOverviewView(guild, actor_id),
        )

    items = tuple(page.get("items") or ())
    total = int(page.get("total", 0))
    page_offset = int(page.get("offset", 0))
    lines = [
        "# 🖼️ Awin Creative Library",
        f"Showing {page_offset + 1 if total else 0}–{min(page_offset + len(items), total)} of {total}",
        "",
    ]
    for item in items:
        label = _escape(item.get("advertiserName") or item.get("title") or item.get("id"), 70)
        dimensions = _escape(item.get("dimensions") or "size unknown", 30)
        state = _escape(item.get("state") or "unknown", 20)
        enabled = "Enabled" if item.get("userEnabled") else "Disabled"
        lines.append(f"• **{label}** · {dimensions} · {state} · {enabled}")
    if not items:
        lines.append("No Creatives matched the current view.")

    await interaction.response.edit_message(
        content="\n".join(lines)[:1950],
        view=AwinCreativeLibraryView(
            guild,
            actor_id,
            items=items,
            offset=page_offset,
            has_previous=bool(page.get("hasPrevious")),
            has_next=bool(page.get("hasNext")),
            advertiser_id=advertiser_id,
        ),
    )


class AwinCreativeSelect(discord.ui.Select):
    def __init__(self, items):
        options = []
        for item in items[:25]:
            creative_id = str(item.get("id", "")).strip()
            if not creative_id:
                continue
            advertiser = str(item.get("advertiserName") or "Awin Creative")
            state = str(item.get("state") or "unknown")
            options.append(
                discord.SelectOption(
                    label=advertiser[:100],
                    value=creative_id,
                    description=(f"{state} · {'Enabled' if item.get('userEnabled') else 'Disabled'} · {item.get('dimensions') or 'size unknown'}")[:100],
                )
            )
        super().__init__(
            placeholder="Choose Creative",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        await open_creative_detail(
            interaction,
            self.view.guild,
            self.view.admin_id,
            creative_id=self.values[0],
            offset=self.view.offset,
            advertiser_id=self.view.advertiser_id,
        )


class AwinCreativeLibraryView(AwinMenu):
    def __init__(self, guild, actor_id, *, items, offset, has_previous, has_next, advertiser_id):
        super().__init__(guild, actor_id)
        self.offset = offset
        self.advertiser_id = advertiser_id
        if items:
            self.add_item(AwinCreativeSelect(items))
        if has_previous:
            self.action("Previous", self.previous)
        if has_next:
            self.action("Next", self.next)
        self.action("Back", self.back)

    async def previous(self, interaction):
        await open_creatives(
            interaction,
            self.guild,
            self.admin_id,
            offset=max(0, self.offset - 10),
            advertiser_id=self.advertiser_id,
        )

    async def next(self, interaction):
        await open_creatives(
            interaction,
            self.guild,
            self.admin_id,
            offset=self.offset + 10,
            advertiser_id=self.advertiser_id,
        )

    async def back(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)


async def open_creative_detail(interaction, guild, actor_id, *, creative_id, offset=0, advertiser_id=None):
    try:
        response = await _call(
            interaction,
            guild,
            CREATIVE_GET_API,
            {"creativeId": creative_id},
        )
        creative = dict(response.get("creative") or {})
    except Exception:
        return await interaction.response.send_message(
            "Creative details are currently unavailable.",
            ephemeral=True,
        )

    lines = [
        "# 🖼️ Awin Creative",
        f"**Advertiser:** {_escape(creative.get('advertiserName') or creative.get('advertiserId'), 90)}",
        f"**State:** {_escape(creative.get('state'), 30)}",
        f"**Status:** {'Enabled' if creative.get('userEnabled') else 'Disabled'}",
        f"**Dimensions:** {_escape(creative.get('dimensions') or 'Unknown', 30)}",
        f"**Source:** {_escape(creative.get('source') or 'unknown', 60)}",
    ]
    await interaction.response.edit_message(
        content="\n".join(lines)[:1950],
        view=AwinCreativeDetailView(
            guild,
            actor_id,
            creative,
            offset=offset,
            advertiser_id=advertiser_id,
        ),
    )


class AwinCreativeDetailView(AwinMenu):
    def __init__(self, guild, actor_id, creative, *, offset, advertiser_id):
        super().__init__(guild, actor_id)
        self.creative = dict(creative)
        self.offset = offset
        self.advertiser_id = advertiser_id
        self.action("Preview", self.preview, style=discord.ButtonStyle.primary)
        self.action(
            "Disable" if self.creative.get("userEnabled") else "Enable",
            self.toggle,
        )
        tracking_url = str(self.creative.get("trackingUrl") or "").strip()
        if tracking_url.startswith("https://"):
            self.add_item(discord.ui.Button(label="View offer", url=tracking_url))
        self.action("Back", self.back)

    async def preview(self, interaction):
        try:
            response = await _call(
                interaction,
                self.guild,
                CREATIVE_PREVIEW_API,
                {"creativeId": self.creative.get("id")},
            )
            preview = dict(response.get("discordPreview") or {})
        except Exception:
            return await interaction.response.send_message(
                "Creative preview is currently unavailable.",
                ephemeral=True,
            )
        embed = discord.Embed(title=str(preview.get("title") or "Awin Creative")[:256])
        image_url = str(preview.get("imageUrl") or "").strip()
        if image_url.startswith("https://"):
            embed.set_image(url=image_url)
        disclosure = str(preview.get("disclosure") or "Werbung · Affiliate-Link")
        support = str(preview.get("supportingText") or "")
        await interaction.response.send_message(
            f"**{disclosure}**\n{support}"[:2000],
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def toggle(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can change Creative availability.",
                ephemeral=True,
            )
        enabled = not bool(self.creative.get("userEnabled"))
        try:
            await _call(
                interaction,
                self.guild,
                CREATIVES_SET_ENABLED_API,
                {"creativeIds": [self.creative.get("id")], "enabled": enabled},
            )
        except Exception:
            return await interaction.response.send_message(
                "Creative state could not be changed.",
                ephemeral=True,
            )
        await open_creative_detail(
            interaction,
            self.guild,
            self.admin_id,
            creative_id=self.creative.get("id"),
            offset=self.offset,
            advertiser_id=self.advertiser_id,
        )

    async def back(self, interaction):
        await open_creatives(
            interaction,
            self.guild,
            self.admin_id,
            offset=self.offset,
            advertiser_id=self.advertiser_id,
        )
