"""
Toodles RecSys — Script de Entrenamiento Offline
=================================================
Extrae datos de la BBDD (PlayLog, UserLike, UserDislike), construye:
  1. Matriz de interacción implícita (R_u,i) y entrena Implicit ALS.
  2. Secuencias de sesión y entrena Item2Vec (gensim Word2Vec).
Exporta todos los artefactos a data/recsys_artifacts.npz.

Diseñado para ejecutarse diariamente vía cron (ej. 04:00 AM).
Consumo esperado: < 500MB RAM, < 2 min en ARM64 Ampere.

Uso:
    python -m recsys.train
"""

import os
import sys
import time

# Prevenir advertencias y contención de hilos de OpenBLAS/MKL en CPU multi-core
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix

# Agregar el directorio raíz del proyecto al path para importar database.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import get_recsys_data, get_session_sequences
from sznLogger import get_logger

logger = get_logger("recsys")

# Directorio donde se guardan los artefactos entrenados
ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ARTIFACTS_PATH = os.path.join(ARTIFACTS_DIR, "recsys_artifacts.npz")

# ─── Pesos de scoring implícito ──────────────────────────────────────
WEIGHT_LIKE       =  5.0   # Feedback explícito positivo (td?like)
WEIGHT_DISLIKE    = -5.0   # Feedback explícito negativo (td?dislike)
WEIGHT_COMPLETED  =  2.0   # Canción escuchada >= 80% de su duración
WEIGHT_REQUESTED  =  1.0   # El usuario la pidió directamente
WEIGHT_EARLY_SKIP = -3.0   # Skip en los primeros 30 segundos

# ─── Hiperparámetros de modelos (Enterprise High-Precision Grade) ──────
ALS_FACTORS        = 256
ALS_REGULARIZATION = 0.05
ALS_ITERATIONS     = 50
ITEM2VEC_DIM       = 256
ITEM2VEC_WINDOW    = 10
ITEM2VEC_MIN_COUNT = 1
ITEM2VEC_EPOCHS    = 100


def build_interaction_matrix(play_logs, likes, dislikes, songs):
    """Construye la matriz de interacción usuario-canción R(u,i).
    
    Para cada par (user, song), acumula los scores:
      +5.0 por like explícito
      -5.0 por dislike explícito
      +2.0 por escuchar >= 80%
      +1.0 por cada reproducción registrada (el usuario la pidió)
      -3.0 por skip antes de 30 segundos
    
    Returns:
        interaction_matrix (csr_matrix): Matriz dispersa de shape (n_users, n_songs)
        user_id_map (dict): Mapeo user_id -> índice de fila
        song_id_map (dict): Mapeo song_id -> índice de columna
        reverse_song_map (dict): Mapeo índice de columna -> song_id
        reverse_user_map (dict): Mapeo índice de fila -> user_id
    """
    # Construir mapeos de IDs a índices
    all_user_ids = set()
    all_song_ids = set()
    
    for log in play_logs:
        if log['user_id']:
            all_user_ids.add(log['user_id'])
        all_song_ids.add(log['song_id'])
    for like in likes:
        all_user_ids.add(like['user_id'])
        all_song_ids.add(like['song_id'])
    for dislike in dislikes:
        all_user_ids.add(dislike['user_id'])
        all_song_ids.add(dislike['song_id'])
    
    if not all_user_ids or not all_song_ids:
        print("⚠️ No hay suficientes datos para construir la matriz de interacción.")
        return None, {}, {}, {}, {}
    
    user_id_map = {uid: idx for idx, uid in enumerate(sorted(all_user_ids))}
    song_id_map = {sid: idx for idx, sid in enumerate(sorted(all_song_ids))}
    reverse_user_map = {idx: uid for uid, idx in user_id_map.items()}
    reverse_song_map = {idx: sid for sid, idx in song_id_map.items()}
    
    n_users = len(user_id_map)
    n_songs = len(song_id_map)
    
    # Acumular scores en un diccionario (user_idx, song_idx) -> score
    scores = {}
    
    # 1. Procesar play_logs
    for log in play_logs:
        user_id = log.get('user_id')
        if not user_id or user_id not in user_id_map:
            continue
        
        u_idx = user_id_map[user_id]
        s_idx = song_id_map[log['song_id']]
        key = (u_idx, s_idx)
        
        if key not in scores:
            scores[key] = 0.0
        
        # Score base por reproducción (la pidió o la escuchó)
        scores[key] += WEIGHT_REQUESTED
        
        # Bonus por completar (>= 80% de duración)
        song_duration = log.get('song_duration', 0)
        listened = log.get('listened_duration', 0)
        completed = log.get('completed', 0)
        skipped_at = log.get('skipped_at')
        
        if completed == 1 or (song_duration > 0 and listened >= song_duration * 0.8):
            scores[key] += WEIGHT_COMPLETED
        
        # Penalización por skip temprano (< 30s)
        if skipped_at is not None and skipped_at < 30:
            scores[key] += WEIGHT_EARLY_SKIP
    
    # 2. Procesar likes
    for like in likes:
        u_idx = user_id_map[like['user_id']]
        s_idx = song_id_map[like['song_id']]
        key = (u_idx, s_idx)
        if key not in scores:
            scores[key] = 0.0
        scores[key] += WEIGHT_LIKE
    
    # 3. Procesar dislikes
    for dislike in dislikes:
        u_idx = user_id_map[dislike['user_id']]
        s_idx = song_id_map[dislike['song_id']]
        key = (u_idx, s_idx)
        if key not in scores:
            scores[key] = 0.0
        scores[key] += WEIGHT_DISLIKE
    
    # Construir la matriz CSR
    rows, cols, data = [], [], []
    for (u, s), score in scores.items():
        rows.append(u)
        cols.append(s)
        data.append(score)
    
    interaction_matrix = csr_matrix(
        (np.array(data, dtype=np.float32), (np.array(rows), np.array(cols))),
        shape=(n_users, n_songs)
    )
    
    print(f"📊 Matriz de interacción construida: {n_users} usuarios × {n_songs} canciones "
          f"({len(scores)} interacciones no-cero)")
    
    return interaction_matrix, user_id_map, song_id_map, reverse_song_map, reverse_user_map


