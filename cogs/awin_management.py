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
POST_PREVIEW_API = "awin-affiliate.post.preview.v1"
POST_SEND_API = "awin-affiliate.post.send.v1"
CAMPAIGN_LIST_API = "awin-affiliate.campaigns.list.v1"
CAMPAIGN_GET_API = "awin-affiliate.campaigns.get.v1"
CAMPAIGN_CREATE_API = "awin-affiliate.campaigns.create.v1"
CAMPAIGN_UPDATE_API = "awin-affiliate.campaigns.update.v1"
CAMPAIGN_SET_ACTIVE_API = "awin-affiliate.campaigns.set-active.v1"
CAMPAIGN_DELETE_API = "awin-affiliate.campaigns.delete.v1"
CAMPAIGN_RUN_NOW_API = "awin-affiliate.campaigns.run-now.v1"
CAMPAIGN_PREVIEW_NEXT_API = "awin-affiliate.campaigns.preview-next.v1"
CAMPAIGN_HISTORY_API = "awin-affiliate.campaigns.history.v1"


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
            self.action("Post Now", self.post_now, style=discord.ButtonStyle.primary)
            self.action("Campaigns", self.campaigns)
            self.action("Import Saved HTML", self.import_saved_html)
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

    async def post_now(self, interaction):
        await open_post_now(interaction, self.guild, self.admin_id)

    async def campaigns(self, interaction):
        await open_campaigns(interaction, self.guild, self.admin_id)

    async def import_saved_html(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can import Awin Creative HTML.",
                ephemeral=True,
            )
        await interaction.response.send_modal(AwinSavedHtmlModal(self.guild, self.admin_id))

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



class AwinSavedHtmlModal(discord.ui.Modal, title="Import Saved Awin HTML"):
    html = discord.ui.TextInput(
        label="Saved My Creative HTML",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=4000,
        placeholder="Paste saved/rendered My Creative HTML (Discord modal limit: 4000 characters)",
    )
    complete_advertiser_id = discord.ui.TextInput(
        label="Complete advertiser ID (optional)",
        required=False,
        max_length=64,
        placeholder="Only set after confirming this advertiser export is complete",
    )

    def __init__(self, guild, actor_id):
        super().__init__(timeout=300)
        self.guild = guild
        self.actor_id = actor_id

    async def on_submit(self, interaction):
        if interaction.user.id != self.actor_id:
            return await interaction.response.send_message(
                "This import form belongs to another admin session.",
                ephemeral=True,
            )
        payload = {"html": str(self.html.value)}
        complete = str(self.complete_advertiser_id.value or "").strip()
        if complete:
            payload["completeAdvertiserId"] = complete
        await interaction.response.defer(ephemeral=True)
        try:
            response = await _call(interaction, self.guild, IMPORT_SAVED_HTML_API, payload)
            result = dict(response.get("import") or {})
        except Exception:
            return await interaction.edit_original_response(
                content=(
                    "❌ Saved Awin HTML could not be imported. Nothing was inferred as complete. "
                    "Review the saved HTML and try again."
                ),
                view=AwinBackToOverviewView(self.guild, self.actor_id),
            )
        text = (
            "✅ Saved Awin HTML imported.\n"
            f"Groups: {int(result.get('groups', 0))} · Found: {int(result.get('found', 0))} · "
            f"New: {int(result.get('new', 0))} · Updated: {int(result.get('updated', 0))} · "
            f"Missing: {int(result.get('missing', 0))} · Restored: {int(result.get('restored', 0))}"
        )
        if complete:
            text += f"\nAuthoritative missing detection was explicitly enabled only for advertiser ID {_escape(complete, 64)}."
        await interaction.edit_original_response(
            content=text[:1950],
            view=AwinBackToOverviewView(self.guild, self.actor_id),
        )


async def _joined_advertisers(interaction, guild):
    response = await _call(
        interaction,
        guild,
        ADVERTISERS_LIST_API,
        {"relationship": "joined"},
    )
    return tuple(response.get("advertisers") or ())


async def open_post_now(interaction, guild, actor_id):
    try:
        advertisers = await _joined_advertisers(interaction, guild)
    except Exception:
        return await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nJoined advertisers are currently unavailable.",
            view=AwinBackToOverviewView(guild, actor_id),
        )
    if not advertisers:
        return await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nNo joined advertisers are available.",
            view=AwinBackToOverviewView(guild, actor_id),
        )
    await interaction.response.edit_message(
        content="# 📣 Awin Post Now\nChoose the advertiser for this post.",
        view=AwinPostAdvertiserView(guild, actor_id, advertisers),
    )


