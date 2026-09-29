"""
URL and video ID parsing, playlist ID resolution, and playlist file reading/writing.
"""

import os
import re
from urllib.parse import urlparse, parse_qs

from .config import (
    load_playlist_data,
    PLAYLISTS_DIR,
    ENTRY_SEPARATOR,
    DEFAULT_PLAYLIST_ENTRY_FORMAT,
    normalize_playlist_entry_format,
    playlist_entry_format_fields,
)

ENTRY_METADATA_DEFAULTS = {
    "id": "",
    "title": "",
    "channel": "",
    "duration": "",
}

# Known YouTube playlist ID prefixes (uploads, likes, mixes, watch-later, etc.)
_PLAYLIST_PREFIXES = ("PL", "UU", "FL", "LL", "RD", "OL", "VL", "CL", "TL")
# Special reserved playlist IDs that are exactly two characters (no prefix pattern)
_RESERVED_PLAYLIST_IDS = {"WL", "LL", "HL"}
_PLAYLIST_URL_HOSTS = {"youtube.com", "www.youtube.com", "music.youtube.com", "m.youtube.com"}


def _playlist_name_from_file_path(file_path):
    return os.path.splitext(os.path.basename(file_path))[0]


def _load_entry_format_for_file(file_path):
    """Looks up playlist_entry_format for playlists/<name>.txt from playlist-settings.toml."""
    try:
        from .config import load_all_playlist_settings
        entry = load_all_playlist_settings().get(_playlist_name_from_file_path(file_path), {})
        if isinstance(entry, dict):
            return normalize_playlist_entry_format(entry.get("playlist_entry_format"))
    except Exception:
        pass
    return DEFAULT_PLAYLIST_ENTRY_FORMAT


_DURATION_RE = re.compile(r"^\d+:\d{2}(:\d{2})?$")


def parse_playlist_entry_line(line_str, entry_format=None):
    """
    Parses one video line according to playlist_entry_format.
    Returns (video_id_or_None, metadata_dict).

    Fields are separated by "|" in the order given by the format (id always first).
    Titles may themselves contain "|": when a line has more parts than the format
    has fields, the surplus is folded back into the title. Lines with fewer parts
    (e.g. a bare URL or ID) simply leave the remaining fields empty.
    """
    fields = playlist_entry_format_fields(entry_format)
    parts = [p.strip() for p in line_str.split("|")]

    # Drop trailing empty parts beyond what the format expects (e.g. "id | title |  |  | ")
    while len(parts) > len(fields) and parts[-1] == "":
        parts.pop()

    # Intelligent format recovery: if fields was default ["id", "title"] but the line
    # actually has 3 or 4 columns ending in a duration (e.g. "id | title | channel | duration"),
    # do NOT blindly concatenate channel and duration into the title.
    if fields == ["id", "title"]:
        if len(parts) >= 4 and _DURATION_RE.match(parts[-1]):
            fields = ["id", "title", "channel", "duration"]
        elif len(parts) == 3 and _DURATION_RE.match(parts[-1]):
            fields = ["id", "title", "duration"]

    values = {}
    if len(parts) <= len(fields) or "title" not in fields:
        for field, part in zip(fields, parts):
            values[field] = part
    else:
        title_idx = fields.index("title")
        after = fields[title_idx + 1:]
        title_end = len(parts) - len(after)
        for field, part in zip(fields[:title_idx], parts[:title_idx]):
            values[field] = part
        values["title"] = ENTRY_SEPARATOR.join(parts[title_idx:title_end])
        for field, part in zip(after, parts[title_end:]):
            values[field] = part

    metadata = dict(ENTRY_METADATA_DEFAULTS)
    metadata.update({k: v for k, v in values.items() if k != "id"})

    id_candidate = parts[0] if parts else ""
    vid_id = extract_video_id(id_candidate)
    metadata["id"] = vid_id or id_candidate
    return vid_id, metadata


