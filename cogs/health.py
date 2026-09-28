from services.response_service import SafeView, command_error
"""Owner/admin diagnostics, including private complete details and command inventory."""
import io
import discord
from services.game_area_cleanup import authorized
from services import health_service


class HealthView(SafeView):
    def __init__(self,guild,actor_id,findings):
        super().__init__(timeout=180)
        self.guild,self.actor_id,self.findings=guild,actor_id,findings

    async def interaction_check(self,interaction):
        if interaction.user.id!=self.actor_id or not authorized(self.guild,interaction.user):
            await interaction.response.send_message('❌ Owner or administrator access required.',ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Details')
    async def details(self,interaction,button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.edit_original_response(content='🔄 Checking GamerHQ in detail…\nInspecting managed messages and deeper recovery checks.')
        try:
            self.findings = await health_service.scan(self.guild, interaction.client, messages=True)
        except (TimeoutError, discord.HTTPException):
            await interaction.edit_original_response(content='Detailed checks could not finish. Discord may be busy or inaccessible. Nothing was changed; retry Details later.')
            return
        text='\n'.join(f'{"WARNING" if f.state == "WARN" else f.state.replace("_", " ")}: {f.name} — {f.detail}' for f in self.findings)
        text+='\n\nRegistered commands\n'+'\n'.join('/'+name+' — '+description for name,description in health_service.command_inventory(interaction.client,self.guild))
        await interaction.edit_original_response(content='Detailed checks complete. Results are attached.', attachments=[discord.File(io.BytesIO(text.encode('utf-8')),filename='gamerhq-health.txt')])

    @discord.ui.button(label='Refresh')
    async def refresh(self,interaction,button):
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content='🔄 Checking GamerHQ…', view=None)
        self.findings=await health_service.scan(self.guild,interaction.client,messages=False)
        await interaction.edit_original_response(content=health_service.summary(self.findings),view=self)

    @discord.ui.button(label='Review Matches')
    async def reconcile(self, interaction, button):
        from cogs.server import open_operation
        await open_operation(interaction, 'reconcile', replace=True)

    @discord.ui.button(label='Close')
    async def close(self,interaction,button):
        await interaction.response.edit_message(content='Health check closed.',view=None)
        self.stop()
