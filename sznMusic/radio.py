import asyncio
import random
from database import get_db_session, Song
from .player import _clean_title_for_search, GuildPlayer

GENRE_EXPANSION = {
    # Hip Hop / Rap / Trap
    "chilean hip hop": ["latin hip hop", "spanish hip hop", "argentinian hip hop", "boom bap", "mexican hip hop", "rap latina"],
    "chilean rap": ["latin hip hop", "spanish hip hop", "argentinian hip hop", "boom bap", "rap latina"],
    "latin hip hop": ["chilean hip hop", "spanish hip hop", "argentinian hip hop", "rap latino", "mexican hip hop", "boom bap"],
    "rap latino": ["latin hip hop", "chilean hip hop", "mexican hip hop", "argentinian hip hop", "rap latina"],
    "trap latino": ["reggaeton", "urbano latino", "trap argentino", "dembow", "rkt"],
    "reggaeton": ["trap latino", "urbano latino", "dembow", "latin pop", "rkt"],
    "urbano latino": ["reggaeton", "trap latino", "latin pop", "dembow"],
    "trap argentino": ["trap latino", "urbano latino", "rkt"],
    
    # Rock / Indie / Alternative (Chilean, Argentinian, Mexican, Spanish)
    "chilean rock": ["rock en espanol", "argentinian rock", "latin alternative", "mexican rock", "spanish rock", "chilean indie"],
    "chilean pop": ["chilean indie", "latin alternative", "latin pop", "synthpop"],
    "chilean indie": ["chilean pop", "latin alternative", "indie pop", "synthpop"],
    "rock en espanol": ["chilean rock", "argentinian rock", "mexican rock", "spanish rock", "latin alternative", "rock nacional"],
    "argentinian rock": ["rock en espanol", "chilean rock", "mexican rock", "spanish rock", "latin alternative", "rock nacional"],
    "rock nacional": ["argentinian rock", "rock en espanol", "latin alternative", "chilean rock"],
    "mexican rock": ["rock en espanol", "latin alternative", "argentinian rock", "spanish rock"],
    "spanish rock": ["rock en espanol", "latin alternative", "argentinian rock", "mexican rock"],
    "latin alternative": ["rock en espanol", "chilean rock", "argentinian rock", "mexican rock", "spanish rock", "chilean indie"],
    
    # Reggae / Ska
    "reggae en espanol": ["reggae fusion", "latin alternative", "ska", "spanish reggae"],
    "reggae fusion": ["reggae en espanol", "ska", "dancehall"],
    "ska": ["ska argentino", "reggae en espanol", "latin alternative"],
    
    # Pop / Synthpop / Indie Pop
    "synthpop": ["indie pop", "latin alternative", "dance pop"],
    "indie pop": ["synthpop", "latin alternative", "indie rock"],
    
    # Metal
    "metal": ["heavy metal", "thrash metal", "groove metal", "latin metal"],
    "thrash metal": ["speed metal", "heavy metal", "death metal"],
    "heavy metal": ["hard rock", "thrash metal", "power metal"]
}


