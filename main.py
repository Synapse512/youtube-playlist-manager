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
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
                sys.stderr.reconfigure(encoding="utf-8")
            except Exception:
                pass

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
        link_parser.add_argument("--account", "-a", default=None, metavar="EMAIL", help="Google account email to use (skips the account picker)")
        link_parser.add_argument("--method", "-m", choices=["auto", "ytdlp", "api"], default=None, help="Link method: auto (default), ytdlp (0 quota), or api (OAuth)")
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
        pull_parser.add_argument("--account", "-a", default=None, metavar="EMAIL", help="Google account email to use (skips the account picker)")
        pull_parser.add_argument("--method", "-m", choices=["auto", "ytdlp", "api"], default=None, help="Pull method: auto (default), ytdlp (0 quota), or api (OAuth)")
        pull_parser.add_argument("--sections", "-s", action="store_true", help="Pull linked section playlists as well as the main playlist")
        pull_parser.add_argument("--pull-mode", choices=["all", "main_only", "sections_only"], default=None, help="Pull mode: main_only (default), all, or sections_only")
        pull_parser.add_argument("-h", "--help", action="store_true")

        # Command: push
        push_parser = subparsers.add_parser("push", add_help=False)
        push_parser.add_argument("target", nargs="?")
        push_parser.add_argument("--client", "-c", default=None, metavar="CLIENT_NAME")
        push_parser.add_argument("--account", "-a", default=None, metavar="EMAIL", help="Google account email to use (skips the account picker)")
        push_parser.add_argument("-h", "--help", action="store_true")

        # Command: format
        format_parser = subparsers.add_parser("format", add_help=False)
        format_parser.add_argument("target", nargs="?")
        format_parser.add_argument("--client", "-c", default=None, metavar="CLIENT_NAME")
        format_parser.add_argument("--account", "-a", default=None, metavar="EMAIL", help="Google account email to use (skips the account picker)")
        format_parser.add_argument("--dedup", "-d", action="store_true", help="Purge duplicate tracks, keeping the first occurrence")
        format_parser.add_argument("-h", "--help", action="store_true")

        # Command: ai-format (aliases: ai-organize)
        ai_parser = subparsers.add_parser("ai-format", aliases=["ai-organize"], add_help=False)
        ai_parser.add_argument("target", nargs="?")
        ai_parser.add_argument("--prompt", "-p", default=None, help="Custom instructions for AI organization and cleanup")
        ai_parser.add_argument("--provider", default=None, help="AI provider override (openai, gemini, groq, openrouter, anthropic, ollama)")
        ai_parser.add_argument("--model", "-m", default=None, help="Model override (e.g. gpt-4o-mini, claude-3-5-haiku, or your provider's model)")
        ai_parser.add_argument("--dry-run", action="store_true", help="Preview AI organization changes without saving to disk")
        ai_parser.add_argument("-h", "--help", action="store_true")

        # Command: download (supports --format audio/video, prompts if omitted)
        dl_parser = subparsers.add_parser("download", add_help=False)
        dl_parser.add_argument("target", nargs="?")
        dl_parser.add_argument("--format", "-f", choices=["audio", "video"], default=None, help="Download mode: audio or video (prompts if omitted)")
        dl_parser.add_argument("--retry-failed", "-r", action="store_true", help="Re-attempt downloading tracks that previously failed")
        dl_parser.add_argument("--clear-failed", action="store_true", help="Clear all recorded failed tracks from the playlist manifest")
        dl_parser.add_argument("--cookies", default=None, metavar="FILE", help="Path to Netscape-format cookies.txt file")
        dl_parser.add_argument("--cookies-from-browser", default=None, metavar="BROWSER", help="Browser to extract cookies from (e.g. firefox, edge, chrome)")
        dl_parser.add_argument("--export-urls", action="store_true", help="Export playlist track URLs to urls.txt for manual yt-dlp downloading")
        dl_parser.add_argument("-h", "--help", action="store_true")

        # Command: config (aliases: settings, set)
        cfg_parser = subparsers.add_parser("config", aliases=["settings", "set"], add_help=False)
        cfg_parser.add_argument("key", nargs="?", default=None, help="Setting key to view or modify")
        cfg_parser.add_argument("value", nargs="?", default=None, help="New value to set")
        cfg_parser.add_argument("--playlist", "-p", default=None, help="Target playlist to view or modify playlist-specific settings")
        cfg_parser.add_argument("--unset", "-u", action="store_true", help="Reset/clear the setting")
        cfg_parser.add_argument("-h", "--help", action="store_true")

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
