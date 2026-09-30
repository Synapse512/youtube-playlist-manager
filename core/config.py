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

VERSION = "1.2.4"

DEV_SETTINGS_FILE = "DEV-settings.toml"
PROD_SETTINGS_FILE = "settings.toml"
DEV_PLAYLIST_SETTINGS_FILE = "DEV-playlist-settings.toml"
PROD_PLAYLIST_SETTINGS_FILE = "playlist-settings.toml"

def get_settings_file():
    """Returns DEV-settings.toml if it exists on disk, otherwise settings.toml."""
    return DEV_SETTINGS_FILE if os.path.exists(DEV_SETTINGS_FILE) else PROD_SETTINGS_FILE

def get_playlist_settings_file():
    """Returns DEV-playlist-settings.toml if it exists on disk, otherwise playlist-settings.toml."""
    return DEV_PLAYLIST_SETTINGS_FILE if os.path.exists(DEV_PLAYLIST_SETTINGS_FILE) else PROD_PLAYLIST_SETTINGS_FILE

# For backward compatibility with modules importing SETTINGS_FILE / PLAYLIST_SETTINGS_FILE
class _LazyConfigFile(str):
    def __new__(cls, resolver):
        instance = super().__new__(cls, resolver())
        instance._resolver = resolver
        return instance
    def __str__(self):
        return self._resolver()
    def __repr__(self):
        return repr(self._resolver())
    def __fspath__(self):
        return self._resolver()

SETTINGS_FILE = _LazyConfigFile(get_settings_file)
PLAYLIST_SETTINGS_FILE = _LazyConfigFile(get_playlist_settings_file)

DATA_DIR = "data"
TOKENS_DIR = os.path.join(DATA_DIR, "tokens")
PLAYLISTS_DIR = "playlists"
PLAYLISTS_DATA_FILE = os.path.join(DATA_DIR, "playlist-data.json")

OAUTH_CLIENTS_DIR = "oauth-clients"
LOGS_DIR = "logs"
DOWNLOADS_DIR = "playlist-downloads"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",  # lets the tool detect which Google account logged in, to name/cache its token
]

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

    Accepts two syntaxes:
      - Full:      "%(id)s | %(title)s | %(channel)s | %(duration)s"
      - Shorthand: "id, title, channel, duration"  (commas or spaces as separators)
    """
    fmt_str = str(entry_format or "")
    names = _ENTRY_TOKEN_RE.findall(fmt_str)
    if not names:
        # Try shorthand: split on commas and/or whitespace, ignoring empty tokens
        raw_tokens = [t.strip() for t in re.split(r"[,\s]+", fmt_str) if t.strip()]
        known = set(ENTRY_FIELDS) | set(ENTRY_FIELD_ALIASES)
        shorthand_names = [t for t in raw_tokens if t.lower() in known]
        if shorthand_names:
            names = shorthand_names
        else:
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
    "download_path": "",  # Custom download path (blank = default downloads_dir/<playlist>)
    "folder_name_source": "alias",  # Names the "all"-mode full-playlist download folder (was hardcoded "FULL_PLAYLIST"): "alias" = playlist's own name; "header" = the title from the "### " header
    "atomic_writes": True,  # Set to false for direct writes to playlist.txt
    "number_section_folders": False,  # Order section download folders by their position in playlist.txt
    "oauth_client": "",  # Default oauth-client (from oauth-clients/) to use for this playlist (blank = ask/auto)
    "account": "",  # Google account email to always use for this playlist (blank = pick from cached accounts / log in each time). Overridden by --account.
    "include_playlist_name_in_sections": False,  # 'format' will also write the linked section playlist's real title
    "ai_prompt": "",  # Custom prompt to auto-select for 'ai-format' without prompting
}

SETTINGS_INFO_KEY = "HOW_TO_EDIT_SETTINGS"


def _format_toml_settings(settings):
    """Generates clean, human-readable TOML with descriptive comments for all settings."""
    safety = "true" if settings.get("safety_check_before_push", True) else "false"
    menu_show_p = "true" if settings.get("menu_show_playlists", True) else "false"
    menu_show_c = "true" if settings.get("menu_show_clients", True) else "false"
    menu_show_cmd = "true" if settings.get("menu_show_commands", True) else "false"
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
    js_rt_path = settings.get("js_runtime_path", "")
    cookies_file = settings.get("cookies_file", settings.get("ytdlp_cookies_file", ""))
    cookies_browser = settings.get("ytdlp_cookies_from_browser", settings.get("cookies_from_browser", ""))
    player_client = settings.get("ytdlp_player_client", "")
    cache_tokens = "true" if settings.get("cache_oauth_tokens", True) else "false"
    warn_links = "true" if settings.get("warn_on_malformed_section_links", True) else "false"
    retry_failed = settings.get("retry_failed_downloads", False)
    if isinstance(retry_failed, bool):
        retry_failed_str = "true" if retry_failed else "false"
    else:
        retry_failed_str = f'"{retry_failed}"'

    ai_cfg = settings.get("ai", {}) if isinstance(settings.get("ai"), dict) else {}
    ai_provider = ai_cfg.get("provider", "openai")
    ai_api_key = ai_cfg.get("api_key", "")
    ai_model = ai_cfg.get("model", "")
    ai_base_url = ai_cfg.get("base_url", "")
    ai_timeout = ai_cfg.get("timeout", 120)

    return f"""# ypm Global Settings