class MusicRadioMixin:
    """Métodos del algoritmo de Radio, expansión de géneros y autorelleno RecSys ML."""
    async def expand_radio_queue(self, ctx, seed_id: str | None = None, seed_title: str | None = None) -> bool:
        player = self.get_player(ctx)
        target_title = seed_title or getattr(player, 'last_played_title', None)
        if not target_title and player.current_song:
            target_title = player.current_song.get('title')

        if player.current_song and player.current_song.get('title'):
            curr_lower = player.current_song['title'].lower()
            if curr_lower not in player.radio_history:
                player.radio_history.append(curr_lower)
        if target_title:
            tt_lower = target_title.lower()
            if tt_lower not in player.radio_history:
                player.radio_history.append(tt_lower)

        queued_titles = [s['title'].lower() for s in player.song_queue if s.get('title')]
        clean_song, extracted_artist = _clean_title_for_search(target_title) if target_title else ("", None)

        print(f"📻 [RADIO DEBUG] [{ctx.guild.name}] target_title='{target_title}'", flush=True)
        recommended_titles = []

        if self.sp and target_title:
            try:
                search_q = f"artist:{extracted_artist} track:{clean_song}" if extracted_artist else clean_song
                search_result = self.sp.search(q=search_q, type='track', limit=1)

                seed_artist = extracted_artist
                seed_artist_id = None
                if search_result and search_result.get('tracks', {}).get('items'):
                    found_track = search_result['tracks']['items'][0]
                    seed_artist = found_track['artists'][0]['name']
                    seed_artist_id = found_track['artists'][0]['id']
                else:
                    if seed_artist:
                        try:
                            artist_res = self.sp.search(q=f"artist:{seed_artist}", type='artist', limit=1)
                            if artist_res and artist_res.get('artists', {}).get('items'):
                                seed_artist_id = artist_res['artists']['items'][0]['id']
                                seed_artist = artist_res['artists']['items'][0]['name']
                        except Exception as e:
                            print(f"⚠️ [RADIO] Búsqueda de artista falló: {e}", flush=True)

                if seed_artist:
                    # Capa 1: Mismo artista
                    try:
                        artist_search = self.sp.search(q=f"artist:{seed_artist}", type='track', limit=30)
                        artist_tracks = artist_search.get('tracks', {}).get('items', [])
                        random.shuffle(artist_tracks)
                        for t in artist_tracks:
                            full_name = f"{t['name']} - {t['artists'][0]['name']}"
                            if self._radio_is_unique(player, full_name, recommended_titles, queued_titles):
                                recommended_titles.append(full_name)
                                if len(recommended_titles) >= 2:
                                    break
                    except Exception as e:
                        print(f"⚠️ [RADIO] Capa 1 falló: {e}", flush=True)

                    # Capa 2: Géneros consolidados
                    if len(recommended_titles) < 5:
                        try:
                            genres = []
                            artist_ids = getattr(player, 'recent_artist_ids', [])
                            if artist_ids:
                                try:
                                    artists_info = self.sp.artists(artist_ids)
                                    for artist in artists_info.get('artists', []):
                                        if artist and artist.get('genres'):
                                            genres.extend(artist['genres'])
                                except Exception as hist_err:
                                    print(f"⚠️ [RADIO] Error al consultar historial de artistas: {hist_err}", flush=True)

                            if not genres and seed_artist_id:
                                try:
                                    artist_info = self.sp.artist(seed_artist_id)
                                    genres = artist_info.get('genres', [])
                                except Exception as fallback_err:
                                    print(f"⚠️ [RADIO] Error en fallback de artista: {fallback_err}", flush=True)

                            expanded_genres = self._expand_genres(list(set(genres)))
                            if expanded_genres:
                                shuffled_genres = list(expanded_genres)
                                random.shuffle(shuffled_genres)
                                
                                for gen_name in shuffled_genres:
                                    if len(recommended_titles) >= 5:
                                        break
                                    try:
                                        gen_search = self.sp.search(q=f'genre:"{gen_name}"', type='track', limit=20)
                                        gen_tracks = gen_search.get('tracks', {}).get('items', [])
                                        if gen_tracks:
                                            gen_tracks = sorted(gen_tracks, key=lambda x: x.get('popularity', 0), reverse=True)
                                            popular_subset = gen_tracks[:10]
                                            random.shuffle(popular_subset)

                                            for t in popular_subset:
                                                t_artist = t['artists'][0]['name']
                                                already_rec = [r.split(" - ")[1].lower().strip() for r in recommended_titles if " - " in r]
                                                if t_artist.lower().strip() in already_rec:
                                                    continue

                                                full_name = f"{t['name']} - {t_artist}"
                                                if t_artist.lower() != seed_artist.lower() and self._radio_is_unique(player, full_name, recommended_titles, queued_titles):
                                                    recommended_titles.append(full_name)
                                                    break
                                    except Exception:
                                        pass
                        except Exception as artist_info_err:
                            print(f"⚠️ [RADIO] Error procesando géneros: {artist_info_err}", flush=True)

                    # Capa 3: Similar queries
                    if len(recommended_titles) < 5:
                        similar_queries = [
                            f"{seed_artist} similar artists",
                            f"fans also like {seed_artist}",
                            f"{clean_song} similar",
                        ]
                        for sq in similar_queries:
                            if len(recommended_titles) >= 5:
                                break
                            try:
                                sim_results = self.sp.search(q=sq, type='track', limit=20)
                                sim_tracks = sim_results.get('tracks', {}).get('items', [])
                                if sim_tracks:
                                    sim_tracks = sorted(sim_tracks, key=lambda x: x.get('popularity', 0), reverse=True)
                                    popular_subset = sim_tracks[:10]
                                    random.shuffle(popular_subset)

                                    for t in popular_subset:
                                        t_artist = t['artists'][0]['name']
                                        already_rec = [r.split(" - ")[1].lower().strip() for r in recommended_titles if " - " in r]
                                        if t_artist.lower().strip() in already_rec:
                                            continue

                                        full_name = f"{t['name']} - {t_artist}"
                                        if t_artist.lower() != seed_artist.lower() and self._radio_is_unique(player, full_name, recommended_titles, queued_titles):
                                            recommended_titles.append(full_name)
                                            break
                            except Exception:
                                pass
            except Exception as sp_err:
                print(f"⚠️ [RADIO] Error general de Spotify: {sp_err}", flush=True)

        # Fallback BD
        if len(recommended_titles) < 5:
            with get_db_session() as session:
                songs = session.query(Song).all()
                if songs:
                    shuffled_db = list(songs)
                    random.shuffle(shuffled_db)
                    for s in shuffled_db:
                        if self._radio_is_unique(player, s.title, recommended_titles, queued_titles):
                            recommended_titles.append(s.title)
                            if len(recommended_titles) >= 5:
                                break

        if not recommended_titles:
            await ctx.send("⚠️ No se pudieron generar recomendaciones para la radio.")
            player.radio_mode = False
            return False

        await ctx.send(f"📻 **Radio Automática**: Añadidos **{len(recommended_titles)}** temas sugeridos a la cola.")

        for title in recommended_titles:
            player.radio_history.append(title.lower())
            if len(player.radio_history) > 30:
                player.radio_history.pop(0)

            song_dict = {
                'title': title,
                'url': None,
                'duration': 0,
                'uploader': 'Radio Automática',
                'origin': '📻 Radio Automática',
                'guild_id': str(ctx.guild.id)
            }
            player.song_queue.append(song_dict)

        self.schedule_queue_optimizations(ctx)
        return True

    def _radio_is_unique(self, player: GuildPlayer, candidate: str, recommended: list, queued: list) -> bool:
        c_lower = candidate.lower()
        junk_keywords = [
            "chapter", "episode", "audiobook", "audio book", "podcast",
            "imagination audio", "volume", "vol.", "track", "intro",
            "outro", "skit", "interlude", "remastered"
        ]
        if any(jk in c_lower for jk in junk_keywords):
            return False

        c_song_name = c_lower.split(" - ")[0].strip() if " - " in c_lower else c_lower
        if c_song_name.isdigit():
            return False

        for h in player.radio_history:
            h_song_name = h.split(" - ")[0].strip() if " - " in h else h
            if c_song_name in h_song_name or h_song_name in c_song_name:
                return False

        for q in queued:
            q_song_name = q.split(" - ")[0].strip() if " - " in q else q
            if c_song_name in q_song_name or q_song_name in c_song_name:
                return False

        for r in recommended:
            r_lower = r.lower()
            r_song_name = r_lower.split(" - ")[0].strip() if " - " in r_lower else r_lower
            if c_song_name in r_song_name or r_song_name in c_song_name:
                return False

        return True

    def _expand_genres(self, genres_list: list[str]) -> list[str]:
        expanded = set()
        for g in genres_list:
            g_lower = g.lower().strip()
            expanded.add(g_lower)
            if g_lower in GENRE_EXPANSION:
                expanded.update(GENRE_EXPANSION[g_lower])
            else:
                if "rock" in g_lower:
                    expanded.update(["rock en espanol", "latin alternative", "classic rock"])
                if "hip hop" in g_lower or "rap" in g_lower:
                    expanded.update(["latin hip hop", "boom bap", "spanish hip hop"])
                if "pop" in g_lower:
                    expanded.update(["latin pop", "indie pop", "synthpop"])
                if "reggaeton" in g_lower or "trap" in g_lower or "urban" in g_lower:
                    expanded.update(["urbano latino", "trap latino", "reggaeton"])
                if "metal" in g_lower:
                    expanded.update(["heavy metal", "thrash metal", "hard rock"])
        return list(expanded)

    def record_played_track(self, title: str, target):
        player = self.get_player(target)
        if not self.sp or not title:
            return

        async def _async_lookup():
            try:
                clean_song, extracted_artist = _clean_title_for_search(title)
                search_q = f"artist:{extracted_artist} track:{clean_song}" if extracted_artist else clean_song
                res = self.sp.search(q=search_q, type='track', limit=1)
                if res and res.get('tracks', {}).get('items'):
                    track_data = res['tracks']['items'][0]
                    artist_id = track_data['artists'][0]['id']
                    spotify_id = track_data['id']
                    popularity = track_data.get('popularity', 0)

                    if not player.recent_artist_ids or player.recent_artist_ids[-1] != artist_id:
                        if artist_id in player.recent_artist_ids:
                            player.recent_artist_ids.remove(artist_id)
                        player.recent_artist_ids.append(artist_id)
                        if len(player.recent_artist_ids) > 5:
                            player.recent_artist_ids.pop(0)
                        print(f"📻 [RADIO PROFILE] Artista registrado para guild {player.guild_id}: {track_data['artists'][0]['name']} (Total: {len(player.recent_artist_ids)})", flush=True)

                    try:
                        artist_info = self.sp.artist(artist_id)
                        genres_list = artist_info.get('genres', [])
                        genres_str = ",".join(genres_list) if genres_list else ""
                        
                        from database import update_song_features
                        update_song_features(
                            title=title,
                            spotify_id=spotify_id,
                            genres=genres_str,
                            popularity=popularity
                        )
                    except Exception as meta_err:
                        print(f"⚠️ Error al guardar metadatos de telemetría de recomendación en BD: {meta_err}", flush=True)
            except Exception as e:
                print(f"⚠️ Error registrando artista reciente para radio: {e}", flush=True)

        self.bot.loop.create_task(_async_lookup())

    async def _fill_queue_from_recsys(self, ctx, player):
        """Llena la cola usando el motor de recomendación ML cuando autoplay está activo."""
        try:
            self.recsys_engine.reload_if_updated()
            
            user_ids = []
            if ctx.author.voice and ctx.author.voice.channel:
                user_ids = [str(m.id) for m in ctx.author.voice.channel.members if not m.bot]
            
            recommendations = []
            
            if self.recsys_engine.loaded:
                recommendations = await asyncio.to_thread(
                    self.recsys_engine.get_autoplay_recommendations,
                    current_title=player.last_played_title,
                    user_ids=user_ids if user_ids else None,
                    recent_titles=list(player.radio_history),
                    temperature=getattr(player, 'radio_temperature', 0.75),
                    n=5
                )
            
            if not recommendations:
                from database import get_cold_start_recommendations
                guild_id = str(player.guild_id) if player.guild_id else None
                recommendations = await asyncio.to_thread(
                    get_cold_start_recommendations,
                    guild_id=guild_id,
                    exclude_titles=list(player.radio_history),
                    limit=5
                )
            
            if not recommendations:
                return
            
            added_count = 0
            for rec in recommendations:
                title = rec.get('title', '')
                if not title:
                    continue
                
                if any(title.lower() == s.get('title', '').lower() for s in player.song_queue):
                    continue
                
                song_dict = {
                    'title': title,
                    'url': None,
                    'duration': 0,
                    'uploader': rec.get('artist', ''),
                    'origin': '🤖 Autoplay ML',
                    'user_id': None,
                    'username': 'RecSys',
                    'guild_id': str(player.guild_id),
                    'id': None,
                    'cache_path': None
                }
                player.song_queue.append(song_dict)
                player.radio_history.append(title.lower())
                if len(player.radio_history) > 30:
                    player.radio_history.pop(0)
                added_count += 1
            
            if added_count > 0:
                source_info = recommendations[0].get('source', 'ml')
                print(f"🤖 [RecSys] {added_count} canciones añadidas a la cola via {source_info}", flush=True)
                
        except Exception as e:
            print(f"⚠️ [RecSys] Error en autoplay ML: {e}", flush=True)
