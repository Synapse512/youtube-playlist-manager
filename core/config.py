"""
Application configuration, file persistence, settings, and activity logging.
"""

import os
import re
import json
import shutil
from datetime import datetime

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        import tomllib

VERSION = "1.1.2"

SETTINGS_FILE = "settings.toml"
DATA_DIR = "data"
PLAYLISTS_DIR = "playlists"
PLAYLISTS_DATA_FILE = os.path.join(DATA_DIR, "playlist-data.json")
PLAYLIST_SETTINGS_FILE = "playlist-settings.toml"

LEGACY_PLAYLISTS_DATA_FILES = [
    os.path.join(PLAYLISTS_DIR, ".playlist-data"),
    os.path.join(PLAYLISTS_DIR, ".playlist-data.json"),
    os.path.join(PLAYLISTS_DIR, "_playlist-data.json"),
    os.path.join(PLAYLISTS_DIR, "_playlists.json"),
]
LEGACY_PLAYLIST_SETTINGS_FILES = [
    os.path.join(PLAYLISTS_DIR, "_playlist-settings.toml"),
    os.path.join(PLAYLISTS_DIR, "_playlist-settings.json"),
    os.path.join(PLAYLISTS_DIR, "_playlist_settings.json"),
]
OAUTH_CLIENTS_DIR = "oauth-clients"
LOGS_DIR = "logs"
DOWNLOADS_DIR = "playlist-downloads"

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# ---------------------------------------------------------------------------
# Playlist entry format
#
# Controls how each video line is written in playlists/<name>.txt.
# Supported fields: id, title, channel, duration.
#   - "id" is ALWAYS first (it is how lines are mapped back to YouTube videos).
#   - Every other field is optional and can be reordered freely.
#   - Fields are always separated by " | ".
# Anything else in the setting (unknown fields, missing separators, id not first)
# is repaired automatically by normalize_playlist_entry_format().
# ---------------------------------------------------------------------------
ENTRY_FIELDS = ("id", "title", "channel", "duration")
ENTRY_FIELD_ALIASES = {
    "duration_string": "duration",
    "uploader": "channel",
}
ENTRY_SEPARATOR = " | "
DEFAULT_PLAYLIST_ENTRY_FORMAT = "%(id)s | %(title)s"
_ENTRY_TOKEN_RE = re.compile(r"%\(\s*([^)\s]+)\s*\)s")


def playlist_entry_format_fields(entry_format):
    """
    Returns the ordered list of fields for a playlist_entry_format string.
    Always starts with "id"; unsupported fields are dropped, aliases are resolved
    and duplicates removed. An empty/unusable format falls back to id + title.
    """
    names = _ENTRY_TOKEN_RE.findall(str(entry_format or ""))
    if not names:
        return ["id", "title"]
    fields = ["id"]
    for name in names:
        name = name.strip().lower()
        name = ENTRY_FIELD_ALIASES.get(name, name)
        if name in ENTRY_FIELDS and name not in fields:
            fields.append(name)
    return fields


def normalize_playlist_entry_format(entry_format):
    """Rebuilds a clean, canonical format string, e.g. '%(id)s | %(title)s | %(channel)s'."""
    return ENTRY_SEPARATOR.join(f"%({f})s" for f in playlist_entry_format_fields(entry_format))


DEFAULT_PLAYLIST_SETTINGS = {
    "download-format": "audio",
    "embed_thumbnail": True,
    "number_files": True,
    "push_mode": "all",  # "all", "main_only", "sections_only"
    "download_mode": "main_only",  # "main_only", "all", "sections_only"
    "playlist_entry_format": DEFAULT_PLAYLIST_ENTRY_FORMAT,
}

SETTINGS_INFO_KEY = "HOW_TO_EDIT_SETTINGS"


