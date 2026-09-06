"""
Terminal UI, dashboard menu, interactive account display, and help output.
"""

import os
import sys

from .config import VERSION, PLAYLISTS_DIR
from .auth import get_users
from .parser import sanitize_filename, read_playlist_user


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
                safe_name = sanitize_filename(p_name)
                file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")
                file_user = read_playlist_user(file_path) if os.path.exists(file_path) else None
                user_tag = f"  [{file_user}]" if file_user else ""

                act = activity.get(p_name, {})
                last_cmd = act.get("last_command")
                last_time = act.get("last_time")
                if last_cmd and last_time:
                    last_edit_str = f"{last_cmd} on {last_time}"
                else:
                    last_edit_str = "None recorded"

                print(f"    {idx}. {p_name} [{terminal_link(pid, url)}]{user_tag}")
                print(f"       • most recent edit: {last_edit_str}")
                print()

    raw_user_limit = settings.get("menu_user_count", settings.get("menu_account_count", settings.get("show_accounts_in_menu", 3)))

    if raw_user_limit is False or str(raw_user_limit).strip().lower() in ("false", "no"):
        user_limit = 0
    elif isinstance(raw_user_limit, str) and raw_user_limit.strip().lower() == "all":
        user_limit = None
    elif raw_user_limit is True or str(raw_user_limit).strip().lower() in ("true", "yes"):
        user_limit = 3
    else:
        try:
            user_limit = max(0, int(raw_user_limit))
        except (ValueError, TypeError):
            user_limit = 3

    users = get_users()
    if user_limit is not None:
        displayed_users = users[:user_limit]
    else:
        displayed_users = users

    if user_limit != 0:
        leading_newline = "" if has_shown_section else "\n"
        print(f"{leading_newline}  [*] Accounts:")
        has_shown_section = True
        if displayed_users:
            for u in displayed_users:
                print(f"      • {u}")
            print()
        else:
            print("      No accounts found in 'users/'.\n")

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

Multi-Account Setup:
  Place each user's OAuth client secret JSON in the 'users/' folder, renamed to '<username>.json'.
  Example: users/dalton.json, users/john.json
  Cached session tokens are stored automatically in 'data/tokens/<username>.json'.
  Playlist files record their account with a '# user: <username>' header line.

Configuration (settings.json):
  Edit 'settings.json' in any text editor to customize tool behavior:
  - "safety_check_before_push": true | false
      Prompts for confirmation before pushing to YouTube to prevent accidental overwrites. (Default: true)
  - "menu_playlist_count": <number> | "all"
      Number of playlists to show in the menu, or "all" to show all playlists. (Default: 3)
  - "menu_user_count": <number> | "all"
      Number of user accounts to show in the menu, or "all" to show all accounts. (Default: 3)
  - "enable_logging": true | false
      Records operation logs in 'logs/<name>.log' tracking additions, removals, and changes. (Default: true)

Commands:
  menu    python main.py
          Displays the welcome menu, recent playlists, and configured accounts.

  link    python main.py link <id_or_url> [--user <username>]
          Connects a YouTube Playlist ID or URL using the title fetched from YouTube.
          Creates a playlist file with '# user: <username>' header (prompts to choose account
          if multiple exist and --user is omitted).

  unlink  python main.py unlink <name>
          Removes a linked playlist.

  list    python main.py list
          Displays all configured playlists with their associated user accounts and last CLI edit info.

  pull    python main.py pull <name> [--user <username>]
          Downloads the live YouTube playlist into playlists/<name>.txt.
          User is read from the file's '# user:' header if not specified.

  push    python main.py push <name> [--user <username>]
          Pushes local .txt additions, deletions, and track order to YouTube and
          automatically formats URLs/IDs to <video_id> | <video_title> format.
          User is read from the file's '# user:' header if not specified.

  format  python main.py format <name> [--user <username>]
          Normalizes URLs/IDs into <video_id> | <title> format for readability.
          User is read from the file's '# user:' header if not specified.

  help    python main.py help
          Displays this help message with all command usages.

Options:
  --user, -u  Specify the username (must match a file in users/<username>.json).
              If omitted: auto-selected if 1 user exists, or prompted if multiple exist.
  -h, --help  Print help
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
            print("[!] Error: Missing Playlist ID or URL. Syntax: python main.py link <id_or_url> [--user <username>]\n")
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
            print("[!] Error: Missing target. Syntax: python main.py pull <name> [--user <username>]\n")
            print_help()
            sys.exit(1)
        command_pull(args, settings, playlist_data)
    elif args.command == "push":
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py push <name> [--user <username>]\n")
            print_help()
            sys.exit(1)
        command_push(args, settings, playlist_data)
    elif args.command == "format":
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py format <name> [--user <username>]\n")
            print_help()
            sys.exit(1)
        command_format(args, settings, playlist_data)
