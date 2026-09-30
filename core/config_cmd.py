"""
Interactive and CLI configuration manager for ypm.
View and modify global settings (settings.toml) or playlist settings (playlist-settings.toml)
directly from the terminal with full validation and type conversion.
"""

import os
import sys
import json

from .config import (
    SETTINGS_FILE,
    PLAYLIST_SETTINGS_FILE,
    load_settings,
    save_settings,
    load_playlist_settings,
    save_playlist_settings,
    load_all_playlist_settings,
    normalize_playlist_entry_format,
    DEFAULT_PLAYLIST_SETTINGS,
)
from .parser import get_playlist_name_for_target

BOOL_TRUE_VALUES = {"true", "yes", "1", "on", "enable", "enabled"}
BOOL_FALSE_VALUES = {"false", "no", "0", "off", "disable", "disabled"}

ALLOWED_PLAYLIST_SETTINGS = {
    "download-format": ("audio", "video"),
    "format": ("audio", "video"),
    "push_mode": ("all", "main_only", "sections_only"),
    "download_mode": ("main_only", "all", "sections_only"),
    "folder_name_source": ("alias", "header"),
}

BOOLEAN_PLAYLIST_KEYS = {
    "embed_thumbnail",
    "number_files",
    "atomic_writes",
    "number_section_folders",
    "include_playlist_name_in_sections",
}

BOOLEAN_GLOBAL_KEYS = {
    "safety_check_before_push",
    "warn_on_malformed_section_links",
    "menu_show_playlists",
    "menu_show_clients",
    "menu_show_commands",
    "enable_logging",
    "clickable_links_in_playlist_files",
    "cache_oauth_tokens",
}

INT_GLOBAL_KEYS = {
    "ytdlp_sleep_interval",
    "ytdlp_sleep_requests",
}


def _parse_typed_value(raw_value, current_value=None, key_hint=None):
    """
    Parses a string input into its appropriate Python type (bool, int, float, or str).
    """
    if raw_value is None:
        return ""
    val_str = str(raw_value).strip()
    val_lower = val_str.lower()

    # Booleans
    if key_hint in BOOLEAN_GLOBAL_KEYS or key_hint in BOOLEAN_PLAYLIST_KEYS or isinstance(current_value, bool):
        if val_lower in BOOL_TRUE_VALUES:
            return True
        if val_lower in BOOL_FALSE_VALUES:
            return False
        raise ValueError(f"Value '{val_str}' must be a boolean ('true' or 'false').")

    if val_lower in BOOL_TRUE_VALUES and current_value is None:
        return True
    if val_lower in BOOL_FALSE_VALUES and current_value is None:
        return False

    # Integers / Counts
    if key_hint in INT_GLOBAL_KEYS or (key_hint in ("menu_playlist_count", "menu_client_count") and val_lower != "all"):
        if val_str.isdigit():
            return int(val_str)
        raise ValueError(f"Value '{val_str}' for '{key_hint}' must be an integer.")

    if val_str.isdigit() and isinstance(current_value, int):
        return int(val_str)

    # Empty string or quotes
    if val_str in ("''", '""'):
        return ""

    return val_str


def _format_value_display(val):
    """Formats a value nicely for console output."""
    if isinstance(val, bool):
        return f"\033[92mtrue\033[0m" if val else f"\033[91mfalse\033[0m"
    if isinstance(val, (int, float)):
        return f"\033[93m{val}\033[0m"
    if val == "":
        return "\033[90m'' (empty)\033[0m"
    return f"\033[96m'{val}'\033[0m"


