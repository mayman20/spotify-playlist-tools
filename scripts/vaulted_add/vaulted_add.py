import spotipy
from spotipy.oauth2 import SpotifyOAuth
import sys
import os
import time
import itertools
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
CACHE_PATH = PROJECT_ROOT / ".cache" / "spotipy_general.cache"
CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
load_dotenv(dotenv_path=ENV_PATH)

client_id=os.getenv('SPOTIPY_CLIENT_ID')
client_secret=os.getenv('SPOTIPY_CLIENT_SECRET')
redirect_uri=os.getenv('SPOTIPY_REDIRECT_URI')
scope = 'user-top-read user-library-read playlist-modify-public playlist-modify-private playlist-read-private playlist-read-collaborative'

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=client_id,
                                               client_secret=client_secret,
                                               redirect_uri=redirect_uri,
                                               scope=scope,
                                               cache_path=str(CACHE_PATH)))

EXCLUDE_DESCRIPTION_FLAG = '-*'


class ProgressBar:
    """
    A class to display a progress bar with an optional spinner.
    """
    spinner_cycle = itertools.cycle(['|', '/', '-', '\\'])

    def __init__(self, total, prefix='', suffix='', decimals=1, bar_length=50, spinner=True):
        """
        Initializes the ProgressBar.

        :param total: Total iterations (int)
        :param prefix: Prefix string (str)
        :param suffix: Suffix string (str)
        :param decimals: Number of decimals in percent complete (int)
        :param bar_length: Length of the progress bar (int)
        :param spinner: Whether to display a spinner (bool)
        """
        self.total = total
        self.prefix = prefix
        self.suffix = suffix
        self.decimals = decimals
        self.bar_length = bar_length
        self.spinner = spinner
        self.iteration = 0

    def update(self, iteration=None):
        """
        Updates the progress bar.

        :param iteration: Current iteration (int). If None, increments by 1.
        """
        if iteration is not None:
            self.iteration = iteration
        else:
            self.iteration += 1

        percent = f"{100 * (self.iteration / float(self.total)):.{self.decimals}f}"
        filled_length = int(round(self.bar_length * self.iteration / float(self.total)))
        bar = '#' * filled_length + '-' * (self.bar_length - filled_length)

        spinner_char = next(self.spinner_cycle) if self.spinner else ''

        sys.stdout.write(f'\r{self.prefix} |{bar}| {percent}% {self.suffix} {spinner_char}')
        sys.stdout.flush()

        if self.iteration >= self.total:
            sys.stdout.write('\n')
            sys.stdout.flush()


def get_user_playlists():
    playlists = []
    response = sp.current_user_playlists(limit=50)
    while response:
        playlists.extend(response['items'])
        response = sp.next(response) if response['next'] else None
    return playlists


def get_playlist_tracks(playlist_id):
    tracks = []
    results = sp.playlist_tracks(playlist_id, fields="items(track.id),next", limit=100)
    while results:
        tracks.extend([item['track']['id'] for item in results['items'] if item['track'] and item['track']['id']])
        results = sp.next(results) if results['next'] else None
    return tracks


def get_liked_songs():
    liked_songs = []
    results = sp.current_user_saved_tracks(limit=50)
    while results:
        liked_songs.extend(
            [item['track']['id'] for item in results['items'] if item['track'] and item['track']['id']]
        )
        results = sp.next(results) if results['next'] else None
    return liked_songs


def get_existing_playlist_tracks(playlist_id):
    tracks = []
    results = sp.playlist_tracks(playlist_id, fields="items(track.id),next", limit=100)
    while results:
        tracks.extend(
            [item['track']['id'] for item in results['items'] if item['track'] and item['track']['id']]
        )
        results = sp.next(results) if results['next'] else None
    return tracks


def should_exclude_playlist(playlist):
    description = playlist.get('description') or ''
    return EXCLUDE_DESCRIPTION_FLAG in description


def add_tracks_to_existing_playlist(playlist_name):
    user_id = sp.me()['id']
    all_tracks = set()

    playlists = get_user_playlists()

    existing_playlist_id = None
    for playlist in playlists:
        if playlist['name'] == playlist_name and playlist['owner']['id'] == user_id:
            existing_playlist_id = playlist['id']
            break

    if not existing_playlist_id:
        print(f"\nNo existing playlist named '{playlist_name}' found.")
        return

    total_playlists = len(playlists)
    excluded_count = 0

    fetch_pb = ProgressBar(total=total_playlists, prefix='Fetching Playlists:', suffix='Complete', bar_length=50)
    fetch_pb.update(0)
    for i, playlist in enumerate(playlists, start=1):
        is_owned = playlist['owner']['id'] == user_id
        is_vaulted_target = playlist['id'] == existing_playlist_id
        is_excluded = should_exclude_playlist(playlist)

        if is_owned and is_excluded:
            excluded_count += 1

        if is_owned and not is_vaulted_target and not is_excluded:
            all_tracks.update(get_playlist_tracks(playlist['id']))

        fetch_pb.update(i)

    print("Fetching Liked Songs:")
    liked_songs = get_liked_songs()
    all_tracks.update(liked_songs)
    print(f"Fetched {len(liked_songs)} liked songs.")
    print(f"Excluded {excluded_count} owned playlist(s) with '{EXCLUDE_DESCRIPTION_FLAG}' in description.")

    existing_tracks = set(get_existing_playlist_tracks(existing_playlist_id))
    tracks_to_add = [track_id for track_id in (all_tracks - existing_tracks) if track_id]
    tracks_to_remove = [track_id for track_id in (existing_tracks - all_tracks) if track_id]

    if not tracks_to_add and not tracks_to_remove:
        print(f"\nPlaylist '{playlist_name}' is already in sync.")
        return

    if tracks_to_add:
        total_new_tracks = len(tracks_to_add)
        add_pb = ProgressBar(total=total_new_tracks, prefix='Adding Tracks:', suffix='Complete', bar_length=50)
        add_pb.update(0)
        for i in range(0, total_new_tracks, 100):
            chunk = tracks_to_add[i:i+100]
            sp.playlist_add_items(existing_playlist_id, chunk)
            add_pb.update(min(i + len(chunk), total_new_tracks))

    if tracks_to_remove:
        total_removed_tracks = len(tracks_to_remove)
        remove_pb = ProgressBar(total=total_removed_tracks, prefix='Removing Tracks:', suffix='Complete', bar_length=50)
        remove_pb.update(0)
        for i in range(0, total_removed_tracks, 100):
            chunk = tracks_to_remove[i:i+100]
            sp.playlist_remove_all_occurrences_of_items(existing_playlist_id, chunk)
            remove_pb.update(min(i + len(chunk), total_removed_tracks))

    print(
        f"\nSync complete for '{playlist_name}': "
        f"added {len(tracks_to_add)} track(s), removed {len(tracks_to_remove)} track(s)."
    )


if __name__ == '__main__':
    playlist_name = '_vaulted'
    add_tracks_to_existing_playlist(playlist_name)