def _format_playlist_entry_line(vid_id, metadata, entry_format=None, clickable_links=False):
    """Builds one playlist text line: fields in the configured order, joined by ' | '."""
    values = dict(ENTRY_METADATA_DEFAULTS)
    values.update(metadata or {})
    values["id"] = f"https://youtu.be/{vid_id}" if clickable_links else vid_id
    cells = []
    for field in playlist_entry_format_fields(entry_format):
        cells.append(" ".join(str(values.get(field) or "").split()))
    return ENTRY_SEPARATOR.join(cells).rstrip()


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
    if playlist_data is None:
        playlist_data = load_playlist_data()

    playlists = playlist_data.get("playlists", {})
    activity = playlist_data.get("activity", {})

    if target_name and target_name.strip():
        raw_target = target_name.strip()
        # 1. Exact match
        if raw_target in playlists:
            return raw_target
        for p_name, pid in playlists.items():
            if pid == raw_target:
                return p_name

        # 2. Case-insensitive exact match
        lower_target = raw_target.lower()
        lower_map = {k.lower(): k for k in playlists.keys()}
        if lower_target in lower_map:
            return lower_map[lower_target]

        # 3. Case-insensitive prefix match (e.g. 'for' -> 'Forsaken OST (Roblox)')
        prefix_matches = [k for k in playlists.keys() if k.lower().startswith(lower_target)]
        if len(prefix_matches) == 1:
            print(f"[*] Auto-matched playlist '{raw_target}' -> '{prefix_matches[0]}'")
            return prefix_matches[0]
        elif len(prefix_matches) > 1:
            # Narrow candidates to prefix matches if user was prompting or specific
            candidate_keys = prefix_matches
        else:
            # 4. Case-insensitive substring match
            sub_matches = [k for k in playlists.keys() if lower_target in k.lower()]
            if len(sub_matches) == 1:
                print(f"[*] Auto-matched playlist '{raw_target}' -> '{sub_matches[0]}'")
                return sub_matches[0]
            elif len(sub_matches) > 1:
                candidate_keys = sub_matches
            else:
                return raw_target
    else:
        candidate_keys = None

    if not playlists:
        print("\n" + "=" * 65)
        print(" ERROR: No playlists configured yet.")
        print("=" * 65)
        print("  You must link a playlist before running this command.")
        print("  Use: python main.py link <id_or_url> [--client <name>]")
        print("=" * 65 + "\n")
        import sys
        sys.exit(1)

    # Prioritize playlists whose local text file exists on disk (unless command is pull or none exist)
    if candidate_keys is None:
        existing_file_playlists = [
            k for k in playlists.keys()
            if os.path.isfile(os.path.join(PLAYLISTS_DIR, f"{k}.txt")) or
               os.path.isfile(os.path.join(PLAYLISTS_DIR, f"{sanitize_filename(k)}.txt"))
        ]
        candidate_keys = existing_file_playlists if (existing_file_playlists and command_name != "pull") else list(playlists.keys())

    # Sort playlists by recent activity (count, last_time) descending, then by name
    ranked_playlists = sorted(
        candidate_keys,
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


from typing import NamedTuple


class PlaylistParseResult(NamedTuple):
    video_ids: list
    video_titles: dict
    skipped_lines: int
    blank_above: set
    sections_data: dict


def parse_header_line(line):
    """
    Parses a '## ...' header line into a dictionary:
    {
        "playlist_id": Optional[str],
        "url": Optional[str],
        "title": str,
        "raw": str
    }
    Supports:
      - '## https://www.youtube.com/playlist?list=PLxyz | Title'
      - '## PLxyz | Title'
      - '## https://www.youtube.com/playlist?list=PLxyz'
      - '## PLxyz'
      - '## Section Title Without Link'
    """
    raw = line.strip()
    content = raw.lstrip("#").strip()
    if "|" in content:
        parts = [p.strip() for p in content.split("|")]
        left = parts[0]
        pid = extract_playlist_id(left)
        if pid and (pid != left or pid.startswith(_PLAYLIST_PREFIXES)):
            url = left if "://" in left else f"https://www.youtube.com/playlist?list={pid}"
            # parts can be:
            # [url, section_title] or [url, section_title, playlist_name]
            # or [url, playlist_name]
            sec_title = parts[1] if len(parts) > 1 else ""
            pl_name = parts[2] if len(parts) > 2 else ""
            return {
                "playlist_id": pid,
                "url": url,
                "title": sec_title,
                "playlist_name": pl_name,
                "raw": raw
            }
        elif "youtube.com" in left or "youtu.be" in left:
            url = left if "://" in left else f"https://{left}"
            sec_title = parts[1] if len(parts) > 1 else ""
            pl_name = parts[2] if len(parts) > 2 else ""
            return {
                "playlist_id": pid,
                "url": url,
                "title": sec_title,
                "playlist_name": pl_name,
                "raw": raw
            }
        else:
            return {"playlist_id": None, "url": None, "title": content, "raw": raw}
    else:
        pid = extract_playlist_id(content)
        if pid and (pid != content or content.startswith(_PLAYLIST_PREFIXES)):
            url = content if "://" in content else f"https://www.youtube.com/playlist?list={pid}"
            return {"playlist_id": pid, "url": url, "title": "", "raw": raw}
        elif "youtube.com" in content or "youtu.be" in content:
            url = content if "://" in content else f"https://{content}"
            return {"playlist_id": pid, "url": url, "title": "", "raw": raw}
        else:
            return {"playlist_id": None, "url": None, "title": content, "raw": raw}


def playlist_link_format_issue(header):
    """
    Checks whether a parsed header/section dict (as produced by parse_header_line /
    parse_playlist_file) has a playlist link that looks like a genuine, well-formed
    YouTube playlist reference - either a full canonical URL
    (https://www.youtube.com/playlist?list=<id>) or a bare, well-formed playlist ID.

    This exists because it's very easy, when hand-editing a section header, to paste
    a mistyped ID, a truncated URL, or a video link instead of a playlist link - and
    such a mistake would otherwise be silently accepted, which is dangerous for
    'push' (it pushes real inserts/deletes/reorders to whatever playlist ID ends up
    there). This is purely a format/shape check - it can't know whether an ID that
    LOOKS valid actually points at the playlist the user meant.

    Returns None if the link looks fine (or if there's no link at all - a section
    with just a title and no link is valid on its own). Otherwise returns a short,
    human-readable description of what looks wrong.
    """
    raw = (header.get("raw") or "").strip()
    content = raw.lstrip("#").strip()
    left = content.split("|")[0].strip() if content else ""
    url = header.get("url")
    pid = header.get("playlist_id")

    if not pid and not url:
        # No link at all. Could genuinely just be a title-only section/header, but
        # if the raw text looks like a botched attempt at pasting a link, flag it.
        if re.search(r"youtu\.?be|list=|playlist", left, re.IGNORECASE):
            return f"couldn't find a valid playlist ID in '{left}'"
        return None

    if pid in _RESERVED_PLAYLIST_IDS:
        return None

    if url:
        try:
            parsed = urlparse(url)
        except Exception:
            return f"'{url}' doesn't look like a valid URL"
        host = parsed.netloc.lower()
        if host not in _PLAYLIST_URL_HOSTS:
            return f"'{url}' isn't a youtube.com/playlist link"
        if parsed.path.rstrip("/") != "/playlist":
            return f"'{url}' is missing the '/playlist' path (looks like a video link, not a playlist link)"
        list_vals = parse_qs(parsed.query).get("list") or []
        if not list_vals or not re.fullmatch(r"[A-Za-z0-9_-]{10,64}", list_vals[0]):
            return f"'{url}' has a missing or malformed 'list=' ID"
        return None

    # Bare ID, no URL wrapper around it
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,64}", pid or ""):
        return f"'{pid}' doesn't look like a valid playlist ID"
    return None