def _list_global_settings(settings):
    """Prints all global settings in a clean, categorized table."""
    print("\n" + "=" * 70)
    print(f" ypm Global Settings ({SETTINGS_FILE})".center(70))
    print("=" * 70)

    ai = settings.get("ai", {}) if isinstance(settings.get("ai"), dict) else {}

    categories = [
        ("Dashboard Menu", [
            ("menu_show_playlists", settings.get("menu_show_playlists", True), "Show recent playlists section"),
            ("menu_playlist_count", settings.get("menu_playlist_count", 3), "Max playlists displayed ('all' or count)"),
            ("menu_show_clients", settings.get("menu_show_clients", True), "Show OAuth clients section"),
            ("menu_client_count", settings.get("menu_client_count", 3), "Max clients displayed ('all' or count)"),
            ("menu_show_commands", settings.get("menu_show_commands", True), "Show available commands help section"),
        ]),
        ("General & Safety", [
            ("downloads_dir", settings.get("downloads_dir", "playlist-downloads"), "Root folder for downloads"),
            ("safety_check_before_push", settings.get("safety_check_before_push", True), "Confirm pull before pushing"),
            ("warn_on_malformed_section_links", settings.get("warn_on_malformed_section_links", True), "Warn on bad section playlist URLs"),
            ("enable_logging", settings.get("enable_logging", True), "Save operation logs to logs/"),
            ("clickable_links_in_playlist_files", settings.get("clickable_links_in_playlist_files", False), "Save as full URLs vs raw IDs"),
            ("cache_oauth_tokens", settings.get("cache_oauth_tokens", True), "Cache login tokens in data/tokens/"),
        ]),
        ("Downloads & yt-dlp", [
            ("pull_method", settings.get("pull_method", "auto"), "Pull method: auto, ytdlp, api"),
            ("link_method", settings.get("link_method", "auto"), "Link method: auto, ytdlp, api"),
            ("cookies_from_browser", settings.get("cookies_from_browser", ""), "Browser for cookies (firefox, edge, chrome)"),
            ("cookies_file", settings.get("cookies_file", ""), "Path to cookies.txt (data/ is auto-detected)"),
            ("ytdlp_player_client", settings.get("ytdlp_player_client", ""), "Player client override (mweb, ios, default)"),
            ("ytdlp_path", settings.get("ytdlp_path", ""), "Custom yt-dlp executable path"),
            ("ffmpeg_path", settings.get("ffmpeg_path", ""), "Custom ffmpeg executable path"),
            ("retry_failed_downloads", settings.get("retry_failed_downloads", False), "Auto-retry failed tracks (true/false/ask)"),
        ]),
        ("AI Organization & Formatting", [
            ("ai.provider", ai.get("provider", "openai"), "AI provider: openai, gemini, groq, etc."),
            ("ai.api_key", "(set)" if ai.get("api_key") else "", "API key (or set via environment variable)"),
            ("ai.model", ai.get("model", ""), "Model override (blank = provider default)"),
            ("ai.base_url", ai.get("base_url", ""), "Custom endpoint URL (e.g. Ollama localhost)"),
            ("ai.timeout", ai.get("timeout", 120), "AI request timeout in seconds"),
        ]),
    ]

    for cat_name, items in categories:
        print(f"\n  [*] {cat_name}:")
        for key, val, desc in items:
            val_disp = _format_value_display(val)
            print(f"      {key:<34} = {val_disp:<24} # {desc}")

    print("\n" + "=" * 70)
    print("  Tip: View or change any setting:")
    print("       py main.py config <key>")
    print("       py main.py config <key> <new_value>")
    print("       py main.py config <key> <new_value> --playlist <name>")
    print("=" * 70 + "\n")


def _list_playlist_settings(playlist_name, pl_settings):
    """Prints all settings for a specific playlist from playlist-settings.toml or DEV-playlist-settings.toml."""
    print("\n" + "=" * 70)
    print(f" Settings for '{playlist_name}' ({PLAYLIST_SETTINGS_FILE})".center(70))
    print("=" * 70)

    keys_order = [
        ("download-format", "Audio (best audio) or video (MP4)"),
        ("embed_thumbnail", "Embed album art into audio files"),
        ("number_files", "Prefix filenames with order: 01 - Track"),
        ("push_mode", "Sync on push: all, main_only, sections_only"),
        ("download_mode", "Layout: main_only, all, sections_only"),
        ("playlist_entry_format", "Format line structure (%(id)s | %(title)s)"),
        ("download_path", "Custom download folder (blank = default)"),
        ("folder_name_source", "Folder name in 'all' mode: alias or header"),
        ("atomic_writes", "Safe atomic writes (true) or direct writes"),
        ("number_section_folders", "Order section folders (01 - Section)"),
        ("oauth_client", "Pinned OAuth client (blank = auto)"),
        ("account", "Pinned Google account email (blank = auto)"),
        ("include_playlist_name_in_sections", "Append real YouTube playlist name to sections"),
        ("ai_prompt", "Saved prompt for ai-format (blank = ask)"),
        ("cookies_from_browser", "Browser override (blank = use global)"),
        ("cookies_file", "Cookie file override (blank = use global)"),
        ("ytdlp_player_client", "Player client override (blank = use global)"),
    ]

    for key, desc in keys_order:
        val = pl_settings.get(key, "")
        val_disp = _format_value_display(val)
        print(f"    {key:<34} = {val_disp:<24} # {desc}")

    print("\n" + "=" * 70)
    print("  Tip: Modify any playlist setting:")
    print(f"       py main.py config <key> <new_value> --playlist \"{playlist_name}\"")
    print("=" * 70 + "\n")


