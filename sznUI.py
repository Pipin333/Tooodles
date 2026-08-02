# Archivo que maneja las interfaces de usuario dentro de los mensajes del bot, pensado para Toodles v6.
import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput

class PrefixModal(Modal, title="Configurar Prefijo del Bot"):
    prefix_input = TextInput(
        label="Nuevo Prefijo (Máx. 5 caracteres)",
        placeholder="ej: !, $, td?, bot-",
        min_length=1,
        max_length=5,
        required=True
    )

    def __init__(self, settings_view):
        super().__init__()
        self.settings_view = settings_view

    async def on_submit(self, interaction: discord.Interaction):
        new_prefix = self.prefix_input.value.strip()
        guild_id = interaction.guild.id

        from sznUtils import save_config
        save_config(f"prefix_{guild_id}", new_prefix)

        self.settings_view.update_buttons()
        await interaction.response.edit_message(
            embed=self.settings_view.get_embed(interaction.guild),
            view=self.settings_view
        )
        await interaction.followup.send(f"✅ El prefijo del bot ha sido cambiado a `{new_prefix}` para este servidor.", ephemeral=True)


class MusicUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_check(self.check_command_channel)

    def cog_unload(self):
        self.bot.remove_check(self.check_command_channel)

    async def check_command_channel(self, ctx):
        if not ctx.guild:
            return True

        from sznUtils import load_config
        channel_id_str = load_config(f"cmd_channel_{ctx.guild.id}")
        if not channel_id_str:
            return True

        if str(ctx.channel.id) == channel_id_str:
            return True

        if ctx.command.name in ("channel", "canal", "settings"):
            return True

        try:
            await ctx.message.delete(delay=3)
            await ctx.send(
                f"⚠️ Los comandos de música están restringidos al canal exclusivo: <#{channel_id_str}>.", 
                delete_after=5
            )
        except Exception:
            pass

        return False

    async def notify_now_playing(self, ctx, song_dict):
        core = self.bot.get_cog("MusicCore")
        if not core:
            return

        player = core.get_player(ctx)
        song_title = song_dict.get('title', 'Canción Desconocida')
        origin = song_dict.get('origin', '🎵 Solicitada')
        duration = song_dict.get('duration', 0)
        uploader = song_dict.get('uploader', 'Artista Desconocido')
        thumbnail = song_dict.get('thumbnail', '')
        username = song_dict.get('username')

        duration_str = core.format_duration(duration)

        is_radio = "radio" in origin.lower()
        embed_color = 0x1db954 if "spotify" in origin.lower() else (0x8a2be2 if is_radio else 0x7d5fff)
        
        embed = discord.Embed(
            title="🎶 Ahora Reproduciendo" if not is_radio else "📻 Radio Automática",
            description=f"**[{song_title}]({song_dict.get('url', 'https://www.youtube.com')})**",
            color=embed_color
        )

        embed.add_field(name="👤 Artista", value=f"`{uploader}`", inline=True)
        embed.add_field(name="⏱️ Duración", value=f"`{duration_str}`", inline=True)
        
        if username:
            embed.add_field(name="📥 Pedido por", value=f"@{username}", inline=True)
        elif is_radio:
            embed.add_field(name="🧬 Origen", value="Recomendaciones ML", inline=True)
        else:
            embed.add_field(name="🧬 Origen", value=f"`{origin}`", inline=True)

        if thumbnail and thumbnail.startswith("http"):
            embed.set_thumbnail(url=thumbnail)

        vc_name = ctx.author.voice.channel.name if ctx.author.voice else "Canal de voz"
        vibe_size = len(getattr(player, 'recent_artist_ids', []))
        embed.set_footer(text=f"🔊 VC: {vc_name}  •  🧬 Perfil Radio: {vibe_size}/5 artistas")

        view = self.MusicControls(core, ctx)
        if ctx.guild and ctx.channel:
            view.add_item(Button(
                label="📜 Ir al chat", 
                style=discord.ButtonStyle.link, 
                url=f"https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/"
            ))

        try:
            await ctx.send(embed=embed, view=view, delete_after=300)
        except Exception as e:
            print(f"⚠️ Error al enviar notificación de reproducción: {e}", flush=True)

    @commands.command(name="controls", aliases=["ctr", "player"])
    async def controls(self, ctx):
        """Muestra un panel persistente con los controles del reproductor."""
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ No se encontró el módulo de música.")
            return

        player = core.get_player(ctx)
        if not player.current_song:
            await ctx.send("⚠️ No hay ninguna canción reproduciéndose en este momento.")
            return

        await self.notify_now_playing(ctx, player.current_song)

    class MusicControls(View):
        def __init__(self, core, ctx):
            super().__init__(timeout=300)
            self.core = core
            self.ctx = ctx
            self.update_radio_button()

        def get_player(self):
            return self.core.get_player(self.ctx)

        def update_radio_button(self):
            player = self.get_player()
            for child in self.children:
                if getattr(child, 'custom_id', None) == 'toggle_radio':
                    if getattr(player, 'radio_mode', False):
                        child.style = discord.ButtonStyle.success
                        child.label = "📻 Radio: On"
                    else:
                        child.style = discord.ButtonStyle.secondary
                        child.label = "📻 Radio: Off"

        @discord.ui.button(label="⏯️ Pausa/Reanuda", style=discord.ButtonStyle.primary, custom_id="pause_resume")
        async def pause_resume(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.voice_client or not player.current_song:
                await interaction.response.send_message("⚠️ No hay nada reproduciéndose.", ephemeral=True)
                return
            
            if player.voice_client.is_playing():
                player.voice_client.pause()
                await interaction.response.send_message("⏸️ Canción pausada por el usuario.", ephemeral=True)
            elif player.voice_client.is_paused():
                player.voice_client.resume()
                await interaction.response.send_message("▶️ Canción reanudada por el usuario.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ No hay reproducción activa.", ephemeral=True)

        @discord.ui.button(label="⏭️ Saltar", style=discord.ButtonStyle.secondary, custom_id="skip")
        async def skip(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.voice_client:
                await interaction.response.send_message("⚠️ No hay ninguna reproducción activa.", ephemeral=True)
                return
                
            if player.voice_client.is_playing() or player.voice_client.is_paused():
                player.current_song_skipped = True
                player.voice_client.stop()
                await interaction.response.send_message("⏭️ Canción saltada vía controles de UI.", ephemeral=True)
            else:
                await interaction.response.send_message("🎵 La cola está vacía.", ephemeral=True)

        @discord.ui.button(label="📻 Radio", style=discord.ButtonStyle.secondary, custom_id="toggle_radio")
        async def toggle_radio(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            player.radio_mode = not getattr(player, 'radio_mode', False)
            self.update_radio_button()
            status = "activado" if player.radio_mode else "desactivado"
            
            await interaction.response.edit_message(view=self)
            
            if player.radio_mode and len(player.song_queue) < 2:
                await self.core.expand_radio_queue(self.ctx)
                
            await interaction.followup.send(f"📻 Modo radio automático **{status}**.", ephemeral=True)

        @discord.ui.button(label="⏹️ Detener", style=discord.ButtonStyle.danger, custom_id="stop")
        async def stop(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if player.voice_client:
                player.song_queue.clear()
                player.radio_mode = False
                from sznMusic import cleanup_cache
                cleanup_cache()
                if player.voice_client:
                    player.voice_client.stop()
                    await player.voice_client.disconnect()
                    player.voice_client = None
                player.current_song = None
                await interaction.response.send_message("⏹️ Reproducción detenida y bot desconectado del canal.", ephemeral=True)
                
                try:
                    await self.ctx.send("🛑 Reproducción detenida, bot desconectado y cola limpiada vía panel de control.")
                except Exception:
                    pass
            else:
                await interaction.response.send_message("⚠️ El bot no está conectado a ningún canal.", ephemeral=True)

    @commands.command(name="q", aliases=["queue", "cola"])
    async def queueui(self, ctx):
        """Muestra la cola de reproducción interactiva con paginación."""
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ No se encontró el módulo de música.")
            return

        player = core.get_player(ctx)
        if not player.current_song and not player.song_queue:
            await ctx.send("📭 La cola de canciones está vacía.")
            return

        items_per_page = 10
        total_songs = len(player.song_queue)
        total_pages = max(1, (total_songs + items_per_page - 1) // items_per_page)
        current_page = 0

        def get_page_embed(page):
            embed = discord.Embed(
                title="🎵 Cola de Reproducción",
                color=0x7d5fff
            )

            if player.current_song:
                current_title = player.current_song.get('title', 'Desconocido')
                current_uploader = player.current_song.get('uploader', 'Artista')
                current_duration = core.format_duration(player.current_song.get('duration', 0))
                embed.add_field(
                    name="▶️ Sonando Ahora",
                    value=f"**{current_title}** - `{current_uploader}` `[{current_duration}]`",
                    inline=False
                )
                if player.current_song.get('thumbnail') and player.current_song['thumbnail'].startswith("http"):
                    embed.set_thumbnail(url=player.current_song['thumbnail'])

            start = page * items_per_page
            end = start + items_per_page
            songs = player.song_queue[start:end]

            queue_list = ""
            for i, song in enumerate(songs, start=start + 1):
                duration = core.format_duration(song.get('duration', 0))
                origin_icon = "📻" if "radio" in song.get('origin', '').lower() else "🎵"
                requester = f"@{song.get('username')}" if song.get('username') else "Auto"
                queue_list += f"`{i:02d}.` {origin_icon} **{song['title']}** `[{duration}]` (por {requester})\n"

            if not queue_list:
                queue_list = "*No hay canciones en cola. ¡Agrega una o activa la radio!*"

            embed.add_field(
                name="📋 Siguientes Canciones",
                value=queue_list,
                inline=False
            )

            total_duration = sum(s.get('duration', 0) for s in player.song_queue)
            total_duration_str = core.format_duration(total_duration)

            embed.set_footer(
                text=f"Página {page + 1}/{total_pages}  •  {total_songs} canciones  •  Tiempo restante: {total_duration_str}"
            )
            return embed

        class QueueControls(View):
            def __init__(self):
                super().__init__(timeout=300)

            @discord.ui.button(label="⏮️ Primera", style=discord.ButtonStyle.secondary)
            async def first(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                current_page = 0
                await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)

            @discord.ui.button(label="⬅️ Anterior", style=discord.ButtonStyle.secondary)
            async def prev(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                if current_page > 0:
                    current_page -= 1
                    await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="➡️ Siguiente", style=discord.ButtonStyle.secondary)
            async def next(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                if current_page < total_pages - 1:
                    current_page += 1
                    await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)
                else:
                    await interaction.response.defer()

            @discord.ui.button(label="⏭️ Última", style=discord.ButtonStyle.secondary)
            async def last(self, interaction: discord.Interaction, button: Button):
                nonlocal current_page
                current_page = total_pages - 1
                await interaction.response.edit_message(embed=get_page_embed(current_page), view=self)

        await ctx.send(embed=get_page_embed(current_page), view=QueueControls(), delete_after=300)

    @commands.command(name="channel", aliases=["canal"])
    @commands.has_permissions(administrator=True)
    async def channel_config(self, ctx, *, arg: str = None):
        """Configura un único canal de texto para recibir comandos de música (Solo Admin)."""
        from sznUtils import save_config, load_config
        
        guild_id = ctx.guild.id
        config_key = f"cmd_channel_{guild_id}"
        
        if arg is None:
            current = load_config(config_key)
            if current:
                embed = discord.Embed(
                    title="⚙️ Configuración de Canal Exclusivo",
                    description=f"Los comandos están bloqueados al canal: <#{current}>.\n\n"
                                f"Para desactivar esta restricción y permitir comandos en cualquier canal, escribe:\n"
                                f"`td?channel reset`",
                    color=0x7d5fff
                )
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(
                    title="⚙️ Configuración de Canal Exclusivo",
                    description="El bot actualmente responde a comandos en **cualquier canal de texto**.\n\n"
                                f"Para restringir los comandos a un único canal, escribe:\n"
                                f"`td?channel #nombre-del-canal` o `td?channel aqui`",
                    color=0x7d5fff
                )
                await ctx.send(embed=embed)
            return

        arg_lower = arg.lower().strip()
        
        if arg_lower in ("reset", "off", "desactivar", "clear"):
            save_config(config_key, "")
            embed = discord.Embed(
                title="⚙️ Configuración Actualizada",
                description="✅ Se ha desactivado la restricción de canal. El bot ahora responderá en **todos los canales de texto**.",
                color=0x1db954
            )
            await ctx.send(embed=embed)
            return
            
        if arg_lower in ("aqui", "aquí", "here", "this"):
            target_channel = ctx.channel
        else:
            channel_id = None
            if arg.startswith("<#") and arg.endswith(">"):
                try:
                    channel_id = int(arg[2:-1])
                except ValueError:
                    pass
            else:
                try:
                    channel_id = int(arg)
                except ValueError:
                    pass
                    
            if channel_id:
                target_channel = ctx.guild.get_channel(channel_id)
            else:
                target_channel = discord.utils.get(ctx.guild.text_channels, name=arg)

        if not target_channel:
            await ctx.send("❌ No pude encontrar ese canal de texto. Menciónalo como `#nombre-canal` o escribe `td?channel aqui`.")
            return

        save_config(config_key, str(target_channel.id))
        
        embed = discord.Embed(
            title="⚙️ Configuración Actualizada",
            description=f"✅ Los comandos de música ahora están restringidos al canal {target_channel.mention}.\n\n"
                        f"Los comandos enviados en otros canales serán eliminados automáticamente y el bot los ignorará.",
            color=0x1db954
        )
        await ctx.send(embed=embed)

    @channel_config.error
    async def channel_config_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ Necesitas permisos de **Administrador** para configurar el canal exclusivo.")

    @commands.command(name="settings", aliases=["config"])
    async def settings_dashboard(self, ctx):
        """Muestra el panel interactivo de configuración del servidor."""
        core = self.bot.get_cog("MusicCore")
        if not core:
            await ctx.send("❌ Módulo de música no encontrado.")
            return

        view = self.SettingsControls(core, self.bot)
        await ctx.send(embed=view.get_embed(ctx.guild), view=view)

    class SettingsControls(View):
        def __init__(self, core, bot):
            super().__init__(timeout=300)
            self.core = core
            self.bot = bot
            self.update_buttons()

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ Solo los **Administradores** pueden modificar la configuración del servidor.", 
                    ephemeral=True
                )
                return False
            return True

        def get_embed(self, guild):
            from sznUtils import load_config
            guild_id = guild.id
            
            cmd_channel = load_config(f"cmd_channel_{guild_id}")
            cmd_channel_val = f"<#{cmd_channel}>" if cmd_channel else "`Cualquier canal`"
            
            default_radio = load_config(f"default_radio_{guild_id}")
            default_radio_val = "`Activo`" if default_radio == "on" else "`Inactivo (Por defecto)`"
            
            prefix = load_config(f"prefix_{guild_id}") or "td?"
            
            player = self.core.get_player(guild.id)
            vibe_size = len(getattr(player, 'recent_artist_ids', []))
            
            embed = discord.Embed(
                title=f"⚙️ Ajustes del Servidor - {guild.name}",
                description="Haz clic en los botones inferiores para cambiar la configuración de forma interactiva.",
                color=0x7d5fff
            )
            embed.add_field(name="⌨️ Prefijo del Bot", value=f"`{prefix}`", inline=True)
            embed.add_field(name="🔒 Canal de Comandos", value=cmd_channel_val, inline=True)
            embed.add_field(name="📻 Radio Default (Al conectar)", value=default_radio_val, inline=True)
            embed.add_field(name="🧬 Historial de Recomendación", value=f"`{vibe_size}/5` artistas registrados", inline=True)
            return embed

        def update_buttons(self):
            from sznUtils import load_config
            guild_id = self.core.bot.guilds[0].id if self.core.bot.guilds else 0
            
            for child in self.children:
                if getattr(child, 'custom_id', None) == 'toggle_channel':
                    cmd_channel = load_config(f"cmd_channel_{guild_id}")
                    if cmd_channel:
                        child.style = discord.ButtonStyle.danger
                        child.label = "🔓 Liberar Canal"
                    else:
                        child.style = discord.ButtonStyle.primary
                        child.label = "🔒 Fijar Canal Aquí"
                        
                elif getattr(child, 'custom_id', None) == 'toggle_default_radio':
                    default_radio = load_config(f"default_radio_{guild_id}")
                    if default_radio == "on":
                        child.style = discord.ButtonStyle.success
                        child.label = "📻 Radio Default: On"
                    else:
                        child.style = discord.ButtonStyle.secondary
                        child.label = "📻 Radio Default: Off"

                elif getattr(child, 'custom_id', None) == 'change_prefix_btn':
                    prefix = load_config(f"prefix_{guild_id}") or "td?"
                    child.label = f"✏️ Prefijo: {prefix}"

        @discord.ui.button(label="Fijar Canal", style=discord.ButtonStyle.primary, custom_id="toggle_channel")
        async def toggle_channel(self, interaction: discord.Interaction, button: Button):
            from sznUtils import save_config, load_config
            guild_id = interaction.guild.id
            config_key = f"cmd_channel_{guild_id}"
            
            current_channel = load_config(config_key)
            if current_channel:
                save_config(config_key, "")
                confirm_msg = "🔓 Se ha desactivado la restricción de canal. El bot responderá en cualquier lado."
            else:
                save_config(config_key, str(interaction.channel.id))
                confirm_msg = f"🔒 Canal exclusivo fijado a <#{interaction.channel.id}>."
                
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(interaction.guild), view=self)
            await interaction.followup.send(confirm_msg, ephemeral=True)

        @discord.ui.button(label="Radio Default", style=discord.ButtonStyle.secondary, custom_id="toggle_default_radio")
        async def toggle_default_radio(self, interaction: discord.Interaction, button: Button):
            from sznUtils import save_config, load_config
            guild_id = interaction.guild.id
            config_key = f"default_radio_{guild_id}"
            
            current_default = load_config(config_key)
            if current_default == "on":
                save_config(config_key, "off")
                confirm_msg = "📻 Radio automática por defecto desactivada."
            else:
                save_config(config_key, "on")
                confirm_msg = "📻 Radio automática por defecto activada. El bot iniciará la radio al conectar."
                
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(interaction.guild), view=self)
            await interaction.followup.send(confirm_msg, ephemeral=True)

        @discord.ui.button(label="✏️ Prefijo: td?", style=discord.ButtonStyle.secondary, custom_id="change_prefix_btn")
        async def change_prefix_btn(self, interaction: discord.Interaction, button: Button):
            await interaction.response.send_modal(PrefixModal(self))

        @discord.ui.button(label="❌ Cerrar", style=discord.ButtonStyle.danger, custom_id="close_settings")
        async def close_settings(self, interaction: discord.Interaction, button: Button):
            await interaction.message.delete()

async def setup(bot):
    await bot.add_cog(MusicUI(bot))