# Prompts asking if you have pulled before confirming push.
safety_check_before_push = {safety}

# Before 'push' sends changes to a section's linked playlist, warn (and ask you to
# confirm) if that section's '## ' header link doesn't look like a well-formed
# YouTube playlist link (e.g. https://www.youtube.com/playlist?list=PLHd4hClFlvuw...).
# This catches typos/mangled links before they get pushed to the wrong playlist.
warn_on_malformed_section_links = {warn_links}

# Display recent playlists on the dashboard menu (true/false).
menu_show_playlists = {menu_show_p}

# Max playlists in CLI menu ("all" for all, 0 to hide).
menu_playlist_count = {menu_p_str}

# Display OAuth clients on the dashboard menu (true/false).
menu_show_clients = {menu_show_c}

# Max OAuth clients in CLI menu ("all" for all, 0 to hide).
menu_client_count = {menu_c_str}

# Display available commands list on the dashboard menu (true/false).
menu_show_commands = {menu_show_cmd}

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

# Cache OAuth tokens in data/ so you don't have to log in on every run.
cache_oauth_tokens = {cache_tokens}

# Custom executable paths (blank = auto-detect).
ytdlp_path = "{ytdlp}"
ffmpeg_path = "{ffmpeg}"
js_runtime_path = "{js_rt_path}"

# Path to a Netscape-format cookies.txt file for yt-dlp.
# (Note: dropping 'cookies.txt' directly into the 'data/' folder is auto-detected).
cookies_file = "{cookies_file}"

# Browser to extract cookies from for yt-dlp ("firefox", "edge", "chrome", "brave", etc., or blank).
# Firefox is recommended if your browser is open; Chromium browsers lock their database while open.
cookies_from_browser = "{cookies_browser}"
ytdlp_cookies_from_browser = "{cookies_browser}"

# Custom player client for yt-dlp (e.g. "default", "mweb", "ios", "android", or blank for yt-dlp default).
ytdlp_player_client = "{player_client}"

# Automatically retry tracks that previously failed to download (e.g. copyright blocked,
# age-restricted, or deleted). Options: false (default, automatically skip them),
# true (always retry them), "ask" (prompt each time).
retry_failed_downloads = {retry_failed_str}
    
# ==============================================================================
# AI PLAYLIST FORMATTING & ORGANIZING
# ==============================================================================
# Settings for 'ai-format' to reorganize sections, categorize by genre/artist, or clean titles.
# Supports any OpenAI-compatible endpoint (OpenAI, Gemini, Groq, OpenRouter, Ollama) and Anthropic.
[ai]
# Provider: "openai", "gemini", "groq", "openrouter", "anthropic", "ollama", or "custom"
provider = "{ai_provider}"

# API key for the chosen provider (or leave blank and set OPENAI_API_KEY, GEMINI_API_KEY, etc.)
api_key = "{ai_api_key}"

# Model name (leave blank for provider default, or specify a custom model).
model = "{ai_model}"

# Custom API base URL (optional, e.g. "http://localhost:11434/v1" for local Ollama, or custom proxy)
base_url = "{ai_base_url}"

