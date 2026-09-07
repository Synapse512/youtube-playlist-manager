"""
URL and video ID parsing, playlist ID resolution, and playlist file reading/writing.
"""

import os
import re
from urllib.parse import urlparse, parse_qs

from .config import load_playlist_data


def extract_video_id(input_str):
    """
    Extracts an 11-character YouTube video ID from a raw ID or various YouTube URL formats.
    Supports:
      - Raw 11-char ID (e.g. 'dQw4w9WgXcQ')
      - https://www.youtube.com/watch?v=dQw4w9WgXcQ
      - https://youtu.be/dQw4w9WgXcQ
      - https://www.youtube.com/shorts/dQw4w9WgXcQ
      - https://www.youtube.com/live/dQw4w9WgXcQ
      - https://www.youtube.com/embed/dQw4w9WgXcQ
      - https://music.youtube.com/watch?v=dQw4w9WgXcQ
      - URLs with extra query parameters, timestamps, or playlist contexts
    """
    if not input_str:
        return None
    input_str = input_str.strip()
    if not input_str:
        return None

    # If it's a raw 11-char ID
    if re.fullmatch(r"[a-zA-Z0-9_-]{11}", input_str):
        return input_str

    # Parse standard URL
    url_str = input_str if "://" in input_str else f"https://{input_str}"
    try:
        parsed = urlparse(url_str)
        host = parsed.netloc.lower()
        if "youtube.com" in host:
            qs = parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                candidate = qs["v"][0]
                if re.fullmatch(r"[a-zA-Z0-9_-]{11}", candidate):
                    return candidate
            path_parts = [p for p in parsed.path.split("/") if p]
            if len(path_parts) >= 2 and path_parts[0] in ("shorts", "live", "embed", "v"):
                candidate = path_parts[1]
                if re.fullmatch(r"[a-zA-Z0-9_-]{11}", candidate):
                    return candidate
        elif "youtu.be" in host:
            path_parts = [p for p in parsed.path.split("/") if p]
            if path_parts:
                candidate = path_parts[0]
                if re.fullmatch(r"[a-zA-Z0-9_-]{11}", candidate):
                    return candidate
    except Exception:
        pass

    # Regex search fallback
    patterns = [
        r"(?:v=|\/shorts\/|\/live\/|\/embed\/|\/v\/|youtu\.be\/)([a-zA-Z0-9_-]{11})"
    ]
    for pattern in patterns:
        m = re.search(pattern, input_str)
        if m:
            return m.group(1)

    return None


def extract_playlist_id(input_str):
    """Extracts a playlist ID from a raw ID or a full YouTube URL."""
    input_str = input_str.strip()
    if "youtube.com" in input_str or "youtu.be" in input_str:
        parsed = urlparse(input_str)
        qs = parse_qs(parsed.query)
        if "list" in qs:
            return qs["list"][0]
    return input_str


def sanitize_filename(name):
    """Replaces characters that are illegal in filenames."""
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def resolve_playlist_id(name_or_id, playlist_data=None):
    """Resolves a playlist name or URL/ID to the actual YouTube Playlist ID."""
    name_or_id = name_or_id.strip()
    if playlist_data is None:
        playlist_data = load_playlist_data()
    playlists = playlist_data.get("playlists", {})
    resolved = playlists.get(name_or_id, name_or_id)
    return extract_playlist_id(resolved)


def get_playlist_name_for_target(target, playlist_data=None):
    """Finds the playlist name corresponding to a target name or playlist ID/URL."""
    if not target:
        return ""
    target = target.strip()
    if playlist_data is None:
        playlist_data = load_playlist_data()
    playlists = playlist_data.get("playlists", {})
    if target in playlists:
        return target
    clean_id = extract_playlist_id(target)
    for name, pid in playlists.items():
        if pid == clean_id or pid == target:
            return name
    return target


get_alias_for_target = get_playlist_name_for_target


