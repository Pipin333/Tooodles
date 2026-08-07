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


class PersistApprovalView(View):
    def __init__(self, bot, guild, requester_ctx):
        super().__init__(timeout=86400)
        self.bot = bot
        self.guild = guild
        self.requester_ctx = requester_ctx

    @discord.ui.button(label="✅ Autorizar", style=discord.ButtonStyle.success, custom_id="approve_persist")
    async def approve(self, interaction: discord.Interaction, button: Button):
        from sznUtils import set_guild_persist_enabled
        set_guild_persist_enabled(self.guild.id, True)

        for item in self.children:
            item.disabled = True

        embed = interaction.message.embeds[0]
        embed.title = "✅ Solicitud de Persistencia AUTORIZADA"
        embed.color = discord.Color.green()
        embed.set_footer(text=f"Aprobada por @{interaction.user.name}")
        await interaction.response.edit_message(embed=embed, view=self)

        try:
            await self.requester_ctx.send(
                f"🎉 ¡El dueño del bot ha **autorizado** la persistencia de colas para el servidor **{self.guild.name}**!"
            )
        except Exception:
            pass

    @discord.ui.button(label="❌ Rechazar", style=discord.ButtonStyle.danger, custom_id="deny_persist")
    async def deny(self, interaction: discord.Interaction, button: Button):
        from sznUtils import set_guild_persist_enabled
        set_guild_persist_enabled(self.guild.id, False)

        for item in self.children:
            item.disabled = True

        embed = interaction.message.embeds[0]
        embed.title = "❌ Solicitud de Persistencia RECHAZADA"
        embed.color = discord.Color.red()
        embed.set_footer(text=f"Rechazada por @{interaction.user.name}")
        await interaction.response.edit_message(embed=embed, view=self)

        try:
            await self.requester_ctx.send(
                f"❌ La solicitud de persistencia de colas para el servidor **{self.guild.name}** fue rechazada por el dueño del bot."
            )
        except Exception:
            pass


