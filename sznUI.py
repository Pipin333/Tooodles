# Archivo que maneja las interfaces de usuario dentro de los mensajes del bot, pensado para Toodles v6.
import discord
from discord.ext import commands
from discord.ui import View, Button

class MusicUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def notify_now_playing(self, ctx, song_title, origin=None):
        core = self.bot.get_cog("MusicCore")
        view = self.MusicControls(core, ctx)
        if ctx.guild and ctx.channel:
            view.add_item(Button(label="📜 Ver Canal", style=discord.ButtonStyle.link, url=f"https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/"))
        
        prefix = "🔁" if origin and "radio" in origin.lower() else "🎶"
        embed = discord.Embed(
            title=f"{prefix} Ahora Reproduciendo",
            description=f"**{song_title}**",
            color=discord.Color.green()
        )
        if origin:
            embed.set_footer(text=origin)
        else:
            embed.set_footer(text="Usa los botones para controlar la música o revisa la cola.")
        
        try:
            await ctx.send(embed=embed, view=view, delete_after=300)
        except Exception as e:
            print(f"⚠️ Error al enviar notificación de reproducción: {e}")

    @commands.command()
    async def controls(self, ctx):
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ No se encontró el módulo de música.")
            return
        view = self.MusicControls(core, ctx)
        await ctx.send("🎛️ Controles de reproducción:", view=view, delete_after=300)

    class MusicControls(View):
        def __init__(self, core, ctx):
            super().__init__(timeout=300)
            self.core = core
            self.ctx = ctx

        @discord.ui.button(label="⏯️ Pausa/Reanuda", style=discord.ButtonStyle.primary)
        async def pause_resume(self, interaction: discord.Interaction, button: Button):
            if not self.core or not self.core.voice_client or not self.core.current_song:
                await interaction.response.send_message("⚠️ No hay nada reproduciéndose.", ephemeral=True)
                return
            if self.core.voice_client.is_playing():
                self.core.voice_client.pause()
                await interaction.response.send_message("⏸️ Canción pausada.", ephemeral=True)
            elif self.core.voice_client.is_paused():
                self.core.voice_client.resume()
                await interaction.response.send_message("▶️ Canción reanudada.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ No hay reproducción activa.", ephemeral=True)

        @discord.ui.button(label="⏭️ Saltar", style=discord.ButtonStyle.secondary)
        async def skip(self, interaction: discord.Interaction, button: Button):
            if not self.core or not self.core.voice_client:
                await interaction.response.send_message("⚠️ No hay ninguna reproducción activa.", ephemeral=True)
                return
            if self.core.voice_client.is_playing() or self.core.voice_client.is_paused():
                self.core.voice_client.stop()
                await interaction.response.send_message("⏭️ Canción saltada.", ephemeral=True)
            else:
                await interaction.response.send_message("🎵 La cola está vacía.", ephemeral=True)

        @discord.ui.button(label="⏹️ Detener", style=discord.ButtonStyle.danger)
        async def stop(self, interaction: discord.Interaction, button: Button):
            if self.core and self.core.voice_client:
                await self.core.voice_client.disconnect()
                self.core.voice_client = None
                self.core.song_queue.clear()
                self.core.current_song = None
                await interaction.response.send_message("⏹️ Reproducción detenida y desconectado.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ No estoy conectado.", ephemeral=True)

    @commands.command()
    async def queueui(self, ctx):
        core = self.bot.get_cog("MusicCore")
        if not core or not core.song_queue:
            await ctx.send("📭 La cola está vacía.")
            return

        items_per_page = 10
        total_pages = (len(core.song_queue) + items_per_page - 1) // items_per_page
        current_page = 0

        def get_page_content(page):
            start = page * items_per_page
            end = start + items_per_page
            songs = core.song_queue[start:end]
            content = f"**🎵 Cola de canciones (página {page + 1}/{total_pages}):**\n"
            for i, song in enumerate(songs, start=start + 1):
                duration = core.format_duration(song.get('duration', 0))
                content += f"{i}. **{song['title']}** ({duration})\n"
            return content

        class QueueControls(View):
            def __init__(self):
                super().__init__(timeout=300)

            @discord.ui.button(label="⏮️ Primera", style=discord.ButtonStyle.secondary)
            async def first(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                current_page = 0
                await interaction.response.edit_message(content=get_page_content(current_page), view=self)

            @discord.ui.button(label="⬅️ Anterior", style=discord.ButtonStyle.secondary)
            async def prev(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                if current_page > 0:
                    current_page -= 1
                    await interaction.response.edit_message(content=get_page_content(current_page), view=self)
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="➡️ Siguiente", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                if current_page < total_pages - 1:
                    current_page += 1
                    await interaction.response.edit_message(content=get_page_content(current_page), view=self)
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="⏭️ Última", style=discord.ButtonStyle.secondary)
            async def last(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                current_page = total_pages - 1
                await interaction.response.edit_message(content=get_page_content(current_page), view=self)

        await ctx.send(get_page_content(current_page), view=QueueControls(), delete_after=300)

async def setup(bot):
    await bot.add_cog(MusicUI(bot))