"""Member-facing read-only GamerHQ profile command."""
import discord
from discord import app_commands
from discord.ext import commands

from services.member_profile_service import profile_embed, project_member_profile


class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profile", description="Show a GamerHQ member profile")
    @app_commands.describe(member="Optional member whose public GamerHQ profile you want to view")
    async def profile(self, interaction: discord.Interaction, member: discord.Member | None = None):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message(
                "Use this command inside GamerHQ.",
                ephemeral=True,
            )

        target = member or interaction.user
        if target.guild.id != interaction.guild.id:
            return await interaction.response.send_message(
                "That member is not part of this server.",
                ephemeral=True,
            )

        profile = project_member_profile(target)
        await interaction.response.send_message(
            embed=profile_embed(target, profile),
            ephemeral=False,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
