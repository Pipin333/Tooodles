import os
from contextlib import contextmanager
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime
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
    """Crea todas las tablas si no existen."""
    Base.metadata.create_all(engine)
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
            duration=duration
        )
        session.add(new_song)
        session.flush()
        session.refresh(new_song)
        return new_song

def get_top_songs(limit=10, offset=0):
    """Obtiene las canciones más reproducidas desde la base de datos."""
    with get_db_session() as session:
        top_songs = (
            session.query(Song.title, Song.played_count)
            .order_by(Song.played_count.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
    return top_songs

def get_top_songs_cached():
    """Devuelve las canciones más populares desde la caché en memoria."""
    return list(cached_songs.items())

def preload_top_songs_cache(limit=10):
    """Precarga las canciones más reproducidas en la caché."""
    global cached_songs
    try:
        top_songs = get_top_songs(limit=limit)
        cached_songs = {title: count for title, count in top_songs}
        print("🎶 Top de canciones precargado en caché.")
    except Exception as e:
        print(f"⚠️ No se pudo precargar la caché de canciones: {e}")

def log_play_event(title, artist, duration, user_id, username, guild_id, listened_duration, completed, skipped_at=None):
    """Registra un evento de reproducción y actualiza estadísticas."""
    with get_db_session() as session:
        # 1. Asegurar que la canción existe en la tabla de canciones
        existing_song = session.query(Song).filter_by(title=title).first()
        if not existing_song:
            existing_song = Song(title=title, artist=artist, duration=duration)
            session.add(existing_song)
            session.flush()
            session.refresh(existing_song)
        
        # 2. Incrementar contador de reproducciones
        existing_song.played_count += 1
        
        # 3. Guardar log de telemetría
        log_entry = PlayLog(
            user_id=user_id,
            username=username,
            song_id=existing_song.id,
            guild_id=guild_id,
            listened_duration=listened_duration,
            completed=1 if completed else 0,
            skipped_at=skipped_at
        )
        session.add(log_entry)
        print(f"📊 [DATABASE] Telemetría registrada para '{title}' por @{username}. (Completada: {completed})", flush=True)