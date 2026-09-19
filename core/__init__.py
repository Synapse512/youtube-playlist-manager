"""
YouTube Playlist Manager - Core Package
"""

from .config import (
    VERSION,
    SETTINGS_FILE,
    DATA_DIR,
    PLAYLISTS_DATA_FILE,
    OAUTH_CLIENTS_DIR,
    PLAYLISTS_DIR,
    LOGS_DIR,
    SCOPES,
    load_settings,
    save_settings,
    load_playlist_data,
    save_playlist_data,
    load_all_playlist_settings,
    save_all_playlist_settings,
    load_playlist_settings,
    save_playlist_settings,
    record_activity,
    log_playlist_event,
)