def parse_playlist_file(file_path, entry_format=None):
    """
    Parses a local playlist text file supporting section headers.
    Supports lines formatted as:
      - '### <link | title>' or '### <title>' (the single main playlist header)
      - '## <link | title>' or '## <title>' (section headers)
      - video lines laid out by the playlist's playlist_entry_format
        (e.g. '<video_id> | <title> | <channel> | <duration>'); the ID may also be a URL
      - '<video_id>' / '<video_url>' on its own
    Ignores single '#' comments and empty lines.

    The main header is written with three hashes ('###') so it can never be confused
    with a section header ('##'), no matter where lines get reordered by hand. Files
    written before this convention only ever have '##' headers; for those, the first
    '##' header encountered before any track line is still treated as the main header,
    exactly as before, so nothing already on disk breaks.
    Returns PlaylistParseResult(target_video_ids, target_video_titles, skipped_lines, blank_above, sections_data).
    """
    target_video_ids = []
    target_video_titles = {}
    video_metadata = {}
    skipped_lines = 0
    blank_above = set()
    first_song_seen = False
    blank_pending = False

    main_header = None
    sections = []
    current_section = None
    first_header = True
    entry_format = normalize_playlist_entry_format(entry_format) if entry_format else _load_entry_format_for_file(file_path)

    # If the format is default ["id", "title"], probe the file lines to see
    # if it was written with channel and/or duration fields
    if entry_format == DEFAULT_PLAYLIST_ENTRY_FORMAT and os.path.isfile(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f_detect:
                for det_line in f_detect:
                    det_s = det_line.strip()
                    if det_s and not det_s.startswith("#"):
                        det_parts = [p.strip() for p in det_s.split("|")]
                        if len(det_parts) >= 4 and _DURATION_RE.match(det_parts[-1]):
                            entry_format = "%(id)s | %(title)s | %(channel)s | %(duration)s"
                            break
                        elif len(det_parts) == 3 and _DURATION_RE.match(det_parts[-1]):
                            entry_format = "%(id)s | %(title)s | %(duration)s"
                            break
        except Exception:
            pass

    # Quick pre-scan: if the file has any explicit '###' (or more) header anywhere,
    # that marker - not line position - decides which header is the main one.
    with open(file_path, "r", encoding="utf-8") as f:
        has_explicit_main_marker = any(l.strip().startswith("###") for l in f)

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                blank_pending = True
                continue

            # Header line (starts with ##)
            if line_str.startswith("##"):
                hdr = parse_header_line(line_str)
                header_blank_above = blank_pending
                hash_count = len(line_str) - len(line_str.lstrip("#"))

                if has_explicit_main_marker:
                    # '###'+ always wins as the main header, regardless of position
                    # (that's the whole point - it no longer has to be first).
                    is_main_header_line = main_header is None and hash_count >= 3
                else:
                    # Legacy file with no '###' anywhere - original rule applies:
                    # whichever '##' header comes first (before any track) is main.
                    is_main_header_line = first_header and not first_song_seen

                if is_main_header_line:
                    # Top-level main playlist header
                    if current_section and (current_section["video_ids"] or not current_section.get("is_implicit")):
                        sections.append(current_section)
                    main_header = hdr
                    main_header["blank_above"] = header_blank_above
                    current_section = {
                        "title": hdr.get("title") or "Main",
                        "playlist_id": hdr.get("playlist_id"),
                        "url": hdr.get("url"),
                        "video_ids": [],
                        "blank_above": set(),
                        "header_blank_above": False,
                        "is_implicit": True,
                    }
                else:
                    if current_section and (current_section["video_ids"] or not current_section.get("is_implicit")):
                        sections.append(current_section)
                    current_section = {
                        "title": hdr.get("title", "").strip(),
                        "playlist_name": hdr.get("playlist_name", "").strip(),
                        "playlist_id": hdr.get("playlist_id"),
                        "url": hdr.get("url"),
                        "video_ids": [],
                        "blank_above": set(),
                        "header_blank_above": header_blank_above,
                        "is_implicit": False,
                    }

                first_header = False
                blank_pending = False
                continue

            # Comments (single #)
            if line_str.startswith("#"):
                continue

            vid_id, metadata = parse_playlist_entry_line(line_str, entry_format)
            if vid_id:
                target_video_ids.append(vid_id)
                video_metadata[vid_id] = metadata
                title_candidate = metadata.get("title", "")
                if title_candidate:
                    target_video_titles[vid_id] = title_candidate
                if blank_pending:
                    blank_above.add(vid_id)
                    if current_section:
                        current_section["blank_above"].add(vid_id)
                    blank_pending = False
                if current_section is None:
                    current_section = {
                        "title": "Main",
                        "playlist_id": None,
                        "url": None,
                        "video_ids": [],
                        "blank_above": set(),
                        "header_blank_above": False,
                        "is_implicit": True,
                    }
                current_section["video_ids"].append(vid_id)
                first_song_seen = True
            else:
                id_candidate = metadata.get("id", line_str)
                print(f"    [!] Line {line_num}: Skipping unparseable video ID or URL: '{id_candidate}'")
                skipped_lines += 1

    if current_section and (current_section["video_ids"] or not current_section.get("is_implicit")):
        sections.append(current_section)

    explicit_sections = [s for s in sections if not s.get("is_implicit")]
    is_sectioned = len(explicit_sections) > 0

    sections_data = {
        "main_header": main_header,
        "is_sectioned": is_sectioned,
        "sections": sections,
        "video_metadata": video_metadata,
    }

    return PlaylistParseResult(
        target_video_ids,
        target_video_titles,
        skipped_lines,
        blank_above,
        sections_data
    )


def save_playlist_file(file_path, video_ids, video_titles, blank_above=None, clickable_links=None, sections_data=None, main_header=None, video_metadata=None, playlist_entry_format=None):
    """
    Rewrites the local playlist text file atomically with normalized format:

    ### <playlist_url> | <playlist_title>

    followed by track list or sections:
    ## <section_url> | <section_title>
    <video_id> | <video_title> | ...   (fields per the playlist's playlist_entry_format)

    The main header always gets three hashes ('###') and section headers always get
    two ('##'), so the two can never be confused with each other regardless of order.
    """
    if clickable_links is None:
        try:
            from .config import load_settings
            clickable_links = load_settings().get("clickable_links_in_playlist_files", False)
        except Exception:
            clickable_links = False

    if blank_above is None:
        blank_above = set()
    elif not isinstance(blank_above, set):
        blank_above = set(blank_above)

    if video_metadata is None:
        video_metadata = {}
    if sections_data and isinstance(sections_data.get("video_metadata"), dict):
        merged_metadata = dict(sections_data.get("video_metadata", {}))
        merged_metadata.update(video_metadata)
        video_metadata = merged_metadata

    if playlist_entry_format is None:
        playlist_entry_format = _load_entry_format_for_file(file_path)
    playlist_entry_format = normalize_playlist_entry_format(playlist_entry_format)

    # Determine main header line
    header_line = None
    hdr = main_header or (sections_data.get("main_header") if sections_data else None)
    if hdr:
        pid = hdr.get("playlist_id")
        title = hdr.get("title") or ""
        url = hdr.get("url") or (f"https://www.youtube.com/playlist?list={pid}" if pid else None)
        id_or_url = url if clickable_links else (pid or url)
        if id_or_url and title:
            header_line = f"### {id_or_url} | {title}"
        elif id_or_url:
            header_line = f"### {id_or_url}"
        elif title:
            header_line = f"### {title}"
        else:
            raw = hdr.get("raw") or "## Playlist"
            # Normalize a legacy '##'-only raw main header up to '###' on rewrite.
            header_line = "#" + raw if raw.startswith("##") and not raw.startswith("###") else raw
    else:
        basename = os.path.splitext(os.path.basename(file_path))[0]
        try:
            from .config import load_playlist_data
            pdata = load_playlist_data()
            pid = pdata.get("playlists", {}).get(basename)
            if pid:
                id_or_url = f"https://www.youtube.com/playlist?list={pid}" if clickable_links else pid
                header_line = f"### {id_or_url} | {basename}"
            else:
                header_line = f"### {basename}"
        except Exception:
            header_line = f"### {basename}"

    lines = [header_line, ""]

    if sections_data and sections_data.get("is_sectioned") and sections_data.get("sections"):
        for sec in sections_data["sections"]:
            sec_vids = sec.get("video_ids", [])
            if not sec.get("is_implicit"):
                if sec.get("header_blank_above") and lines and lines[-1] != "":
                    lines.append("")

                pid = sec.get("playlist_id")
                title = sec.get("title", "").strip()
                sec_pl_name = sec.get("playlist_name", "").strip()
                url = sec.get("url") or (f"https://www.youtube.com/playlist?list={pid}" if pid else None)
                if clickable_links:
                    id_prefix = url if url else (f"https://www.youtube.com/playlist?list={pid}" if pid else "")
                else:
                    id_prefix = pid or url or ""
                
                # Format: Always ensure ID / URL is first, followed by section title, and optionally playlist title
                if id_prefix and title and title != pid:
                    if sec_pl_name:
                        sec_hdr = f"## {id_prefix} | {title} | {sec_pl_name}"
                    else:
                        sec_hdr = f"## {id_prefix} | {title}"
                elif id_prefix:
                    if sec_pl_name:
                        sec_hdr = f"## {id_prefix} | {sec_pl_name}"
                    else:
                        sec_hdr = f"## {id_prefix}"
                else:
                    if sec_pl_name:
                        sec_hdr = f"## {title or 'Section'} | {sec_pl_name}"
                    else:
                        sec_hdr = f"## {title or 'Section'}"

                lines.append(sec_hdr)

            sec_blank = sec.get("blank_above", set())
            for i, vid_id in enumerate(sec_vids):
                if i > 0 and vid_id in sec_blank:
                    lines.append("")
                metadata = dict(video_metadata.get(vid_id, {}))
                metadata["title"] = video_titles.get(vid_id) or metadata.get("title") or "Untitled Video"
                lines.append(_format_playlist_entry_line(vid_id, metadata, playlist_entry_format, clickable_links=clickable_links))
    else:
        for i, vid_id in enumerate(video_ids):
            if i > 0 and vid_id in blank_above:
                lines.append("")
            metadata = dict(video_metadata.get(vid_id, {}))
            metadata["title"] = video_titles.get(vid_id) or metadata.get("title") or "Untitled Video"
            lines.append(_format_playlist_entry_line(vid_id, metadata, playlist_entry_format, clickable_links=clickable_links))

    # Ensure clean trailing newline without multiple blank lines
    while len(lines) > 1 and lines[-1] == "" and lines[-2] == "":
        lines.pop()
    if lines and lines[-1] != "":
        lines.append("")

    # Check atomic_writes / direct write option from playlist settings
    atomic = True
    try:
        from .config import load_all_playlist_settings
        pl_name = _playlist_name_from_file_path(file_path)
        all_s = load_all_playlist_settings()
        if pl_name in all_s and isinstance(all_s[pl_name], dict):
            atomic = all_s[pl_name].get("atomic_writes", True)
    except Exception:
        atomic = True

    try:
        if not atomic:
            # Direct write
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return True

        temp_file = f"{file_path}.tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        if os.path.exists(file_path):
            os.replace(temp_file, file_path)
        else:
            os.rename(temp_file, file_path)
        return True
    except OSError as e:
        print(f"[!] Error saving normalized playlist to '{file_path}': {e}")
        return False