class AwinPostAdvertiserSelect(discord.ui.Select):
    def __init__(self, advertisers):
        options = []
        for item in advertisers[:25]:
            advertiser_id = str(item.get("id", "")).strip()
            if not advertiser_id:
                continue
            options.append(
                discord.SelectOption(
                    label=str(item.get("name") or f"Advertiser {advertiser_id}")[:100],
                    value=advertiser_id,
                    description=(f"ID {advertiser_id}")[:100],
                )
            )
        super().__init__(
            placeholder="Choose advertiser",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nChoose how the Creative should be selected.",
            view=AwinPostModeView(
                self.view.guild,
                self.view.admin_id,
                advertiser_id=self.values[0],
            ),
        )


class AwinPostAdvertiserView(AwinMenu):
    def __init__(self, guild, actor_id, advertisers):
        super().__init__(guild, actor_id)
        self.add_item(AwinPostAdvertiserSelect(advertisers))
        self.action("Back", self.back)

    async def back(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)


class AwinPostModeView(AwinMenu):
    def __init__(self, guild, actor_id, *, advertiser_id):
        super().__init__(guild, actor_id)
        self.advertiser_id = advertiser_id
        self.action("Specific", self.specific)
        self.action("Random", self.random)
        self.action("Next", self.next)
        self.action("Back", self.back)

    async def specific(self, interaction):
        try:
            response = await _call(
                interaction,
                self.guild,
                CREATIVES_LIST_API,
                {
                    "advertiserId": self.advertiser_id,
                    "enabledOnly": True,
                    "activeOnly": True,
                    "offset": 0,
                    "limit": 25,
                },
            )
            items = tuple((response.get("creatives") or {}).get("items") or ())
        except Exception:
            items = ()
        if not items:
            return await interaction.response.edit_message(
                content="# 📣 Awin Post Now\nNo enabled active Creative is available for this advertiser.",
                view=AwinPostModeView(
                    self.guild,
                    self.admin_id,
                    advertiser_id=self.advertiser_id,
                ),
            )
        await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nChoose the exact Creative.",
            view=AwinPostCreativeView(
                self.guild,
                self.admin_id,
                advertiser_id=self.advertiser_id,
                items=items,
            ),
        )

    async def random(self, interaction):
        await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nChoose the destination channel.",
            view=AwinPostChannelView(
                self.guild,
                self.admin_id,
                selection_mode="random",
                advertiser_id=self.advertiser_id,
            ),
        )

    async def next(self, interaction):
        await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nChoose the destination channel.",
            view=AwinPostChannelView(
                self.guild,
                self.admin_id,
                selection_mode="next",
                advertiser_id=self.advertiser_id,
            ),
        )

    async def back(self, interaction):
        await open_post_now(interaction, self.guild, self.admin_id)


class AwinPostCreativeSelect(discord.ui.Select):
    def __init__(self, items):
        options = []
        for item in items[:25]:
            creative_id = str(item.get("id", "")).strip()
            if not creative_id:
                continue
            options.append(
                discord.SelectOption(
                    label=str(item.get("title") or item.get("dimensions") or "Awin Creative")[:100],
                    value=creative_id,
                    description=(f"{item.get('state') or 'unknown'} · {item.get('dimensions') or 'size unknown'}")[:100],
                )
            )
        super().__init__(
            placeholder="Choose Creative",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nChoose the destination channel.",
            view=AwinPostChannelView(
                self.view.guild,
                self.view.admin_id,
                selection_mode="specific",
                advertiser_id=self.view.advertiser_id,
                creative_id=self.values[0],
            ),
        )


class AwinPostCreativeView(AwinMenu):
    def __init__(self, guild, actor_id, *, advertiser_id, items):
        super().__init__(guild, actor_id)
        self.advertiser_id = advertiser_id
        self.add_item(AwinPostCreativeSelect(items))
        self.action("Back", self.back)

    async def back(self, interaction):
        await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nChoose how the Creative should be selected.",
            view=AwinPostModeView(
                self.guild,
                self.admin_id,
                advertiser_id=self.advertiser_id,
            ),
        )


class AwinPostChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Choose destination channel",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction):
        channel_id = int(self.values[0].id)
        await interaction.response.send_modal(
            AwinPostCopyModal(
                self.view.guild,
                self.view.admin_id,
                selection_mode=self.view.selection_mode,
                advertiser_id=self.view.advertiser_id,
                creative_id=self.view.creative_id,
                channel_id=channel_id,
            )
        )


