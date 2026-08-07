# 🎶 Tooodles — Discord Music Bot

> A high-performance, self-hosted Discord music bot with a **hybrid ML recommendation engine** (ALS + Item2Vec + Audio Features), native `yt-dlp` YouTube extraction, Spotify integration, persistent queues, zero-downtime deployments via GitHub Actions CI/CD, and a 3-tier queue buffering engine optimized for ARM64 cloud VMs.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [File Structure](#file-structure)
- [Module Reference](#module-reference)
  - [main.py](#mainpy)
  - [database.py](#databasepy)
  - [sznUtils.py](#sznutilspy)
  - [sznMusic/](#sznmusic)
  - [sznDB.py](#szndbpy)
  - [sznUI.py](#sznuipy)
  - [recsys/](#recsys)
  - [sznLogger.py](#sznloggerpy)
- [Commands Reference](#commands-reference)
- [Recommendation System (RecSys)](#recommendation-system-recsys)
- [Queue Persistence System](#queue-persistence-system)
- [Graceful Drain (Zero-Downtime Restarts)](#graceful-drain-zero-downtime-restarts)
- [Performance & Buffering Engine](#performance--buffering-engine)
- [Environment Variables](#environment-variables)
- [Deployment](#deployment)
  - [GitHub Actions CI/CD](#github-actions-cicd)
  - [Manual Deployment](#manual-deployment)
- [Local Development](#local-development)
- [Cookies Setup](#cookies-setup)
- [Security Notes](#security-notes)

---

## Overview

**Tooodles** is an asynchronous Python Discord music bot designed for maximum speed, low CPU consumption, and absolute playback stability. It features:

- Direct native `yt-dlp` extraction with Netscape `cookies.txt` authentication
- Instant Spotify playlist queuing with < 400ms startup
- Low-latency FFmpeg Opus passthrough (< 0.5% CPU on 1 vCPU)
- **Hybrid ML Recommendation Engine** — Implicit ALS (matrix factorization) + Item2Vec (Word2Vec) + Audio Feature vectors for personalized autoplay
- **Persistent queue recovery** across bot restarts and redeployments
- **Zero-downtime CI/CD** via GitHub Actions — push to `main` and the bot waits for the current song to end before restarting
- Database-backed favorites, dislikes, playback telemetry, saved playlists, and per-guild configuration
- **Centralized logging** with automatic rotation (5 MB × 3 backups) and dual-stream output to console + disk

The bot runs inside a Docker container (Python 3.11-slim) and is fully optimized for Oracle Cloud Free Tier (ARM64 Ampere) and any Linux VM.

---

## Key Features

- 🎵 **Multi-Source Playback** — YouTube (URLs or search queries), YouTube Music, Spotify (tracks, playlists, albums & top artist tracks), SoundCloud.
- ⚡ **Instant Spotify Playlist Loader** — Queues 100+ song Spotify playlists in **< 400ms** by resolving track 1 immediately and loading remaining tracks into queue memory.
- 🚀 **FFmpeg Opus Passthrough** — Streams WebM/Opus audio natively without CPU-heavy re-encoding (**< 0.5% CPU** on 1 vCPU). Includes `user-agent` and `Referer` headers to prevent YouTube CDN throttling.
- 🧠 **Hybrid ML Recommendation Engine** — Trains Implicit ALS (256-dim matrix factorization) and Item2Vec (256-dim Word2Vec) models on user listening history. Combines both with 8 acoustic audio features (danceability, energy, valence, tempo, etc.) for multimodal recommendations. Auto-trains every 6 hours.
- 🤖 **ML Autoplay Mode** — `td?autoplay` generates continuous recommendations using the hybrid engine with temperature-controlled softmax sampling, anti-repetition filtering, and dislike-aware re-ranking.
- 📻 **Auto-Radio & Group Radio** — `td?radio` generates 5 contextual recommendations using Spotify's genre/artist graph when the queue ends; `td?favradio` builds collective playlists from channel members' favorites.
- 💾 **Queue Persistence** — When any disconnect or restart occurs, the pending queue + current song are serialized as JSON into SQLite/PostgreSQL. Upon reconnecting with `td?j`, the full queue is restored automatically — **zero songs lost across restarts**.
- 🔄 **Zero-Downtime Graceful Drain** — On `SIGTERM`/`SIGINT`, the bot saves the queue, disables radio mode, and waits for the current song to finish before shutting down cleanly (up to 10 min timeout).
- 🤖 **GitHub Actions CI/CD** — Push to `main` and GitHub automatically SSHs into your server, pulls the latest code, rebuilds the Docker image, and triggers a graceful restart.
- 🧠 **3-Tier Hybrid Queue Buffering**:
  - **Track 1 (Playing)**: Plays smoothly with zero network interference.
  - **Track 2 (Next in Queue)**: Full audio download to `/tmp/cache_{id}.webm` with atomic rename (`.tmp` → `.webm`) for 0ms transition delay. Audio features (BPM, energy, brightness) extracted from the cached file.
  - **Track 3+ (Queued)**: Background URL pre-resolution so streams are pre-fetched before reaching the front of the queue.
- 📊 **Now Playing with Progress Bar** — `td?np` shows elapsed time, total duration, and a visual scrubber (`▬▬▬▬🔘▬▬▬▬▬▬`).
- ❤️ **User Favorites & Feedback** — Save liked songs (`td?like`), dislike songs (`td?dislike`), view personal favorites (`td?liked`), and check global stats (`td?top`).
- 📥 **Playlist Preloader** — `td?preload <URL> [@user]` bulk-imports a Spotify/YouTube playlist into the database as likes + play logs for a target user, then retrains the ML model immediately.
- 📚 **Saved Playlists** — `td?playlists` provides an interactive CRUD manager for server-wide playlist bookmarks.
- 🛡️ **Trusted Users System** — Bot owner can grant `Trusted` status to users, enabling access to admin/diagnostic commands via DM.
- 🎛️ **Interactive Discord UI** — Buttons for Pause/Resume, Skip, Stop, Radio toggle, and paginated queue navigation (`td?q`).
- 🔐 **Cookie Management** — Reads `cookies.txt` from disk or database, with Playwright Stealth fallback for automatic cookie generation via headless Chromium.
- 🏠 **Per-Guild Configuration** — Custom prefix, queue persistence toggle, restricted command channel, default radio mode — all stored in the database.
- 📋 **Centralized Logging** — `sznLogger.py` provides rotating file logs (5 MB × 3 backups), colored console output, and dual-stream `stdout`/`stderr` capture.
- 💤 **Smart Auto-Disconnect** — Disconnects after 60 seconds of inactivity (empty queue, not playing), and after 30 seconds if left alone in a voice channel.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          Discord Gateway                                │
│                 (discord.py Bot, default prefix: td?)                   │
└───────────────────────────┬──────────────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            ▼               ▼               ▼
      ┌──────────┐   ┌──────────────┐  ┌─────────┐
      │  sznDB   │   │  sznMusic/   │  │  sznUI  │
      │ (MusicDB)│   │ (MusicCore)  │  │(MusicUI)│
      └────┬─────┘   └──┬───────┬──┘  └────┬────┘
           │             │       │          │
           ▼             ▼       ▼          ▼
      ┌──────────┐  ┌─────────┐ ┌──────────────────────────────┐
      │database.py│ │recsys/  │ │        sznUtils.py           │
      │(SQLAlchemy│ │(Hybrid  │ │  yt-dlp + Queue Persistence  │
      │SQLite/PG) │ │ML RecSys│ └──────────────┬───────────────┘
      └──────────┘  │Engine)  │                │
                    └─────────┘                ▼
                                    ┌─────────────────────┐
                    sznLogger.py    │   YouTube Audio     │
                    (Rotating logs  │  (Direct Opus or    │
                     + Dual stream) │   Local Cache)      │
                                    └─────────────────────┘
```

### Cog System (discord.py Extensions)

| Cog | Class | Responsibility |
|-----|-------|----------------|
| `sznDB` | `MusicDB` | Database operations: user likes/dislikes, playback history, fuzzy title matching, `favradio`, `top`, `historial` |
| `sznMusic` | `MusicCore` | Queue state, `yt-dlp` extraction, FFmpeg playback, Spotify integration, hybrid buffering, queue persistence, radio, autoplay, RecSys training loop |
| `sznUI` | `MusicUI` | Discord button components, now-playing embeds, progress bar, paginated queue view, settings panel, help, logs, trusted users, playlists, debug diagnostics |

---

## File Structure

```
Tooodles/
├── main.py                        # Bot startup, graceful drain, signal handling, cog loading
├── database.py                    # SQLAlchemy models (Song, AppConfig, UserLike, UserDislike,
│                                  #   PlayLog, SavedPlaylist, TrustedUser) & session handlers
├── sznUtils.py                    # Audio extraction engine, queue persistence functions
├── sznLogger.py                   # Centralized logging: RotatingFileHandler (5MB×3), colored
│                                  #   console formatter, DualStreamWriter for stdout/stderr capture
├── sznMusic/                      # Modular MusicCore package: queue, FFmpeg, Spotify, buffering
│   ├── __init__.py                # MusicCore Cog, setup(bot), all user-facing commands,
│   │                              #   background loops (inactivity, cache cleanup, RecSys training)
│   ├── player.py                  # GuildPlayer state, prefetch_chunk_throttled, audio extraction,
│   │                              #   Spotify/YouTube resolvers, play_next loop, telemetry logging
│   └── radio.py                   # Radio algorithm: 3-layer Spotify genre expansion, GENRE_EXPANSION
│                                  #   map (40+ genre mappings), ML autoplay via RecSysEngine
├── recsys/                        # Hybrid ML Recommendation System
│   ├── __init__.py                # Package exports
│   ├── engine.py                  # RecSysEngine: inference engine for ALS + Item2Vec + Audio
│   │                              #   Features with cosine similarity, re-ranking, and temperature
│   │                              #   controlled softmax sampling. Responds in < 10ms.
│   └── train.py                   # Offline training pipeline: builds user-item interaction matrix
│                                  #   (5 signal types), trains Implicit ALS (256d, 50 iters) and
│                                  #   Gensim Item2Vec (256d, 100 epochs). Exports to .npz artifacts.
├── sznDB.py                       # MusicDB cog: likes, dislikes, top songs, history, group radio
├── sznUI.py                       # MusicUI cog: interactive buttons, paginated queue, settings panel,
│                                  #   help command, saved playlists CRUD, trusted users, logs viewer,
│                                  #   debug diagnostics, manual RecSys training trigger
├── alembic/                       # Database migration scripts (Alembic)
│   ├── env.py                     # Migration environment configuration
│   ├── script.py.mako             # Migration template
│   └── versions/                  # Incremental schema change scripts
├── data/                          # RecSys trained artifacts (generated, git-ignored)
│   └── recsys_artifacts.npz       # Exported model weights, embeddings, and mappings
├── logs/                          # Application runtime logs
│   └── tooodles.log               # Auto-rotated (5 MB × 3 backups)
├── requirements.txt               # Python dependencies
├── Dockerfile                     # Container definition (Python 3.11-slim + FFmpeg + Node.js 22.x)
├── Procfile                       # Process file for PaaS deployments
├── start.sh                       # One-click deployment script (git pull, docker build, docker run)
├── alembic.ini                    # Alembic configuration
├── .github/
│   └── workflows/
│       └── deploy.yml             # GitHub Actions CI/CD — auto-deploy on push to main
└── .gitignore                     # Excludes cookies.txt, .env, *.db, data/, logs/, /tmp cache
```

---

## Module Reference

### `main.py`

Bot entry point executed by Docker's `CMD`.

**Startup Workflow:**
1. Executes `setup_database()` to create/migrate database tables.
2. Checks for `cookies.txt` locally; falls back to database config, then Playwright Stealth auto-generation.
3. Sets `os.environ["cookies"]` for global availability.
4. Loads extensions: `sznDB`, `sznMusic`, `sznUI`.
5. Registers `SIGTERM`/`SIGINT` signal handlers pointing to `graceful_shutdown()`.
6. Logs into Discord using `token_priv`.

**`graceful_shutdown(bot)` Flow:**
1. Sets `bot.is_draining = True` (prevents double-execution and blocks queue overwrite events).
2. Saves pending queues to database for all guilds with persistence enabled.
3. Clears in-memory queues and disables radio mode.
4. Polls every 2 seconds until all voice clients finish playing (max 10 minutes).
5. Force-disconnects any remaining voice clients.
6. Closes the Discord connection cleanly.

---

### `database.py`

Handles database persistence via SQLAlchemy. Supports SQLite (local default with WAL mode + 5000ms busy_timeout) and PostgreSQL (`DATABASE_URL`).

#### Models

| Model | Table | Fields |
|-------|-------|--------|
| `Song` | `songs` | `id`, `title`, `url`, `artist`, `duration`, `played_count`, `spotify_id`, `genres`, `popularity`, `danceability`, `energy`, `valence`, `tempo`, `acousticness`, `instrumentalness`, `liveness`, `speechiness` |
| `AppConfig` | `config` | `key`, `value` (stores queue JSON, cookies, prefix, persist flags, channel locks, default radio) |
| `UserLike` | `likes` | `id`, `user_id`, `song_id`, `timestamp` |
| `UserDislike` | `dislikes` | `id`, `user_id`, `song_id`, `timestamp` |
| `PlayLog` | `play_logs` | `id`, `user_id`, `username`, `song_id`, `guild_id`, `played_at`, `listened_duration` (s), `completed` (0/1), `skipped_at` (s) |
| `SavedPlaylist` | `saved_playlists` | `id`, `guild_id`, `user_id`, `name`, `url`, `created_at` |
| `TrustedUser` | `trusted_users` | `user_id`, `username`, `added_at` |

#### Key Functions

| Function | Description |
|----------|-------------|
| `setup_database()` | Creates all tables and handles safe column migration for new audio feature columns. |
| `get_db_session()` | Context manager for thread-safe transactional sessions with auto-commit/rollback. |
| `log_play_event(...)` | Records playback telemetry including listen duration, completion status, and skip position. |
| `get_recsys_data()` | Exports play_logs, likes, dislikes, and song catalog for RecSys training. |
| `get_session_sequences()` | Extracts listening session sequences (30-min gap threshold) for Item2Vec training. |
| `get_cold_start_recommendations()` | Weighted sampling from play history when no ML model is available. |
| `bulk_preload_tracks(...)` | Batch-imports tracks from playlists as songs + likes + simulated play logs for a target user. |

---

### `sznUtils.py`

Audio extraction module and persistence utilities.

#### Audio Functions

| Function | Description |
|----------|-------------|
| `extract_info(query)` | Primary audio stream extractor. Uses `yt-dlp` with `player_client: ["mweb", "android_creator", "web"]` and cookies. Returns stream metadata dict. |
| `extract_flat_metadata(query)` | Rapid metadata-only lookup using `extract_flat=True` without downloading audio streams. |
| `extract_playlist_metadata(url)` | Extracts all track metadata from a YouTube/YouTube Music playlist. |
| `get_cookie_file_path()` | Returns the absolute path to a valid `cookies.txt` file (checks disk root, then database/environment). |
| `fetch_stealth_cookies()` | Launches headless Chromium via Playwright Stealth to export fresh YouTube session cookies if missing. |
| `extract_local_audio_features(path)` | Analyzes a cached `.webm` file to extract BPM, energy, and brightness metrics. |

#### Queue Persistence Functions

| Function | Description |
|----------|-------------|
| `save_guild_queue(guild_id, queue)` | Serializes and saves the queue (title, url, duration, uploader, origin, user) to `AppConfig` as JSON. |
| `load_guild_queue(guild_id)` | Loads and clears the saved queue from the database, returning the deserialized list. |
| `is_guild_persist_enabled(guild_id)` | Returns `True` if queue persistence is toggled on for the given guild. |
| `set_guild_persist_enabled(guild_id, enabled)` | Writes the `persist_queue_{guild_id}` flag to the database. |
| `save_config(key, value)` / `load_config(key)` | Generic key-value config storage/retrieval from `AppConfig`. |

---

### `sznMusic/`

Core music player package handling queue logic, playback, Spotify resolution, and persistence. Split into three modules:

#### `__init__.py` — MusicCore Cog

- Initializes Spotify API client, RecSys engine, and background task loops.
- Exposes all user-facing playback commands (`td?play`, `td?skip`, etc.).
- **Background Loops:**
  - `inactivity_check` (every 60s): Auto-disconnects if queue is empty and nothing is playing.
  - `cache_cleanup_loop` (every 30 min): Removes `.webm`/`.tmp` cache files older than 1 hour.
  - `recsys_training_loop` (every 6 hours): Retrains the ML model and hot-reloads artifacts.
- **Voice State Event Handler**: Saves queue on disconnect, auto-disconnects after 30s if left alone in a channel.

#### `player.py` — GuildPlayer & Playback

- **`GuildPlayer`**: Encapsulates per-guild state (queue, current song, voice client, radio/autoplay modes, play lock, radio history).
- **`MusicPlayerMixin`**: Methods for audio extraction, Spotify resolution (tracks, playlists, albums, artist top tracks), YouTube playlist loading, and the `play_next()` loop.
- **`prefetch_chunk_throttled()`**: Downloads full audio to `/tmp/cache_{id}.webm` using atomic `.tmp` → `.webm` rename. Extracts real audio features (BPM, energy, brightness) from cached files. Skips songs > 10 minutes.
- **`schedule_queue_optimizations()`**: 30-second delayed pre-resolution and pre-caching of the next song in queue (N+1 strategy).
- **Queue Protection Guard**: `play_next()` uses `asyncio.Lock` and checks `is_playing()` to prevent race conditions.
- **FFmpeg Opus Passthrough**: Streams native WebM/Opus with `-user_agent` and `-headers "Referer: https://www.youtube.com/"` to prevent YouTube CDN throttling.

#### `radio.py` — Radio & ML Autoplay

- **`MusicRadioMixin`**: Methods for the 3-layer radio algorithm:
  - **Layer 1**: Same artist tracks from Spotify (up to 2 songs).
  - **Layer 2**: Genre expansion using `GENRE_EXPANSION` map (40+ genre mappings for Latin/Chilean/Argentine music) with popularity-weighted sampling and artist diversity enforcement.
  - **Layer 3**: Similar artist queries via Spotify search.
  - **Fallback**: Random songs from the local database.
- **`_fill_queue_from_recsys()`**: ML autoplay using `RecSysEngine.get_autoplay_recommendations()` with cold-start fallback.
- **`GENRE_EXPANSION`**: Static dictionary mapping 40+ genres to related genres (Hip Hop, Rock, Reggaeton, Metal, Pop, Reggae, etc.) for contextual radio diversity.
- **Anti-Repetition**: Maintains a 30-song history window and filters duplicates, audiobooks, podcasts, interludes, and numeric-only titles.

---

### `sznDB.py`

Manages song statistics, favorites, dislikes, and fuzzy matching.

#### Commands & Features

- **`td?like` / `td?unlike`**: Toggle favorites for the currently playing song.
- **`td?dislike` / `td?undislike`**: Register/remove negative feedback (used by RecSys for filtering).
- **`td?liked`**: Display the user's saved favorite tracks.
- **`td?favradio [temp]`**: Queries favorites of all members in the current voice channel to build a collective personalized radio.
- **`td?top`**: Shows the most-played tracks per server (or globally as fallback).
- **`td?historial`**: Shows the most recently played tracks per server.

---

### `sznUI.py`

Provides interactive UI controls, configuration panels, and admin tools.

#### User-Facing Features

- **`notify_now_playing()`**: Sends now-playing embeds with interactive buttons (Pause/Resume, Skip, Radio Toggle, Stop, Go to Channel) and auto-deletes after 5 minutes.
- **Progress Bar in `td?np`**: Calculates elapsed time from `current_song_start_time` and renders a visual scrubber bar.
- **`td?q`**: Paginated view of the queue with 10 songs per page and navigation buttons.
- **`td?controls`**: Resends the interactive playback control buttons.
- **`td?help`**: Context-aware help — shows server commands in guilds or admin/diagnostic commands in DMs.
- **`td?playlists`**: Interactive CRUD manager for saved server playlists (add, remove, list).
- **`td?settings`**: Interactive settings panel with buttons for prefix, persistence, channel lock, and default radio configuration.

#### Admin / Trusted Features (Restricted)

- **`td?persist`**: Toggles queue persistence. If requested by non-owner, sends DM approval buttons (`✅ Authorize` / `❌ Reject`) to the bot owner.
- **`td?channel`**: Locks bot commands to a specific text channel.
- **`td?trusted [add/remove] @user`**: Manages the Trusted Users list (owner-only).
- **`td?logs [N]`**: Displays the last N lines of `tooodles.log` (Admin/Trusted).
- **`td?debug`**: System diagnostics — Discord connections, log file size, cache files, RecSys status (Admin/Trusted).
- **`td?train`**: Manually triggers RecSys offline training (Admin/Trusted).

---

### `recsys/`

Hybrid ML recommendation system with offline training and real-time inference.

#### `engine.py` — RecSysEngine (Inference)

Real-time inference engine designed to respond in **< 10ms** using NumPy/SciPy.

| Method | Description |
|--------|-------------|
| `load(force)` | Loads `.npz` artifacts. Detects file changes via `mtime` for atomic hot-reload. |
| `reload_if_updated()` | Reloads only if the artifact file on disk was modified. |
| `recommend_for_user(user_id, n)` | Personalized Top-N via ALS cosine similarity. |
| `recommend_for_group(user_ids, n)` | Group recommendations by averaging user embedding vectors. |
| `recommend_similar_songs(title, n)` | Similar songs via hybrid Item2Vec + Audio Feature cosine similarity. |
| `rerank(candidates, ...)` | Anti-repetition filter, dislike filter, temperature-controlled softmax sampling. |
| `get_autoplay_recommendations(...)` | High-level API combining Item2Vec (70% weight for vibe continuity) + ALS (30% weight for personalization), then re-ranking. |

**Hybrid Multimodal Space:**
- Item2Vec embeddings (256d) are concatenated with normalized Audio Feature vectors (8d × 0.5 weight) to create a combined multimodal embedding space for similarity search.

#### `train.py` — Offline Training Pipeline

Designed to run every 6 hours via background loop or manually via `td?train`.

**Pipeline Steps:**
1. Extract data from database (`PlayLog`, `UserLike`, `UserDislike`, `Song`).
2. Sync Spotify audio features (danceability, energy, valence, tempo, etc.).
3. Build user-item interaction matrix with 5 weighted signal types:
   - `+5.0` for explicit like
   - `-5.0` for explicit dislike
   - `+2.0` for completing ≥ 80% of a song
   - `+1.0` for each play event
   - `-3.0` for skipping within first 30 seconds
4. Train Implicit ALS (256 factors, 50 iterations, L2 regularization = 0.05).
5. Extract session sequences (30-min gap threshold) and train Item2Vec (256d, window=10, Skip-gram, 100 epochs).
6. Build normalized 8-feature audio matrix (danceability, energy, valence, tempo, acousticness, instrumentalness, liveness, speechiness).
7. Export all artifacts to `data/recsys_artifacts.npz`.

**Resource Profile:** < 500MB RAM, < 2 min on ARM64 Ampere (1 vCPU).

---

### `sznLogger.py`

Centralized logging system.

- **`DualStreamWriter`**: Intercepts all `print()` and `sys.stderr` output, duplicating it to `logs/tooodles.log` with timestamps. Strips ANSI color codes for clean file output.
- **`RotatingFileHandler`**: 5 MB per file, 3 backup files (`tooodles.log`, `tooodles.log.1`, `tooodles.log.2`, `tooodles.log.3`).
- **`ColoredFormatter`**: ANSI-colored log levels for console output (Cyan=DEBUG, Green=INFO, Yellow=WARNING, Red=ERROR).
- **`get_logger(submodule)`**: Returns a child logger under the `tooodles` namespace (e.g., `tooodles.recsys`, `tooodles.bot`).

---

## Commands Reference

> **Default prefix:** `td?` (configurable per guild via `td?settings`)

### Connection & Playback

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?j` | `join`, `connect`, `conectar`, `unir` | Connect to your voice channel and restore any saved queue. |
| `td?p <query>` | `play` | Play a song (YouTube search, YouTube URL, Spotify track/playlist/album/artist). |
| `td?s` | `skip` | Skip the currently playing song. |
| `td?pause` | — | Pause audio playback. |
| `td?resume` | `r`, `reanudar` | Resume paused audio or start playing a restored queue. |
| `td?stop` | `disconnect`, `leave`, `exit`, `dc` | Stop playback, save queue to DB (if persist enabled), and disconnect. |
| `td?np` | `nowplaying` | Show now-playing embed with progress bar and elapsed time. |
| `td?controls` | `ctr`, `player` | Resend the interactive playback buttons. |

### Queue Management

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?q` | `queue`, `cola` | Paginated queue view (10 songs per page with navigation buttons). |
| `td?shuffle` | — | Randomly shuffle all queued tracks. |
| `td?remove <index>` | — | Remove a track from the queue by position number. |
| `td?move <from> <to>` | — | Move a queued track from one position to another. |
| `td?clear` | `clean`, `cq` | Clear the entire queue (in-memory + database) without stopping current song. |
| `td?search <query>` | — | Search YouTube and queue the best result. |

### Radio, Autoplay & Favorites

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?radio [off]` | — | Toggle auto-radio mode. Generates 5 Spotify-based recommendations when queue ends. |
| `td?autoplay [off]` | — | Toggle ML autoplay mode. Uses RecSys hybrid engine (ALS + Item2Vec) for smart recommendations. |
| `td?favradio [temp]` | — | Build a group radio from all channel members' liked songs (temp: 0.0–1.0). |
| `td?like` | — | Save the current song to your personal favorites (+5.0 signal for RecSys). |
| `td?unlike` | — | Remove the current song from your favorites. |
| `td?dislike` | — | Mark the current song as disliked (-5.0 signal for RecSys). |
| `td?undislike` | — | Remove the dislike mark from the current song. |
| `td?liked` | — | View your personal favorite songs list. |
| `td?historial` | — | View the most recently played tracks in this server. |
| `td?top` | — | View the most played songs in this server. |

### Playlists & Preload

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?playlists` | `pl`, `playlist` | Interactive saved playlists manager (list, add, remove). |
| `td?playlists add <URL> [Alias]` | — | Save a playlist URL with an optional alias. |
| `td?playlists remove <Name>` | — | Delete a saved playlist by name. |
| `td?preload <URL> [@user]` | — | Bulk-import a Spotify/YouTube playlist into the DB as likes + play logs for a user, then retrain RecSys. |

### Server Configuration

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?persist` | `persistencia` | Toggle queue persistence. Sends DM approval request to bot owner if requested by admin. |
| `td?channel` | `canal` | Lock bot commands to a specific text channel. |
| `td?settings` | `config` | Interactive settings panel with buttons for prefix, persistence, channel, default radio. |
| `td?help` | `ayuda`, `h` | Context-aware help guide (server commands in guilds, admin commands in DMs). |

### Admin & Diagnostics (Owner / Trusted / Admin)

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?trusted [add/remove] @user` | `trust` | Manage the Trusted Users list (owner-only). |
| `td?logs [N]` | `log` | Display the last N lines of `tooodles.log` (max 50). |
| `td?debug` | `status`, `botstatus` | System diagnostics: Discord connections, log size, cache, RecSys status. |
| `td?status` | `health`, `diag`, `diagnostico` | Technical health panel: disk usage, WAL mode, RecSys stats, Python/discord.py version (DM-only for Trusted). |
| `td?train` | `recsys_train` | Manually trigger RecSys offline training pipeline. |
| `td?reloadrecsys` | — | Hot-reload RecSys artifacts from disk without retraining. |

---

## Recommendation System (RecSys)

Tooodles includes a full hybrid recommendation engine that learns from user listening behavior to generate personalized music suggestions.

### How it Works

```
                     ┌──────────────────────────────────┐
                     │      PlayLog + UserLike +         │
                     │  UserDislike + Song catalog        │
                     └────────────┬─────────────────────┘
                                  │
                    ┌─────────────┼──────────────┐
                    ▼                            ▼
        ┌─────────────────────┐      ┌─────────────────────┐
        │  Interaction Matrix │      │  Session Sequences   │
        │  R(u,i) with 5      │      │  (30-min gap split)  │
        │  weighted signals   │      │                      │
        └────────┬────────────┘      └──────────┬──────────┘
                 │                               │
                 ▼                               ▼
        ┌─────────────────────┐      ┌─────────────────────┐
        │  Implicit ALS       │      │  Gensim Item2Vec    │
        │  256 factors        │      │  256 dims, Skip-gram│
        │  50 iterations      │      │  100 epochs         │
        └────────┬────────────┘      └──────────┬──────────┘
                 │                               │
                 ▼                               ▼
        ┌─────────────────────┐      ┌─────────────────────┐
        │  User Embeddings    │      │ Song Embeddings +   │
        │  (n_users × 256)    │      │ Audio Features (8d) │
        └────────┬────────────┘      │ → Hybrid Space      │
                 │                   └──────────┬──────────┘
                 │                               │
                 └───────────┬───────────────────┘
                             │
                             ▼
                 ┌─────────────────────────┐
                 │  Hybrid Re-Ranking      │
                 │  • Anti-repetition      │
                 │  • Dislike filtering    │
                 │  • Softmax sampling    │
                 │    (temperature T)      │
                 └────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │  Top-5 Recommendations  │
                 │  → Enqueued as songs    │
                 └─────────────────────────┘
```

### Signal Weights

| Signal | Weight | Description |
|--------|--------|-------------|
| `td?like` | +5.0 | Explicit positive feedback |
| `td?dislike` | -5.0 | Explicit negative feedback |
| Completed (≥ 80%) | +2.0 | Song was listened to near completion |
| Played / Requested | +1.0 | The user queued the song |
| Early skip (< 30s) | -3.0 | Song was skipped within 30 seconds |

### Inference Strategy

1. **Item2Vec (70% weight)** → Finds songs with similar "vibe" to the currently playing track using the hybrid multimodal embedding space (Item2Vec + Audio Features).
2. **ALS (30% weight)** → Personalizes recommendations based on the users present in the voice channel (single user or group average).
3. **Re-ranking** → Filters recently played titles, removes disliked songs, and applies temperature-controlled softmax sampling for controlled diversity.

---

## Queue Persistence System

Queue persistence allows the bot to recover pending songs after any type of disconnection or restart — no songs lost.

### How it Works

```
[Bot receives SIGTERM / td?stop / disconnects from voice]
         │
         ▼
  is_guild_persist_enabled(guild_id)?
         │ YES
         ▼
  save_guild_queue(guild_id, current_song + song_queue)
  → Serialized as JSON in AppConfig: "queue_{guild_id}"
         │
         ▼
  [Bot restarts / Graceful Drain completes]
         │
         ▼
  User types: td?j (join)
         │
         ▼
  connect_to_voice() → load_guild_queue(guild_id)
  → Deserializes JSON → pushes songs back into player.song_queue
  → Calls play_next() automatically
         │
         ▼
  📥 "Cola recuperada: Se restauraron N canciones" ✅
```

### When the Queue is Saved

| Trigger | Behavior |
|---------|----------|
| `SIGTERM` / `SIGINT` (restart/deploy) | `graceful_shutdown` saves queue, waits for current song to end |
| `td?stop` / `td?dc` | Saves queue before disconnecting if persist is enabled |
| External disconnect (voice state event) | Saves queue unless `bot.is_draining` is set (prevents race condition) |
| Left alone in voice channel (30s) | Saves queue and auto-disconnects |
| `td?clear` | Clears both in-memory queue **and** the database record |

### Enabling Persistence

Enable persistence for a guild via the bot or directly in the database:

```bash
docker exec tooodles python3 -c "
from database import setup_database
from sznUtils import set_guild_persist_enabled
setup_database()
set_guild_persist_enabled(YOUR_GUILD_ID, True)
print('Persistence enabled.')
"
```

---

## Graceful Drain (Zero-Downtime Restarts)

When the server receives a deploy signal (e.g., from GitHub Actions), the bot does **not** cut the music mid-song. Instead:

1. `SIGTERM` is caught → `graceful_shutdown()` is invoked.
2. `bot.is_draining = True` is set.
3. Pending queues are saved to the database per guild.
4. Radio mode is disabled.
5. In-memory queues are cleared.
6. The bot polls every 2 seconds until all active voice clients stop playing.
7. The bot disconnects cleanly and closes the Discord connection.
8. Docker starts the new container with the latest code.
9. Users rejoin music with `td?j` — queue is restored automatically.

**Maximum wait time before forced shutdown:** 10 minutes.

---

## Performance & Buffering Engine

Tooodles uses a 3-tier hybrid buffering pipeline designed for instant startup and zero playback stuttering:

```
[User adds Spotify Playlist (100+ tracks)]
        │
        ├── Track 1: Resolved immediately (<400ms) ➔ FFmpegPCMAudio
        │            + user-agent & Referer headers (prevents CDN throttling)
        │
        ├── Track 2: Pushed to Queue ➔ prefetch_chunk_throttled()
        │            (Full audio download to /tmp/cache_{id}.webm)
        │            + Atomic .tmp → .webm rename (prevents truncated reads)
        │            + Audio feature extraction (BPM, Energy, Brightness)
        │
        └── Tracks 3..100+: Pushed to Queue memory instantly ➔ URL pre-resolution
                            (30-second delayed pre-fetch as queue advances)
```

**CPU Profile on Oracle Cloud Ampere (ARM64, 1 vCPU):**

| Operation | CPU Usage |
|-----------|-----------|
| FFmpeg Opus passthrough (streaming) | < 0.5% |
| `yt-dlp` stream extraction | ~2–4% (< 1 second) |
| Audio pre-download (full file) | < 1% |
| Spotify playlist load (100+ tracks) | ~1–2% for < 400ms |
| RecSys inference (recommendations) | < 0.1% (< 10ms) |
| RecSys training (offline) | ~50% for < 2 min |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `token_priv` | ✅ Yes | Discord Bot Token |
| `client_id` | Optional | Spotify API Client ID (enables Spotify playback + radio + RecSys audio features) |
| `client_secret` | Optional | Spotify API Client Secret |
| `DATABASE_URL` | Optional | PostgreSQL URI (defaults to `sqlite:///tooodles.db`) |
| `FERNET_KEY` | Optional | 32-byte Fernet key for symmetric encryption (`cryptography.fernet`) of session cookies stored in `AppConfig`. If absent, cookies are stored as plaintext. |

---

## Deployment

### GitHub Actions CI/CD

Tooodles ships with a GitHub Actions workflow at `.github/workflows/deploy.yml` that automatically deploys on every push to `main`.

**How it works:**
1. You push code to `main` from your local machine.
2. GitHub Actions triggers, SSHs into your server using stored secrets.
3. Runs `./start.sh` on the server: `git pull` → `docker build` → `docker run`.
4. The running bot receives `SIGTERM` from Docker and enters Graceful Drain (60s timeout).
5. The current song finishes. The new container starts. Music resumes via `td?j`.

**Required GitHub Secrets:**

| Secret | Description |
|--------|-------------|
| `VPS_HOST` | Your server IP address |
| `VPS_USERNAME` | SSH login username (e.g., `ubuntu`) |
| `VPS_SSH_KEY` | Private SSH key (PEM format, content of `~/.ssh/id_rsa`) |

**Setting up secrets:**
Go to your GitHub repo → Settings → Secrets and variables → Actions → New repository secret.

### Manual Deployment

SSH into your server and run:

```bash
chmod +x start.sh && ./start.sh
```

**What `start.sh` executes:**
1. `git fetch origin main && git reset --hard origin/main` — Force-syncs to latest remote.
2. `docker build -t tooodles-bot .` — Rebuilds the image with latest code.
3. `docker stop -t 60 tooodles` — Sends `SIGTERM` and waits up to 60s for graceful drain.
4. `docker rm tooodles` — Removes the old container.
5. `docker run -d --name tooodles --restart always --env-file .env` — Launches with persistent volume mounts for `tooodles.db`, `cookies.txt`, `logs/`, and `data/`.

**Persistent Docker Volumes:**

| Host Path | Container Path | Purpose |
|-----------|----------------|---------|
| `~/Tooodles/tooodles.db` | `/app/tooodles.db` | SQLite database |
| `~/Tooodles/cookies.txt` | `/app/cookies.txt` | YouTube session cookies |
| `~/Tooodles/logs/` | `/app/logs/` | Rotating log files |
| `~/Tooodles/data/` | `/app/data/` | RecSys trained artifacts (.npz) |

---

## Local Development

```bash
# Install Python dependencies
pip install -r requirements.txt

# Set required environment variables
export token_priv="YOUR_DISCORD_BOT_TOKEN"
export client_id="YOUR_SPOTIFY_CLIENT_ID"       # Optional
export client_secret="YOUR_SPOTIFY_CLIENT_SECRET" # Optional

# Run the bot
python main.py
```

> **Tip:** Place `cookies.txt` in the project root for YouTube authentication.

### Training the RecSys Manually

```bash
python -m recsys.train
```

This exports trained artifacts to `data/recsys_artifacts.npz`. The bot auto-reloads them on the next recommendation request.

---

## Cookies Setup

YouTube requires session cookies to serve audio streams from cloud server IPs.

1. Open an **Incognito Browser Window** and navigate to `https://www.youtube.com/robots.txt`.
2. Export cookies using the browser extension **"Get cookies.txt LOCALLY"** (Chrome/Firefox).
3. Save the output file as `cookies.txt` in the `~/Tooodles/` directory on your server.
4. Run `./start.sh` — cookies are loaded automatically on startup.

**Alternative (Automatic):** If no `cookies.txt` is found, the bot will attempt to generate fresh cookies via Playwright Stealth (headless Chromium). This requires Playwright to be installed in the container (included in the Dockerfile).

---

## Security Notes

- `cookies.txt` is in `.gitignore` and must **never** be committed to Git.
- The `token_priv` Discord token is passed via Docker environment variables (`.env` file), never hardcoded.
- The `FERNET_KEY` symmetrically encrypts session cookies via `cryptography.fernet` before storing them in `AppConfig`. Even if the database file is exposed, raw cookie strings are not readable without the key.
- SSH keys used by GitHub Actions should be dedicated deploy keys with minimal permissions.
- The `TrustedUser` system restricts diagnostic commands (`logs`, `debug`, `train`) to explicitly authorized users.
- The `data/recsys_artifacts.npz` file is git-ignored and contains only model embeddings — no raw user data.

---

## License & Credits

Built with ❤️ for music lovers.
Powered by [`discord.py`](https://github.com/Rapptz/discord.py), [`yt-dlp`](https://github.com/yt-dlp/yt-dlp), [`spotipy`](https://github.com/spotipy-dev/spotipy), [`FFmpeg`](https://ffmpeg.org/), [`Implicit`](https://github.com/benfred/implicit), [`Gensim`](https://github.com/piskvorky/gensim), and [`RapidFuzz`](https://github.com/maxbachmann/RapidFuzz).
