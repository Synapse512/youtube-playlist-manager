"""
YouTube Playlist Manager (ypm)
Entry point launcher. Orchestrates CLI arguments and dispatches to the core package.
"""

import sys
import argparse

from core.config import (
    load_settings,
    load_playlist_data,
)
from core.ui import show_menu, print_help, dispatch_command


def main():
    try:
        settings = load_settings()
        playlist_data = load_playlist_data()

        # Handle top-level help command before parsing
        if len(sys.argv) > 1 and sys.argv[1].lower() in ("help", "-h", "--help"):
            print_help()
            sys.exit(0)

        parser = argparse.ArgumentParser(
            description="YouTube Playlist CLI Manager - Reorder, sync, and manage YouTube playlists using local text files.",
            add_help=False
        )
        parser.add_argument("-h", "--help", action="store_true")
        subparsers = parser.add_subparsers(dest="command")

        # Command: menu
        menu_parser = subparsers.add_parser("menu", add_help=False)
        menu_parser.add_argument("-h", "--help", action="store_true")

        # Command: link
        link_parser = subparsers.add_parser("link", add_help=False)
        link_parser.add_argument("target", nargs="?", metavar="ID_OR_URL")
        link_parser.add_argument("--client", "-c", default=None, metavar="CLIENT_NAME")
        link_parser.add_argument("-h", "--help", action="store_true")

        # Command: unlink
        unlink_parser = subparsers.add_parser("unlink", add_help=False)
        unlink_parser.add_argument("name", nargs="?")
        unlink_parser.add_argument("-h", "--help", action="store_true")

        # Command: list
        list_parser = subparsers.add_parser("list", add_help=False)
        list_parser.add_argument("-h", "--help", action="store_true")

        # Command: pull
        pull_parser = subparsers.add_parser("pull", add_help=False)
        pull_parser.add_argument("target", nargs="?")
        pull_parser.add_argument("--client", "-c", default=None, metavar="CLIENT_NAME")
        pull_parser.add_argument("-h", "--help", action="store_true")

        # Command: push
        push_parser = subparsers.add_parser("push", add_help=False)
        push_parser.add_argument("target", nargs="?")
        push_parser.add_argument("--client", "-c", default=None, metavar="CLIENT_NAME")
        push_parser.add_argument("-h", "--help", action="store_true")

        # Command: format
        format_parser = subparsers.add_parser("format", add_help=False)
        format_parser.add_argument("target", nargs="?")
        format_parser.add_argument("--client", "-c", default=None, metavar="CLIENT_NAME")
        format_parser.add_argument("-h", "--help", action="store_true")

        # Command: download (supports --format audio/video, prompts if omitted)
        dl_parser = subparsers.add_parser("download", add_help=False)
        dl_parser.add_argument("target", nargs="?")
        dl_parser.add_argument("--format", "-f", choices=["audio", "video"], default=None, help="Download mode: audio or video (prompts if omitted)")
        dl_parser.add_argument("-h", "--help", action="store_true")

        # Command: help
        help_parser = subparsers.add_parser("help", add_help=False)
        help_parser.add_argument("-h", "--help", action="store_true")

        args = parser.parse_args()

        if getattr(args, "help", False) or (args.command == "help"):
            print_help()
            sys.exit(0)

        # No params -> open menu
        if not args.command:
            show_menu(settings, playlist_data, parser)
            sys.exit(0)

        dispatch_command(args, settings, playlist_data, parser)

    except KeyboardInterrupt:
        print("\n\n[!] Operation cancelled by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