def _format_toml_settings(settings):
    """Generates clean, human-readable TOML with descriptive comments for all settings."""
    safety = "true" if settings.get("safety_check_before_push", True) else "false"
    menu_p = settings.get("menu_playlist_count", 3)
    menu_p_str = f'"{menu_p}"' if isinstance(menu_p, str) else str(menu_p)
    menu_c = settings.get("menu_client_count", 3)
    menu_c_str = f'"{menu_c}"' if isinstance(menu_c, str) else str(menu_c)
    logging = "true" if settings.get("enable_logging", True) else "false"
    dl_dir = settings.get("downloads_dir", "playlist-downloads")
    clickable = "true" if settings.get("clickable_links_in_playlist_files", False) else "false"
    pull_m = settings.get("pull_method", "auto")
    link_m = settings.get("link_method", "auto")
    ytdlp = settings.get("ytdlp_path", "")
    ffmpeg = settings.get("ffmpeg_path", "")

    return f"""# ypm Global Settings

# Prompts asking if you have pulled before confirming push.
safety_check_before_push = {safety}

# Max playlists in CLI menu ("all" for all).
menu_playlist_count = {menu_p_str}

# Max OAuth clients in CLI menu ("all" for all).
menu_client_count = {menu_c_str}

# Save log files for operations.
enable_logging = {logging}

# Folder path for downloaded files.
downloads_dir = "{dl_dir}"

# Save links as full URLs instead of IDs. Run `format` to apply.
clickable_links_in_playlist_files = {clickable}

# Method to pull playlists ("auto", "ytdlp", "api").
# Auto uses yt-dlp when possible and api as a fallback.
pull_method = "{pull_m}"

# Method to link playlists ("auto", "ytdlp", "api").
# Auto uses yt-dlp when possible and api as a fallback.
link_method = "{link_m}"

# Custom executable paths (blank = auto-detect).
ytdlp_path = "{ytdlp}"
ffmpeg_path = "{ffmpeg}"
"""


def load_settings():
    """Loads settings.toml safely, recreating with defaults if missing or corrupted."""
    default_settings = {
        "safety_check_before_push": True,
        "menu_playlist_count": 3,
        "menu_client_count": 3,
        "enable_logging": True,
        "downloads_dir": "playlist-downloads",
        "clickable_links_in_playlist_files": False,
        "pull_method": "auto",
        "link_method": "auto",
        "ytdlp_path": "",
        "ffmpeg_path": ""
    }
    if not os.path.exists(SETTINGS_FILE):
        print(f"[*] Settings file not found. Creating default '{SETTINGS_FILE}'...")
        save_settings(default_settings)
        return default_settings

    try:
        with open(SETTINGS_FILE, "rb") as f:
            settings = tomllib.load(f)
            if not isinstance(settings, dict):
                raise ValueError("Settings file must contain a TOML table.")
            settings.setdefault("safety_check_before_push", True)
            settings.setdefault("menu_playlist_count", 3)
            settings.setdefault("menu_client_count", 3)
            settings.setdefault("enable_logging", True)
            settings.setdefault("downloads_dir", "playlist-downloads")
            settings.setdefault("clickable_links_in_playlist_files", False)
            settings.setdefault("pull_method", "auto")
            settings.setdefault("link_method", "auto")
            settings.setdefault("ytdlp_path", "")
            settings.setdefault("ffmpeg_path", "")
            return settings
    except (tomllib.TOMLDecodeError, ValueError, OSError) as e:
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
    """Saves settings dictionary to settings.toml atomically with descriptive comments."""
    temp_file = f"{SETTINGS_FILE}.tmp"
    content = _format_toml_settings(settings)
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(content)
        if os.path.exists(SETTINGS_FILE):
            os.replace(temp_file, SETTINGS_FILE)
        else:
            os.rename(temp_file, SETTINGS_FILE)
    except OSError as e:
        print(f"[!] Error saving settings to '{SETTINGS_FILE}': {e}")


