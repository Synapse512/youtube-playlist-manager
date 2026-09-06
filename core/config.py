"""
Application configuration, file persistence, settings, and activity logging.
"""

import os
import json
import shutil
from datetime import datetime

VERSION = "1.0.2"

SETTINGS_FILE = "settings.json"
DATA_DIR = "data"
PLAYLISTS_DATA_FILE = os.path.join(DATA_DIR, "playlists.json")
OAUTH_CLIENTS_DIR = "oauth-clients"
PLAYLISTS_DIR = "playlists"
LOGS_DIR = "logs"

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


def load_settings():
    """Loads settings.json safely, recreating with defaults if missing or corrupted."""
    default_settings = {
        "safety_check_before_push": True,
        "menu_playlist_count": 3,
        "menu_client_count": 3,
        "enable_logging": True
    }
    if not os.path.exists(SETTINGS_FILE):
        print(f"[*] Settings file not found. Creating default '{SETTINGS_FILE}'...")
        save_settings(default_settings)
        return default_settings

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
            if not isinstance(settings, dict):
                raise ValueError("Settings file must contain a JSON object.")
            settings.setdefault("safety_check_before_push", True)
            settings.setdefault("menu_playlist_count", 3)
            settings.setdefault("menu_client_count", 3)
            settings.setdefault("enable_logging", True)
            return settings
    except (json.JSONDecodeError, ValueError, OSError) as e:
        backup_file = f"{SETTINGS_FILE}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        print(f"[!] Warning: '{SETTINGS_FILE}' is invalid or corrupted ({e}).")
        print(f"[*] Backing up damaged settings to '{backup_file}' and resetting to defaults.")
        try:
            shutil.copyfile(SETTINGS_FILE, backup_file)
        except OSError:
            pass
        save_settings(default_settings)
        return default_settings


def save_settings(settings):
    """Saves settings dictionary to settings.json atomically."""
    temp_file = f"{SETTINGS_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
        if os.path.exists(SETTINGS_FILE):
            os.replace(temp_file, SETTINGS_FILE)
        else:
            os.rename(temp_file, SETTINGS_FILE)
    except OSError as e:
        print(f"[!] Error saving settings to '{SETTINGS_FILE}': {e}")


def load_playlist_data():
    """Loads data/playlists.json containing linked playlists and activity tracking."""
    os.makedirs(DATA_DIR, exist_ok=True)
    default_data = {
        "playlists": {},
        "activity": {}
    }

    if not os.path.exists(PLAYLISTS_DATA_FILE):
        save_playlist_data(default_data)
        return default_data

    try:
        with open(PLAYLISTS_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Playlist data file must contain a JSON object.")
            # Drop the legacy per-playlist account association; oauth clients are
            # no longer tied to a specific playlist.
            if "playlist_users" in data:
                del data["playlist_users"]
            should_save = any(k not in data for k in default_data)
            data.setdefault("playlists", {})
            data.setdefault("activity", {})
            if should_save:
                save_playlist_data(data)
            return data
    except (json.JSONDecodeError, ValueError, OSError) as e:
        backup_file = f"{PLAYLISTS_DATA_FILE}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        print(f"[!] Warning: '{PLAYLISTS_DATA_FILE}' is invalid or corrupted ({e}).")
        print(f"[*] Backing up damaged playlist data to '{backup_file}' and resetting.")
        try:
            shutil.copyfile(PLAYLISTS_DATA_FILE, backup_file)
        except OSError:
            pass
        save_playlist_data(default_data)
        return default_data


def save_playlist_data(data):
    """Saves playlist data dictionary to data/playlists.json atomically."""
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_file = f"{PLAYLISTS_DATA_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        if os.path.exists(PLAYLISTS_DATA_FILE):
            os.replace(temp_file, PLAYLISTS_DATA_FILE)
        else:
            os.rename(temp_file, PLAYLISTS_DATA_FILE)
    except OSError as e:
        print(f"[!] Error saving playlist data to '{PLAYLISTS_DATA_FILE}': {e}")


def record_activity(playlist_data, target, command_name):
    """Records CLI activity (last command, timestamp, interaction count) for a playlist."""
    from .parser import get_playlist_name_for_target

    if playlist_data is None:
        playlist_data = load_playlist_data()
    playlist_name = get_playlist_name_for_target(target, playlist_data)
    if not playlist_name:
        return
    activity = playlist_data.setdefault("activity", {})
    entry = activity.setdefault(playlist_name, {
        "last_command": command_name,
        "last_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": 0
    })
    entry["last_command"] = command_name
    entry["last_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry["count"] = entry.get("count", 0) + 1
    save_playlist_data(playlist_data)


def log_playlist_event(settings, target, operation, oauth_client, summary_lines=None, detail_lines=None, playlist_data=None):
    """Appends a structured log entry to logs/<name>.log if logging is enabled."""
    from .parser import get_playlist_name_for_target, sanitize_filename

    if not settings.get("enable_logging", True):
        return
    playlist_name = get_playlist_name_for_target(target, playlist_data)
    if not playlist_name:
        return

    safe_name = sanitize_filename(playlist_name)
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = os.path.join(LOGS_DIR, f"{safe_name}.log")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"=== [{timestamp}] {operation.upper()} (oauth client: {oauth_client or 'unknown'}) ==="

    lines = [header]
    if summary_lines:
        for s in summary_lines:
            lines.append(f"  Summary: {s}")
    if detail_lines:
        for d in detail_lines:
            lines.append(f"  {d}")
    lines.append("")  # Blank line separator

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print(f"[!] Warning: Could not write to log file '{log_path}': {e}")
