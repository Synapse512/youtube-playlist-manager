"""
Terminal UI, dashboard menu, interactive oauth-client display, and help output.
"""

import sys
import os

from .config import VERSION, PLAYLISTS_DIR
from .auth import get_oauth_clients
from .parser import sanitize_filename


def terminal_link(text, url):
    """Formats text as a clickable OSC 8 terminal hyperlink."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def show_menu(settings, playlist_data, parser=None):
    """Displays the welcome menu with recent playlists and quick actions."""
    playlists = playlist_data.get("playlists", {})
    activity = playlist_data.get("activity", {})

    # Filter out playlists whose local text file does not exist on disk
    visible_playlists = {
        name: pid for name, pid in playlists.items()
        if os.path.isfile(os.path.join(PLAYLISTS_DIR, f"{name}.txt")) or
           os.path.isfile(os.path.join(PLAYLISTS_DIR, f"{sanitize_filename(name)}.txt"))
    }

    print("\n" + "=" * 70)
    print(f"ypm - Dashboard - V{VERSION}".center(70))
    print("=" * 70)

    raw_playlist_limit = settings.get("menu_playlist_count", 3)
    if isinstance(raw_playlist_limit, str) and raw_playlist_limit.strip().lower() == "all":
        menu_limit = None
    else:
        try:
            menu_limit = max(0, int(raw_playlist_limit))
        except (ValueError, TypeError):
            menu_limit = 3

    # Rank playlists by interaction count (descending), then by last_time (descending)
    ranked_playlists = sorted(
        visible_playlists.keys(),
        key=lambda a: (
            activity.get(a, {}).get("count", 0),
            activity.get(a, {}).get("last_time", "")
        ),
        reverse=True
    )
    recent_playlists = ranked_playlists if menu_limit is None else ranked_playlists[:menu_limit]

    has_shown_section = False

    if menu_limit != 0:
        print("\n  [*] Recent Playlists:")
        has_shown_section = True
        if not visible_playlists:
            print("      No playlists configured yet.\n")
        else:
            for idx, p_name in enumerate(recent_playlists, 1):
                pid = visible_playlists[p_name]
                url = f"https://www.youtube.com/playlist?list={pid}"

                act = activity.get(p_name, {})
                last_cmd = act.get("last_command")
                last_time = act.get("last_time")
                if last_cmd and last_time:
                    last_edit_str = f"{last_cmd} on {last_time}"
                else:
                    last_edit_str = "None recorded"

                print(f"    {idx}. {p_name} [{terminal_link(pid, url)}]")
                print(f"       • most recent edit: {last_edit_str}")
                print()

    raw_client_limit = settings.get("menu_client_count", 3)

    if raw_client_limit is False or str(raw_client_limit).strip().lower() in ("false", "no"):
        client_limit = 0
    elif isinstance(raw_client_limit, str) and raw_client_limit.strip().lower() == "all":
        client_limit = None
    elif raw_client_limit is True or str(raw_client_limit).strip().lower() in ("true", "yes"):
        client_limit = 3
    else:
        try:
            client_limit = max(0, int(raw_client_limit))
        except (ValueError, TypeError):
            client_limit = 3

    clients = get_oauth_clients()
    if client_limit is not None:
        displayed_clients = clients[:client_limit]
    else:
        displayed_clients = clients

    if client_limit != 0:
        leading_newline = "" if has_shown_section else "\n"
        print(f"{leading_newline}  [*] OAuth Clients:")
        has_shown_section = True
        if displayed_clients:
            for c in displayed_clients:
                print(f"      • {c}")
            print()
        else:
            print("      No oauth clients found in 'oauth-clients/'.\n")

    cmd_leading_newline = "" if has_shown_section else "\n"
    print(f"{cmd_leading_newline}  [?] Available Commands:")
    print("      python main.py pull <name>            Download playlist to local file")
    print("      python main.py push <name>            Push changes and sync to YouTube")
    print("      python main.py format <name>          Normalize track IDs and titles")
    print("      python main.py download <name>        Download playlist as audio or video (yt-dlp)")
    print("      python main.py list                   List all configured playlists")
    print("      python main.py link <id_or_url>       Link a new playlist (uses YouTube title)")
    print("      python main.py unlink <name>          Remove a playlist link")
    print("      python main.py help                   Show full documentation and flags")
    print("=" * 70 + "\n")


def print_help():
    """Prints complete CLI usage and all command descriptions."""
    help_text = f"""YouTube Playlist Manager v{VERSION}
A CLI tool to manage, reorder, backup, and synchronize YouTube playlists locally using plain text files.

Usage:
  python main.py <command> [arguments]