def resolve_target_playlist(target_name=None, playlist_data=None, allow_prompt=False, command_name=None):
    """
    Resolves which playlist to use for an operation.
    - If target_name is provided, returns it stripped.
    - If no target_name is provided and no playlists exist, prints an error and exits.
    - If only one playlist exists, auto-selects it.
    - If multiple playlists exist and allow_prompt is True, interactively prompts the user.
    - If multiple playlists exist and allow_prompt is False, prints an error and exits.
    """
    if target_name and target_name.strip():
        return target_name.strip()

    if playlist_data is None:
        playlist_data = load_playlist_data()

    playlists = playlist_data.get("playlists", {})
    activity = playlist_data.get("activity", {})

    if not playlists:
        print("\n" + "=" * 65)
        print(" ERROR: No playlists configured yet.")
        print("=" * 65)
        print("  You must link a playlist before running this command.")
        print("  Use: python main.py link <id_or_url> [--client <name>]")
        print("=" * 65 + "\n")
        import sys
        sys.exit(1)

    # Sort playlists by recent activity (count, last_time) descending, then by name
    ranked_playlists = sorted(
        playlists.keys(),
        key=lambda a: (
            activity.get(a, {}).get("count", 0),
            activity.get(a, {}).get("last_time", "")
        ),
        reverse=True
    )

    if len(ranked_playlists) == 1:
        selected = ranked_playlists[0]
        print(f"[*] Auto-selected playlist: '{selected}'")
        return selected

    if allow_prompt:
        cmd_str = f" for '{command_name}'" if command_name else ""
        print(f"\n[?] Multiple playlists found. Which playlist do you want to use{cmd_str}?")
        for idx, p_name in enumerate(ranked_playlists, 1):
            act = activity.get(p_name, {})
            last_cmd = act.get("last_command")
            last_time = act.get("last_time")
            if last_cmd and last_time:
                info = f" (last: {last_cmd} on {last_time})"
            else:
                info = ""
            print(f"    [{idx}] {p_name}{info}")
        print()
        import sys
        while True:
            try:
                choice = input(f"Select a playlist (1-{len(ranked_playlists)}) or type its name: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[!] Operation cancelled by user.")
                sys.exit(130)

            if not choice:
                continue

            if choice.isdigit():
                val = int(choice)
                if 1 <= val <= len(ranked_playlists):
                    selected = ranked_playlists[val - 1]
                    print(f"[+] Selected playlist: '{selected}'")
                    return selected
            elif choice in playlists:
                print(f"[+] Selected playlist: '{choice}'")
                return choice
            else:
                # Check case-insensitive match
                lower_map = {k.lower(): k for k in ranked_playlists}
                if choice.lower() in lower_map:
                    selected = lower_map[choice.lower()]
                    print(f"[+] Selected playlist: '{selected}'")
                    return selected

            print(f"[!] Invalid selection '{choice}'. Please enter a number between 1 and {len(ranked_playlists)} or a valid playlist name.")

    import sys
    print(f"\n[!] Error: Multiple playlists found but none specified.")
    print(f"    Available playlists: {', '.join(ranked_playlists)}")
    sys.exit(1)


def parse_playlist_file(file_path):
    """
    Parses a local playlist text file.
    Supports lines formatted as:
      - '<video_id> | <title>'
      - '<video_url> | <title>'
      - '<video_id>'
      - '<video_url>'
    Ignores empty lines and comments (lines starting with '#').
    Returns (target_video_ids, target_video_titles, skipped_lines, blank_above).
    blank_above is a set of video IDs that had at least one blank line above them.
    """
    target_video_ids = []
    target_video_titles = {}
    skipped_lines = 0
    blank_above = set()
    first_song_seen = False
    blank_pending = False

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                if first_song_seen:
                    blank_pending = True
                continue

            if line.startswith("#"):
                continue

            if "|" in line:
                parts = line.split("|", 1)
                id_candidate = parts[0].strip()
                title_candidate = parts[1].strip()
            else:
                id_candidate = line.strip()
                title_candidate = ""

            vid_id = extract_video_id(id_candidate)
            if vid_id:
                target_video_ids.append(vid_id)
                if title_candidate:
                    target_video_titles[vid_id] = title_candidate
                if blank_pending:
                    blank_above.add(vid_id)
                    blank_pending = False
                first_song_seen = True
            else:
                print(f"    [!] Line {line_num}: Skipping unparseable video ID or URL: '{id_candidate}'")
                skipped_lines += 1

    return target_video_ids, target_video_titles, skipped_lines, blank_above


def save_playlist_file(file_path, video_ids, video_titles, blank_above=None):
    """
    Rewrites the local playlist text file atomically with normalized format:

    # PULL BEFORE MAKING CHANGES

    <video_id> | <video_title>
    """
    if blank_above is None:
        blank_above = set()
    elif not isinstance(blank_above, set):
        blank_above = set(blank_above)

    lines = ["# PULL BEFORE MAKING CHANGES", ""]
    for i, vid_id in enumerate(video_ids):
        if i > 0 and vid_id in blank_above:
            lines.append("")
        title = video_titles.get(vid_id) or "Untitled Video"
        lines.append(f"{vid_id} | {title}")

    temp_file = f"{file_path}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        if os.path.exists(file_path):
            os.replace(temp_file, file_path)
        else:
            os.rename(temp_file, file_path)
        return True
    except OSError as e:
        print(f"[!] Error saving normalized playlist to '{file_path}': {e}")
        return False