def load_playlist_data():
    """Loads data/playlist-data.json containing linked playlists and activity tracking."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    default_data = {
        "playlists": {},
        "activity": {}
    }

    target_file = PLAYLISTS_DATA_FILE

    if not os.path.exists(target_file):
        for legacy_file in LEGACY_PLAYLISTS_DATA_FILES:
            if os.path.exists(legacy_file) and legacy_file != target_file:
                try:
                    os.replace(legacy_file, target_file)
                    print(f"[*] Migrated playlist data from '{legacy_file}' to '{target_file}'.")
                    break
                except OSError:
                    try:
                        shutil.copyfile(legacy_file, target_file)
                        os.remove(legacy_file)
                        print(f"[*] Migrated playlist data from '{legacy_file}' to '{target_file}'.")
                        break
                    except OSError:
                        pass

    if not os.path.exists(target_file):
        save_playlist_data(default_data)
        return default_data

    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Playlist data file must contain a JSON object.")
            should_save = any(k not in data for k in default_data)
            data.setdefault("playlists", {})
            data.setdefault("activity", {})
            if should_save:
                save_playlist_data(data)
            return data
    except (json.JSONDecodeError, ValueError, OSError) as e:
        backup_file = f"{target_file}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        print(f"[!] Warning: '{target_file}' is invalid or corrupted ({e}).")
        print(f"[*] Backing up damaged playlist data to '{backup_file}' and resetting.")
        try:
            shutil.copyfile(target_file, backup_file)
        except OSError:
            pass
        save_playlist_data(default_data)
        return default_data


def save_playlist_data(data):
    """Saves playlist data dictionary to data/playlist-data.json atomically."""
    os.makedirs(DATA_DIR, exist_ok=True)
    target_file = PLAYLISTS_DATA_FILE
    temp_file = f"{target_file}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        if os.path.exists(target_file):
            os.replace(temp_file, target_file)
        else:
            os.rename(temp_file, target_file)
    except OSError as e:
        print(f"[!] Error saving playlist data to '{target_file}': {e}")


def _format_toml_table_key(name):
    """Formats a playlist name as a valid TOML table header [key] or [\"key\"]."""
    import re
    if re.match(r'^[A-Za-z0-9_-]+$', name):
        return f"[{name}]"
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'["{escaped}"]'


def _format_playlist_settings_toml(all_settings):
    """
    Generates clean, human-readable TOML for playlist-settings.toml.
    Includes an unlinked commented-out example playlist with setting documentation
    at the top, so users can safely edit or delete their playlists without disruption.
    """
    lines = [
        '# [example_playlist]',
        '# Download format: "audio" (best-quality audio) or "video" (MP4)',
        '# download-format = "audio"',
        '#',
        '# Embed album art into downloaded audio files (requires ffmpeg)',
        '# embed_thumbnail = true',
        '#',
        '# Prefix downloaded filenames with playlist position ("01 - Song.opus")',
        '# number_files = true',
        '#',
        '# Which playlists to sync to YouTube on \'push\': "all", "main_only", or "sections_only"',
        '# push_mode = "all"',
        '#',
        '# Download layout: "main_only" (flat folder), "all", or "sections_only"',
        '# download_mode = "main_only"',
        '#',
        '# How each video line is written in the playlist .txt file (run `pull` after changing it).',
        '# %(id)s is always first. Optional, reorderable fields: %(title)s, %(channel)s, %(duration)s',
        '# Example: "%(id)s | %(title)s | %(channel)s | %(duration)s"',
        '# playlist_entry_format = "%(id)s | %(title)s"',
        '',
        ''
    ]

    playlist_names = [k for k in all_settings if k != SETTINGS_INFO_KEY and not k.startswith("_")]
    playlist_names.sort()

    for name in playlist_names:
        entry = all_settings[name]
        if not isinstance(entry, dict):
            continue

        header = _format_toml_table_key(name)
        lines.append(header)

        fmt = entry.get("download-format", entry.get("format", DEFAULT_PLAYLIST_SETTINGS["download-format"]))
        thumb = "true" if entry.get("embed_thumbnail", DEFAULT_PLAYLIST_SETTINGS["embed_thumbnail"]) else "false"
        num = "true" if entry.get("number_files", DEFAULT_PLAYLIST_SETTINGS["number_files"]) else "false"
        push_m = entry.get("push_mode", DEFAULT_PLAYLIST_SETTINGS["push_mode"])
        dl_m = entry.get("download_mode", DEFAULT_PLAYLIST_SETTINGS["download_mode"])
        entry_fmt = normalize_playlist_entry_format(
            entry.get("playlist_entry_format", DEFAULT_PLAYLIST_SETTINGS["playlist_entry_format"])
        )

        lines.append(f'download-format = "{fmt}"')
        lines.append(f'embed_thumbnail = {thumb}')
        lines.append(f'number_files = {num}')
        lines.append(f'push_mode = "{push_m}"')
        lines.append(f'download_mode = "{dl_m}"')
        lines.append(f'playlist_entry_format = "{entry_fmt}"')

        # Any extra custom keys
        for k, v in entry.items():
            if k in ("format", "download-format", "embed_thumbnail", "number_files", "push_mode", "download_mode", "playlist_entry_format"):
                continue
            if isinstance(v, bool):
                v_str = "true" if v else "false"
            elif isinstance(v, (int, float)):
                v_str = str(v)
            else:
                v_str = f'"{v}"'
            lines.append(f'{k} = {v_str}')

        lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def load_all_playlist_settings():
    """Loads playlist-settings.toml containing per-playlist preferences."""

    # Legacy migration: check old playlist-folder TOML/JSON settings files.
    if not os.path.exists(PLAYLIST_SETTINGS_FILE):
        for legacy_path in LEGACY_PLAYLIST_SETTINGS_FILES:
            if os.path.exists(legacy_path):
                try:
                    if legacy_path.endswith(".toml"):
                        with open(legacy_path, "rb") as f:
                            old_data = tomllib.load(f)
                    else:
                        with open(legacy_path, "r", encoding="utf-8") as f:
                            old_data = json.load(f)
                    if isinstance(old_data, dict):
                        cleaned = {
                            k: v for k, v in old_data.items()
                            if k != SETTINGS_INFO_KEY and not k.startswith("_") and isinstance(v, dict)
                        }
                        save_all_playlist_settings(cleaned)
                        print(f"[*] Migrated playlist settings from '{legacy_path}' to '{PLAYLIST_SETTINGS_FILE}'.")
                    try:
                        os.remove(legacy_path)
                    except OSError:
                        pass
                    break
                except Exception as e:
                    print(f"[!] Warning: Failed migrating legacy settings from '{legacy_path}': {e}")

    if not os.path.exists(PLAYLIST_SETTINGS_FILE):
        return {}

    try:
        with open(PLAYLIST_SETTINGS_FILE, "rb") as f:
            data = tomllib.load(f)
            return data if isinstance(data, dict) else {}
    except (tomllib.TOMLDecodeError, ValueError, OSError) as e:
        backup_file = f"{PLAYLIST_SETTINGS_FILE}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        print(f"[!] Warning: '{PLAYLIST_SETTINGS_FILE}' is invalid or corrupted ({e}).")
        print(f"[*] Backing up damaged playlist settings to '{backup_file}' and resetting.")
        try:
            shutil.copyfile(PLAYLIST_SETTINGS_FILE, backup_file)
        except OSError:
            pass
        return {}


def save_all_playlist_settings(all_settings):
    """Saves all playlist settings to playlist-settings.toml atomically."""
    temp_file = f"{PLAYLIST_SETTINGS_FILE}.tmp"
    content = _format_playlist_settings_toml(all_settings)
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(content)
        if os.path.exists(PLAYLIST_SETTINGS_FILE):
            os.replace(temp_file, PLAYLIST_SETTINGS_FILE)
        else:
            os.rename(temp_file, PLAYLIST_SETTINGS_FILE)
    except OSError as e:
        print(f"[!] Error saving playlist settings to '{PLAYLIST_SETTINGS_FILE}': {e}")


def load_playlist_settings(playlist_name):
    """
    Loads settings for a specific playlist from playlist-settings.toml.
    - Fills in any missing settings with defaults.
    - Migrates the old 'format' key to 'download-format'.
    - Migrates format/embed_thumbnail/number_files from legacy download settings files.
    - Repairs 'playlist_entry_format' (id first, supported fields only, " | " separators).
    """
    if playlist_name == SETTINGS_INFO_KEY:
        return dict(DEFAULT_PLAYLIST_SETTINGS)

    all_settings = load_all_playlist_settings()
    entry = all_settings.get(playlist_name)
    save_needed = False

    known_keys = set(DEFAULT_PLAYLIST_SETTINGS) | {"format"}
    is_new_entry = not isinstance(entry, dict) or not any(k in entry for k in known_keys)
    if not isinstance(entry, dict):
        entry = {}

    # Rename legacy 'format' -> 'download-format'
    if "format" in entry:
        legacy_format = str(entry.pop("format")).strip().lower()
        if "download-format" not in entry and legacy_format:
            entry["download-format"] = legacy_format
        save_needed = True

    # Brand-new entry: try to import old per-playlist download settings
    if is_new_entry:
        from .parser import sanitize_filename
        safe_name = sanitize_filename(playlist_name)
        dl_dir = os.path.join(DOWNLOADS_DIR, safe_name)
        for legacy_name in ("_manifest.json", "_setting.json", "_settings.json"):
            legacy_file = os.path.join(dl_dir, legacy_name)
            if os.path.isfile(legacy_file):
                try:
                    with open(legacy_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        if data.get("format"):
                            entry["download-format"] = str(data["format"]).lower()
                        if "embed_thumbnail" in data:
                            entry["embed_thumbnail"] = bool(data["embed_thumbnail"])
                        if "number_files" in data:
                            entry["number_files"] = bool(data["number_files"])
                    break
                except Exception:
                    pass
        save_needed = True

    # Fill in missing defaults
    for k, v in DEFAULT_PLAYLIST_SETTINGS.items():
        if k not in entry:
            entry[k] = v
            save_needed = True

    # Normalize types in case the user edited the TOML with strings for booleans
    for key in ("embed_thumbnail", "number_files"):
        if isinstance(entry.get(key), str):
            entry[key] = entry[key].strip().lower() in ("true", "1", "yes")

    # Repair the entry format if needed
    raw_fmt = entry.get("playlist_entry_format")
    clean_fmt = normalize_playlist_entry_format(raw_fmt)
    if raw_fmt != clean_fmt:
        if isinstance(raw_fmt, str) and raw_fmt.strip():
            print(f"[*] Repaired playlist_entry_format for '{playlist_name}': {raw_fmt!r} -> {clean_fmt!r}")
        entry["playlist_entry_format"] = clean_fmt
        save_needed = True

    if save_needed:
        all_settings[playlist_name] = entry
        save_all_playlist_settings(all_settings)

    return entry


def save_playlist_settings(playlist_name, settings_dict):
    """Saves updated settings for a specific playlist into playlist-settings.toml."""
    if playlist_name == SETTINGS_INFO_KEY:
        return dict(DEFAULT_PLAYLIST_SETTINGS)
    all_settings = load_all_playlist_settings()
    entry = all_settings.get(playlist_name, dict(DEFAULT_PLAYLIST_SETTINGS))
    if "format" in settings_dict:
        settings_dict = dict(settings_dict)
        settings_dict["download-format"] = settings_dict.pop("format")
    entry.update(settings_dict)
    if "format" in entry and "download-format" in entry:
        entry.pop("format", None)
    all_settings[playlist_name] = entry
    save_all_playlist_settings(all_settings)
    return entry



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
