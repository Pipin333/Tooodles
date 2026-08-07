"""
Toodles RecSys — Motor de Inferencia en Tiempo Real
====================================================
Carga los artefactos entrenados (ALS + Item2Vec) y provee:
  1. Recomendaciones personalizadas por usuario (ALS)
  2. Recomendaciones por similitud de canción (Item2Vec)
  3. Recomendaciones grupales (promedio de vectores)
  4. Re-ranking con reglas de experiencia de usuario

Diseñado para responder en < 10ms con NumPy/SciPy puro.

Uso:
    from recsys.engine import RecSysEngine
    engine = RecSysEngine()
    engine.load()
    recommendations = engine.recommend_for_user(user_id, n=10)
"""

import os
import random
import time
import numpy as np
from typing import Optional
from sznLogger import get_logger

logger = get_logger("recsys")

# Directorio de artefactos entrenados
ARTIFACTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ARTIFACTS_PATH = os.path.join(ARTIFACTS_DIR, "recsys_artifacts.npz")


class RecSysEngine:
    """Motor de recomendación híbrido ALS + Item2Vec para Toodles.
    
    Atributos principales:
        loaded (bool): Si los artefactos están cargados en memoria.
        user_factors (np.ndarray): Embeddings ALS de usuarios (n_users, 64)
        item_factors (np.ndarray): Embeddings ALS de canciones (n_songs, 64)
        item2vec_vectors (np.ndarray): Embeddings Item2Vec (n_songs, 64)
        item_norms (np.ndarray): Normas precalculadas para similitud coseno
    """
    
    def __init__(self):
        self.loaded = False
        self.user_factors = None
        self.item_factors = None
        self.item2vec_vectors = None
        self.item_norms = None
        self.item2vec_norms = None
        self.audio_feature_vectors = None
        self.hybrid_item_factors = None
        self.hybrid_norms = None
        
        # Mapeos
        self.user_id_map = {}      # user_id (str) -> user_idx (int)
        self.song_id_map = {}      # song_id (int) -> song_idx (int)
        self.reverse_song_map = {} # song_idx (int) -> song_id (int)
        self.reverse_user_map = {} # user_idx (int) -> user_id (str)
        
        # Metadatos del catálogo
        self.song_titles = {}      # song_id -> título
        self.song_artists = {}     # song_id -> artista
        
        # Pares con score negativo (para filtrado en re-ranking)
        self.negative_pairs = set()  # set de (user_idx, song_idx)
        
        # Timestamp de última carga (para detección de cambios)
        self._last_load_time = 0
        self._last_file_mtime = 0
    
    def load(self, force=False) -> bool:
        """Carga artefactos desde disco. Recarga atómica si el archivo cambió.
        
        Args:
            force: Si True, recarga aunque el archivo no haya cambiado.
        
        Returns:
            True si se cargó/recargó exitosamente, False si no hay artefactos.
        """
        if not os.path.exists(ARTIFACTS_PATH):
            logger.warning("⚠️ [RecSys] No se encontraron artefactos entrenados en "
                           f"{ARTIFACTS_PATH}. Ejecuta 'python -m recsys.train' primero.")
            return False
        
        # Verificar si el archivo cambió desde la última carga
        file_mtime = os.path.getmtime(ARTIFACTS_PATH)
        if not force and self.loaded and file_mtime == self._last_file_mtime:
            return True  # Ya cargado y sin cambios
        
        try:
            t0 = time.time()
            data = np.load(ARTIFACTS_PATH, allow_pickle=True)
            
            # Reconstruir mapeos
            self.user_id_map = dict(zip(
                data['user_id_map_keys'].tolist(),
                data['user_id_map_values'].tolist()
            ))
            self.song_id_map = dict(zip(
                data['song_id_map_keys'].tolist(),
                data['song_id_map_values'].tolist()
            ))
            self.reverse_song_map = dict(zip(
                data['reverse_song_map_keys'].tolist(),
                data['reverse_song_map_values'].tolist()
            ))
            self.reverse_user_map = dict(zip(
                data['reverse_user_map_keys'].tolist(),
                data['reverse_user_map_values'].tolist()
            ))
            
            # Metadatos de canciones
            meta_ids = data['meta_song_ids'].tolist()
            meta_titles = data['meta_song_titles'].tolist()
            meta_artists = data['meta_song_artists'].tolist()
            self.song_titles = dict(zip(meta_ids, meta_titles))
            self.song_artists = dict(zip(meta_ids, meta_artists))
            
            # Embeddings ALS
            if 'user_factors' in data and 'item_factors' in data:
                self.user_factors = data['user_factors']
                self.item_factors = data['item_factors']
                # Precalcular normas para similitud coseno
                self.item_norms = np.linalg.norm(self.item_factors, axis=1, keepdims=True)
                self.item_norms = np.where(self.item_norms == 0, 1e-10, self.item_norms)
            
            self.audio_feature_vectors = None
            self.hybrid_item_factors = None
            self.hybrid_norms = None

            # Embeddings Item2Vec
            if 'item2vec_vectors' in data:
                self.item2vec_vectors = data['item2vec_vectors']
                self.item2vec_norms = np.linalg.norm(self.item2vec_vectors, axis=1, keepdims=True)
                self.item2vec_norms = np.where(self.item2vec_norms == 0, 1e-10, self.item2vec_norms)
            
            # Características acústicas (Audio Features) de Spotify
            if 'audio_feature_vectors' in data:
                self.audio_feature_vectors = data['audio_feature_vectors']

            # Construir espacio vectorial híbrido multimodal (Item2Vec + Audio Features)
            if self.item2vec_vectors is not None:
                if self.audio_feature_vectors is not None:
                    audio_norm = self.audio_feature_vectors / (np.linalg.norm(self.audio_feature_vectors, axis=1, keepdims=True) + 1e-10)
                    item2vec_norm = self.item2vec_vectors / self.item2vec_norms
                    self.hybrid_item_factors = np.hstack([item2vec_norm, audio_norm * 0.5])
                else:
                    self.hybrid_item_factors = self.item2vec_vectors

                self.hybrid_norms = np.linalg.norm(self.hybrid_item_factors, axis=1, keepdims=True)
                self.hybrid_norms = np.where(self.hybrid_norms == 0, 1e-10, self.hybrid_norms)
            
            # Pares negativos
            neg_users = data.get('negative_score_users', np.array([], dtype=np.int32))
            neg_songs = data.get('negative_score_songs', np.array([], dtype=np.int32))
            self.negative_pairs = set(zip(neg_users.tolist(), neg_songs.tolist()))
            
            self.loaded = True
            self._last_load_time = time.time()
            self._last_file_mtime = file_mtime
            
            elapsed = (time.time() - t0) * 1000
            n_users = len(self.user_id_map)
            n_songs = len(self.song_id_map)
            logger.info(f"✅ [RecSys] Artefactos cargados en {elapsed:.0f}ms "
                        f"({n_users} usuarios, {n_songs} canciones)")
            return True
            
        except Exception as e:
            logger.error(f"❌ [RecSys] Error al cargar artefactos: {e}")
            return False
    
    def reload_if_updated(self) -> bool:
        """Recarga los artefactos solo si el archivo en disco fue actualizado."""
        if not os.path.exists(ARTIFACTS_PATH):
            return False
        file_mtime = os.path.getmtime(ARTIFACTS_PATH)
        if file_mtime != self._last_file_mtime:
            logger.info("🔄 [RecSys] Detectado cambio en artefactos, recargando...")
            return self.load(force=True)
        return self.loaded
    
    # ─── Fase 1: Retrieval (Búsqueda de Candidatos) ────────────────
    
    def recommend_for_user(self, user_id: str, n: int = 20) -> list[dict]:
        """Genera Top-N recomendaciones personalizadas para un usuario vía ALS.
        
        Returns:
            Lista de dicts: [{'song_id': int, 'title': str, 'artist': str, 'score': float}, ...]
        """
        if not self.loaded or self.user_factors is None:
            return []
        
        user_id = str(user_id)
        if user_id not in self.user_id_map:
            return []
        
        u_idx = self.user_id_map[user_id]
        user_vec = self.user_factors[u_idx]  # (factors,)
        
        # Similitud coseno: U · V^T / (||U|| * ||V||)
        scores = self.item_factors @ user_vec  # (n_songs,)
        item_norms_flat = self.item_norms.flatten()
        user_norm = np.linalg.norm(user_vec)
        if user_norm > 0:
            scores = scores / (item_norms_flat * user_norm)
        
        # Top-N índices
        top_indices = np.argsort(scores)[::-1][:n * 2]  # Extra para compensar filtros
        
        results = []
        for idx in top_indices:
            if len(results) >= n:
                break
            song_id = self.reverse_song_map.get(int(idx))
            if song_id is None:
                continue
            results.append({
                'song_id': song_id,
                'title': self.song_titles.get(song_id, ''),
                'artist': self.song_artists.get(song_id, ''),
                'score': float(scores[idx]),
                'source': 'als'
            })
        
        return results
    
    def recommend_for_group(self, user_ids: list[str], n: int = 20) -> list[dict]:
        """Genera recomendaciones para un grupo de usuarios promediando sus vectores ALS.
        
        Fórmula: U_grupo = (1/N) * Σ U_usuario_k
        """
        if not self.loaded or self.user_factors is None:
            return []
        
        valid_indices = []
        for uid in user_ids:
            uid = str(uid)
            if uid in self.user_id_map:
                valid_indices.append(self.user_id_map[uid])
        
        if not valid_indices:
            return []
        
        # Promedio de vectores de usuario
        group_vec = np.mean(self.user_factors[valid_indices], axis=0)
        
        scores = self.item_factors @ group_vec
        item_norms_flat = self.item_norms.flatten()
        group_norm = np.linalg.norm(group_vec)
        if group_norm > 0:
            scores = scores / (item_norms_flat * group_norm)
        
        top_indices = np.argsort(scores)[::-1][:n * 2]
        
        results = []
        for idx in top_indices:
            if len(results) >= n:
                break
            song_id = self.reverse_song_map.get(int(idx))
            if song_id is None:
                continue
            results.append({
                'song_id': song_id,
                'title': self.song_titles.get(song_id, ''),
                'artist': self.song_artists.get(song_id, ''),
                'score': float(scores[idx]),
                'source': 'als_group'
            })
        
        return results
    
    def recommend_similar_songs(self, song_title: str, n: int = 20, 
                                 use_item2vec: bool = True) -> list[dict]:
        """Encuentra canciones similares a una canción dada usando Item2Vec o ALS.
        
        Busca la canción por título en el catálogo y devuelve las más similares
        por similitud coseno en el espacio de embeddings.
        
        Args:
            song_title: Título de la canción semilla.
            n: Número de recomendaciones.
            use_item2vec: Si True, usa Item2Vec (mejor para sesiones).
                         Si False, usa ALS item factors.
        """
        if not self.loaded:
            return []
        
        # Buscar la canción por título (búsqueda exacta primero, luego parcial)
        target_song_id = None
        song_title_lower = song_title.lower().strip()
        
        for sid, title in self.song_titles.items():
            if title and title.lower().strip() == song_title_lower:
                target_song_id = sid
                break
        
        if target_song_id is None:
            # Búsqueda parcial (contiene)
            for sid, title in self.song_titles.items():
                if title and song_title_lower in title.lower():
                    target_song_id = sid
                    break
        
        if target_song_id is None or target_song_id not in self.song_id_map:
            return []
        
        target_idx = self.song_id_map[target_song_id]
        
        if use_item2vec and getattr(self, 'hybrid_item_factors', None) is not None:
            vectors = self.hybrid_item_factors
            norms = self.hybrid_norms
            source_label = 'hybrid_item2vec_audio'
        elif use_item2vec and self.item2vec_vectors is not None:
            vectors = self.item2vec_vectors
            norms = self.item2vec_norms
            source_label = 'item2vec'
        elif self.item_factors is not None:
            vectors = self.item_factors
            norms = self.item_norms
            source_label = 'als_item'
        else:
            return []
        
        target_vec = vectors[target_idx]
        
        # Si el vector es cero (canción sin datos), no podemos recomendar
        if np.linalg.norm(target_vec) < 1e-10:
            return []
        
        # Similitud coseno contra todas las canciones
        similarities = (vectors @ target_vec) / (
            norms.flatten() * np.linalg.norm(target_vec)
        )
        
        # Excluir la canción semilla
        similarities[target_idx] = -1.0
        
        top_indices = np.argsort(similarities)[::-1][:n * 2]
        
        results = []
        for idx in top_indices:
            if len(results) >= n:
                break
            song_id = self.reverse_song_map.get(int(idx))
            if song_id is None:
                continue
            sim_score = float(similarities[idx])
            if sim_score <= 0:
                continue
            results.append({
                'song_id': song_id,
                'title': self.song_titles.get(song_id, ''),
                'artist': self.song_artists.get(song_id, ''),
                'score': sim_score,
                'source': source_label
            })
        
        return results
    
    # ─── Fase 2: Re-Ranking (Filtrado y Reglas de Negocio) ──────────
    
    def rerank(self, candidates: list[dict], 
               recent_titles: list[str] = None,
               user_id: str = None,
               temperature: float = 0.75,
               top_k_sample: int = 5,
               max_results: int = 5) -> list[dict]:
        """Aplica reglas de experiencia de usuario sobre los candidatos.
        
        Filtros aplicados:
        1. Anti-repetición: Elimina canciones que suenen en recent_titles.
        2. Filtro de score negativo: Elimina canciones con dislike del usuario.
        3. Diversidad / Temperatura: Muestrea del Top-K con probabilidad
           proporcional al score ajustado por temperatura.
        
        Args:
            candidates: Lista de dicts con 'song_id', 'title', 'score'.
            recent_titles: Títulos de canciones recientes (últimas 3-5 horas).
            user_id: ID del usuario solicitante (para filtro de dislikes).
            temperature: Factor de aleatoriedad (0.0 = determinista, 1.0 = más aleatorio).
            top_k_sample: Número de candidatos del Top a muestrear.
            max_results: Número máximo de resultados finales.
        
        Returns:
            Lista filtrada y re-rankeada de candidatos.
        """
        if not candidates:
            return []
        
        filtered = list(candidates)
        
        # 1. Anti-repetición: Filtrar canciones recientes
        if recent_titles:
            recent_lower = {t.lower().strip() for t in recent_titles if t}
            filtered = [c for c in filtered 
                       if c.get('title', '').lower().strip() not in recent_lower]
        
        # 2. Filtro de score negativo por usuario
        if user_id and self.loaded:
            user_id_str = str(user_id)
            if user_id_str in self.user_id_map:
                u_idx = self.user_id_map[user_id_str]
                filtered = [
                    c for c in filtered
                    if (u_idx, self.song_id_map.get(c.get('song_id', -1), -1)) 
                       not in self.negative_pairs
                ]
        
        if not filtered:
            return []
        
        # 3. Muestreo con temperatura del Top-K
        top_candidates = filtered[:top_k_sample]
        
        if temperature <= 0 or len(top_candidates) <= 1:
            return top_candidates[:max_results]
        
        # Calcular probabilidades con temperatura (softmax)
        scores = np.array([c.get('score', 0.0) for c in top_candidates], dtype=np.float64)
        
        # Normalizar scores a rango [0, 1] para estabilidad numérica
        score_min = scores.min()
        score_range = scores.max() - score_min
        if score_range > 0:
            scores = (scores - score_min) / score_range
        else:
            scores = np.ones_like(scores)
        
        # Aplicar temperatura: T baja → más determinista, T alta → más uniforme
        scores_tempered = scores / max(temperature, 0.01)
        exp_scores = np.exp(scores_tempered - scores_tempered.max())
        probs = exp_scores / exp_scores.sum()
        
        # Muestrear sin reemplazo
        n_sample = min(max_results, len(top_candidates))
        try:
            selected_indices = np.random.choice(
                len(top_candidates), size=n_sample, replace=False, p=probs
            )
        except ValueError:
            selected_indices = list(range(n_sample))
        
        return [top_candidates[i] for i in sorted(selected_indices)]
    
    # ─── API de Alto Nivel (para integrar con el bot) ───────────────
    
    def get_autoplay_recommendations(self, 
                                      current_title: str = None,
                                      user_ids: list[str] = None,
                                      recent_titles: list[str] = None,
                                      temperature: float = 0.75,
                                      n: int = 5) -> list[dict]:
        """API principal para autoplay: combina Item2Vec + ALS + Re-Ranking.
        
        Estrategia:
        1. Si hay current_title → Item2Vec (similitud de canción)
        2. Si hay user_ids → ALS grupal (preferencias de usuarios en la llamada)
        3. Mezcla ambas fuentes y aplica re-ranking
        
        Args:
            current_title: Título de la última canción reproducida.
            user_ids: IDs de usuarios en el canal de voz.
            recent_titles: Títulos recientes para anti-repetición.
            temperature: Parámetro de diversidad.
            n: Número de canciones a recomendar.
        
        Returns:
            Lista de dicts listos para encolar: [{'title': str, 'artist': str, ...}, ...]
        """
        if not self.loaded:
            return []
        
        # Fuente 1: Similitud de canción (Item2Vec) — para continuidad de "vibe"
        similar = []
        if current_title:
            similar = self.recommend_similar_songs(current_title, n=30, use_item2vec=True)
            if not similar:
                similar = self.recommend_similar_songs(current_title, n=30, use_item2vec=False)
        
        # Fuente 2: Preferencias de usuarios (ALS) — para personalización
        user_recs = []
        if user_ids:
            if len(user_ids) == 1:
                user_recs = self.recommend_for_user(user_ids[0], n=30)
            else:
                user_recs = self.recommend_for_group(user_ids, n=30)
        
        if not similar and not user_recs:
            return []

        # Normalizar scores de ambas fuentes a rango [0, 1] antes de combinar
        combined_dict = {}
        
        if similar:
            max_sim = max([c.get('score', 1.0) for c in similar] or [1.0])
            for c in similar:
                sid = c['song_id']
                norm_score = c.get('score', 0.0) / max_sim
                c_copy = dict(c)
                # Darle 70% de peso a la continuidad del género si hay canción sonando
                c_copy['score'] = norm_score * (0.7 if user_recs else 1.0)
                combined_dict[sid] = c_copy

        if user_recs:
            max_als = max([c.get('score', 1.0) for c in user_recs] or [1.0])
            for c in user_recs:
                sid = c['song_id']
                norm_score = c.get('score', 0.0) / max_als
                weight = 0.3 if similar else 1.0
                if sid in combined_dict:
                    combined_dict[sid]['score'] += norm_score * weight
                else:
                    c_copy = dict(c)
                    c_copy['score'] = norm_score * weight
                    combined_dict[sid] = c_copy

        deduplicated = sorted(combined_dict.values(), key=lambda x: x.get('score', 0), reverse=True)
        
        # Aplicar re-ranking
        primary_user = user_ids[0] if user_ids else None
        final = self.rerank(
            candidates=deduplicated,
            recent_titles=recent_titles,
            user_id=primary_user,
            temperature=temperature,
            top_k_sample=max(n * 3, 15),
            max_results=n
        )
        
        return final
    
    def get_song_title_by_id(self, song_id: int) -> Optional[str]:
        """Obtiene el título de una canción por su ID en la BBDD."""
        return self.song_titles.get(song_id)
    
    @property
    def stats(self) -> dict:
        """Devuelve estadísticas del motor para diagnóstico."""
        return {
            'loaded': self.loaded,
            'n_users': len(self.user_id_map),
            'n_songs': len(self.song_id_map),
            'has_als': self.user_factors is not None,
            'has_item2vec': self.item2vec_vectors is not None,
            'als_factors': self.user_factors.shape[1] if self.user_factors is not None else 0,
            'n_negative_pairs': len(self.negative_pairs),
            'last_load_time': self._last_load_time,
            'artifacts_path': ARTIFACTS_PATH
        }
