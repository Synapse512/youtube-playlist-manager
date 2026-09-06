"""
Application configuration, file persistence, settings, and activity logging.
"""

import os
import json
import shutil
from datetime import datetime

VERSION = "1.0.0"

SETTINGS_FILE = "settings.json"
DATA_DIR = "data"
PLAYLISTS_DATA_FILE = os.path.join(DATA_DIR, "playlists.json")
USERS_DIR = "users"
TOKENS_DIR = os.path.join(DATA_DIR, "tokens")
PLAYLISTS_DIR = "playlists"
LOGS_DIR = "logs"

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


def load_settings():
    """Loads settings.json safely, recreating with defaults if missing or corrupted."""
    default_settings = {
        "safety_check_before_push": True,
        "menu_playlist_count": 3,
        "menu_user_count": 3,
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
            # Support migration from legacy show_accounts_in_menu
            if "menu_user_count" not in settings and "menu_account_count" not in settings:
                legacy_val = settings.get("show_accounts_in_menu")
                if legacy_val is False:
                    settings["menu_user_count"] = 0
                else:
                    settings["menu_user_count"] = 3
            settings.setdefault("menu_user_count", 3)
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
    clean_settings = {k: v for k, v in settings.items() if k not in ("playlists", "activity")}
    temp_file = f"{SETTINGS_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(clean_settings, f, indent=4)
        if os.path.exists(SETTINGS_FILE):
            os.replace(temp_file, SETTINGS_FILE)
        else:
            os.rename(temp_file, SETTINGS_FILE)
    except OSError as e:
        print(f"[!] Error saving settings to '{SETTINGS_FILE}': {e}")


def load_playlist_data(settings=None):
    """Loads data/playlists.json containing linked playlists and activity tracking, migrating from settings.json if needed."""
    os.makedirs(DATA_DIR, exist_ok=True)
    default_data = {
        "playlists": {},
        "activity": {}
    }

    # Auto-migration: if settings has legacy 'playlists' or 'activity', transfer them
    migrated = False
    legacy_playlists = {}
    legacy_activity = {}
    if settings is not None:
        if "playlists" in settings:
            legacy_playlists = settings.pop("playlists")
            migrated = True
        if "activity" in settings:
            legacy_activity = settings.pop("activity")
            migrated = True
        if migrated:
            save_settings(settings)

    if not os.path.exists(PLAYLISTS_DATA_FILE):
        default_data["playlists"].update(legacy_playlists)
        default_data["activity"].update(legacy_activity)
        save_playlist_data(default_data)
        return default_data

    try:
        with open(PLAYLISTS_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Playlist data file must contain a JSON object.")
            data.setdefault("playlists", {})
            data.setdefault("activity", {})
            if migrated:
                for k, v in legacy_playlists.items():
                    data["playlists"].setdefault(k, v)
                for k, v in legacy_activity.items():
                    data["activity"].setdefault(k, v)
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


def check_tokens_migration():
    """Migrates cached OAuth tokens from legacy 'tokens/' directory to 'data/tokens/'."""
    old_tokens_dir = "tokens"
    if os.path.isdir(old_tokens_dir) and os.path.abspath(old_tokens_dir) != os.path.abspath(TOKENS_DIR):
        os.makedirs(TOKENS_DIR, exist_ok=True)
        for fname in os.listdir(old_tokens_dir):
            if fname.endswith(".json") and not fname.startswith("."):
                old_path = os.path.join(old_tokens_dir, fname)
                new_path = os.path.join(TOKENS_DIR, fname)
                if not os.path.exists(new_path):
                    try:
                        shutil.move(old_path, new_path)
                    except OSError:
                        pass


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


def log_playlist_event(settings, target, operation, user, summary_lines=None, detail_lines=None, playlist_data=None):
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
    header = f"=== [{timestamp}] {operation.upper()} (user: {user or 'unknown'}) ==="

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