OAuth Client Setup:
  Place OAuth client secret JSONs in the 'oauth-clients/' folder, named however you like
  (e.g. oauth-clients/project-a.json, oauth-clients/project-b.json).
  Each file is just a credential tied to a Google Cloud project's API quota - it is
  NOT a fixed user identity. Multiple oauth-client files can point at the same
  project (and therefore share its quota), and any oauth-client file can be used
  to log into any Google account.
  No session tokens are cached: every command that talks to YouTube opens a fresh
  browser login so you can pick the Google account you want for that run. If one
  oauth-client's project runs out of daily quota, just re-run the command with a
  different --client pointed at a project that still has quota left.

Configuration:
  Edit 'settings.toml' in any text editor to customize behavior. Every setting is documented directly in the file.

Commands:
  menu          python main.py
                Displays the welcome menu, recent playlists, and configured oauth clients.

  link          python main.py link <id_or_url> [--method auto|ytdlp|api] [--client <name>]
                Connects a YouTube Playlist ID or URL using the title fetched from YouTube.
                Uses yt-dlp by default (0 Google API quota; falls back to YouTube API if unavailable).

  unlink        python main.py unlink <name>
                Removes a linked playlist.

  list          python main.py list
                Displays all configured playlists with their last CLI edit info.

  pull          python main.py pull [<name>] [--method auto|ytdlp|api] [--client <name>]
                Downloads the live YouTube playlist into playlists/<name>.txt.
                Uses yt-dlp by default (0 Google API quota; falls back to YouTube API if unavailable).
                (prompts to select playlist if omitted and multiple exist).

  push          python main.py push [<name>] [--client <name>]
                Pushes local .txt additions, deletions, and track order to YouTube and
                automatically formats URLs/IDs to <video_id> | <video_title> format
                (prompts to select playlist if omitted and multiple exist).

  format        python main.py format [<name>] [--client <name>]
                Normalizes URLs/IDs into <video_id> | <title> format for readability.
                Uses yt-dlp by default (0 Google API quota; falls back to YouTube API if unavailable).
                (prompts to select playlist if omitted and multiple exist).

  download      python main.py download [<name>] [--format audio|video]
                Downloads all tracks from playlists/<name>.txt using yt-dlp.
                Audio mode downloads best quality in native format (no ffmpeg needed).
                Video mode downloads MP4 video (requires ffmpeg).
                Saves to <downloads_dir>/<name>/ with incremental caching.
                (Prompts to select playlist and audio/video format if omitted).

  help          python main.py help
                Displays this help message with all command usages.

Options:
  --method, -m  Select operation method: 'auto' (yt-dlp with API fallback), 'ytdlp' (0 quota), or 'api' (OAuth).
  --client, -c  Specify which oauth-client JSON to use (must match a file in
                oauth-clients/<name>.json). If omitted: auto-selected if only 1
                oauth client exists, or prompted if multiple exist. This is never
                remembered between runs - you choose it fresh every time.
  -h, --help    Print help
"""
    print(help_text)


def dispatch_command(args, settings, playlist_data, parser=None):
    """Dispatches parsed arguments to the corresponding command handler."""
    from .commands import (
        command_link,
        command_unlink,
        command_list,
        command_pull,
        command_push,
        command_format,
    )
    from .parser import resolve_target_playlist
    from .downloader import command_download

    if not args or not getattr(args, "command", None):
        return

    if getattr(args, "help", False) or args.command == "help":
        print_help()
        return

    if args.command == "menu":
        show_menu(settings, playlist_data, parser)
    elif args.command == "link":
        target = getattr(args, "target", None) or getattr(args, "name", None)
        if not target:
            print("[!] Error: Missing Playlist ID or URL. Syntax: python main.py link <id_or_url> [--client <name>]\n")
            print_help()
            sys.exit(1)
        command_link(args, settings, playlist_data)
    elif args.command == "unlink":
        if not args.name:
            print("[!] Error: Missing playlist name. Syntax: python main.py unlink <name>\n")
            print_help()
            sys.exit(1)
        command_unlink(args, settings, playlist_data)
    elif args.command == "list":
        command_list(args, settings, playlist_data)
    elif args.command == "pull":
        args.target = resolve_target_playlist(
            getattr(args, "target", None),
            playlist_data,
            allow_prompt=True,
            command_name="pull"
        )
        command_pull(args, settings, playlist_data)
    elif args.command == "push":
        args.target = resolve_target_playlist(
            getattr(args, "target", None),
            playlist_data,
            allow_prompt=True,
            command_name="push"
        )
        command_push(args, settings, playlist_data)
    elif args.command == "format":
        args.target = resolve_target_playlist(
            getattr(args, "target", None),
            playlist_data,
            allow_prompt=True,
            command_name="format"
        )
        command_format(args, settings, playlist_data)
    elif args.command == "download":
        args.target = resolve_target_playlist(
            getattr(args, "target", None),
            playlist_data,
            allow_prompt=True,
            command_name="download"
        )
        fmt = getattr(args, "format", None)
        command_download(args, settings, playlist_data, fmt=fmt)