# Timeout in seconds for AI requests (default: 120)
timeout = {ai_timeout}
"""


def load_settings():
    """Loads settings.toml safely, recreating with defaults if missing or corrupted."""
    default_settings = {
        "safety_check_before_push": True,
        "warn_on_malformed_section_links": True,
        "menu_show_playlists": True,
        "menu_playlist_count": 3,
        "menu_show_clients": True,
        "menu_client_count": 3,
        "menu_show_commands": True,
        "enable_logging": True,
        "downloads_dir": "playlist-downloads",
        "clickable_links_in_playlist_files": False,
        "pull_method": "auto",
        "link_method": "auto",
        "cache_oauth_tokens": True,
        "ytdlp_path": "",
        "ffmpeg_path": "",
        "js_runtime_path": "",
        "cookies_file": "",
        "cookies_from_browser": "",
        "ytdlp_cookies_from_browser": "",
        "ytdlp_player_client": "",
        "ytdlp_sleep_interval": 0,
        "ytdlp_sleep_requests": 0,
        "retry_failed_downloads": False,
        "ai": {
            "provider": "openai",
            "api_key": "",
            "model": "",
            "base_url": "",
            "timeout": 120,
        },
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
            settings.setdefault("warn_on_malformed_section_links", True)
            settings.setdefault("menu_show_playlists", True)
            settings.setdefault("menu_playlist_count", 3)
            settings.setdefault("menu_show_clients", True)
            settings.setdefault("menu_client_count", 3)
            settings.setdefault("menu_show_commands", True)
            settings.setdefault("enable_logging", True)
            settings.setdefault("downloads_dir", "playlist-downloads")
            settings.setdefault("clickable_links_in_playlist_files", False)
            settings.setdefault("pull_method", "auto")
            settings.setdefault("link_method", "auto")
            settings.setdefault("cache_oauth_tokens", True)
            settings.setdefault("ytdlp_path", "")
            settings.setdefault("ffmpeg_path", "")
            settings.setdefault("js_runtime_path", "")
            settings.setdefault("cookies_file", "")
            c_browser = (settings.get("cookies_from_browser") or settings.get("ytdlp_cookies_from_browser") or "").strip()
            settings["cookies_from_browser"] = c_browser
            settings["ytdlp_cookies_from_browser"] = c_browser
            settings.setdefault("ytdlp_player_client", "")
            settings.setdefault("ytdlp_sleep_interval", 0)
            settings.setdefault("ytdlp_sleep_requests", 0)
            ai_cfg = settings.setdefault("ai", {})
            if not isinstance(ai_cfg, dict):
                ai_cfg = {}
                settings["ai"] = ai_cfg
            ai_cfg.setdefault("provider", "openai")
            ai_cfg.setdefault("api_key", "")
            ai_cfg.setdefault("model", "")
            ai_cfg.setdefault("base_url", "")
            ai_cfg.setdefault("timeout", 120)
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
        '# You can use shorthand (e.g. "id, title, channel") or the full %(field)s syntax.',
        '# Example: "id, title"  or  "%(id)s | %(title)s"',
        '# playlist_entry_format = "%(id)s | %(title)s"',
        '#',
        '# Custom folder for this playlist\'s downloads (blank = <downloads_dir>/<playlist name>)',
        '# download_path = "D:/Music/MyPlaylist"',
        '#',
        '# Which name to use for the "everything" download folder in download_mode = "all"',
        '# (previously always called "FULL_PLAYLIST"). "alias" = use the playlist\'s own',
        '# name/alias (default). "header" = use the title from the "### " header in the .txt file.',
        '# folder_name_source = "alias"',
        '#',
        '# Write playlists/<name>.txt atomically (safe, default) or directly in-place ("direct write").',
        '# Direct writes are slightly faster but can leave a corrupted/truncated file if interrupted.',
        '# atomic_writes = true',
        '#',
        '# Prefix section download folders with their order in the .txt file ("01 - Chill", "02 - Hype")',
        '# number_section_folders = false',
        '#',
        '# oauth-client (from oauth-clients/<name>.json) to use automatically for this playlist,',
        '# so you are not prompted every time. Leave blank to be asked/auto-selected as usual.',
        '# oauth_client = "project-a"',
        '#',
        '# Google account email to always use for this playlist (e.g. "user@gmail.com").',
        '# Logins are cached per account in data/tokens/. Leave blank to pick from your',
        '# cached accounts (or log in with a new one) each time. --account overrides this.',
        '# account = "user@gmail.com"',
        '#',
        '# Path to a Netscape-formatted cookies.txt file specifically for this playlist.',
        '# cookies_file = "data/cookies.txt"',
        '#',
        '# Browser to extract cookies from specifically for this playlist ("chrome", "firefox", "edge", etc.).',
        '# cookies_from_browser = "firefox"',
        '#',
        '# Custom player client for yt-dlp ("default", "mweb", "ios", "android", or blank for default).',
        '# ytdlp_player_client = ""',
        '#',
        '# When `format` fills in a section header, also include the linked section playlist\'s',
        '# real YouTube title alongside your own section name: "## <url> | <your name> | <real title>"',
        '# include_playlist_name_in_sections = false',
        '#',
        '# Custom instructions to automatically use for \'ai-format\' without prompting',
        '# ai_prompt = "Group into sections by genre: Hip Hop, R&B, Rock, Ambient"',
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
        dl_path = entry.get("download_path", "")
        folder_src = entry.get("folder_name_source", DEFAULT_PLAYLIST_SETTINGS["folder_name_source"])
        atomic_w = "true" if entry.get("atomic_writes", DEFAULT_PLAYLIST_SETTINGS["atomic_writes"]) else "false"
        num_sec_f = "true" if entry.get("number_section_folders", DEFAULT_PLAYLIST_SETTINGS["number_section_folders"]) else "false"
        oauth_c = entry.get("oauth_client", "") or ""
        account_v = entry.get("account", "") or ""
        inc_pl_name = "true" if entry.get("include_playlist_name_in_sections", DEFAULT_PLAYLIST_SETTINGS["include_playlist_name_in_sections"]) else "false"
        ck_file = entry.get("cookies_file", "") or ""
        ck_browser = entry.get("cookies_from_browser", "") or ""
        yt_client = entry.get("ytdlp_player_client", "") or ""
        ai_pr = entry.get("ai_prompt", "") or ""

        lines.append(f'download-format = "{fmt}"')
        lines.append(f'embed_thumbnail = {thumb}')
        lines.append(f'number_files = {num}')
        lines.append(f'push_mode = "{push_m}"')
        lines.append(f'download_mode = "{dl_m}"')
        lines.append(f'playlist_entry_format = "{entry_fmt}"')
        # These are always written (even at their default) so they're visible and
        # editable in the file - previously they were hidden unless already
        # non-default, which made them look unsupported.
        lines.append(f'download_path = "{dl_path}"')
        lines.append(f'folder_name_source = "{folder_src}"')
        lines.append(f'atomic_writes = {atomic_w}')
        lines.append(f'number_section_folders = {num_sec_f}')
        lines.append(f'oauth_client = "{oauth_c}"')
        lines.append(f'account = "{account_v}"')
        lines.append(f'include_playlist_name_in_sections = {inc_pl_name}')
        if ai_pr:
            escaped_ai_pr = ai_pr.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'ai_prompt = "{escaped_ai_pr}"')
        if ck_file:
            lines.append(f'cookies_file = "{ck_file}"')
        if ck_browser:
            lines.append(f'cookies_from_browser = "{ck_browser}"')
        if yt_client:
            lines.append(f'ytdlp_player_client = "{yt_client}"')

        # Any extra custom keys
        known_written_keys = (
            "format", "download-format", "embed_thumbnail", "number_files", "push_mode",
            "download_mode", "playlist_entry_format", "download_path", "folder_name_source",
            "atomic_writes", "number_section_folders", "oauth_client", "account",
            "include_playlist_name_in_sections", "ai_prompt", "cookies_file", "cookies_from_browser",
            "ytdlp_player_client",
        )
        for k, v in entry.items():
            if k in known_written_keys:
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
    Fills in any missing settings with defaults and repairs 'playlist_entry_format'.
    """
    if playlist_name == SETTINGS_INFO_KEY:
        return dict(DEFAULT_PLAYLIST_SETTINGS)

    all_settings = load_all_playlist_settings()
    entry = all_settings.get(playlist_name)
    save_needed = False

    if not isinstance(entry, dict):
        entry = {}

    # Fill in missing defaults
    for k, v in DEFAULT_PLAYLIST_SETTINGS.items():
        if k not in entry:
            entry[k] = v
            save_needed = True

    # Normalize types in case the user edited the TOML with strings for booleans
    for key in ("embed_thumbnail", "number_files", "atomic_writes", "number_section_folders",
                "include_playlist_name_in_sections"):
        if isinstance(entry.get(key), str):
            entry[key] = entry[key].strip().lower() in ("true", "1", "yes")

    # Normalize case/whitespace on the mode strings. A stray typo like "Sections_Only"
    # or trailing whitespace would otherwise silently fail the exact-match checks in
    # command_push/command_download and fall back to unexpected behavior (e.g. still
    # pushing to the main playlist even though 'sections_only' was intended).
    for key, allowed, fallback in (
        ("push_mode", ("all", "main_only", "sections_only"), "all"),
        ("download_mode", ("main_only", "all", "sections_only"), "main_only"),
    ):
        raw_val = entry.get(key)
        if isinstance(raw_val, str):
            clean_val = raw_val.strip().lower()
            if clean_val != raw_val:
                entry[key] = clean_val
                save_needed = True
            if clean_val not in allowed:
                print(f"[!] Warning: '{key}' = '{raw_val}' for '{playlist_name}' is not one of {allowed}; "
                      f"using '{fallback}' instead. Fix this in '{PLAYLIST_SETTINGS_FILE}'.")
                entry[key] = fallback
                save_needed = True

    for key in ("oauth_client", "account"):
        if not isinstance(entry.get(key), str):
            entry[key] = str(entry.get(key) or "")

    entry.setdefault("cookies_file", "")
    pl_browser = (entry.get("cookies_from_browser") or entry.get("ytdlp_cookies_from_browser") or "").strip()
    entry["cookies_from_browser"] = pl_browser
    entry["ytdlp_cookies_from_browser"] = pl_browser
    entry.setdefault("ytdlp_player_client", "")

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