class AwinPostChannelView(AwinMenu):
    def __init__(self, guild, actor_id, *, selection_mode, advertiser_id, creative_id=None):
        super().__init__(guild, actor_id)
        self.selection_mode = selection_mode
        self.advertiser_id = advertiser_id
        self.creative_id = creative_id
        self.add_item(AwinPostChannelSelect())
        self.action("Back", self.back)

    async def back(self, interaction):
        await interaction.response.edit_message(
            content="# 📣 Awin Post Now\nChoose how the Creative should be selected.",
            view=AwinPostModeView(
                self.guild,
                self.admin_id,
                advertiser_id=self.advertiser_id,
            ),
        )


class AwinPostCopyModal(discord.ui.Modal, title="Review Awin Post"):
    post_title = discord.ui.TextInput(
        label="Title (optional)",
        required=False,
        max_length=256,
    )
    post_text = discord.ui.TextInput(
        label="Text (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )

    def __init__(
        self,
        guild,
        actor_id,
        *,
        selection_mode,
        advertiser_id,
        channel_id,
        creative_id=None,
    ):
        super().__init__(timeout=300)
        self.guild = guild
        self.actor_id = actor_id
        self.selection_mode = selection_mode
        self.advertiser_id = advertiser_id
        self.channel_id = channel_id
        self.creative_id = creative_id

    async def on_submit(self, interaction):
        payload = {
            "channelId": self.channel_id,
            "selectionMode": self.selection_mode,
            "advertiserId": self.advertiser_id,
            "title": str(self.post_title.value or "").strip() or None,
            "text": str(self.post_text.value or "").strip() or None,
        }
        if self.creative_id:
            payload["creativeId"] = self.creative_id

        await interaction.response.defer(ephemeral=True)
        try:
            response = await _call(
                interaction,
                self.guild,
                POST_PREVIEW_API,
                payload,
            )
            preview = dict(response.get("preview") or {})
            confirm_payload = dict(response.get("confirmPayload") or {})
        except Exception:
            return await interaction.edit_original_response(
                content=(
                    "❌ Awin could not build this preview. "
                    "The Creative may no longer be eligible or the channel may be unavailable."
                ),
                view=AwinBackToOverviewView(self.guild, self.actor_id),
            )

        embed_data = preview.get("embed") if isinstance(preview.get("embed"), dict) else {}
        embed = discord.Embed.from_dict(embed_data) if embed_data else None
        content = str(preview.get("content") or "Awin post preview")[:2000]
        await interaction.edit_original_response(
            content=content,
            embed=embed,
            view=AwinPostConfirmView(
                self.guild,
                self.actor_id,
                confirm_payload=confirm_payload,
                preview=preview,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class AwinPostConfirmView(AwinMenu):
    def __init__(self, guild, actor_id, *, confirm_payload, preview):
        super().__init__(guild, actor_id)
        self.confirm_payload = dict(confirm_payload)
        self.preview = dict(preview)
        buttons = self.preview.get("linkButtons")
        if isinstance(buttons, (list, tuple)):
            for item in buttons[:1]:
                if isinstance(item, dict):
                    url = str(item.get("url") or "")
                    label = str(item.get("label") or "View offer")[:80]
                    if url.startswith("https://"):
                        self.add_item(discord.ui.Button(label=label, url=url))
        self.action("Confirm Send", self.confirm, style=discord.ButtonStyle.success)
        self.action("Cancel", self.cancel)

    async def confirm(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can confirm Awin affiliate posts.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        try:
            result = await _call(
                interaction,
                self.guild,
                POST_SEND_API,
                dict(self.confirm_payload),
            )
        except Exception:
            return await interaction.edit_original_response(
                content=(
                    "❌ Awin did not send the post. "
                    "The preview may have become stale or Discord rejected the destination."
                ),
                view=AwinBackToOverviewView(self.guild, self.admin_id),
            )
        await interaction.edit_original_response(
            content=(
                "✅ Awin affiliate post sent.\n"
                f"Channel ID: {int(result.get('channelId', 0))} · "
                f"Message ID: {int(result.get('messageId', 0))}"
            ),
            embed=None,
            view=AwinBackToOverviewView(self.guild, self.admin_id),
        )

    async def cancel(self, interaction):
        await interaction.response.edit_message(
            content="Post cancelled. Nothing was sent.",
            embed=None,
            view=AwinBackToOverviewView(self.guild, self.admin_id),
        )



async def open_campaigns(interaction, guild, actor_id):
    try:
        response = await _call(interaction, guild, CAMPAIGN_LIST_API, {})
        campaigns = tuple(response.get("campaigns") or ())
    except Exception:
        return await interaction.response.edit_message(
            content="# 🔁 Awin Campaigns\nCampaigns are currently unavailable.",
            view=AwinBackToOverviewView(guild, actor_id),
        )

    lines = ["# 🔁 Awin Campaigns", ""]
    if campaigns:
        for campaign in campaigns[:15]:
            status = (
                "Paused"
                if not campaign.get("enabled")
                else "Blocked"
                if campaign.get("blockedReason")
                else "Active"
            )
            interval = int(campaign.get("intervalSeconds", 0))
            lines.append(
                f"• **{_escape(campaign.get('name') or 'Campaign', 70)}** · "
                f"{status} · {_escape(campaign.get('rotation') or 'unknown', 20)} · "
                f"every {max(1, interval // 60)} min"
            )
        if len(campaigns) > 15:
            lines.append(f"• + {len(campaigns) - 15} more")
    else:
        lines.append("No Awin campaigns configured yet.")

    await interaction.response.edit_message(
        content="\n".join(lines)[:1950],
        view=AwinCampaignListView(guild, actor_id, campaigns),
    )


class AwinCampaignSelect(discord.ui.Select):
    def __init__(self, campaigns):
        options = []
        for campaign in campaigns[:25]:
            campaign_id = str(campaign.get("id", "")).strip()
            if not campaign_id:
                continue
            status = (
                "Paused"
                if not campaign.get("enabled")
                else "Blocked"
                if campaign.get("blockedReason")
                else "Active"
            )
            options.append(
                discord.SelectOption(
                    label=str(campaign.get("name") or "Awin Campaign")[:100],
                    value=campaign_id,
                    description=(f"{status} · {campaign.get('rotation') or 'unknown'}")[:100],
                )
            )
        super().__init__(
            placeholder="Choose campaign",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        await open_campaign_detail(
            interaction,
            self.view.guild,
            self.view.admin_id,
            campaign_id=self.values[0],
        )


class AwinCampaignListView(AwinMenu):
    def __init__(self, guild, actor_id, campaigns):
        super().__init__(guild, actor_id)
        if campaigns:
            self.add_item(AwinCampaignSelect(campaigns))
        self.action("Create Campaign", self.create, style=discord.ButtonStyle.primary)
        self.action("Back", self.back)

    async def create(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can create Awin campaigns.",
                ephemeral=True,
            )
        await open_campaign_create_advertiser(interaction, self.guild, self.admin_id)

    async def back(self, interaction):
        await open_awin(interaction, self.guild, self.admin_id)


async def open_campaign_detail(interaction, guild, actor_id, *, campaign_id):
    try:
        response = await _call(
            interaction,
            guild,
            CAMPAIGN_GET_API,
            {"campaignId": campaign_id},
        )
        campaign = dict(response.get("campaign") or {})
    except Exception:
        return await interaction.response.send_message(
            "Campaign details are currently unavailable.",
            ephemeral=True,
        )

    status = str(campaign.get("status") or ("active" if campaign.get("enabled") else "paused")).title()
    interval = int(campaign.get("intervalSeconds", 0))
    lines = [
        f"# 🔁 {_escape(campaign.get('name') or 'Awin Campaign', 80)}",
        f"**Status:** {status}",
        f"**Advertiser ID:** {_escape(campaign.get('advertiserId'), 40)}",
        f"**Channel:** <#{int(campaign.get('channelId', 0))}>",
        f"**Rotation:** {_escape(campaign.get('rotation'), 30)}",
        f"**Interval:** every {max(1, interval // 60)} minutes",
        f"**Creatives:** {int(campaign.get('eligibleCreativeCount', 0))} eligible / "
        f"{int(campaign.get('configuredCreativeCount', len(campaign.get('selectedCreativeIds') or ())))} configured",
        f"**Avoid immediate repeat:** {'Yes' if campaign.get('avoidImmediateRepeat') else 'No'}",
    ]
    if campaign.get("blockedReason"):
        lines.extend(["", f"⚠️ {_escape(campaign.get('blockedReason'), 300)}"])

    await interaction.response.edit_message(
        content="\n".join(lines)[:1950],
        view=AwinCampaignDetailView(guild, actor_id, campaign),
    )


class AwinCampaignDetailView(AwinMenu):
    def __init__(self, guild, actor_id, campaign):
        super().__init__(guild, actor_id)
        self.campaign = dict(campaign)
        self.campaign_id = str(campaign.get("id"))
        self.action("Preview Next", self.preview_next, style=discord.ButtonStyle.primary)
        self.action("Run Now", self.run_now)
        self.action("Edit", self.edit)
        self.action("Pause" if campaign.get("enabled") else "Resume", self.toggle)
        self.action("History", self.history)
        self.action("Delete", self.delete, style=discord.ButtonStyle.danger)
        self.action("Back", self.back)

    async def preview_next(self, interaction):
        try:
            response = await _call(
                interaction,
                self.guild,
                CAMPAIGN_PREVIEW_NEXT_API,
                {"campaignId": self.campaign_id},
            )
        except Exception:
            return await interaction.response.send_message(
                "Campaign preview is currently unavailable.",
                ephemeral=True,
            )
        if response.get("blocked"):
            return await interaction.response.send_message(
                f"⚠️ Campaign is blocked: {_escape(response.get('reason') or 'no eligible Creative', 300)}",
                ephemeral=True,
            )
        preview = dict(response.get("preview") or {})
        embed_data = preview.get("embed") if isinstance(preview.get("embed"), dict) else {}
        embed = discord.Embed.from_dict(embed_data) if embed_data else None
        await interaction.response.send_message(
            str(preview.get("content") or "Awin campaign preview")[:2000],
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def run_now(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can run Awin campaigns.",
                ephemeral=True,
            )
        await interaction.response.defer(ephemeral=True)
        try:
            result = await _call(
                interaction,
                self.guild,
                CAMPAIGN_RUN_NOW_API,
                {"campaignId": self.campaign_id},
            )
        except Exception:
            return await interaction.edit_original_response(
                content="❌ Campaign execution failed. No host-side rotation state was changed."
            )
        if result.get("blocked"):
            text = f"⚠️ Campaign is blocked: {_escape(result.get('reason') or 'no eligible Creative', 300)}"
        else:
            text = (
                "✅ Campaign post sent. "
                f"Message ID {int(result.get('messageId', 0))}."
            )
        await interaction.edit_original_response(content=text)

    async def edit(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can edit Awin campaigns.",
                ephemeral=True,
            )
        await open_campaign_edit_creatives(
            interaction,
            self.guild,
            self.admin_id,
            campaign=self.campaign,
        )

    async def toggle(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can pause or resume Awin campaigns.",
                ephemeral=True,
            )
        try:
            response = await _call(
                interaction,
                self.guild,
                CAMPAIGN_SET_ACTIVE_API,
                {
                    "campaignId": self.campaign_id,
                    "active": not bool(self.campaign.get("enabled")),
                },
            )
            campaign = dict(response.get("campaign") or {})
        except Exception:
            return await interaction.response.send_message(
                "Campaign state could not be changed.",
                ephemeral=True,
            )
        await open_campaign_detail(
            interaction,
            self.guild,
            self.admin_id,
            campaign_id=str(campaign.get("id") or self.campaign_id),
        )

    async def history(self, interaction):
        try:
            response = await _call(
                interaction,
                self.guild,
                CAMPAIGN_HISTORY_API,
                {"campaignId": self.campaign_id, "limit": 20},
            )
            rows = tuple(response.get("history") or ())
        except Exception:
            rows = ()
        lines = [f"# 🕘 {_escape(self.campaign.get('name') or 'Campaign', 80)} History", ""]
        if not rows:
            lines.append("No retained delivery history.")
        for row in rows[:20]:
            outcome = _escape(row.get("outcome") or "unknown", 20)
            occurred = int(row.get("occurredAt", 0))
            creative = _escape(row.get("creativeId") or "—", 45)
            reason = _escape(row.get("reason") or "", 80)
            suffix = f" · {reason}" if reason else ""
            lines.append(f"• {outcome} · t={occurred} · Creative {creative}{suffix}")
        await interaction.response.edit_message(
            content="\n".join(lines)[:1950],
            view=AwinCampaignHistoryView(
                self.guild,
                self.admin_id,
                campaign_id=self.campaign_id,
            ),
        )

    async def delete(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can delete Awin campaigns.",
                ephemeral=True,
            )
        await interaction.response.edit_message(
            content=(
                f"# 🗑️ Delete {_escape(self.campaign.get('name') or 'Awin Campaign', 80)}?\n"
                "The campaign configuration and scheduler job will be removed. "
                "Previously sent Discord messages are not deleted.\n\n"
                "Nothing changes until you confirm."
            ),
            view=AwinCampaignDeleteConfirmView(
                self.guild,
                self.admin_id,
                campaign_id=self.campaign_id,
            ),
        )

    async def back(self, interaction):
        await open_campaigns(interaction, self.guild, self.admin_id)


class AwinCampaignHistoryView(AwinMenu):
    def __init__(self, guild, actor_id, *, campaign_id):
        super().__init__(guild, actor_id)
        self.campaign_id = campaign_id
        self.action("Back", self.back)

    async def back(self, interaction):
        await open_campaign_detail(
            interaction,
            self.guild,
            self.admin_id,
            campaign_id=self.campaign_id,
        )


class AwinCampaignDeleteConfirmView(AwinMenu):
    def __init__(self, guild, actor_id, *, campaign_id):
        super().__init__(guild, actor_id)
        self.campaign_id = campaign_id
        self.action("Confirm Delete", self.confirm, style=discord.ButtonStyle.danger)
        self.action("Cancel", self.cancel)

    async def confirm(self, interaction):
        if interaction.user.id != self.guild.owner_id:
            return await interaction.response.send_message(
                "Only the server owner can delete Awin campaigns.",
                ephemeral=True,
            )
        try:
            await _call(
                interaction,
                self.guild,
                CAMPAIGN_DELETE_API,
                {"campaignId": self.campaign_id},
            )
        except Exception:
            return await interaction.response.send_message(
                "Campaign deletion failed. Reopen the campaign and try again.",
                ephemeral=True,
            )
        await open_campaigns(interaction, self.guild, self.admin_id)

    async def cancel(self, interaction):
        await open_campaign_detail(
            interaction,
            self.guild,
            self.admin_id,
            campaign_id=self.campaign_id,
        )


async def open_campaign_create_advertiser(interaction, guild, actor_id):
    try:
        advertisers = await _joined_advertisers(interaction, guild)
    except Exception:
        advertisers = ()
    if not advertisers:
        return await interaction.response.edit_message(
            content="# 🔁 Create Awin Campaign\nNo joined advertiser is available.",
            view=AwinBackToOverviewView(guild, actor_id),
        )
    await interaction.response.edit_message(
        content="# 🔁 Create Awin Campaign\nChoose the advertiser.",
        view=AwinCampaignAdvertiserView(guild, actor_id, advertisers),
    )


class AwinCampaignAdvertiserSelect(discord.ui.Select):
    def __init__(self, advertisers):
        options = []
        for item in advertisers[:25]:
            advertiser_id = str(item.get("id", "")).strip()
            if advertiser_id:
                options.append(
                    discord.SelectOption(
                        label=str(item.get("name") or f"Advertiser {advertiser_id}")[:100],
                        value=advertiser_id,
                    )
                )
        super().__init__(
            placeholder="Choose advertiser",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        await open_campaign_create_creatives(
            interaction,
            self.view.guild,
            self.view.admin_id,
            advertiser_id=self.values[0],
        )


class AwinCampaignAdvertiserView(AwinMenu):
    def __init__(self, guild, actor_id, advertisers):
        super().__init__(guild, actor_id)
        self.add_item(AwinCampaignAdvertiserSelect(advertisers))
        self.action("Back", self.back)

    async def back(self, interaction):
        await open_campaigns(interaction, self.guild, self.admin_id)


async def _campaign_creative_items(interaction, guild, advertiser_id):
    response = await _call(
        interaction,
        guild,
        CREATIVES_LIST_API,
        {
            "advertiserId": advertiser_id,
            "enabledOnly": True,
            "activeOnly": True,
            "offset": 0,
            "limit": 25,
        },
    )
    return tuple((response.get("creatives") or {}).get("items") or ())


async def open_campaign_create_creatives(interaction, guild, actor_id, *, advertiser_id):
    try:
        items = await _campaign_creative_items(interaction, guild, advertiser_id)
    except Exception:
        items = ()
    if not items:
        return await interaction.response.edit_message(
            content="# 🔁 Create Awin Campaign\nNo enabled active Creative is available for this advertiser.",
            view=AwinBackToOverviewView(guild, actor_id),
        )
    await interaction.response.edit_message(
        content="# 🔁 Create Awin Campaign\nSelect one or more Creatives.",
        view=AwinCampaignCreativeView(
            guild,
            actor_id,
            advertiser_id=advertiser_id,
            items=items,
            editing_campaign=None,
        ),
    )


class AwinCampaignCreativeSelect(discord.ui.Select):
    def __init__(self, items, defaults=()):
        default_ids = set(defaults)
        options = []
        for item in items[:25]:
            creative_id = str(item.get("id", "")).strip()
            if not creative_id:
                continue
            options.append(
                discord.SelectOption(
                    label=str(item.get("title") or item.get("dimensions") or "Awin Creative")[:100],
                    value=creative_id,
                    description=(f"{item.get('state') or 'unknown'} · {item.get('dimensions') or 'size unknown'}")[:100],
                    default=creative_id in default_ids,
                )
            )
        super().__init__(
            placeholder="Choose campaign Creatives",
            min_values=1,
            max_values=max(1, min(25, len(options))),
            options=options,
        )

    async def callback(self, interaction):
        await interaction.response.edit_message(
            content="# 🔁 Awin Campaign\nChoose the destination channel.",
            view=AwinCampaignChannelView(
                self.view.guild,
                self.view.admin_id,
                advertiser_id=self.view.advertiser_id,
                creative_ids=tuple(self.values),
                editing_campaign=self.view.editing_campaign,
            ),
        )


class AwinCampaignCreativeView(AwinMenu):
    def __init__(self, guild, actor_id, *, advertiser_id, items, editing_campaign):
        super().__init__(guild, actor_id)
        self.advertiser_id = advertiser_id
        self.editing_campaign = dict(editing_campaign) if editing_campaign else None
        defaults = (
            tuple(self.editing_campaign.get("selectedCreativeIds") or ())
            if self.editing_campaign
            else ()
        )
        self.add_item(AwinCampaignCreativeSelect(items, defaults))
        self.action("Back", self.back)

    async def back(self, interaction):
        if self.editing_campaign:
            await open_campaign_detail(
                interaction,
                self.guild,
                self.admin_id,
                campaign_id=str(self.editing_campaign.get("id")),
            )
        else:
            await open_campaign_create_advertiser(interaction, self.guild, self.admin_id)


class AwinCampaignChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Choose campaign channel",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction):
        await interaction.response.edit_message(
            content="# 🔁 Awin Campaign\nChoose the rotation strategy.",
            view=AwinCampaignRotationView(
                self.view.guild,
                self.view.admin_id,
                advertiser_id=self.view.advertiser_id,
                creative_ids=self.view.creative_ids,
                channel_id=int(self.values[0].id),
                editing_campaign=self.view.editing_campaign,
            ),
        )


class AwinCampaignChannelView(AwinMenu):
    def __init__(self, guild, actor_id, *, advertiser_id, creative_ids, editing_campaign):
        super().__init__(guild, actor_id)
        self.advertiser_id = advertiser_id
        self.creative_ids = tuple(creative_ids)
        self.editing_campaign = dict(editing_campaign) if editing_campaign else None
        self.add_item(AwinCampaignChannelSelect())
        self.action("Back", self.back)

    async def back(self, interaction):
        try:
            items = await _campaign_creative_items(
                interaction,
                self.guild,
                self.advertiser_id,
            )
        except Exception:
            items = ()
        await interaction.response.edit_message(
            content="# 🔁 Awin Campaign\nSelect one or more Creatives.",
            view=AwinCampaignCreativeView(
                self.guild,
                self.admin_id,
                advertiser_id=self.advertiser_id,
                items=items,
                editing_campaign=self.editing_campaign,
            ),
        )


class AwinCampaignRotationSelect(discord.ui.Select):
    def __init__(self, selected=None):
        values = ("fixed", "sequential", "random", "shuffle")
        super().__init__(
            placeholder="Choose rotation",
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
        rotation = self.values[0]
        if rotation == "fixed" and len(self.view.creative_ids) != 1:
            return await interaction.response.send_message(
                "Fixed rotation requires exactly one selected Creative.",
                ephemeral=True,
            )
        await interaction.response.send_modal(
            AwinCampaignDetailsModal(
                self.view.guild,
                self.view.admin_id,
                advertiser_id=self.view.advertiser_id,
                creative_ids=self.view.creative_ids,
                channel_id=self.view.channel_id,
                rotation=rotation,
                editing_campaign=self.view.editing_campaign,
            )
        )


class AwinCampaignRotationView(AwinMenu):
    def __init__(
        self,
        guild,
        actor_id,
        *,
        advertiser_id,
        creative_ids,
        channel_id,
        editing_campaign,
    ):
        super().__init__(guild, actor_id)
        self.advertiser_id = advertiser_id
        self.creative_ids = tuple(creative_ids)
        self.channel_id = channel_id
        self.editing_campaign = dict(editing_campaign) if editing_campaign else None
        current_rotation = self.editing_campaign.get("rotation") if self.editing_campaign else None
        self.add_item(AwinCampaignRotationSelect(current_rotation))
        self.action("Back", self.back)

    async def back(self, interaction):
        await interaction.response.edit_message(
            content="# 🔁 Awin Campaign\nChoose the destination channel.",
            view=AwinCampaignChannelView(
                self.guild,
                self.admin_id,
                advertiser_id=self.advertiser_id,
                creative_ids=self.creative_ids,
                editing_campaign=self.editing_campaign,
            ),
        )


class AwinCampaignDetailsModal(discord.ui.Modal, title="Awin Campaign Details"):
    name = discord.ui.TextInput(label="Name", required=True, max_length=80)
    interval_minutes = discord.ui.TextInput(
        label="Interval minutes (min. 15)",
        required=True,
        max_length=8,
    )
    avoid_repeat = discord.ui.TextInput(
        label="Avoid immediate repeat? yes/no",
        required=True,
        max_length=3,
        default="yes",
    )

    def __init__(
        self,
        guild,
        actor_id,
        *,
        advertiser_id,
        creative_ids,
        channel_id,
        rotation,
        editing_campaign,
    ):
        super().__init__(timeout=300)
        self.guild = guild
        self.actor_id = actor_id
        self.advertiser_id = advertiser_id
        self.creative_ids = tuple(creative_ids)
        self.channel_id = channel_id
        self.rotation = rotation
        self.editing_campaign = dict(editing_campaign) if editing_campaign else None
        if self.editing_campaign:
            self.name.default = str(self.editing_campaign.get("name") or "")[:80]
            interval_seconds = int(self.editing_campaign.get("intervalSeconds", 900))
            self.interval_minutes.default = str(max(15, interval_seconds // 60))
            self.avoid_repeat.default = (
                "yes" if self.editing_campaign.get("avoidImmediateRepeat", True) else "no"
            )

    async def on_submit(self, interaction):
        try:
            interval_minutes = int(str(self.interval_minutes.value).strip())
        except ValueError:
            return await interaction.response.send_message(
                "Interval must be a whole number of minutes.",
                ephemeral=True,
            )
        if interval_minutes < 15:
            return await interaction.response.send_message(
                "Awin campaigns require an interval of at least 15 minutes.",
                ephemeral=True,
            )
        avoid_text = str(self.avoid_repeat.value).strip().lower()
        if avoid_text not in {"yes", "no"}:
            return await interaction.response.send_message(
                "Use yes or no for immediate-repeat avoidance.",
                ephemeral=True,
            )
        payload = {
            "name": str(self.name.value).strip(),
            "advertiserId": self.advertiser_id,
            "channelId": self.channel_id,
            "selectedCreativeIds": list(self.creative_ids),
            "rotation": self.rotation,
            "intervalSeconds": interval_minutes * 60,
            "avoidImmediateRepeat": avoid_text == "yes",
        }
        contract_id = CAMPAIGN_CREATE_API
        if self.editing_campaign:
            contract_id = CAMPAIGN_UPDATE_API
            payload["campaignId"] = str(self.editing_campaign.get("id"))

        await interaction.response.defer(ephemeral=True)
        try:
            response = await _call(interaction, self.guild, contract_id, payload)
            campaign = dict(response.get("campaign") or {})
        except Exception:
            return await interaction.edit_original_response(
                content=(
                    "❌ Campaign could not be saved. "
                    "Review the selected advertiser, Creatives, channel and interval."
                )
            )
        await interaction.edit_original_response(
            content="✅ Awin campaign saved.",
            view=AwinCampaignSavedView(
                self.guild,
                self.actor_id,
                campaign_id=str(campaign.get("id")),
            ),
        )


class AwinCampaignSavedView(AwinMenu):
    def __init__(self, guild, actor_id, *, campaign_id):
        super().__init__(guild, actor_id)
        self.campaign_id = campaign_id
        self.action("Open Campaign", self.open_campaign)
        self.action("Back to Campaigns", self.back)

    async def open_campaign(self, interaction):
        await open_campaign_detail(
            interaction,
            self.guild,
            self.admin_id,
            campaign_id=self.campaign_id,
        )

    async def back(self, interaction):
        await open_campaigns(interaction, self.guild, self.admin_id)


async def open_campaign_edit_creatives(interaction, guild, actor_id, *, campaign):
    advertiser_id = str(campaign.get("advertiserId", "")).strip()
    try:
        items = await _campaign_creative_items(interaction, guild, advertiser_id)
    except Exception:
        items = ()
    if not items:
        return await interaction.response.edit_message(
            content="# 🔁 Edit Awin Campaign\nNo eligible Creative is currently available for this advertiser.",
            view=AwinCampaignDetailView(guild, actor_id, campaign),
        )
    await interaction.response.edit_message(
        content=(
            "# 🔁 Edit Awin Campaign\n"
            "Select the Creatives to keep/use. Changing this selection resets rotation state."
        ),
        view=AwinCampaignCreativeView(
            guild,
            actor_id,
            advertiser_id=advertiser_id,
            items=items,
            editing_campaign=campaign,
        ),
    )
