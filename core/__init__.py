"""
YouTube Playlist Manager - Core Package
"""

from .config import (
    VERSION,
    SETTINGS_FILE,
    DATA_DIR,
    PLAYLISTS_DATA_FILE,
    USERS_DIR,
    TOKENS_DIR,
    PLAYLISTS_DIR,
    LOGS_DIR,
    SCOPES,
    load_settings,
    save_settings,
    load_playlist_data,
    save_playlist_data,
    record_activity,
    log_playlist_event,
    check_tokens_migration,
)
