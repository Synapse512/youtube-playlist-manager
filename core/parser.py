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


def _format_playlist_entry_line(vid_id, metadata, entry_format=None):
    """Builds one playlist text line: fields in the configured order, joined by ' | '."""
    values = dict(ENTRY_METADATA_DEFAULTS)
    values.update(metadata or {})
    values["id"] = vid_id
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

    # Prioritize playlists whose local text file exists on disk (unless command is pull or none exist)
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
    _PLAYLIST_PREFIXES = ("PL", "UU", "FL", "LL", "RD", "OL", "VL", "CL", "TL")
    if "|" in content:
        parts = content.split("|", 1)
        left = parts[0].strip()
        title = parts[1].strip()
        pid = extract_playlist_id(left)
        if pid and (pid != left or pid.startswith(_PLAYLIST_PREFIXES)):
            url = left if "://" in left else f"https://www.youtube.com/playlist?list={pid}"
            return {"playlist_id": pid, "url": url, "title": title, "raw": raw}
        elif "youtube.com" in left or "youtu.be" in left:
            url = left if "://" in left else f"https://{left}"
            return {"playlist_id": pid, "url": url, "title": title, "raw": raw}
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


def parse_playlist_file(file_path, entry_format=None):
    """
    Parses a local playlist text file supporting section headers.
    Supports lines formatted as:
      - '## <link | title>' or '## <title>' (section / main headers)
      - video lines laid out by the playlist's playlist_entry_format
        (e.g. '<video_id> | <title> | <channel> | <duration>'); the ID may also be a URL
      - '<video_id>' / '<video_url>' on its own
    Ignores single '#' comments and empty lines.
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
                if first_header and not first_song_seen:
                    # Top-level main playlist header
                    main_header = hdr
                    main_header["blank_above"] = header_blank_above
                    first_header = False
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
                    first_header = False
                    if current_section and (current_section["video_ids"] or not current_section.get("is_implicit")):
                        sections.append(current_section)
                    current_section = {
                        "title": hdr.get("title", "").strip(),
                        "playlist_id": hdr.get("playlist_id"),
                        "url": hdr.get("url"),
                        "video_ids": [],
                        "blank_above": set(),
                        "header_blank_above": header_blank_above,
                        "is_implicit": False,
                    }

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

    ## <playlist_url> | <playlist_title>

    followed by track list or sections:
    ## <section_url> | <section_title>
    <video_id> | <video_title> | ...   (fields per the playlist's playlist_entry_format)
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
        if url and title:
            header_line = f"## {url} | {title}"
        elif url:
            header_line = f"## {url}"
        elif title:
            header_line = f"## {title}"
        else:
            header_line = hdr.get("raw") or "## Playlist"
    else:
        basename = os.path.splitext(os.path.basename(file_path))[0]
        try:
            from .config import load_playlist_data
            pdata = load_playlist_data()
            pid = pdata.get("playlists", {}).get(basename)
            if pid:
                header_line = f"## https://www.youtube.com/playlist?list={pid} | {basename}"
            else:
                header_line = f"## {basename}"
        except Exception:
            header_line = f"## {basename}"

    lines = [header_line, ""]

    if sections_data and sections_data.get("is_sectioned") and sections_data.get("sections"):
        for sec in sections_data["sections"]:
            sec_vids = sec.get("video_ids", [])
            if not sec.get("is_implicit"):
                if sec.get("header_blank_above") and lines and lines[-1] != "":
                    lines.append("")

                pid = sec.get("playlist_id")
                title = sec.get("title", "").strip()
                url = sec.get("url") or (f"https://www.youtube.com/playlist?list={pid}" if pid else None)
                if url and title and title != pid:
                    sec_hdr = f"## {url} | {title}"
                elif url:
                    sec_hdr = f"## {url}"
                elif pid and title and title != pid:
                    sec_hdr = f"## https://www.youtube.com/playlist?list={pid} | {title}"
                elif pid:
                    sec_hdr = f"## https://www.youtube.com/playlist?list={pid}"
                else:
                    sec_hdr = f"## {title or 'Section'}"

                lines.append(sec_hdr)

            sec_blank = sec.get("blank_above", set())
            for i, vid_id in enumerate(sec_vids):
                if i > 0 and vid_id in sec_blank:
                    lines.append("")
                metadata = dict(video_metadata.get(vid_id, {}))
                metadata["title"] = video_titles.get(vid_id) or metadata.get("title") or "Untitled Video"
                lines.append(_format_playlist_entry_line(vid_id, metadata, playlist_entry_format))
    else:
        for i, vid_id in enumerate(video_ids):
            if i > 0 and vid_id in blank_above:
                lines.append("")
            metadata = dict(video_metadata.get(vid_id, {}))
            metadata["title"] = video_titles.get(vid_id) or metadata.get("title") or "Untitled Video"
            lines.append(_format_playlist_entry_line(vid_id, metadata, playlist_entry_format))

    # Ensure clean trailing newline without multiple blank lines
    while len(lines) > 1 and lines[-1] == "" and lines[-2] == "":
        lines.pop()
    if lines and lines[-1] != "":
        lines.append("")

    temp_file = f"{file_path}.tmp"
    try:
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
