import os
from contextlib import contextmanager
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# URL de la base de datos desde las variables de entorno (Railway / local fallback)
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DATABASE_URL = "sqlite:///tooodles.db"

# Crear el motor de la base de datos
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update({"pool_size": 5, "max_overflow": 10})

engine = create_engine(DATABASE_URL, **engine_kwargs)

# Base compartida para todos los modelos
Base = declarative_base()

# Caché en memoria para canciones populares
cached_songs = {}

# Modelo para canciones
class Song(Base):
    __tablename__ = 'songs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False, index=True)
    url = Column(String)
    artist = Column(String, index=True)
    duration = Column(Integer)
    played_count = Column(Integer, default=0, index=True)
    spotify_id = Column(String, index=True)
    genres = Column(String)  # Guardado como string separado por comas
    popularity = Column(Integer)
    danceability = Column(Float, nullable=True)
    energy = Column(Float, nullable=True)
    valence = Column(Float, nullable=True)
    tempo = Column(Float, nullable=True)
    acousticness = Column(Float, nullable=True)
    instrumentalness = Column(Float, nullable=True)
    liveness = Column(Float, nullable=True)
    speechiness = Column(Float, nullable=True)

    def __repr__(self):
        return f"<Song(id={self.id}, title={self.title}, artist={self.artist}, played_count={self.played_count})>"

# Modelo para configuración del bot (ej. cookies, settings)
class AppConfig(Base):
    __tablename__ = "config"

    key = Column(String, primary_key=True)
    value = Column(String)

# Modelo para me gustas / canciones favoritas por usuario
class UserLike(Base):
    __tablename__ = 'likes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    song_id = Column(Integer, ForeignKey('songs.id'), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Modelo para no me gusta / feedback negativo explícito por usuario
class UserDislike(Base):
    __tablename__ = 'dislikes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    song_id = Column(Integer, ForeignKey('songs.id'), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Modelo para playlists guardadas por el servidor
class SavedPlaylist(Base):
    __tablename__ = 'saved_playlists'

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Modelo para registro de reproducción y telemetría de recomendador
class PlayLog(Base):
    __tablename__ = 'play_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, index=True)
    username = Column(String)
    song_id = Column(Integer, ForeignKey('songs.id'), nullable=False)
    guild_id = Column(String, index=True)
    played_at = Column(DateTime, default=datetime.utcnow)
    listened_duration = Column(Integer)  # en segundos
    completed = Column(Integer)  # 1 si terminó completo, 0 si fue skip
    skipped_at = Column(Integer)  # segundos transcurridos al saltar

# Creador de sesiones
SessionFactory = sessionmaker(bind=engine)

