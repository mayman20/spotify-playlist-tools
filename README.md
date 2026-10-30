# Playlist Automations for Spotify

Three small Python scripts for looking after a Spotify library: one mirrors Liked Songs into a playlist, one keeps a combined "vault" playlist in sync, and one builds monthly discovery playlists. They began as a personal tool and are shared here as a snapshot. This project is not affiliated with or endorsed by Spotify.

## Workflows

### Liked Songs mirror

`scripts/liked_add/liked_add.py`

Mirrors your Liked Songs into a named playlist, newest first. It pages through the full list, finds the target playlist by name and replaces its contents in batches so the two match exactly.

### Vault sync

`scripts/vaulted_add/vaulted_add.py`

Keeps an existing playlist you own named `_vaulted` (create it first) in step with your Liked Songs plus every other playlist you own, including any written by the other two scripts. Playlists with `-*` in their description are skipped as sources. Each run adds missing tracks and removes ones that no longer appear in any source. It never removes tracks from source playlists.

> **`_vaulted` is a synchronizer, not an archive.** Any track that is neither liked nor in an included playlist is dropped from `_vaulted` on the next run, even one you added by hand.

### Monthly discovery

`scripts/monthly_recommend/monthly_recommend.py`

Gathers candidate tracks from up to four sources:

- Co-occurrence in playlists named after an artist
- Co-occurrence across multiple seed artists
- Last.fm similar artists (optional)
- Last.fm similar tracks (optional)

The Last.fm sources need a Last.fm API key. Results go into separate monthly playlists that the script creates or overwrites. It excludes top/recent tracks plus tracks from an owned playlist tagged `[spotipy:vaulted_add]` in its description, falling back to Liked Songs if no tagged playlist is found. An untagged `_vaulted` playlist is not automatically recognized by the discovery script. These are heuristics, not a trained machine-learning recommender.

## Architecture

Each workflow is one Python script in its own folder under `scripts/`, run directly from the command line. This package has no server or web interface.

- **Spotipy** calls the Spotify Web API and handles OAuth, caching tokens locally.
- **python-dotenv** loads settings from a local `.env` file.
- Python's **urllib.request** handles the optional Last.fm queries over HTTPS.
- Defaults such as target playlist names live in the code; there are no command-line flags.

## Before you run: these scripts modify your playlists

Running a script makes real changes to your Spotify account, and there is no preview or dry-run mode.

- `liked_add.py` can create or overwrite its target playlist; anything in it that isn't in Liked Songs is removed.
- `vaulted_add.py` both adds tracks to and removes tracks from `_vaulted`.
- `monthly_recommend.py` creates its monthly playlists or overwrites their contents.

Check the playlist names defined in each script, and make a copy of any playlist you care about before the first run.

## Requirements and setup

- Python 3.10 or newer
- A Spotify account and your own app registered in the Spotify Developer Dashboard, with a redirect URI configured. Access and redirect rules change over time, so check the [Spotify Web API documentation](https://developer.spotify.com/documentation/web-api) first.
- Optional: a Last.fm API key for the Last.fm sources

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # Windows: copy .env.example .env
```

Fill in `.env` with your own app's values, following `.env.example`. Keep `.env` and the local token cache private and out of version control.

## Usage

From the repository root, with the virtual environment active, run one script by its path:

```bash
python scripts/liked_add/liked_add.py
```

```bash
python scripts/vaulted_add/vaulted_add.py
```

```bash
python scripts/monthly_recommend/monthly_recommend.py
```

The first run takes you through Spotify's authorization flow; later runs reuse the cached token.

## Limitations

- These scripts haven't been tested against a live Spotify account while preparing this release.
- Spotify's API policies and endpoint availability change, and new developer apps may not have access to everything these scripts use.
- Recommendation quality varies with the playlist and Last.fm data available.

## Offline checks

```bash
python -m unittest discover -s tests -v
```

The tests use fake clients and never authorize Spotify or modify a real playlist. This public snapshot includes bounded mirror retries, an ownership check before reusing a monthly playlist, and HTTPS for Last.fm requests. It is a curated copy of the original tools; private listening data, token caches and deployment files are excluded.

## AI assistance

Claude and ChatGPT helped during development, including with writing and revising code, and this README was drafted with AI assistance. Not all of the code was written by hand.
