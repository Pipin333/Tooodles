# 🎶 Tooodles — Discord Music Bot

> A high-performance, self-hosted Discord music bot built with `discord.py`, native `yt-dlp` YouTube extraction with Node.js 22 EJS challenge solving, Spotify integration, and a hybrid queue buffering engine optimized for low-resource cloud VMs.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [File Structure](#file-structure)
- [Module Reference](#module-reference)
  - [main.py](#mainpy)
  - [database.py](#databasepy)
  - [sznUtils.py](#sznUtilspy)
  - [sznMusic.py](#sznMusicpy)
  - [sznDB.py](#sznDBpy)
  - [sznUI.py](#sznUIpy)
- [Commands Reference](#commands-reference)
- [Performance & Buffering Engine](#performance--buffering-engine)
- [Environment Variables](#environment-variables)
- [Deployment (Oracle Cloud / Docker)](#deployment-oracle-cloud--docker)
- [Local Development](#local-development)
- [Cookies Setup](#cookies-setup)
- [Security Notes](#security-notes)

---

## Overview

**Tooodles** is an asynchronous Python Discord music bot designed for maximum speed, low CPU consumption, and absolute playback stability. It features direct native `yt-dlp` extraction with Netscape `cookies.txt` authentication, instant Spotify playlist queuing, low-latency FFmpeg Opus passthrough (<0.5% CPU on 1 vCPU), and a database-backed favorites and history tracking system.

The bot runs inside a Docker container (Python 3.11 + Node.js 22.x) and is fully optimized to run smoothly on any Linux VM, including Oracle Cloud Free Tier.

---

## Key Features

- 🎵 **Multi-Source Playback** — YouTube (URLs or search queries), Spotify tracks and playlists, SoundCloud.
- ⚡ **Instant Spotify Playlist Loader** — Queues 35+ song Spotify playlists in **< 400ms** by resolving track 1 immediately and loading remaining tracks into queue memory.
- 🚀 **FFmpeg Opus Passthrough** — Uses `discord.FFmpegOpusAudio.from_probe()` to stream WebM/Opus audio natively without CPU-heavy re-encoding (**< 0.5% CPU** on 1 vCPU).
- 🛡️ **Native `yt-dlp` EJS Solver** — Built-in Node.js 22.x runtime with GitHub EJS remote components to automatically solve YouTube JavaScript `n-token` and signature challenges.
- 🧠 **Hybrid Queue Optimization Engine**:
  - **Track 1 (Playing)**: Plays smoothly with zero network interference.
  - **Track 2 (Next in Queue)**: Throttled 64 KB chunk pre-buffering to `/tmp/cache_{id}.webm` for 0ms track transition delay.
  - **Track 3+ (Queued)**: Background URL pre-resolution so streams are pre-fetched before reaching the front of the queue.
- ❤️ **User Favorites & History** — Save liked songs (`td?like`), view personal top tracks (`td?liked`), and check global playback stats (`td?top`).
- 📻 **Auto-Radio & Group Radio** — `td?radio` auto-plays from database history when queue finishes; `td?favradio` generates a collective playlist based on channel members' favorites.
- 🎛️ **Interactive Discord UI** — Buttons for Pause/Resume, Skip, Stop, and paginated queue navigation (`td?queueui`).
- 🔐 **Cookie Management** — Reads `cookies.txt` from disk/database, with Playwright Stealth fallback for automatic cookie generation.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Discord Gateway                          │
│                  (discord.py Bot, prefix: td?)                  │
└───────────────────────┬─────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  ┌──────────┐   ┌──────────────┐  ┌─────────┐
  │  sznDB   │   │  sznMusic    │  │  sznUI  │
  │ (MusicDB)│   │ (MusicCore)  │  │(MusicUI)│
  └────┬─────┘   └──────┬───────┘  └────┬────┘
       │                │               │
       ▼                ▼               ▼
  ┌──────────┐   ┌──────────────────────────────┐
  │database.py│   │          sznUtils.py          │
  │(SQLAlchemy│   │  yt-dlp + Node 22 EJS Solver  │
  │SQLite/PG) │   └──────────────┬──────────────┘
  └──────────┘                  │
                                ▼
                     ┌─────────────────────┐
                     │   YouTube Audio     │
                     │  (Direct Opus Stream│
                     │   or Local Cache)   │
                     └─────────────────────┘
```

### Cog System (discord.py Extensions)

| Cog | Class | Responsibility |
|-----|-------|----------------|
| `sznDB` | `MusicDB` | Database operations: user likes, playback history, fuzzy title matching, `favradio` |
| `sznMusic` | `MusicCore` | Queue state, `yt-dlp` extraction, FFmpeg playback, Spotify integration, hybrid buffering |
| `sznUI` | `MusicUI` | Discord button components, now-playing embeds, paginated queue view |

---

## File Structure

```
Tooodles/
├── main.py            # Bot startup, cookie initialization, cog loading
├── database.py        # SQLAlchemy models (Song, AppConfig, UserLike) & session handlers
├── sznUtils.py        # Audio extraction engine (yt-dlp + cookies + Node 22 EJS solver)
├── sznMusic.py        # MusicCore cog: queue control, FFmpeg playback, Spotify, hybrid buffer
├── sznDB.py           # MusicDB cog: likes, top songs, history, group radio
├── sznUI.py           # MusicUI cog: interactive buttons, paginated queue UI, embeds
├── requirements.txt   # Python dependencies (discord.py, yt-dlp[default], yt-dlp-ejs, etc.)
├── Dockerfile         # Container definition (Python 3.11 + FFmpeg + Node.js 22.x)
├── start.sh           # One-click deployment script (git pull, docker build, docker run)
└── .gitignore         # Excludes cookies.txt, .env, *.db, /tmp cache files
```

---

## Module Reference

### `main.py`

Bot entry point executed by Python or Docker's `CMD`.

**Startup Workflow:**
1. Executes `setup_database()` to create database tables.
2. Checks for `cookies.txt` locally; falls back to database config, then Playwright Stealth auto-generation.
3. Sets `os.environ["cookies"]` for global availability.
4. Loads extensions: `sznDB`, `sznMusic`, `sznUI`.
5. Logs into Discord using `token_priv`.

---

### `database.py`

Handles database persistence via SQLAlchemy. Supports SQLite (local default) and PostgreSQL (`DATABASE_URL`).

#### Models

| Model | Table | Fields |
|-------|-------|--------|
| `Song` | `songs` | `id`, `title`, `url` (YouTube ID), `artist`, `duration`, `played_count` |
| `AppConfig` | `config` | `key`, `value` (encrypted bot settings/cookies) |
| `UserLike` | `likes` | `id`, `user_id`, `song_id`, `timestamp` |

---

### `sznUtils.py`

Audio extraction module built natively around `yt-dlp`.

#### Main Functions

| Function | Description |
|----------|-------------|
| `extract_info(query)` | Primary audio stream extractor. Uses `yt-dlp` with `cookiefile`, `js_runtimes={"node": {"path": node_path}}`, and `remote_components=["ejs:github"]`. Returns stream metadata dict. |
| `extract_flat_metadata(query)` | Rapid metadata-only lookup using `extract_flat=True` without downloading audio streams. |
| `get_cookie_file_path()` | Returns the absolute path to a valid `cookies.txt` file (checks disk root, then database/environment). |
| `fetch_stealth_cookies()` | Launches headless Chromium via Playwright Stealth to export fresh YouTube session cookies if missing. |

---

### `sznMusic.py`

Core music player handling queue logic, playback performance, and Spotify track resolution.

#### Key Systems

- **Instant Spotify Playlist Loader (`add_playlist_from_spotify`)**: Resolves track 1 immediately (<400ms) and pushes tracks 2..N into queue memory instantly.
- **Throttled Chunk Pre-buffering (`prefetch_chunk_throttled`)**: Downloads 2 MB of the next song in 64 KB chunks with 20ms micro-pauses (`asyncio.sleep(0.02)`), avoiding CPU/network spikes while the active song plays.
- **Queue Protection Guard**: `play_next()` checks `if self.voice_client.is_playing(): return`, ensuring adding new songs with `td?p` never interrupts active playback.
- **FFmpeg Opus Passthrough**: Uses `discord.FFmpegOpusAudio.from_probe(target_path, before_options='-probesize 32k -analyzeduration 0')` to pass native WebM/Opus streams directly to Discord.

---

### `sznDB.py`

Manages song statistics, favorites, and fuzzy matching.

#### Features
- **`td?like` / `td?unlike`**: Toggle favorites for the currently playing song.
- **`td?liked`**: Display the user's saved favorite tracks.
- **`td?favradio`**: Queries favorites of all members in the current voice channel to build a collective radio.
- **`td?top` / `td?historial`**: Shows most played tracks and recent playback history.

---

### `sznUI.py`

Provides interactive UI controls via Discord Buttons and Embeds.

- **`notify_now_playing()`**: Sends now-playing embeds with interactive buttons (Pause/Resume, Skip, Stop) and auto-deletes after 300 seconds.
- **`td?queueui`**: Paginated view of the queue with 10 songs per page and navigation controls (First, Prev, Next, Last).

---

## Commands Reference

> **Prefix:** `td?`

### Playback Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `td?p <query>` | `play` | Play a song (YouTube search, YouTube URL, Spotify track or playlist URL). |
| `td?s` | `skip` | Skip the currently playing song. |
| `td?pause` | — | Pause audio playback. |
| `td?resume` | — | Resume paused audio. |
| `td?stop` | — | Stop playback, clear queue, disconnect from voice. |
| `td?np` | `nowplaying` | Display details of the song currently playing. |
| `td?q` | `queue` | Display the next 10 songs in the queue. |
| `td?queueui` | — | Interactive paginated queue menu with navigation buttons. |
| `td?controls` | — | Resend the interactive playback control buttons. |
| `td?shuffle` | — | Randomly shuffle all queued tracks. |
| `td?remove <index>` | — | Remove a track from the queue by its position number. |
| `td?move <from> <to>` | — | Move a queued track from one position to another. |
| `td?search <query>` | — | Search YouTube and queue the best result. |

### Radio & Favorites Commands

| Command | Description |
|---------|-------------|
| `td?radio [0.0–1.0]` | Toggle auto-radio mode when queue ends (default temperature: 0.75). |
| `td?radio off` | Turn off auto-radio mode. |
| `td?favradio [temp]` | Generate a group radio based on channel members' liked songs. |
| `td?like` | Save the current song to your personal favorites. |
| `td?unlike` | Remove the current song from your favorites. |
| `td?liked` | View your personal favorite songs list. |
| `td?historial` | View the last 10 played tracks. |
| `td?top` | View the top 10 most played songs of all time. |

---

## Performance & Buffering Engine

Tooodles uses a 3-tier hybrid buffering pipeline designed for instant startup and zero playback stuttering:

```
[User adds Spotify Playlist (35 tracks)]
        │
        ├── Track 1: Resolved immediately (<400ms) ➔ Sent to FFmpegOpusAudio
        │
        ├── Track 2: Pushed to Queue ➔ prefetch_chunk_throttled()
        │            (Downloads 2 MB in 64 KB chunks + 20ms sleep ➔ 0% CPU impact)
        │
        └── Tracks 3..35: Pushed to Queue memory instantly ➔ URL pre-resolution
                         (Pre-fetches stream URLs in background as queue advances)
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `token_priv` | ✅ Yes | Discord Bot Token |
| `client_id` | Optional | Spotify API Client ID |
| `client_secret` | Optional | Spotify API Client Secret |
| `DATABASE_URL` | Optional | PostgreSQL URI (Defaults to `sqlite:///tooodles.db`) |
| `FERNET_KEY` | Optional | 32-byte Fernet key for encrypting stored cookies |

---

## Deployment (Oracle Cloud / Docker)

### Automated One-Click Deployment

Execute on your server:

```bash
./start.sh
```

**What `start.sh` executes:**
1. `git pull` — Pulls latest updates.
2. `docker stop tooodles` & `docker rm tooodles` — Cleans up previous container.
3. `docker build -t tooodles .` — Builds image with Node.js 22.x and Python dependencies.
4. `docker run -d` — Launches container with volume mounts and env variables.

> ⚠️ Always deploy using `./start.sh` to ensure Docker rebuilds the image with the latest Node.js and code updates.

---

## Cookies Setup

YouTube requires session cookies to serve video audio streams to cloud server IPs.

1. Export cookies from an **Incognito Browser Window** (`https://www.youtube.com/robots.txt`) using the extension **Get cookies.txt LOCALLY**.
2. Save/upload the output file to `~/Tooodles/cookies.txt` on your server.
3. Run `./start.sh`.

---

## License & Credits

Built with ❤️ for music lovers. Powered by `discord.py`, `yt-dlp`, `spotipy`, and `FFmpeg`.