@contextmanager
def get_db_session():
    """Context manager para obtener una sesión segura por transacción."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def setup_database():
    """Crea todas las tablas si no existen y maneja migraciones de columnas de forma segura."""
    from sqlalchemy import text
    Base.metadata.create_all(engine)
    
    # Manejar migración de nuevas columnas con transacciones explícitas
    with engine.begin() as conn:
        audio_cols = [
            ("spotify_id", "VARCHAR"), ("genres", "VARCHAR"), ("popularity", "INTEGER"),
            ("danceability", "FLOAT"), ("energy", "FLOAT"), ("valence", "FLOAT"),
            ("tempo", "FLOAT"), ("acousticness", "FLOAT"), ("instrumentalness", "FLOAT"),
            ("liveness", "FLOAT"), ("speechiness", "FLOAT")
        ]
        for col_name, col_type in audio_cols:
            try:
                conn.execute(text(f"ALTER TABLE songs ADD COLUMN {col_name} {col_type}"))
                print(f"🗄️ Columna '{col_name}' agregada exitosamente a 'songs'.")
            except Exception:
                pass  # La columna ya existe
            
    print("🗄️ Tablas de base de datos creadas/verificadas correctamente.")

def add_or_update_song(title, url=None, artist=None, duration=0):
    """Agrega una canción nueva si no existe o retorna la existente."""
    with get_db_session() as session:
        existing_song = session.query(Song).filter_by(title=title, artist=artist).first()
        if existing_song:
            return existing_song

        new_song = Song(
            title=title,
            url=url,
            artist=artist,
            duration=duration,
            played_count=0
        )
        session.add(new_song)
        session.flush()
        session.refresh(new_song)
        return new_song

def get_top_songs(guild_id=None, limit=10, offset=0):
    """Obtiene las canciones más reproducidas por servidor (guild_id) o globalmente."""
    with get_db_session() as session:
        if guild_id:
            from sqlalchemy import func
            results = (
                session.query(Song.title, func.count(PlayLog.id).label('played_count'))
                .join(PlayLog, Song.id == PlayLog.song_id)
                .filter(PlayLog.guild_id == str(guild_id))
                .group_by(Song.id, Song.title)
                .order_by(func.count(PlayLog.id).desc())
                .limit(limit)
                .offset(offset)
                .all()
            )
            if results:
                return results

        # Fallback global si no hay guild_id o no hay historial de servidor aún
        return (
            session.query(Song.title, Song.played_count)
            .order_by(Song.played_count.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

def get_recent_history(guild_id=None, limit=10):
    """Obtiene el historial reciente de reproducción filtrado por servidor."""
    with get_db_session() as session:
        if guild_id:
            results = (
                session.query(Song.title)
                .join(PlayLog, Song.id == PlayLog.song_id)
                .filter(PlayLog.guild_id == str(guild_id))
                .order_by(PlayLog.played_at.desc())
                .limit(limit)
                .all()
            )
            if results:
                # Eliminar duplicados consecutivos manteniendo el orden
                seen = set()
                history = []
                for (title,) in results:
                    if title not in seen:
                        seen.add(title)
                        history.append(title)
                return history
    return []

def get_top_songs_cached():
    """Devuelve las canciones más populares desde la caché en memoria."""
    return list(cached_songs.items())

def preload_top_songs_cache(limit=10):
    """Precarga las canciones más reproducidas en la caché."""
    global cached_songs
    try:
        top_songs = get_top_songs(limit=limit)
        cached_songs = {title: count for title, count in top_songs if title}
        print("🎶 Top de canciones precargado en caché.")
    except Exception as e:
        print(f"⚠️ No se pudo precargar la caché de canciones: {e}")

def log_play_event(title, artist, duration, user_id, username, guild_id, listened_duration, completed, skipped_at=None):
    """Registra un evento de reproducción y actualiza estadísticas."""
    with get_db_session() as session:
        # 1. Asegurar que la canción existe en la tabla de canciones
        existing_song = session.query(Song).filter_by(title=title).first()
        if not existing_song:
            existing_song = Song(title=title, artist=artist, duration=duration, played_count=1)
            session.add(existing_song)
            session.flush()
            session.refresh(existing_song)
        else:
            existing_song.played_count = (existing_song.played_count or 0) + 1
        
        # 2. Guardar log de telemetría con casteo de tipos consistente
        log_entry = PlayLog(
            user_id=str(user_id) if user_id is not None else None,
            username=username,
            song_id=existing_song.id,
            guild_id=str(guild_id) if guild_id is not None else None,
            listened_duration=listened_duration,
            completed=1 if completed else 0,
            skipped_at=skipped_at
        )
        session.add(log_entry)
        print(f"📊 [DATABASE] Telemetría registrada para '{title}' por @{username}. (Completada: {completed})", flush=True)

def update_song_features(title, spotify_id=None, genres=None, popularity=None):
    """Actualiza los metadatos de recomendación de una canción existente."""
    with get_db_session() as session:
        song = session.query(Song).filter_by(title=title).first()
        if song:
            if spotify_id:
                song.spotify_id = spotify_id
            if genres:
                song.genres = genres
            if popularity is not None:
                song.popularity = popularity

def log_dislike_event(user_id, song_title):
    """Registra un dislike explícito de un usuario hacia una canción."""
    with get_db_session() as session:
        song = session.query(Song).filter_by(title=song_title).first()
        if not song:
            return False
        existing = session.query(UserDislike).filter_by(user_id=str(user_id), song_id=song.id).first()
        if not existing:
            dislike = UserDislike(user_id=str(user_id), song_id=song.id)
            session.add(dislike)
            return True
        return False

def remove_dislike(user_id, song_title):
    """Elimina un dislike existente de un usuario hacia una canción."""
    with get_db_session() as session:
        song = session.query(Song).filter_by(title=song_title).first()
        if not song:
            return False
        existing = session.query(UserDislike).filter_by(user_id=str(user_id), song_id=song.id).first()
        if existing:
            session.delete(existing)
            return True
        return False

def add_saved_playlist(guild_id: str, user_id: str, name: str, url: str) -> bool:
    """Guarda o actualiza una playlist en el servidor."""
    g_id = str(guild_id)
    u_id = str(user_id)
    n_clean = name.strip()
    with get_db_session() as session:
        existing = session.query(SavedPlaylist).filter_by(guild_id=g_id, name=n_clean).first()
        if existing:
            existing.url = url
            existing.user_id = u_id
        else:
            pl = SavedPlaylist(guild_id=g_id, user_id=u_id, name=n_clean, url=url)
            session.add(pl)
    return True

def remove_saved_playlist(guild_id: str, name_or_id: str) -> bool:
    """Elimina una playlist guardada por nombre o ID."""
    g_id = str(guild_id)
    with get_db_session() as session:
        if name_or_id.isdigit():
            pl = session.query(SavedPlaylist).filter_by(guild_id=g_id, id=int(name_or_id)).first()
        else:
            pl = session.query(SavedPlaylist).filter_by(guild_id=g_id, name=name_or_id.strip()).first()
        if pl:
            session.delete(pl)
            return True
    return False

def get_saved_playlists(guild_id: str) -> list[dict]:
    """Retorna todas las playlists guardadas para un servidor."""
    g_id = str(guild_id)
    with get_db_session() as session:
        pls = session.query(SavedPlaylist).filter_by(guild_id=g_id).order_by(SavedPlaylist.id.desc()).all()
        return [{'id': p.id, 'name': p.name, 'url': p.url, 'user_id': p.user_id} for p in pls]

def get_recsys_data():
    """Exporta todos los datos necesarios para entrenar el sistema de recomendación.
    
    Retorna un diccionario con:
    - 'play_logs': Lista de dicts con user_id, song_id, guild_id, listened_duration, 
                   duration (de la canción), completed, skipped_at, played_at
    - 'likes': Lista de dicts con user_id, song_id
    - 'dislikes': Lista de dicts con user_id, song_id
    - 'songs': Lista de dicts con id, title, artist, duration, spotify_id, genres, popularity
    """
    with get_db_session() as session:
        # Play logs con duración de la canción
        play_logs = []
        logs = session.query(PlayLog, Song.duration).join(Song, PlayLog.song_id == Song.id).all()
        for log, song_duration in logs:
            play_logs.append({
                'user_id': log.user_id,
                'song_id': log.song_id,
                'guild_id': log.guild_id,
                'listened_duration': log.listened_duration or 0,
                'song_duration': song_duration or 0,
                'completed': log.completed,
                'skipped_at': log.skipped_at,
                'played_at': log.played_at
            })

        # Likes
        likes = [{'user_id': l.user_id, 'song_id': l.song_id}
                 for l in session.query(UserLike).all()]

        # Dislikes
        dislikes = [{'user_id': d.user_id, 'song_id': d.song_id}
                    for d in session.query(UserDislike).all()]

        # Catálogo de canciones
        songs = []
        for s in session.query(Song).all():
            songs.append({
                'id': s.id, 'title': s.title, 'artist': s.artist,
                'duration': s.duration, 'spotify_id': s.spotify_id,
                'genres': s.genres, 'popularity': s.popularity,
                'danceability': s.danceability, 'energy': s.energy,
                'valence': s.valence, 'tempo': s.tempo,
                'acousticness': s.acousticness, 'instrumentalness': s.instrumentalness,
                'liveness': s.liveness, 'speechiness': s.speechiness
            })

        return {
            'play_logs': play_logs,
            'likes': likes,
            'dislikes': dislikes,
            'songs': songs
        }

def save_spotify_audio_features_bulk(features_list: list[dict]):
    """Guarda un lote de audio features de Spotify en la BD."""
    if not features_list:
        return
    with get_db_session() as session:
        for f in features_list:
            if not f or not isinstance(f, dict) or not f.get('id'):
                continue
            sp_id = f['id']
            songs = session.query(Song).filter(Song.spotify_id == sp_id).all()
            for song in songs:
                song.danceability = f.get('danceability')
                song.energy = f.get('energy')
                song.valence = f.get('valence')
                song.tempo = f.get('tempo')
                song.acousticness = f.get('acousticness')
                song.instrumentalness = f.get('instrumentalness')
                song.liveness = f.get('liveness')
                song.speechiness = f.get('speechiness')

def update_song_audio_features(song_identifier, features_dict: dict):
    """Actualiza las características acústicas de una canción por ID o por título."""
    if not features_dict or not isinstance(features_dict, dict):
        return
    with get_db_session() as session:
        song = None
        if isinstance(song_identifier, int):
            song = session.query(Song).filter_by(id=song_identifier).first()
        else:
            song = session.query(Song).filter_by(title=str(song_identifier)).first()
        
        if song:
            if 'danceability' in features_dict: song.danceability = features_dict['danceability']
            if 'energy' in features_dict: song.energy = features_dict['energy']
            if 'valence' in features_dict: song.valence = features_dict['valence']
            if 'tempo' in features_dict: song.tempo = features_dict['tempo']
            if 'acousticness' in features_dict: song.acousticness = features_dict['acousticness']
            if 'instrumentalness' in features_dict: song.instrumentalness = features_dict['instrumentalness']
            if 'liveness' in features_dict: song.liveness = features_dict['liveness']
            if 'speechiness' in features_dict: song.speechiness = features_dict['speechiness']

def get_session_sequences(guild_id=None, session_gap_minutes=30):
    """Extrae secuencias de reproducción por sesión para entrenamiento Item2Vec.
    
    Una 'sesión' se define como una serie de canciones reproducidas consecutivamente
    en el mismo guild sin una pausa mayor a session_gap_minutes entre ellas.
    
    Retorna: Lista de listas de song_id (cada sublista es una sesión).
    """
    from datetime import timedelta
    with get_db_session() as session:
        query = session.query(PlayLog).order_by(PlayLog.guild_id, PlayLog.played_at)
        if guild_id:
            query = query.filter(PlayLog.guild_id == str(guild_id))
        
        logs = query.all()
        if not logs:
            return []

        sessions = []
        current_session = [logs[0].song_id]
        current_guild = logs[0].guild_id

        for i in range(1, len(logs)):
            prev = logs[i - 1]
            curr = logs[i]
            
            time_gap = (curr.played_at - prev.played_at) if (curr.played_at and prev.played_at) else timedelta(hours=1)
            same_guild = curr.guild_id == current_guild
            
            if same_guild and time_gap <= timedelta(minutes=session_gap_minutes):
                current_session.append(curr.song_id)
            else:
                if len(current_session) >= 2:
                    sessions.append(current_session)
                current_session = [curr.song_id]
                current_guild = curr.guild_id

        if len(current_session) >= 2:
            sessions.append(current_session)

        return sessions

def get_cold_start_recommendations(guild_id=None, exclude_titles=None, limit=10):
    """Recomendaciones basadas en historial cuando no hay modelo ML entrenado.
    
    Estrategia: Puntúa canciones del servidor por popularidad + likes,
    filtra recientes, y devuelve con algo de aleatoriedad.
    Mucho mejor que el radio de Spotify para cold-start.
    
    Returns:
        Lista de dicts: [{'song_id': int, 'title': str, 'artist': str, 'score': float}, ...]
    """
    import random
    from sqlalchemy import func
    
    exclude_lower = set()
    if exclude_titles:
        exclude_lower = {t.lower().strip() for t in exclude_titles if t}
    
    with get_db_session() as session:
        # Consultar canciones con sus stats del guild
        query = (
            session.query(
                Song.id, Song.title, Song.artist,
                func.count(PlayLog.id).label('play_count'),
                func.avg(PlayLog.completed).label('avg_completed')
            )
            .outerjoin(PlayLog, Song.id == PlayLog.song_id)
        )
        
        if guild_id:
            query = query.filter(
                (PlayLog.guild_id == str(guild_id)) | (PlayLog.guild_id.is_(None))
            )
        
        query = query.group_by(Song.id, Song.title, Song.artist)
        results = query.all()
        
        if not results:
            return []
        
        # Obtener canciones con likes (bonus)
        liked_song_ids = set()
        likes = session.query(UserLike.song_id).all()
        for (sid,) in likes:
            liked_song_ids.add(sid)
        
        # Obtener canciones con dislikes (penalización)
        disliked_song_ids = set()
        dislikes = session.query(UserDislike.song_id).all()
        for (sid,) in dislikes:
            disliked_song_ids.add(sid)
        
        # Calcular score para cada canción
        candidates = []
        for song_id, title, artist, play_count, avg_completed in results:
            if not title:
                continue
            if title.lower().strip() in exclude_lower:
                continue
            if song_id in disliked_song_ids:
                continue
            
            score = float(play_count or 0)
            
            # Bonus por tasa de completación alta
            if avg_completed and avg_completed > 0.7:
                score *= 1.5
            
            # Bonus por tener likes
            if song_id in liked_song_ids:
                score *= 2.0
            
            # Un mínimo para canciones nuevas sin plays
            score = max(score, 0.1)
            
            candidates.append({
                'song_id': song_id,
                'title': title,
                'artist': artist or '',
                'score': score,
                'source': 'cold_start'
            })
        
        if not candidates:
            return []
        
        # Ordenar por score y tomar el top pool
        candidates.sort(key=lambda x: x['score'], reverse=True)
        pool_size = min(len(candidates), limit * 3)
        pool = candidates[:pool_size]
        
        # Muestreo ponderado por score (no siempre el #1)
        weights = [c['score'] for c in pool]
        total_weight = sum(weights)
        if total_weight > 0:
            weights = [w / total_weight for w in weights]
        else:
            weights = [1.0 / len(pool)] * len(pool)
        
        n_select = min(limit, len(pool))
        selected_indices = []
        available = list(range(len(pool)))
        
        for _ in range(n_select):
            if not available:
                break
            avail_weights = [weights[i] for i in available]
            total_w = sum(avail_weights)
            if total_w > 0:
                avail_weights = [w / total_w for w in avail_weights]
            else:
                avail_weights = [1.0 / len(available)] * len(available)
            
            chosen = random.choices(available, weights=avail_weights, k=1)[0]
            selected_indices.append(chosen)
            available.remove(chosen)
        
        return [pool[i] for i in selected_indices]

def bulk_preload_tracks(tracks: list[dict], user_id: str, username: str = None, guild_id: str = None) -> dict:
    """Registra en lote una lista de canciones asociadas a una playlist/álbum.
    
    Para cada canción:
      1. La agrega o recupera de la tabla `songs`.
      2. Le asigna un `UserLike` para el usuario si no existe.
      3. Registra una entrada en `play_logs` con tiempo consecutivo para simular una sesión.
    
    Returns:
        dict con 'total': int, 'new_songs': int, 'likes_added': int
    """
    from datetime import datetime, timedelta
    
    user_id_str = str(user_id) if user_id else "unknown"
    guild_id_str = str(guild_id) if guild_id else "default_guild"
    base_time = datetime.utcnow() - timedelta(minutes=len(tracks))
    
    new_songs_count = 0
    likes_count = 0
    
    with get_db_session() as session:
        for idx, track_info in enumerate(tracks):
            title = track_info.get('title', '').strip()
            artist = track_info.get('artist') or track_info.get('uploader') or ''
            duration = track_info.get('duration', 0)
            url = track_info.get('url')
            
            if not title:
                continue
            
            # 1. Tabla Song
            song = session.query(Song).filter_by(title=title).first()
            if not song:
                song = Song(title=title, artist=artist, duration=duration, url=url, played_count=1)
                session.add(song)
                session.flush()
                session.refresh(song)
                new_songs_count += 1
            else:
                song.played_count = (song.played_count or 0) + 1
                if not song.artist and artist:
                    song.artist = artist
                if not song.duration and duration:
                    song.duration = duration
                if not song.url and url:
                    song.url = url
            
            # 2. UserLike
            existing_like = session.query(UserLike).filter_by(user_id=user_id_str, song_id=song.id).first()
            if not existing_like:
                session.add(UserLike(user_id=user_id_str, song_id=song.id, timestamp=base_time + timedelta(seconds=idx)))
                likes_count += 1
            
            # 3. PlayLog (simulando secuencia de sesión consecutiva)
            play_log = PlayLog(
                user_id=user_id_str,
                username=username or "PreloadUser",
                song_id=song.id,
                guild_id=guild_id_str,
                played_at=base_time + timedelta(seconds=idx * 2),
                listened_duration=duration or 180,
                completed=1,
                skipped_at=None
            )
            session.add(play_log)

    return {
        'total': len(tracks),
        'new_songs': new_songs_count,
        'likes_added': likes_count
    }