def train_als_model(interaction_matrix):
    """Entrena el modelo Implicit ALS sobre la matriz de interacción.
    
    La librería `implicit` espera una matriz item-user (transpuesta), por lo que
    transponemos la matriz de interacción antes de pasarla.
    
    Para manejar scores negativos, la librería usa la convención:
    - Valores positivos = preferencia
    - confidence = 1 + alpha * |score|
    
    Returns:
        user_factors (np.ndarray): Embeddings de usuarios (n_users, factors)
        item_factors (np.ndarray): Embeddings de canciones (n_songs, factors)
    """
    from implicit.als import AlternatingLeastSquares
    
    # implicit espera la matriz como item-user (transpuesta)
    # y necesita valores positivos para las interacciones que importan.
    # Convertimos scores negativos a 0 para la confianza, pero mantenemos
    # la estructura: ALS trata los 0 como "no observado" y positivos como señal.
    positive_matrix = interaction_matrix.copy()
    positive_matrix.data = np.maximum(positive_matrix.data, 0)
    positive_matrix.eliminate_zeros()
    
    if positive_matrix.nnz == 0:
        print("⚠️ No hay interacciones positivas para entrenar ALS.")
        return None, None
    
    model = AlternatingLeastSquares(
        factors=ALS_FACTORS,
        regularization=ALS_REGULARIZATION,
        iterations=ALS_ITERATIONS,
        use_gpu=False,  # Siempre CPU en ARM64
        random_state=42
    )
    
    # implicit 0.6+ espera matriz user-item CSR (n_users x n_songs)
    user_items = positive_matrix.tocsr()
    
    print(f"🧠 Entrenando Implicit ALS (factors={ALS_FACTORS}, "
          f"iterations={ALS_ITERATIONS})...")
    t0 = time.time()
    model.fit(user_items)
    elapsed = time.time() - t0
    print(f"✅ ALS entrenado en {elapsed:.1f}s")
    
    # Extraer factores como arrays numpy
    user_factors = np.array(model.user_factors)
    item_factors = np.array(model.item_factors)
    
    print(f"   → User factors shape: {user_factors.shape}")
    print(f"   → Item factors shape: {item_factors.shape}")
    
    return user_factors, item_factors