class MusicUI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.add_check(self.check_command_channel)

    def cog_unload(self):
        self.bot.remove_check(self.check_command_channel)

    async def check_command_channel(self, ctx):
        if not ctx.guild or not ctx.command:
            return True

        from sznUtils import load_config
        channel_id_str = load_config(f"cmd_channel_{ctx.guild.id}")
        if not channel_id_str or channel_id_str.strip() == "":
            return True

        if str(ctx.channel.id) == channel_id_str:
            return True

        exempt_commands = (
            "channel", "canal", "settings", "persist", "persistencia", 
            "join", "connect", "conectar", "unir", "j", "help", "ayuda",
            "reloadrecsys"
        )
        cmd_name = getattr(ctx.command, 'name', '').lower()
        invoked_with = getattr(ctx, 'invoked_with', '').lower()
        if cmd_name in exempt_commands or invoked_with in exempt_commands:
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
        import time
        start_time = getattr(player, 'current_song_start_time', None)
        elapsed_sec = int(time.time() - start_time) if start_time else 0
        duration_sec = duration

        elapsed_str = core.format_duration(elapsed_sec)
        total_str = core.format_duration(duration_sec)

        if duration_sec > 0:
            percent = min(1.0, max(0.0, elapsed_sec / duration_sec))
            bar_len = 10
            filled = int(bar_len * percent)
            progress_bar = f"`{'▬' * filled}🔘{'▬' * (bar_len - filled)}`"
            time_display = f"`{elapsed_str} / {total_str}`\n{progress_bar}"
        else:
            time_display = f"`{elapsed_str}` 🔴 EN VIVO"

        is_radio = "radio" in origin.lower()
        embed_color = 0x1db954 if "spotify" in origin.lower() else (0x8a2be2 if is_radio else 0x7d5fff)
        
        embed = discord.Embed(
            title="🎶 Ahora Reproduciendo" if not is_radio else "📻 Radio Automática",
            description=f"**[{song_title}]({song_dict.get('url', 'https://www.youtube.com')})**",
            color=embed_color
        )

        embed.add_field(name="👤 Artista", value=f"`{uploader}`", inline=True)
        embed.add_field(name="⏱️ Progreso", value=time_display, inline=True)
        
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

    @commands.command(name="persist", aliases=["persistencia"])
    async def toggle_persist_queue(self, ctx, estado: str = None):
        """Habilita o deshabilita la persistencia de colas para este servidor (Solicitud al Dueño)."""
        if not ctx.guild:
            await ctx.send("⚠️ Este comando solo se puede usar en un servidor.")
            return

        is_admin = ctx.author.guild_permissions.administrator
        is_owner = await self.bot.is_owner(ctx.author)

        if not (is_admin or is_owner):
            await ctx.send("❌ Solo los administradores del servidor o el dueño del bot pueden cambiar esta configuración.")
            return

        from sznUtils import is_guild_persist_enabled, set_guild_persist_enabled

        if not estado:
            current = is_guild_persist_enabled(ctx.guild.id)
            status_str = "🟢 Activada" if current else "🔴 Desactivada"
            await ctx.send(f"ℹ️ La persistencia de colas en este servidor está: **{status_str}**.\nUsa `{ctx.prefix}persist on` o `{ctx.prefix}persist off` para cambiarla.")
            return

        mode = estado.lower().strip()
        if mode in ("on", "activar", "activado", "true", "1"):
            if is_owner:
                set_guild_persist_enabled(ctx.guild.id, True)
                await ctx.send("✅ Persistencia de colas **activada** directamente por el dueño del bot.")
            else:
                try:
                    app_info = await self.bot.application_info()
                    owner = app_info.owner

                    embed = discord.Embed(
                        title="📩 Solicitud de Persistencia de Colas",
                        description=f"El administrador **@{ctx.author.name}** solicita activar la persistencia de colas.",
                        color=discord.Color.gold()
                    )
                    embed.add_field(name="🏰 Servidor", value=f"`{ctx.guild.name}` (`{ctx.guild.id}`)", inline=True)
                    embed.add_field(name="👤 Solicitante", value=f"@{ctx.author.name} (`{ctx.author.id}`)", inline=True)
                    embed.set_footer(text="Usa los botones para responder a esta solicitud.")

                    view = PersistApprovalView(self.bot, ctx.guild, ctx)
                    await owner.send(embed=embed, view=view)
                    await ctx.send("📩 **Solicitud enviada**: Se envió un mensaje al dueño del bot para solicitar autorización. Te notificaremos cuando responda.")
                except Exception as e:
                    print(f"⚠️ Error al enviar solicitud por DM al dueño: {e}", flush=True)
                    await ctx.send("⚠️ No se pudo enviar el mensaje privado al dueño del bot. Por favor intenta más tarde.")

        elif mode in ("off", "desactivar", "desactivado", "false", "0"):
            set_guild_persist_enabled(ctx.guild.id, False)
            await ctx.send("❌ Persistencia de colas **desactivada** para este servidor.")
        else:
            await ctx.send("⚠️ Opción no válida. Usa `on` o `off`.")

    class MusicControls(View):
        def __init__(self, core, ctx):
            super().__init__(timeout=300)
            self.core = core
            self.ctx = ctx
            self.update_toggle_buttons()

        def get_player(self):
            return self.core.get_player(self.ctx)

        def update_toggle_buttons(self):
            player = self.get_player()
            for child in self.children:
                if getattr(child, 'custom_id', None) == 'toggle_autoplay':
                    if getattr(player, 'autoplay_mode', False):
                        child.style = discord.ButtonStyle.success
                        child.label = "🤖 Autoplay: On"
                    else:
                        child.style = discord.ButtonStyle.secondary
                        child.label = "🤖 Autoplay: Off"

        def update_radio_button(self):
            self.update_toggle_buttons()

        @discord.ui.button(label="⏯️ Pausa/Reanuda", style=discord.ButtonStyle.primary, custom_id="pause_resume", row=0)
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

        @discord.ui.button(label="⏭️ Saltar", style=discord.ButtonStyle.secondary, custom_id="skip", row=0)
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

        @discord.ui.button(label="❤️ Like", style=discord.ButtonStyle.secondary, custom_id="like_song", row=0)
        async def like_song_button(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.current_song or not player.current_song.get('title'):
                await interaction.response.send_message("⚠️ No hay ninguna canción en reproducción.", ephemeral=True)
                return
            
            song_title = player.current_song['title']
            user_id = str(interaction.user.id)
            
            from database import get_db_session, UserLike, Song
            with get_db_session() as session:
                song = session.query(Song).filter_by(title=song_title).first()
                if song:
                    existing = session.query(UserLike).filter_by(user_id=user_id, song_id=song.id).first()
                    if existing:
                        session.delete(existing)
                        await interaction.response.send_message(f"❌ Eliminaste de tus favoritas: **{song_title}**", ephemeral=True)
                    else:
                        session.add(UserLike(user_id=user_id, song_id=song.id))
                        await interaction.response.send_message(f"❤️ Guardaste en tus favoritas: **{song_title}**", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❤️ Registrado me gusta para: **{song_title}**", ephemeral=True)

        @discord.ui.button(label="👎 Dislike", style=discord.ButtonStyle.secondary, custom_id="dislike_song", row=0)
        async def dislike_song_button(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if not player.current_song or not player.current_song.get('title'):
                await interaction.response.send_message("⚠️ No hay ninguna canción en reproducción.", ephemeral=True)
                return
            
            song_title = player.current_song['title']
            user_id = str(interaction.user.id)
            
            import asyncio
            from database import log_dislike_event, remove_dislike
            added = await asyncio.to_thread(log_dislike_event, user_id, song_title)
            if added:
                await interaction.response.send_message(f"👎 Marcaste como no gustada: **{song_title}**", ephemeral=True)
            else:
                await asyncio.to_thread(remove_dislike, user_id, song_title)
                await interaction.response.send_message(f"✅ Eliminaste la marca de no gustada: **{song_title}**", ephemeral=True)

        @discord.ui.button(label="🤖 Autoplay", style=discord.ButtonStyle.secondary, custom_id="toggle_autoplay", row=1)
        async def toggle_autoplay(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            player.autoplay_mode = not getattr(player, 'autoplay_mode', False)
            self.update_toggle_buttons()
            status = "activado" if player.autoplay_mode else "desactivado"
            
            await interaction.response.edit_message(view=self)
            
            if player.autoplay_mode and not player.song_queue and not (player.voice_client and player.voice_client.is_playing()):
                await self.core._fill_queue_from_recsys(self.ctx, player)
                if player.song_queue and player.voice_client and not player.voice_client.is_playing():
                    await self.core.play_next(self.ctx)
                    
            await interaction.followup.send(f"🤖 Autoplay ML **{status}**.", ephemeral=True)

        @discord.ui.button(label="⏹️ Detener", style=discord.ButtonStyle.danger, custom_id="stop", row=1)
        async def stop(self, interaction: discord.Interaction, button: Button):
            player = self.get_player()
            if player.voice_client:
                player.song_queue.clear()
                player.radio_mode = False
                player.autoplay_mode = False
                from sznMusic import cleanup_cache
                cleanup_cache()
                if player.voice_client:
                    player.voice_client.stop()
                    await player.voice_client.disconnect()
                    player.voice_client = None
                player.current_song = None
                await interaction.response.send_message("⏹️ Reproducción detenida y bot desconectado del canal.", ephemeral=True)
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
            target_channel = None
            import re
            match = re.search(r'\d+', arg)
            if match:
                try:
                    channel_id = int(match.group(0))
                    target_channel = ctx.guild.get_channel(channel_id)
                except ValueError:
                    pass
            
            if not target_channel:
                clean_name = arg.strip().lstrip("#").lower()
                target_channel = discord.utils.find(
                    lambda c: isinstance(c, discord.TextChannel) and c.name.lower() == clean_name,
                    ctx.guild.channels
                )

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

    async def _is_authorized_admin_or_trusted(self, ctx) -> bool:
        from database import is_user_trusted
        if is_user_trusted(ctx.author.id):
            return True
        is_owner = await self.bot.is_owner(ctx.author)
        if is_owner:
            return True
        if ctx.guild and ctx.author.guild_permissions.administrator:
            return True
        return False

    @commands.command(name="trusted", aliases=["trust"])
    async def trusted_cmd(self, ctx, action: str = None, user_arg: str = None):
        """Administra los usuarios de confianza (Solo Dueño del Bot)."""
        is_owner = await self.bot.is_owner(ctx.author)
        if not is_owner:
            await ctx.send("❌ Solo el **Dueño del Bot** puede ver o gestionar la lista de usuarios Trusted.")
            return

        from database import add_trusted_user, remove_trusted_user, get_trusted_users

        if action and action.lower() in ["add", "agregar", "sumar"]:
            if not user_arg:
                await ctx.send("⚠️ Uso: `td?trusted add @usuario_o_ID`")
                return
            import re
            match = re.search(r'\d+', user_arg)
            target_id = match.group(0) if match else user_arg.strip()
            
            target_user = self.bot.get_user(int(target_id)) if target_id.isdigit() else None
            uname = target_user.name if target_user else f"User_{target_id}"
            
            ok = add_trusted_user(target_id, uname)
            if ok:
                await ctx.send(f"✅ Usuario **@{uname}** (`{target_id}`) añadido como **Trusted**.")
            else:
                await ctx.send(f"ℹ️ El usuario **@{uname}** ya estaba en la lista de Trusted.")
            return

        if action and action.lower() in ["remove", "del", "delete", "eliminar"]:
            if not user_arg:
                await ctx.send("⚠️ Uso: `td?trusted remove @usuario_o_ID`")
                return
            import re
            match = re.search(r'\d+', user_arg)
            target_id = match.group(0) if match else user_arg.strip()
            
            ok = remove_trusted_user(target_id)
            if ok:
                await ctx.send(f"🗑️ Usuario con ID `{target_id}` eliminado de la lista **Trusted**.")
            else:
                await ctx.send(f"⚠️ No se encontró al usuario con ID `{target_id}` en la lista Trusted.")
            return

        users = get_trusted_users()
        embed = discord.Embed(
            title="🛡️ Usuarios de Confianza (Trusted Users)",
            description=f"Hay **{len(users)}** usuarios con acceso prioritario a comandos de Admin / Debug por DM.",
            color=0x1db954
        )
        if users:
            lines = [f"• **@{u['username']}** (`{u['user_id']}`)" for u in users]
            embed.add_field(name="📋 Lista de Trusted Users", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="ℹ️ Lista Vacía", value="Aún no hay usuarios de confianza registrados. Usa `td?trusted add @usuario`.", inline=False)
            
        await ctx.send(embed=embed)

    @commands.command(name="logs", aliases=["log"])
    async def view_logs(self, ctx, lines: int = 20):
        """Muestra las últimas N líneas del archivo de logs del bot (Admin / Trusted / DM)."""
        if not await self._is_authorized_admin_or_trusted(ctx):
            await ctx.send("❌ Este comando requiere permisos de Administrador o estar en la lista Trusted.")
            return

        import os
        log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "tooodles.log")
        
        if not os.path.exists(log_file):
            await ctx.send("📭 Aún no existe el archivo de logs.")
            return

        lines = max(1, min(lines, 50))
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                tail_lines = all_lines[-lines:] if len(all_lines) >= lines else all_lines

            content = "".join(tail_lines).strip()
            if not content:
                await ctx.send("📭 El archivo de logs está vacío.")
                return

            import re
            content = re.sub(r'\x1b\[[0-9;]*m', '', content)

            if len(content) > 1900:
                content = content[-1900:]

            embed = discord.Embed(
                title=f"📋 Últimas {len(tail_lines)} líneas de Logs",
                description=f"```text\n{content}\n```",
                color=0x7d5fff
            )
            embed.set_footer(text=f"Solicitado por @{ctx.author.name}")
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Error al leer el archivo de logs: {e}")

    @commands.command(name="debug", aliases=["status", "botstatus", "health", "diag"])
    async def debug_status(self, ctx):
        """Muestra el estado de diagnóstico y telemetría del sistema (exclusivo vía DM para usuarios Trusted)."""
        if ctx.guild is not None:
            await ctx.send("⚠️ El comando `status` / `debug` solo está disponible en Mensajes Directos (DM) para usuarios de confianza.")
            return

        if not await self._is_authorized_admin_or_trusted(ctx):
            await ctx.send("❌ Este comando requiere estar registrado en la lista de usuarios Trusted.")
            return

        import os, sys, time, glob, shutil, tempfile
        from datetime import datetime

        # 1. Espacio en Disco
        try:
            total, used, free = shutil.disk_usage(tempfile.gettempdir())
            used_pct = (used / total) * 100
            disk_status = "🟢" if used_pct < 85 else ("⚠️" if used_pct < 95 else "🔴")
            disk_str = f"{disk_status} `{used / (1024**3):.1f}GB / {total / (1024**3):.1f}GB ({used_pct:.1f}% usado)` (Libre: `{free / (1024**3):.1f}GB`)"
        except Exception as e:
            disk_str = f"⚠️ Error consultando disco: {e}"

        # 2. Modo WAL de SQLite
        try:
            from database import engine
            from sqlalchemy import text
            with engine.connect() as conn:
                journal_mode = conn.execute(text("PRAGMA journal_mode;")).scalar()
                busy_timeout = conn.execute(text("PRAGMA busy_timeout;")).scalar()
            wal_str = f"🟢 **Modo WAL Activo** (`journal_mode={journal_mode}`, `busy_timeout={busy_timeout}ms`)"
        except Exception as e:
            wal_str = f"⚠️ Error consultando BBDD: {e}"

        # 3. Motor RecSys ML
        music_cog = self.bot.get_cog("MusicCore") or self.bot.get_cog("MusicPlayerMixin")
        recsys_engine = getattr(music_cog, 'recsys_engine', None)
        if recsys_engine:
            stats = recsys_engine.stats
            from recsys.train import ARTIFACTS_PATH
            if os.path.exists(ARTIFACTS_PATH):
                mtime = datetime.fromtimestamp(os.path.getmtime(ARTIFACTS_PATH)).strftime("%Y-%m-%d %H:%M:%S")
            else:
                mtime = "Sin entrenar en disco"

            recsys_str = (
                f"📊 **Canciones indexadas**: `{stats['n_songs']}`  •  **Usuarios**: `{stats['n_users']}`\n"
                f"⚙️ **Modelos**: ALS `{'✅' if stats['has_als'] else '❌'}` | Item2Vec `{'✅' if stats['has_item2vec'] else '❌'}`\n"
                f"⏱️ **Último entrenamiento**: `{mtime}`"
            )
        else:
            recsys_str = "❌ No disponible"

        # 4. Sistema y Caché
        try:
            temp_dir = tempfile.gettempdir()
            cache_files = glob.glob(os.path.join(temp_dir, "cache_*.webm")) + glob.glob(os.path.join(temp_dir, "*.tmp"))
            cache_size_bytes = sum(os.path.getsize(f) for f in cache_files if os.path.isfile(f))
            n_guilds = len(self.bot.guilds)
            n_vc = len(self.bot.voice_clients)

            sys_str = (
                f"🔊 **VCs Conectados**: `{n_vc}` de `{n_guilds}` servidores\n"
                f"🧹 **Archivos Caché /tmp**: `{len(cache_files)}` archivos (`{cache_size_bytes / (1024**2):.1f} MB`)\n"
                f"🐍 **Python**: `{sys.version.split()[0]}`  •  **discord.py**: `{discord.__version__}`"
            )
        except Exception as e:
            sys_str = f"⚠️ Error consultando sistema: {e}"

        embed = discord.Embed(
            title="📋 Toodles — Ficha Técnica y Diagnóstico de Sistema",
            description="Información de salud del servidor, almacenamiento en disco, base de datos y modelo de recomendación ML.",
            color=0x7d5fff,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="💾 Espacio en Disco (/tmp)", value=disk_str, inline=False)
        embed.add_field(name="🗄️ Base de Datos (SQLite)", value=wal_str, inline=False)
        embed.add_field(name="🧠 Motor RecSys ML", value=recsys_str, inline=False)
        embed.add_field(name="🔊 Sistema y Caché", value=sys_str, inline=False)
        embed.set_footer(text=f"Solicitado vía DM Trusted  •  @{ctx.author.name}")

        await ctx.send(embed=embed)

    @commands.command(name="train", aliases=["recsys_train"])
    async def trigger_recsys_train(self, ctx):
        """Gatilla manualmente el reentrenamiento offline del RecSys (Admin / Trusted / DM)."""
        if not await self._is_authorized_admin_or_trusted(ctx):
            await ctx.send("❌ Este comando requiere permisos de Administrador o estar en la lista Trusted.")
            return

        msg = await ctx.send("🧠 Iniciando entrenamiento offline del RecSys Híbrido (256 dims, 50 ALS iters, 100 Item2Vec epochs)...")
        try:
            import subprocess
            res = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "recsys.train"],
                capture_output=True,
                text=True,
                timeout=60
            )
            if res.returncode == 0:
                music_cog = self.bot.get_cog("Music")
                if music_cog and getattr(music_cog, 'recsys', None):
                    music_cog.recsys.reload_if_updated()
                await msg.edit(content="✅ **Entrenamiento del RecSys completado y motor recargado exitosamente.**")
            else:
                await msg.edit(content=f"⚠️ Entrenamiento finalizó con código {res.returncode}:\n```text\n{res.stderr[:500]}\n```")
        except Exception as e:
            await ctx.send(f"❌ Error al ejecutar entrenamiento: {e}")

    @commands.command(name="help", aliases=["ayuda", "h"])
    async def help_cmd(self, ctx):
        """Muestra la guía contextual de comandos del bot (Servidor vs DM)."""
        is_dm = ctx.guild is None
        from sznUtils import load_config

        if is_dm:
            embed = discord.Embed(
                title="📬 Toodles — Guía de Comandos Directos (DM)",
                description="En Mensajes Directos tienes acceso a comandos de administración, diagnóstico y control del sistema.\n\n*Tip: No necesitas usar prefijo en DM; puedes escribir los comandos directamente (ej: `logs`, `debug`, `train`).*",
                color=0x7d5fff,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="📋 Diagnóstico y Logs",
                value="• `logs [N]` — Muestra las últimas N líneas del archivo de logs (`tooodles.log`).\n"
                      "• `status` / `debug` — Ficha técnica de estado en tiempo real (salud de disco, modo WAL de SQLite, caché `/tmp` y RecSys).",
                inline=False
            )
            embed.add_field(
                name="🧠 Inteligencia RecSys & Modelos",
                value="• `train` — Gatilla el reentrenamiento offline inmediato del RecSys Híbrido (520 dims).\n"
                      "• `reloadrecsys` — Recarga los artefactos entrenados en caliente.",
                inline=False
            )
            embed.add_field(
                name="🛡️ Seguridad & Permisos",
                value="• `trusted list` — Muestra la lista de usuarios de confianza.\n"
                      "• `trusted add @user` — Agrega un usuario a la lista Trusted.\n"
                      "• `trusted remove @user` — Elimina un usuario de la lista Trusted.",
                inline=False
            )
            embed.add_field(
                name="📚 Colección",
                value="• `playlists` — Abre el gestor interactivo de playlists guardadas.",
                inline=False
            )
            embed.set_footer(text=f"Sesión Privada  •  @{ctx.author.name}")
        else:
            prefix = load_config(f"prefix_{ctx.guild.id}") or "td?"
            embed = discord.Embed(
                title=f"🎵 Toodles — Comandos del Servidor ({ctx.guild.name})",
                description=f"Prefijo actual: `{prefix}`  •  Bot de música con recomendador por IA.",
                color=0x00d2d3,
                timestamp=discord.utils.utcnow()
            )
            embed.add_field(
                name="🎧 Reproducción de Música",
                value=f"• `{prefix}play <canción / URL>` — Reproduce o añade a la cola (YouTube/Spotify).\n"
                      f"• `{prefix}skip` (`s`) — Salta la canción actual.\n"
                      f"• `{prefix}queue` (`q`) — Muestra la cola interactiva paginada.\n"
                      f"• `{prefix}stop` / `{prefix}clear` — Detiene la música o limpia la cola.",
                inline=False
            )
            embed.add_field(
                name="📻 Recomendador IA & Feedback",
                value=f"• `{prefix}radio` — Activa/desactiva el modo Autoplay por IA.\n"
                      f"• `{prefix}like` — Registra tu Like en el sistema de recomendación.\n"
                      f"• `{prefix}dislike` — Registra tu Dislike y salta la canción.",
                inline=False
            )
            embed.add_field(
                name="📚 Colección de Playlists",
                value=f"• `{prefix}playlists` (`pl`) — Despliega el menú interactivo para seleccionar o guardar playlists.",
                inline=False
            )
            embed.add_field(
                name="⚙️ Configuración del Servidor (Admin)",
                value=f"• `{prefix}channel aqui` — Restringe los comandos a este canal de texto.\n"
                      f"• `{prefix}settings` — Panel de control interactivo del bot.",
                inline=False
            )
            embed.set_footer(text=f"Solicitado por @{ctx.author.name}")

        await ctx.send(embed=embed)

    @commands.command(name="playlists", aliases=["pl", "playlist"])
    async def playlists_cmd(self, ctx, action: str = None, *, args: str = None):
        """Muestra o administra las playlists guardadas del servidor."""
        if not ctx.guild:
            await ctx.send("⚠️ Este comando solo funciona en servidores.")
            return

        guild_id = ctx.guild.id
        from database import add_saved_playlist, remove_saved_playlist, get_saved_playlists
        from sznUtils import get_playlist_title
        
        if action and action.lower() in ["add", "agregar", "guardar"]:
            if not args:
                await ctx.send("⚠️ Uso: `td?playlist add <URL> [Alias opcional]`")
                return
            
            parts = args.strip().split(" ", 1)
            url = parts[0]
            alias = parts[1] if len(parts) > 1 else None

            msg = await ctx.send("🔍 Obteniendo nombre oficial de la playlist...")
            
            official_title = await get_playlist_title(url)
            final_name = alias if alias else official_title

            add_saved_playlist(guild_id, ctx.author.id, final_name, url)
            await msg.edit(content=f"✅ Playlist **{final_name}** guardada correctamente.")
            return

        if action and action.lower() in ["remove", "del", "delete", "eliminar"]:
            if not args:
                await ctx.send("⚠️ Uso: `td?playlist remove <Nombre o Alias>`")
                return
            ok = remove_saved_playlist(guild_id, args)
            if ok:
                await ctx.send(f"🗑️ Playlist **{args}** eliminada de la colección.")
            else:
                await ctx.send(f"⚠️ No se encontró la playlist **{args}**.")
            return

        pls = get_saved_playlists(guild_id)
        embed = discord.Embed(
            title="📚 Colección de Playlists del Servidor",
            description=f"Hay **{len(pls)}** playlists guardadas.\nSelecciona una en el menú desplegable para reproducirla de inmediato.",
            color=0x00d2d3
        )
        if pls:
            pl_lines = [f"• **{p['name']}** — `{p['url'][:45]}...`" for p in pls[:10]]
            embed.add_field(name="📋 Playlists Guardadas", value="\n".join(pl_lines), inline=False)
        else:
            embed.add_field(name="ℹ️ Colección vacía", value="Aún no hay playlists guardadas. Usa el botón `➕ Agregar Playlist` o `td?playlist add <URL>`.", inline=False)

        view = PlaylistsView(guild_id, self.bot)
        await ctx.send(embed=embed, view=view)


class AddPlaylistModal(Modal, title="➕ Guardar Nueva Playlist"):
    url_input = TextInput(
        label="Link de YouTube o Spotify",
        placeholder="https://www.youtube.com/playlist?list=... o Spotify URL",
        min_length=10,
        max_length=300,
        required=True
    )
    alias_input = TextInput(
        label="Alias Opcional (ej: Cumbia 90s, Mambo)",
        placeholder="Déjalo vacío para usar el nombre oficial automático",
        min_length=0,
        max_length=40,
        required=False
    )

    def __init__(self, parent_view=None):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        url = self.url_input.value.strip()
        alias = self.alias_input.value.strip() if self.alias_input.value else None
        
        from database import add_saved_playlist
        from sznUtils import get_playlist_title

        official_title = await get_playlist_title(url)
        final_name = alias if alias else official_title
        add_saved_playlist(interaction.guild.id, interaction.user.id, final_name, url)
        
        await interaction.followup.send(
            f"✅ Playlist **{final_name}** guardada correctamente.",
            ephemeral=True
        )
        if self.parent_view:
            await self.parent_view.refresh(interaction)


class PlaylistSelect(discord.ui.Select):
    def __init__(self, playlists):
        options = []
        for pl in playlists[:25]:
            options.append(discord.SelectOption(
                label=pl['name'][:100],
                value=pl['url'],
                description=f"Link: {pl['url'][:45]}...",
                emoji="🎵"
            ))
        super().__init__(
            placeholder="🔍 Selecciona una playlist para reproducir...",
            min_values=1,
            max_values=1,
            options=options if options else [discord.SelectOption(label="Sin playlists guardadas", value="none")]
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("⚠️ No hay playlists guardadas en este servidor.", ephemeral=True)
            return

        selected_url = self.values[0]
        await interaction.response.send_message(f"🚀 Reproduciendo playlist seleccionada: {selected_url}", ephemeral=True)
        
        try:
            music_cog = interaction.client.get_cog("MusicCore") or interaction.client.get_cog("Music")
            if music_cog:
                ctx = await interaction.client.get_context(interaction.message)
                ctx.author = interaction.user
                await music_cog.play(ctx, query=selected_url)
            else:
                print("⚠️ [PLAYLIST SELECT] MusicCore cog no encontrado.", flush=True)
        except Exception as e:
            print(f"❌ [PLAYLIST SELECT ERROR] {e}", flush=True)


class EditPlaylistModal(Modal, title="✏️ Editar Playlist Guardada"):
    def __init__(self, old_name: str, current_url: str, parent_view=None):
        super().__init__()
        self.old_name = old_name
        self.parent_view = parent_view

        self.name_input = TextInput(
            label="Nombre / Alias de la Playlist",
            default=old_name,
            min_length=1,
            max_length=100,
            required=True
        )
        self.url_input = TextInput(
            label="URL de YouTube o Spotify",
            default=current_url,
            min_length=10,
            max_length=300,
            required=True
        )
        self.add_item(self.name_input)
        self.add_item(self.url_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new_name = self.name_input.value.strip()
        new_url = self.url_input.value.strip()

        from database import remove_saved_playlist, add_saved_playlist
        remove_saved_playlist(interaction.guild.id, self.old_name)
        add_saved_playlist(interaction.guild.id, interaction.user.id, new_name, new_url)

        await interaction.followup.send(f"✏️ Playlist **{new_name}** actualizada correctamente.", ephemeral=True)
        if self.parent_view:
            self.parent_view.mode = "play"
            await self.parent_view.refresh(interaction)


class EditPlaylistSelect(discord.ui.Select):
    def __init__(self, playlists, parent_view=None):
        self.parent_view = parent_view
        self.playlists_map = {pl['name']: pl['url'] for pl in playlists[:25]}
        options = []
        for pl in playlists[:25]:
            options.append(discord.SelectOption(
                label=pl['name'][:100],
                value=pl['name'],
                description=f"Editar: {pl['url'][:40]}...",
                emoji="✏️"
            ))
        super().__init__(
            placeholder="✏️ Selecciona la playlist que deseas editar...",
            min_values=1,
            max_values=1,
            options=options if options else [discord.SelectOption(label="Sin playlists guardadas", value="none")]
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("⚠️ No hay playlists guardadas para editar.", ephemeral=True)
            return

        target_name = self.values[0]
        current_url = self.playlists_map.get(target_name, "")
        modal = EditPlaylistModal(old_name=target_name, current_url=current_url, parent_view=self.parent_view)
        await interaction.response.send_modal(modal)


class DeletePlaylistSelect(discord.ui.Select):
    def __init__(self, playlists, parent_view=None):
        self.parent_view = parent_view
        options = []
        for pl in playlists[:25]:
            options.append(discord.SelectOption(
                label=pl['name'][:100],
                value=pl['name'],
                description=f"Eliminar: {pl['url'][:40]}...",
                emoji="🗑️"
            ))
        super().__init__(
            placeholder="🗑️ Selecciona la playlist que deseas eliminar...",
            min_values=1,
            max_values=1,
            options=options if options else [discord.SelectOption(label="Sin playlists guardadas", value="none")]
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("⚠️ No hay playlists guardadas para eliminar.", ephemeral=True)
            return

        target_name = self.values[0]
        from database import remove_saved_playlist
        ok = remove_saved_playlist(interaction.guild.id, target_name)
        if ok:
            await interaction.response.send_message(f"🗑️ Playlist **{target_name}** eliminada de la colección.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ No se pudo eliminar la playlist **{target_name}**.", ephemeral=True)

        if self.parent_view:
            await self.parent_view.refresh(interaction)


class PlaylistsView(View):
    def __init__(self, guild_id, bot):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.bot = bot
        self.mode = "play"
        self.update_components()

    def update_components(self):
        self.clear_items()
        from database import get_saved_playlists
        pls = get_saved_playlists(self.guild_id)
        if pls:
            if self.mode == "delete":
                self.add_item(DeletePlaylistSelect(pls, parent_view=self))
            elif self.mode == "edit":
                self.add_item(EditPlaylistSelect(pls, parent_view=self))
            else:
                self.add_item(PlaylistSelect(pls))

        btn_add = Button(label="➕ Agregar", style=discord.ButtonStyle.success)
        btn_add.callback = self.on_add_click
        self.add_item(btn_add)

        if self.mode == "edit":
            btn_edit = Button(label="◀️ Reproducir", style=discord.ButtonStyle.primary)
            btn_edit.callback = self.on_toggle_edit_mode
            self.add_item(btn_edit)
        else:
            btn_edit = Button(label="✏️ Editar", style=discord.ButtonStyle.secondary)
            btn_edit.callback = self.on_toggle_edit_mode
            self.add_item(btn_edit)

        if self.mode == "delete":
            btn_del = Button(label="◀️ Reproducir", style=discord.ButtonStyle.primary)
            btn_del.callback = self.on_toggle_delete_mode
            self.add_item(btn_del)
        else:
            btn_del = Button(label="🗑️ Eliminar", style=discord.ButtonStyle.danger)
            btn_del.callback = self.on_toggle_delete_mode
            self.add_item(btn_del)

    async def on_add_click(self, interaction: discord.Interaction):
        modal = AddPlaylistModal(parent_view=self)
        await interaction.response.send_modal(modal)

    async def on_toggle_edit_mode(self, interaction: discord.Interaction):
        self.mode = "play" if self.mode == "edit" else "edit"
        await self.refresh(interaction)

    async def on_toggle_delete_mode(self, interaction: discord.Interaction):
        self.mode = "play" if self.mode == "delete" else "delete"
        await self.refresh(interaction)

    async def refresh(self, interaction: discord.Interaction):
        self.update_components()
        from database import get_saved_playlists
        pls = get_saved_playlists(self.guild_id)
        if self.mode == "delete":
            mode_desc = "Modo Eliminar: selecciona una playlist en el menú para borrarla."
            color = 0xff3f34
        elif self.mode == "edit":
            mode_desc = "Modo Editar: selecciona una playlist en el menú para modificar su nombre o enlace."
            color = 0xffa801
        else:
            mode_desc = "Selecciona una playlist en el menú desplegable para reproducirla de inmediato."
            color = 0x00d2d3

        embed = discord.Embed(
            title="📚 Colección de Playlists del Servidor",
            description=f"Hay **{len(pls)}** playlists guardadas.\n{mode_desc}",
            color=color
        )
        if pls:
            pl_lines = [f"• **{p['name']}** — `{p['url'][:45]}...`" for p in pls[:10]]
            embed.add_field(name="📋 Playlists Guardadas", value="\n".join(pl_lines), inline=False)
        else:
            embed.add_field(name="ℹ️ Colección vacía", value="Aún no hay playlists guardadas. Usa el botón `➕ Agregar`.", inline=False)

        try:
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception:
            try:
                await interaction.message.edit(embed=embed, view=self)
            except Exception:
                pass

async def setup(bot):
    await bot.add_cog(MusicUI(bot))