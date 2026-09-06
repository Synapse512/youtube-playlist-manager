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


def parse_playlist_file(file_path):
    """
    Parses a local playlist text file.
    Supports lines formatted as:
      - '<video_id> | <title>'
      - '<video_url> | <title>'
      - '<video_id>'
      - '<video_url>'
    Ignores empty lines and comments (lines starting with '#').
    Returns (target_video_ids, target_video_titles, skipped_lines).
    """
    target_video_ids = []
    target_video_titles = {}
    skipped_lines = 0

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
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
            else:
                print(f"    [!] Line {line_num}: Skipping unparseable video ID or URL: '{id_candidate}'")
                skipped_lines += 1

    return target_video_ids, target_video_titles, skipped_lines


def save_playlist_file(file_path, video_ids, video_titles):
    """
    Rewrites the local playlist text file atomically with normalized format:

    # PULL BEFORE MAKING CHANGES

    <video_id> | <video_title>
    """
    lines = ["# PULL BEFORE MAKING CHANGES", ""]
    for vid_id in video_ids:
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