def train_item2vec(session_sequences, song_id_map):
    """Entrena Item2Vec (Word2Vec adaptado) sobre secuencias de sesión.
    
    Cada sesión es una lista de song_ids que sonaron consecutivamente en un
    canal de voz de Discord. Tratamos cada song_id como una "palabra" y cada
    sesión como una "frase".
    
    Returns:
        item2vec_vectors (np.ndarray): Matriz de embeddings (n_songs, dim)
            Indexada por song_idx (del song_id_map).
            Canciones sin aparición en sesiones tendrán vector de ceros.
    """
    from gensim.models import Word2Vec
    
    if not session_sequences:
        print("⚠️ No hay secuencias de sesión para entrenar Item2Vec.")
        return None
    
    # Convertir song_ids a strings (Word2Vec trabaja con tokens string)
    sentences = [[str(sid) for sid in seq] for seq in session_sequences]
    
    print(f"🧠 Entrenando Item2Vec (dim={ITEM2VEC_DIM}, "
          f"window={ITEM2VEC_WINDOW}, {len(sentences)} sesiones)...")
    t0 = time.time()
    
    model = Word2Vec(
        sentences=sentences,
        vector_size=ITEM2VEC_DIM,
        window=ITEM2VEC_WINDOW,
        min_count=ITEM2VEC_MIN_COUNT,
        sg=1,  # Skip-gram (mejor para ítems)
        workers=4,
        epochs=ITEM2VEC_EPOCHS,
        seed=42
    )
    
    elapsed = time.time() - t0
    print(f"✅ Item2Vec entrenado en {elapsed:.1f}s ({len(model.wv)} canciones en vocabulario)")
    
    # Construir matriz de embeddings alineada con song_id_map
    n_songs = len(song_id_map)
    item2vec_vectors = np.zeros((n_songs, ITEM2VEC_DIM), dtype=np.float32)
    
    mapped_count = 0
    for song_id, idx in song_id_map.items():
        token = str(song_id)
        if token in model.wv:
            item2vec_vectors[idx] = model.wv[token]
            mapped_count += 1
    
    print(f"   → {mapped_count}/{n_songs} canciones mapeadas a vectores Item2Vec")
    
    return item2vec_vectors


def fetch_spotify_audio_features(songs_metadata):
    """Consulta y guarda metadatos y características acústicas de Spotify para canciones en catálogo."""
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    from database import save_spotify_audio_features_bulk, get_db_session, Song
    
    cid = os.getenv("client_id")
    secret = os.getenv("client_secret")
    if not cid or not secret:
        return

    missing_sp_ids = [s.get('spotify_id') for s in songs_metadata if s.get('spotify_id') and s.get('danceability') is None]

    if not missing_sp_ids:
        print("✅ Catálogo sincronizado con metadatos acústicos en BD.")
        return

    print(f"🎵 Sincronizando metadatos de Spotify para {len(missing_sp_ids)} canciones...")
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=cid, client_secret=secret))
        
        # 1. Obtener popularidad de tracks (Endpoint 100% Abierto)
        for i in range(0, len(missing_sp_ids), 50):
            batch = missing_sp_ids[i:i+50]
            tracks_res = sp.tracks(batch)
            if tracks_res and 'tracks' in tracks_res:
                with get_db_session() as session:
                    for tr in tracks_res['tracks']:
                        if tr and tr.get('id'):
                            s_obj = session.query(Song).filter_by(spotify_id=tr['id']).first()
                            if s_obj and tr.get('popularity') is not None:
                                s_obj.popularity = tr['popularity']

        # 2. Intentar audio-features si está disponible
        for i in range(0, len(missing_sp_ids), 100):
            batch = missing_sp_ids[i:i+100]
            try:
                features_res = sp.audio_features(batch)
                if features_res:
                    valid_features = [f for f in features_res if f and isinstance(f, dict)]
                    save_spotify_audio_features_bulk(valid_features)
            except Exception:
                pass
                
        print("✅ Metadatos de Spotify procesados.")
    except Exception as e:
        print(f"ℹ️ Sincronización de Spotify finalizada ({e})")