def command_config(args, settings, playlist_data):
    """
    Main handler for the 'config' command.
    """
    key = getattr(args, "key", None)
    value = getattr(args, "value", None)
    playlist_arg = getattr(args, "playlist", None)
    unset = getattr(args, "unset", False)

    # -------------------------------------------------------------
    # 1. Playlist-specific settings mode
    # -------------------------------------------------------------
    if playlist_arg:
        from .parser import resolve_target_playlist
        playlist_name = resolve_target_playlist(playlist_arg, playlist_data, allow_prompt=False, command_name="config")
        pl_settings = load_playlist_settings(playlist_name)

        if not key:
            _list_playlist_settings(playlist_name, pl_settings)
            return

        norm_key = key.strip().lower()
        if norm_key == "format":
            norm_key = "download-format"

        # View single playlist setting
        if value is None and not unset:
            current_val = pl_settings.get(norm_key)
            if current_val is None:
                print(f"[!] Unknown playlist setting '{key}'. Run 'py main.py config -p \"{playlist_name}\"' to see all settings.")
                return
            print(f"[*] Playlist '{playlist_name}' setting '{norm_key}' = {_format_value_display(current_val)}")
            return

        # Modify playlist setting
        if unset:
            new_val = DEFAULT_PLAYLIST_SETTINGS.get(norm_key, "")
        else:
            current_val = pl_settings.get(norm_key)
            try:
                new_val = _parse_typed_value(value, current_value=current_val, key_hint=norm_key)
            except ValueError as e:
                print(f"[!] Invalid value: {e}")
                return

        # Validation
        if norm_key in ALLOWED_PLAYLIST_SETTINGS:
            allowed = ALLOWED_PLAYLIST_SETTINGS[norm_key]
            if str(new_val).lower() not in allowed:
                print(f"[!] Invalid value '{new_val}' for '{norm_key}'. Allowed values: {', '.join(allowed)}")
                return
            new_val = str(new_val).lower()

        if norm_key == "playlist_entry_format":
            new_val = normalize_playlist_entry_format(str(new_val))

        old_val = pl_settings.get(norm_key, "")
        save_playlist_settings(playlist_name, {norm_key: new_val})
        print(f"[+] Updated '{playlist_name}' setting '{norm_key}': {_format_value_display(old_val)} -> {_format_value_display(new_val)}")
        print(f"[*] Saved to '{PLAYLIST_SETTINGS_FILE}'")
        return

    # -------------------------------------------------------------
    # 2. Global settings mode (settings.toml)
    # -------------------------------------------------------------
    if not key:
        _list_global_settings(settings)
        return

    norm_key = key.strip()

    # View single global setting
    if value is None and not unset:
        # Check nested key (e.g. ai.provider)
        if "." in norm_key:
            table, subkey = norm_key.split(".", 1)
            tbl = settings.get(table, {})
            if isinstance(tbl, dict) and subkey in tbl:
                print(f"[*] Global setting '{norm_key}' = {_format_value_display(tbl[subkey])}")
                return
            print(f"[!] Unknown global setting '{norm_key}'. Run 'py main.py config' to list settings.")
            return

        if norm_key in settings:
            print(f"[*] Global setting '{norm_key}' = {_format_value_display(settings[norm_key])}")
            return
        print(f"[!] Unknown global setting '{norm_key}'. Run 'py main.py config' to list settings.")
        return

    # Modify global setting
    if "." in norm_key:
        table, subkey = norm_key.split(".", 1)
        tbl = settings.setdefault(table, {})
        if not isinstance(tbl, dict):
            tbl = {}
            settings[table] = tbl

        if unset:
            new_val = ""
        else:
            current_val = tbl.get(subkey)
            try:
                new_val = _parse_typed_value(value, current_value=current_val, key_hint=subkey)
            except ValueError as e:
                print(f"[!] Invalid value: {e}")
                return

        old_val = tbl.get(subkey, "")
        tbl[subkey] = new_val
        save_settings(settings)
        print(f"[+] Updated global setting '{norm_key}': {_format_value_display(old_val)} -> {_format_value_display(new_val)}")
        print(f"[*] Saved to '{SETTINGS_FILE}'")
        return

    # Top-level global setting
    if unset:
        new_val = ""
    else:
        current_val = settings.get(norm_key)
        try:
            new_val = _parse_typed_value(value, current_value=current_val, key_hint=norm_key)
        except ValueError as e:
            print(f"[!] Invalid value: {e}")
            return

    # Extra validation
    if norm_key in ("pull_method", "link_method"):
        if str(new_val).lower() not in ("auto", "ytdlp", "api"):
            print(f"[!] Invalid value '{new_val}' for '{norm_key}'. Allowed: 'auto', 'ytdlp', 'api'")
            return
        new_val = str(new_val).lower()

    if norm_key in ("cookies_from_browser", "ytdlp_cookies_from_browser"):
        settings["cookies_from_browser"] = str(new_val).strip()
        settings["ytdlp_cookies_from_browser"] = str(new_val).strip()
    else:
        old_val = settings.get(norm_key, "")
        settings[norm_key] = new_val

    save_settings(settings)
    print(f"[+] Updated global setting '{norm_key}': {_format_value_display(current_val if current_val is not None else '')} -> {_format_value_display(new_val)}")
    print(f"[*] Saved to '{SETTINGS_FILE}'")
