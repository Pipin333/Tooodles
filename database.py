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