def build_audio_feature_matrix(songs_metadata, song_id_map):
    """Construye la matriz normalizada de 8 características acústicas por canción.
    
    Campos: danceability, energy, valence, tempo, acousticness, instrumentalness, liveness, speechiness.
    """
    n_songs = len(song_id_map)
    audio_matrix = np.zeros((n_songs, 8), dtype=np.float32)
    
    song_dict = {s['id']: s for s in songs_metadata}
    for s_id, s_idx in song_id_map.items():
        s = song_dict.get(s_id)
        if not s:
            continue
        
        tempo = s.get('tempo') or 120.0
        norm_tempo = min(max(float(tempo) / 220.0, 0.0), 1.0)
        
        audio_matrix[s_idx] = [
            float(s.get('danceability') or 0.5),
            float(s.get('energy') or 0.5),
            float(s.get('valence') or 0.5),
            norm_tempo,
            float(s.get('acousticness') or 0.5),
            float(s.get('instrumentalness') or 0.0),
            float(s.get('liveness') or 0.2),
            float(s.get('speechiness') or 0.1)
        ]
        
    return audio_matrix


def export_artifacts(user_factors, item_factors, item2vec_vectors, audio_feature_vectors,
                      user_id_map, song_id_map, reverse_song_map, 
                      reverse_user_map, songs_metadata, interaction_matrix):
    """Exporta todos los artefactos de entrenamiento a un archivo .npz.
    
    Contenido del archivo:
    - user_factors: Embeddings ALS de usuarios
    - item_factors: Embeddings ALS de canciones
    - item2vec_vectors: Embeddings Item2Vec de canciones
    - audio_feature_vectors: Características acústicas
    - user_id_map_keys/values: Mapeo user_id -> índice
    - song_id_map_keys/values: Mapeo song_id -> índice
    - reverse_song_map_keys/values: Mapeo índice -> song_id
    - reverse_user_map_keys/values: Mapeo índice -> user_id
    - songs_metadata: Catálogo de canciones (para títulos/artistas)
    - negative_scores: Pares (user_idx, song_idx) con score negativo (para filtrado)
    """
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    
    # Extraer pares con score negativo para filtrado en el re-ranking
    neg_users, neg_songs = [], []
    if interaction_matrix is not None:
        cx = interaction_matrix.tocoo()
        for u, s, v in zip(cx.row, cx.col, cx.data):
            if v < 0:
                neg_users.append(u)
                neg_songs.append(s)
    
    save_dict = {
        'user_id_map_keys': np.array(list(user_id_map.keys()), dtype=object),
        'user_id_map_values': np.array(list(user_id_map.values()), dtype=np.int32),
        'song_id_map_keys': np.array(list(song_id_map.keys()), dtype=np.int32),
        'song_id_map_values': np.array(list(song_id_map.values()), dtype=np.int32),
        'reverse_song_map_keys': np.array(list(reverse_song_map.keys()), dtype=np.int32),
        'reverse_song_map_values': np.array(list(reverse_song_map.values()), dtype=np.int32),
        'reverse_user_map_keys': np.array(list(reverse_user_map.keys()), dtype=np.int32),
        'reverse_user_map_values': np.array(list(reverse_user_map.values()), dtype=object),
        'negative_score_users': np.array(neg_users, dtype=np.int32),
        'negative_score_songs': np.array(neg_songs, dtype=np.int32),
    }
    
    # Guardar metadatos de canciones como arrays paralelos
    song_ids_list = [s['id'] for s in songs_metadata]
    song_titles_list = [s.get('title', '') or '' for s in songs_metadata]
    song_artists_list = [s.get('artist', '') or '' for s in songs_metadata]
    save_dict['meta_song_ids'] = np.array(song_ids_list, dtype=np.int32)
    save_dict['meta_song_titles'] = np.array(song_titles_list, dtype=object)
    save_dict['meta_song_artists'] = np.array(song_artists_list, dtype=object)
    
    if user_factors is not None:
        save_dict['user_factors'] = user_factors
    if item_factors is not None:
        save_dict['item_factors'] = item_factors
    if item2vec_vectors is not None:
        save_dict['item2vec_vectors'] = item2vec_vectors
    if audio_feature_vectors is not None:
        save_dict['audio_feature_vectors'] = audio_feature_vectors
    
    np.savez_compressed(ARTIFACTS_PATH, **save_dict)
    file_size = os.path.getsize(ARTIFACTS_PATH) / (1024 * 1024)
    print(f"💾 Artefactos exportados a {ARTIFACTS_PATH} ({file_size:.2f} MB)")


