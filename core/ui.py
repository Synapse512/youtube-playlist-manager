"""
Terminal UI, dashboard menu, interactive oauth-client display, and help output.
"""

import sys

from .config import VERSION
from .auth import get_oauth_clients


def terminal_link(text, url):
    """Formats text as a clickable OSC 8 terminal hyperlink."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def show_menu(settings, playlist_data, parser=None):
    """Displays the welcome menu with recent playlists and quick actions."""
    playlists = playlist_data.get("playlists", {})
    activity = playlist_data.get("activity", {})

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
        playlists.keys(),
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
        if not playlists:
            print("      No playlists configured yet.\n")
        else:
            for idx, p_name in enumerate(recent_playlists, 1):
                pid = playlists[p_name]
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
    print("      python main.py pull <name>        Download playlist to local file")
    print("      python main.py push <name>        Push changes and sync to YouTube")
    print("      python main.py format <name>      Normalize track IDs and titles")
    print("      python main.py list               List all configured playlists")
    print("      python main.py link <id_or_url>   Link a new playlist (uses YouTube title)")
    print("      python main.py unlink <name>      Remove a playlist link")
    print("      python main.py help               Show full documentation and flags")
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

Configuration (settings.json):
  Edit 'settings.json' in any text editor to customize tool behavior:
  - "safety_check_before_push": true | false
      Prompts for confirmation before pushing to YouTube to prevent accidental overwrites. (Default: true)
  - "menu_playlist_count": <number> | "all"
      Number of playlists to show in the menu, or "all" to show all playlists. (Default: 3)
  - "menu_client_count": <number> | "all"
      Number of oauth clients to show in the menu, or "all" to show all of them. (Default: 3)
  - "enable_logging": true | false
      Records operation logs in 'logs/<name>.log' tracking additions, removals, and changes. (Default: true)

Commands:
  menu    python main.py
          Displays the welcome menu, recent playlists, and configured oauth clients.

  link    python main.py link <id_or_url> [--client <name>]
          Connects a YouTube Playlist ID or URL using the title fetched from YouTube
          (prompts to choose an oauth client if multiple exist and --client is omitted).

  unlink  python main.py unlink <name>
          Removes a linked playlist.

  list    python main.py list
          Displays all configured playlists with their last CLI edit info.

  pull    python main.py pull <name> [--client <name>]
          Downloads the live YouTube playlist into playlists/<name>.txt.

  push    python main.py push <name> [--client <name>]
          Pushes local .txt additions, deletions, and track order to YouTube and
          automatically formats URLs/IDs to <video_id> | <video_title> format.

  format  python main.py format <name> [--client <name>]
          Normalizes URLs/IDs into <video_id> | <title> format for readability.

  help    python main.py help
          Displays this help message with all command usages.

Options:
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
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py pull <name> [--client <name>]\n")
            print_help()
            sys.exit(1)
        command_pull(args, settings, playlist_data)
    elif args.command == "push":
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py push <name> [--client <name>]\n")
            print_help()
            sys.exit(1)
        command_push(args, settings, playlist_data)
    elif args.command == "format":
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py format <name> [--client <name>]\n")
            print_help()
            sys.exit(1)
        command_format(args, settings, playlist_data)