def main():
    """Pipeline principal de entrenamiento del RecSys."""
    print("=" * 60)
    print("🚀 Toodles RecSys — Entrenamiento Offline (Híbrido Multimodal)")
    print("=" * 60)
    
    total_start = time.time()
    
    # ─── Paso 1: Extraer datos ──────────────────────────────────────
    print("\n📥 Paso 1: Extrayendo datos de la base de datos...")
    from database import get_recsys_data, get_session_sequences
    
    data = get_recsys_data()
    play_logs = data['play_logs']
    likes = data['likes']
    dislikes = data['dislikes']
    songs = data['songs']
    
    print(f"   → {len(play_logs)} eventos de reproducción")
    print(f"   → {len(likes)} likes")
    print(f"   → {len(dislikes)} dislikes")
    print(f"   → {len(songs)} canciones en catálogo")
    
    if len(play_logs) < 5 and len(likes) < 3:
        print("\n⚠️ Datos insuficientes para entrenamiento (< 5 reproducciones y < 3 likes).")
        print("   El bot usará recomendaciones basadas en Spotify como fallback.")
        return
    
    # ─── Paso 2: Audio Features de Spotify ─────────────────────────
    fetch_spotify_audio_features(songs)
    # Volver a cargar la lista de canciones actualizada con las nuevas audio features
    data = get_recsys_data()
    songs = data['songs']

    # ─── Paso 3: Construir matriz de interacción ────────────────────
    print("\n📊 Paso 3: Construyendo matriz de interacción R(u,i)...")
    result = build_interaction_matrix(play_logs, likes, dislikes, songs)
    interaction_matrix, user_id_map, song_id_map, reverse_song_map, reverse_user_map = result
    
    if interaction_matrix is None:
        print("❌ No se pudo construir la matriz. Abortando.")
        return
    
    # ─── Paso 4: Entrenar ALS ────────────────────────────────────────
    print("\n🧠 Paso 4: Entrenando modelo Implicit ALS...")
    user_factors, item_factors = train_als_model(interaction_matrix)
    
    # ─── Paso 5: Extraer secuencias de sesión y entrenar Item2Vec ──
    print("\n🎵 Paso 5: Extrayendo secuencias de sesión...")
    session_sequences = get_session_sequences()
    print(f"   → {len(session_sequences)} sesiones extraídas")
    
    if session_sequences:
        total_tracks = sum(len(s) for s in session_sequences)
        avg_len = total_tracks / len(session_sequences)
        print(f"   → {total_tracks} tracks totales (promedio {avg_len:.1f} tracks/sesión)")
    
    print("\n🧠 Paso 6: Entrenando Item2Vec...")
    item2vec_vectors = train_item2vec(session_sequences, song_id_map)
    
    # ─── Paso 7: Matriz de Audio Features ───────────────────────────
    print("\n🎼 Paso 7: Construyendo matriz de Audio Features (Danceability, Energy, Valence, Tempo)...")
    audio_feature_vectors = build_audio_feature_matrix(songs, song_id_map)
    print(f"   → {audio_feature_vectors.shape[0]} canciones x {audio_feature_vectors.shape[1]} características acústicas")

    # ─── Paso 8: Exportar artefactos ─────────────────────────────────
    print("\n💾 Paso 8: Exportando artefactos...")
    export_artifacts(
        user_factors=user_factors,
        item_factors=item_factors,
        item2vec_vectors=item2vec_vectors,
        audio_feature_vectors=audio_feature_vectors,
        user_id_map=user_id_map,
        song_id_map=song_id_map,
        reverse_song_map=reverse_song_map,
        reverse_user_map=reverse_user_map,
        songs_metadata=songs,
        interaction_matrix=interaction_matrix
    )
    
    total_elapsed = time.time() - total_start
    print(f"\n{'=' * 60}")
    print(f"✅ Entrenamiento completado en {total_elapsed:.1f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
