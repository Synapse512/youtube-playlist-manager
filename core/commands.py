"""
Command handlers for link, unlink, list, pull, push, and format.
"""

import os
import re

try:
    from googleapiclient.errors import HttpError
except ImportError:
    HttpError = Exception

from .config import (
    PLAYLISTS_DIR,
    save_playlist_data,
    record_activity,
    log_playlist_event,
    playlist_entry_format_fields,
)
from .auth import resolve_oauth_client, get_youtube_service
from .parser import (
    extract_playlist_id,
    sanitize_filename,
    resolve_playlist_id,
    get_playlist_name_for_target,
    parse_playlist_file,
    save_playlist_file,
    playlist_link_format_issue,
)
from .sync import compute_minimal_moves


def _format_iso8601_duration(duration):
    """Converts YouTube API PT#H#M#S durations to h:mm:ss or m:ss."""
    if not duration:
        return ""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return duration
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _metadata_titles(video_metadata):
    return {
        vid: meta.get("title", "Untitled Video")
        for vid, meta in (video_metadata or {}).items()
    }


def _enrich_metadata_via_api(youtube, video_ids, video_metadata, required_fields):
    """
    Fills missing title/channel/duration values in video_metadata (in place) using
    YouTube Data API videos.list (1 quota unit per 50 videos).
    Returns the number of quota units used.
    """
    from .downloader import find_missing_metadata

    missing = find_missing_metadata(video_ids, video_metadata, required_fields)
    units = 0
    for i in range(0, len(missing), 50):
        batch_ids = missing[i:i + 50]
        try:
            res = youtube.videos().list(
                part="snippet,contentDetails",
                id=",".join(batch_ids)
            ).execute()
            units += 1
        except Exception as e:
            print(f"[!] Warning: Could not fetch extra video metadata via API: {e}")
            continue
        for item in res.get("items", []):
            vid = item.get("id")
            if not vid:
                continue
            meta = video_metadata.setdefault(vid, {"id": vid})
            snippet = item.get("snippet", {})
            content = item.get("contentDetails", {})
            for key, value in (
                ("title", snippet.get("title", "").strip()),
                ("channel", snippet.get("channelTitle", "").strip()),
                ("duration", _format_iso8601_duration(content.get("duration", ""))),
            ):
                if value and not meta.get(key):
                    meta[key] = value
    return units


def _resolve_command_client(args, default_client=None, default_account=None):
    """
    Resolves which oauth-client JSON to use for this operation, and which Google
    account to authenticate as (for token-cache keying).
    Account priority: --account flag > playlist "account" setting > picker in get_youtube_service.
    Client priority: --client flag > this playlist's 'oauth_client' setting (see
    playlist-settings.toml) > auto-select (if only one exists) > interactive prompt.
    Returns (client_name, account) where account may be an empty string.
    """
    explicit_client = getattr(args, "client", None)
    if not explicit_client and default_client:
        default_client = str(default_client).strip()
        if default_client:
            from .auth import get_oauth_clients
            if default_client in get_oauth_clients():
                print(f"[*] Using this playlist's configured oauth client: '{default_client}'")
                explicit_client = default_client
            else:
                print(f"[!] Warning: playlist's configured oauth_client '{default_client}' was not found "
                      f"in 'oauth-clients/' - falling back to normal selection.")
    account = (getattr(args, "account", None) or default_account or "").strip()
    return resolve_oauth_client(explicit_client, allow_prompt=True), account


def command_link(args, settings, playlist_data):
    """Links a YouTube playlist to data/playlist-data.json using the title fetched from YouTube."""
    from .downloader import (
        fetch_playlist_title_ytdlp,
        DOWNLOAD_SETTINGS_FILENAME,
        save_playlist_download_settings,
        load_playlist_download_settings,
    )
    from .config import DOWNLOADS_DIR

    raw_input = getattr(args, "target", None) or getattr(args, "name", None) or getattr(args, "id", None)
    if not raw_input:
        print("[!] Error: Missing Playlist ID or URL. Syntax: python main.py link <id_or_url> [--client <name>]\n")
        from .ui import print_help
        print_help()
        return

    raw_id = raw_input.strip()
    playlist_id = extract_playlist_id(raw_id)
    if not playlist_id:
        print(f"[!] Error: Could not extract a valid Playlist ID from '{raw_id}'.")
        return

    # Already linked? Check by playlist ID first, before any yt-dlp / API call.
    for existing_name, existing_id in playlist_data.get("playlists", {}).items():
        if existing_id == playlist_id:
            print(f"[*] This playlist is already linked as '{existing_name}' (ID: {playlist_id}). Nothing to do.")
            print(f"    Use 'python main.py pull {existing_name}' to sync it, or 'unlink' first to re-link it.")
            return

    link_method = (getattr(args, "method", None) or settings.get("link_method", "auto")).strip().lower()
    explicit_client = getattr(args, "client", None)
    if explicit_client:
        link_method = "api"

    playlist_name = None
    oauth_client = None
    method_used = "yt-dlp (0 quota)"

    if link_method in ("auto", "ytdlp"):
        print(f"[*] Fetching playlist title using yt-dlp ({playlist_id})...")
        title = fetch_playlist_title_ytdlp(playlist_id, settings)
        if title:
            playlist_name = sanitize_filename(title)
            print(f"[+] Fetched playlist title via yt-dlp: '{playlist_name}' (0 API quota)")
        else:
            if link_method == "ytdlp":
                print(f"[!] Error: Could not fetch playlist title via yt-dlp. Make sure the playlist is public/unlisted.")
                return
            from . import downloader as _dl
            reason = _dl.LAST_YTDLP_ERROR or "unknown reason"
            print(f"[*] yt-dlp could not get the playlist title: {reason}")
            print(f"[*] Falling back to YouTube API...")

    if not playlist_name:
        oauth_client, account = _resolve_command_client(args)
        method_used = f"YouTube API ({oauth_client})"
        print(f"[*] Fetching playlist title from YouTube API ({playlist_id})...")
        youtube = get_youtube_service(oauth_client, account=account)
        try:
            res = youtube.playlists().list(part="snippet", id=playlist_id).execute()
            items = res.get("items", [])
            if not items:
                print(f"[!] Error: Playlist ID '{playlist_id}' not found on YouTube.")
                return
            title = items[0].get("snippet", {}).get("title", "").strip()
            if not title:
                title = playlist_id
            playlist_name = sanitize_filename(title)
            print(f"[+] Using fetched playlist title: '{playlist_name}'")
        except Exception as e:
            print(f"[!] Error fetching playlist title from YouTube: {e}")
            return

    playlists = playlist_data.setdefault("playlists", {})
    if playlist_name in playlists:
        existing_id = playlists[playlist_name]
        if existing_id == playlist_id:
            print(f"[*] Playlist '{playlist_name}' is already linked to Playlist ID: '{existing_id}'.")
        else:
            print(f"[!] Warning: Playlist '{playlist_name}' already linked to '{existing_id}'. Updating to '{playlist_id}'.")
            playlists[playlist_name] = playlist_id
            save_playlist_data(playlist_data)
    else:
        playlists[playlist_name] = playlist_id
        save_playlist_data(playlist_data)

    # Create initial playlist file with main header
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{playlist_name}.txt")
    if not os.path.exists(file_path):
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"### https://www.youtube.com/playlist?list={playlist_id} | {playlist_name}\n\n")
            print(f"[+] Created initial playlist file '{file_path}'.")
        except OSError as e:
            print(f"[!] Warning: Could not create initial file '{file_path}': {e}")

    # Initialize per-playlist settings in playlist-settings.toml
    from .config import load_playlist_settings
    load_playlist_settings(playlist_name)

    # Initialize the playlist-downloads folder and default _manifest.json
    downloads_root = settings.get("downloads_dir", DOWNLOADS_DIR)
    playlist_download_dir = os.path.join(downloads_root, playlist_name)
    os.makedirs(playlist_download_dir, exist_ok=True)
    from .downloader import DOWNLOAD_MANIFEST_FILENAME, save_playlist_manifest, load_playlist_manifest
    manifest_path = os.path.join(playlist_download_dir, DOWNLOAD_MANIFEST_FILENAME)
    if not os.path.exists(manifest_path):
        existing = load_playlist_manifest(playlist_download_dir)
        if not existing or not existing.get("tracks"):
            save_playlist_manifest(playlist_download_dir, {"tracks": {}})
            print(f"[+] Initialized download folder and manifest: '{playlist_download_dir}/'")
    else:
        print(f"[*] Download folder already initialized: '{playlist_download_dir}/'")


    print(f"[+] Successfully linked '{playlist_name}' -> Playlist ID: '{playlist_id}'")
    record_activity(playlist_data, playlist_name, "link")
    log_playlist_event(
        settings,
        playlist_name,
        "link",
        oauth_client or method_used,
        summary_lines=[f"Linked '{playlist_name}' -> Playlist ID '{playlist_id}' via {method_used}"],
        playlist_data=playlist_data,
    )


def command_unlink(args, settings, playlist_data):
    """Removes a linked playlist from data/playlist-data.json."""
    name = args.name.strip()
    playlists = playlist_data.get("playlists", {})

    target_name = None
    if name in playlists:
        target_name = name
    else:
        for p_name, pid in playlists.items():
            if pid == name:
                target_name = p_name
                break

    if target_name:
        del playlists[target_name]
        activity = playlist_data.get("activity", {})
        if target_name in activity:
            del activity[target_name]
        save_playlist_data(playlist_data)
        print(f"[+] Unlinked playlist '{target_name}'.")
        log_playlist_event(
            settings,
            target_name,
            "unlink",
            None,
            summary_lines=[f"Unlinked playlist '{target_name}' from playlist data"],
            playlist_data=playlist_data
        )
    else:
        print(f"[!] Playlist '{name}' not found in playlist data.")


def command_list(args, settings, playlist_data):
    """Lists all configured playlists with last edit info."""
    from .ui import terminal_link

    playlists = playlist_data.get("playlists", {})
    activity = playlist_data.get("activity", {})

    if not playlists:
        print("No playlists configured. Use 'python main.py link <id_or_url>'")
        return

    print("Configured Playlists:")
    for name, pid in playlists.items():
        url = f"https://www.youtube.com/playlist?list={pid}"

        act = activity.get(name, {})
        last_cmd = act.get("last_command")
        last_time = act.get("last_time")
        if last_cmd and last_time:
            last_info = f"  (last: {last_cmd} on {last_time})"
        else:
            last_info = "  (no CLI edits yet)"

        safe_n = sanitize_filename(name)
        fpath = os.path.join(PLAYLISTS_DIR, f"{safe_n}.txt")
        sec_info = ""
        if os.path.isfile(fpath):
            try:
                _, _, _, _, sdata = parse_playlist_file(fpath)
                if sdata.get("is_sectioned"):
                    explicit_secs = [s for s in sdata.get("sections", []) if not s.get("is_implicit")]
                    sec_info = f" • {len(explicit_secs)} section(s)"
            except Exception:
                pass

        print(f"  {name}  [{terminal_link(pid, url)}]{last_info}{sec_info}")



def command_pull(args, settings, playlist_data):
    """Pulls a remote YouTube playlist into a local text file."""
    from .downloader import fetch_playlist_tracks_ytdlp, fill_missing_metadata
    from .config import load_playlist_settings

    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, playlist_data)
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)
    pl_settings = load_playlist_settings(playlist_name)
    entry_fields = playlist_entry_format_fields(pl_settings.get("playlist_entry_format"))

    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    # Check if local file already exists to preserve custom blank line spacing, sections, and compute diff
    existing_blank_above = set()
    existing_video_ids = []
    existing_titles = {}
    existing_sections_data = None
    if os.path.exists(file_path):
        try:
            existing_video_ids, existing_titles, _, existing_blank_above, existing_sections_data = parse_playlist_file(file_path)
        except Exception:
            pass


    pull_method = (getattr(args, "method", None) or settings.get("pull_method", "auto")).strip().lower()
    explicit_client = getattr(args, "client", None)
    if explicit_client:
        pull_method = "api"

    if playlist_id != target_name:
        print(f"[*] Target playlist '{target_name}' resolved to Playlist ID: {playlist_id}")
    else:
        print(f"[*] Using Playlist ID: {playlist_id}")

    pulled_video_ids = None
    pulled_titles = {}
    pulled_metadata = {}
    method_used = "yt-dlp"
    oauth_client = None
    pages_read = 0
    quota_units = 0

    if pull_method in ("auto", "ytdlp"):
        print(f"[*] Fetching live track list from YouTube for playlist '{playlist_id}' using yt-dlp (0 Google API quota)...")
        ytdlp_ids, ytdlp_titles, ytdlp_metadata = fetch_playlist_tracks_ytdlp(playlist_id, settings)
        if ytdlp_ids is not None:
            pulled_video_ids = ytdlp_ids
            pulled_titles = ytdlp_titles
            pulled_metadata = ytdlp_metadata or {}
            # The playlist listing may omit some fields (e.g. duration); fill any gaps per video.
            fill_missing_metadata(pulled_video_ids, pulled_metadata, entry_fields, settings)
            pulled_titles = _metadata_titles(pulled_metadata)
            method_used = "yt-dlp"
        else:
            if pull_method == "ytdlp":
                print(f"[!] Error: Could not pull playlist tracks via yt-dlp. Make sure the playlist is public/unlisted.")
                return
            print(f"[*] yt-dlp could not access playlist tracks (it may be private). Falling back to YouTube API...")

    if pulled_video_ids is None:
        # Resolve which oauth-client JSON to use for this operation
        oauth_client, account = _resolve_command_client(args, default_client=pl_settings.get("oauth_client"), default_account=pl_settings.get("account"))
        youtube = get_youtube_service(oauth_client, account=account)
        print(f"[*] Fetching live track list from YouTube for playlist '{playlist_id}' via YouTube API...")

        next_page_token = None
        raw_items = []
        page_num = 1
        quota_units = 0

        try:
            while True:
                print(f"    Fetching page {page_num}...")
                res = youtube.playlistItems().list(
                    part="snippet",
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=next_page_token
                ).execute()
                quota_units += 1  # 1 unit per playlistItems.list call

                for item in res.get("items", []):
                    snippet = item.get("snippet", {})
                    v_id = snippet.get("resourceId", {}).get("videoId")
                    title = snippet.get("title", "Untitled")
                    channel = snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle", "")
                    pos = snippet.get("position", len(raw_items))
                    if v_id:
                        raw_items.append((pos, v_id, {
                            "id": v_id,
                            "title": title,
                            "channel": channel,
                            "duration": "",
                        }))

                next_page_token = res.get("nextPageToken")
                page_num += 1
                if not next_page_token:
                    break

        except HttpError as e:
            status_code = e.resp.status if hasattr(e, "resp") else "Unknown"
            if "quotaExceeded" in str(e) or status_code == 403:
                print(f"[!] Error: Access forbidden (403). The playlist might be private or API quota was exceeded.\n    Details: {e}")
                record_activity(playlist_data, playlist_name, "pull (quota exceeded)")
                log_playlist_event(
                    settings,
                    playlist_name,
                    "pull (ABANDONED - QUOTA EXCEEDED)",
                    oauth_client,
                    summary_lines=[
                        "Pull operation abandoned: YouTube API quota exceeded or 403 forbidden",
                        f"{quota_units} quota unit(s) consumed prior to abandonment"
                    ],
                    detail_lines=[f"Details: {e}"],
                    playlist_data=playlist_data
                )
            elif status_code == 404:
                print(f"[!] Error: Playlist '{playlist_id}' not found (404). Check the ID or playlist name.")
            else:
                print(f"[!] YouTube API Error ({status_code}): {e}")
            return

        # Ensure items are ordered by their actual position in the playlist
        raw_items.sort(key=lambda x: x[0])
        pulled_video_ids = [x[1] for x in raw_items]
        pulled_metadata = {x[1]: x[2] for x in raw_items}
        if "duration" in entry_fields:
            # Only duration needs the extra videos.list call (title/channel come with the playlist items)
            quota_units += _enrich_metadata_via_api(youtube, pulled_video_ids, pulled_metadata, entry_fields)
        pulled_titles = _metadata_titles(pulled_metadata)
        pages_read = page_num - 1
        method_used = "api"

    if existing_sections_data is None:
        existing_sections_data = {}
    existing_sections_data["video_metadata"] = pulled_metadata

    # Diff calculation comparing existing local playlist vs pulled YouTube playlist
    current_list = []
    for vid in existing_video_ids:
        title = existing_titles.get(vid, pulled_titles.get(vid, vid))
        current_list.append({"videoId": vid, "title": title})

    target_counts = {}
    for vid in pulled_video_ids:
        target_counts[vid] = target_counts.get(vid, 0) + 1

    # 1. Deletions from local playlist (present locally, removed on YouTube)
    deleted_count = 0
    deleted_details = []
    curr_counts = {}
    for item in current_list:
        v = item["videoId"]
        curr_counts[v] = curr_counts.get(v, 0) + 1

    for i in range(len(current_list) - 1, -1, -1):
        item = current_list[i]
        vid = item["videoId"]
        target_allowed = target_counts.get(vid, 0)
        if curr_counts.get(vid, 0) > target_allowed:
            track_title = item.get("title", vid)
            print(f"[-] Deleting track from local playlist: '{track_title}' ({vid})")
            deleted_count += 1
            deleted_details.append(f"- Removed: '{vid}' | {track_title}")
            current_list.pop(i)
            curr_counts[vid] -= 1

    # 2. Insertions into local playlist (new tracks added on YouTube)
    inserted_count = 0
    inserted_details = []
    active_counts = {}
    for item in current_list:
        v = item["videoId"]
        active_counts[v] = active_counts.get(v, 0) + 1

    target_seen_counts = {}
    for pos, vid_id in enumerate(pulled_video_ids):
        target_seen_counts[vid_id] = target_seen_counts.get(vid_id, 0) + 1
        if target_seen_counts[vid_id] > active_counts.get(vid_id, 0):
            track_title = pulled_titles.get(vid_id, vid_id)
            print(f"[+] Inserting new track into local playlist at position {pos} ({vid_id})...")
            item_info = {
                "videoId": vid_id,
                "title": track_title,
                "position": pos
            }
            current_list.insert(pos, item_info)
            active_counts[vid_id] = active_counts.get(vid_id, 0) + 1
            inserted_count += 1
            inserted_details.append(f"+ Inserted: '{vid_id}' | {track_title} (pos {pos})")
            print(f"    [+] Inserted '{track_title}'")

    # 3. Reordering in local playlist (tracks whose order changed on YouTube)
    for idx, item in enumerate(current_list):
        item["_id"] = idx
    curr_item_ids = [item["_id"] for item in current_list]
    target_item_ids = []
    available_by_vid = {}
    for item in current_list:
        available_by_vid.setdefault(item["videoId"], []).append(item["_id"])
    for vid in pulled_video_ids:
        if vid in available_by_vid and available_by_vid[vid]:
            target_item_ids.append(available_by_vid[vid].pop(0))

    reorder_moves = compute_minimal_moves(curr_item_ids, target_item_ids)
    moved_count = 0
    moved_details = []

    for item_id, target_pos in reorder_moves:
        curr_ids = [it["_id"] for it in current_list]
        from_idx = curr_ids.index(item_id)
        item_info = current_list[from_idx]
        vid_id = item_info["videoId"]
        short_title = item_info["title"][:35]
        print(f"[*] Moving '{short_title}...' -> position {target_pos} (from position {from_idx})")
        moved_item = current_list.pop(from_idx)
        moved_item["position"] = target_pos
        current_list.insert(target_pos, moved_item)
        moved_count += 1
        moved_details.append(f"~ Reordered: '{vid_id}' | {item_info['title']} (pos {from_idx} -> pos {target_pos})")

    if deleted_count == 0 and inserted_count == 0 and moved_count == 0:
        print("[+] Local playlist is already up-to-date with YouTube.")

    # Reconcile sections if local file is sectioned
    if existing_sections_data and existing_sections_data.get("is_sectioned"):
        existing_id_set = set(existing_video_ids)
        pulled_id_set = set(pulled_video_ids)
        new_inserted_ids = [vid for vid in pulled_video_ids if vid not in existing_id_set]
        deleted_ids_set = existing_id_set - pulled_id_set

        # Remove deleted tracks from existing sections
        for sec in existing_sections_data.get("sections", []):
            sec["video_ids"] = [v for v in sec.get("video_ids", []) if v not in deleted_ids_set]

        # Put new tracks into ## Unorganized section at the end
        if new_inserted_ids:
            unorganized = None
            for sec in existing_sections_data.get("sections", []):
                if sec.get("title", "").strip().lower() in ("unorganized", "uncategorized", "sectionless"):
                    unorganized = sec
                    break
            if unorganized is None:
                unorganized = {
                    "title": "Unorganized",
                    "playlist_id": None,
                    "url": None,
                    "video_ids": [],
                    "blank_above": set(),
                    "header_blank_above": True,
                    "is_implicit": False,
                }
                existing_sections_data["sections"].append(unorganized)

            for new_v in new_inserted_ids:
                if new_v not in unorganized["video_ids"]:
                    unorganized["video_ids"].append(new_v)

        if not save_playlist_file(file_path, pulled_video_ids, pulled_titles, blank_above=existing_blank_above, sections_data=existing_sections_data):
            return
    else:
        if not save_playlist_file(file_path, pulled_video_ids, pulled_titles, blank_above=existing_blank_above, sections_data=existing_sections_data):
            return

    print("\n" + "=" * 60)
    print(" Pull Summary")
    print("=" * 60)
    if method_used == "yt-dlp":
        print(f"  * {'Method:':<22} yt-dlp (0 Google API quota)")
    else:
        print(f"  * {'OAuth Client:':<22} {oauth_client}")
        print(f"  * {'Pages Read:':<22} {pages_read:>4d} request(s)")
    print(f"  * {'Tracks Fetched:':<22} {len(pulled_video_ids):>4d} track(s)")
    print(f"  * {'Deleted:':<22} {deleted_count:>4d} track(s)")
    print(f"  * {'Inserted:':<22} {inserted_count:>4d} track(s)")
    print(f"  * {'Reordered:':<22} {moved_count:>4d} track(s)")
    quota_desc = f"{quota_units:>4d} unit(s)"
    if method_used == "api":
        quota_desc += "      (1 unit/page)"
    print(f"  * {'API Quota Used:':<22} {quota_desc}")
    print("=" * 60)
    print(f"[+] Successfully pulled {len(pulled_video_ids)} tracks to '{file_path}'!\n")
    record_activity(playlist_data, playlist_name, "pull")
    diff_details = deleted_details + inserted_details + moved_details
    log_playlist_event(
        settings,
        playlist_name,
        "pull",
        oauth_client if method_used == "api" else "yt-dlp",
        summary_lines=[
            f"Fetched {len(pulled_video_ids)} track(s) via {method_used} ({quota_units} quota units): "
            f"{inserted_count} inserted, {deleted_count} deleted, {moved_count} reordered"
        ],
        detail_lines=diff_details if diff_details else ["No changes required (already in sync)"],
        playlist_data=playlist_data,
    )

def _sync_single_playlist_to_youtube(
    youtube,
    playlist_id,
    playlist_name,
    target_video_ids,
    target_video_titles,
    oauth_client,
    settings,
    playlist_data,
    file_path=None,
    blank_above=None,
    sections_data=None,
    log_playlist_name=None,
    section_title=None,
):
    """
    Synchronizes a single YouTube playlist with a target list of video IDs and titles.
    Performs deletion, insertion, and minimal-moves reordering.
    Returns (success, resolved_titles).
    """
    from .downloader import fetch_playlist_tracks_ytdlp

    effective_log_name = log_playlist_name or playlist_name

    # Optimization: Zero-quota check if pull_method in ('auto', 'ytdlp')
    pull_method = (settings.get("pull_method", "auto") or "auto").strip().lower()
    if pull_method in ("auto", "ytdlp"):
        print(f"[*] Checking if '{playlist_name}' is already in sync using yt-dlp (0 API quota)...")
        ytdlp_ids, ytdlp_titles, _ = fetch_playlist_tracks_ytdlp(playlist_id, settings)
        if ytdlp_ids is not None and ytdlp_ids == target_video_ids:
            print(f"[+] '{playlist_name}' is already completely in sync on YouTube (0 API quota used)!")
            op_name = f"push [{section_title}]" if section_title else "push"
            record_activity(playlist_data, effective_log_name, op_name)
            log_playlist_event(
                settings,
                effective_log_name,
                op_name,
                "yt-dlp (0 quota)",
                summary_lines=[f"0 inserted, 0 deleted, 0 reordered (0 quota units - verified with yt-dlp)"],
                detail_lines=[f"Playlist '{playlist_name}' is already in sync with local file."],
                playlist_data=playlist_data,
            )
            resolved_titles = {}
            for vid_id in target_video_ids:
                resolved_titles[vid_id] = ytdlp_titles.get(vid_id) or target_video_titles.get(vid_id) or "Untitled Video"
            return True, resolved_titles

    print(f"[*] Fetching current live playlist from YouTube ({playlist_id})...")

    current_items = []
    next_page_token = None
    page_num = 1
    list_units = 0


    try:
        while True:
            print(f"    Fetching remote page {page_num}...")
            res = youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            ).execute()
            list_units += 1

            for item in res.get("items", []):
                snippet = item.get("snippet", {})
                current_items.append({
                    "playlistItemId": item["id"],
                    "videoId": snippet.get("resourceId", {}).get("videoId"),
                    "title": snippet.get("title", "Untitled"),
                    "position": snippet.get("position", 0)
                })

            next_page_token = res.get("nextPageToken")
            page_num += 1
            if not next_page_token:
                break

    except HttpError as e:
        if "quotaExceeded" in str(e) or (hasattr(e, "resp") and e.resp.status == 403):
            print(f"\n[!] YouTube API daily quota limit reached while reading playlist '{playlist_name}'.")
            op_name = f"push [{section_title}]" if section_title else "push"
            record_activity(playlist_data, effective_log_name, op_name + " (quota exceeded)")
            log_playlist_event(
                settings,
                effective_log_name,
                op_name + " (ABANDONED - QUOTA EXCEEDED)",
                oauth_client,
                summary_lines=[
                    f"Push abandoned: YouTube API daily quota limit reached during initial track fetch for '{playlist_name}'",
                    f"{list_units} list request(s) completed before quota exhaustion"
                ],
                detail_lines=[f"Error: {e}"],
                playlist_data=playlist_data
            )
            return False, {}
        print(f"[!] YouTube API Error while fetching current playlist '{playlist_name}': {e}")
        return False, {}

    current_items.sort(key=lambda x: x["position"])
    print(f"[+] Retrieved {len(current_items)} tracks currently on YouTube for '{playlist_name}'.")

    current_list = list(current_items)
    target_counts = {}
    for vid in target_video_ids:
        target_counts[vid] = target_counts.get(vid, 0) + 1

    deleted_count = 0
    inserted_count = 0
    moved_count = 0
    deleted_details = []
    inserted_details = []
    moved_details = []
    op_name = f"push [{section_title}]" if section_title else "push"

    def _handle_quota_exceeded(phase_name):
        list_q = list_units * 1
        del_q = deleted_count * 50
        ins_q = inserted_count * 50
        upd_q = moved_count * 50
        total_q = list_q + del_q + ins_q + upd_q

        print("\n" + "=" * 60)
        print(" [!] YouTube API Daily Quota Exceeded")
        print("=" * 60)
        print(f"  * Operation abandoned during: {phase_name} ({playlist_name})")
        print(f"  * Total quota consumed:       {total_q} unit(s)")
        print(f"  * Successfully deleted:       {deleted_count} track(s)")
        print(f"  * Successfully inserted:      {inserted_count} track(s)")
        print(f"  * Successfully reordered:     {moved_count} track(s)")
        print("=" * 60)
        print("  Wait for your daily quota to reset or use another --client.")

        if file_path and (deleted_count > 0 or inserted_count > 0 or moved_count > 0):
            partial_vids = [it["videoId"] for it in current_list if it.get("videoId")]
            partial_titles = {it["videoId"]: it.get("title", "") for it in current_list if it.get("videoId")}
            save_playlist_file(file_path, partial_vids, partial_titles, blank_above=blank_above, sections_data=sections_data)
            print(f"[*] Updated local file '{file_path}' to match YouTube's current state.")

        record_activity(playlist_data, effective_log_name, op_name + " (quota exceeded)")
        diff_d = deleted_details + inserted_details + moved_details
        log_playlist_event(
            settings,
            effective_log_name,
            op_name + " (ABANDONED - QUOTA EXCEEDED)",
            oauth_client,
            summary_lines=[
                f"Push abandoned: YouTube API daily quota limit reached during {phase_name} for '{playlist_name}'",
                f"Partial progress: {inserted_count} inserted, {deleted_count} deleted, {moved_count} reordered ({total_q} quota units used)"
            ],
            detail_lines=diff_d if diff_d else [f"Quota limit reached during {phase_name} before any modifications were made."],
            playlist_data=playlist_data
        )

    # 1. Delete videos removed locally
    sync_failed = False
    curr_counts = {}
    for item in current_list:
        v = item["videoId"]
        curr_counts[v] = curr_counts.get(v, 0) + 1

    for i in range(len(current_list) - 1, -1, -1):
        item = current_list[i]
        vid = item["videoId"]
        target_allowed = target_counts.get(vid, 0)
        if curr_counts.get(vid, 0) > target_allowed:
            track_title = item.get("title", "Untitled")
            print(f"[-] Deleting track from YouTube: '{track_title}' ({vid})")
            try:
                youtube.playlistItems().delete(id=item["playlistItemId"]).execute()
                deleted_count += 1
                deleted_details.append(f"- Removed: '{vid}' | {track_title}")
                current_list.pop(i)
                curr_counts[vid] -= 1
            except HttpError as e:
                if "quotaExceeded" in str(e):
                    _handle_quota_exceeded("deletion phase")
                    return False, {}
                print(f"[!] Error deleting track {vid}: {e}")
                sync_failed = True

    # 2. Add new tracks present locally
    active_counts = {}
    for item in current_list:
        v = item["videoId"]
        active_counts[v] = active_counts.get(v, 0) + 1

    target_seen_counts = {}
    for pos, vid_id in enumerate(target_video_ids):
        target_seen_counts[vid_id] = target_seen_counts.get(vid_id, 0) + 1
        if target_seen_counts[vid_id] > active_counts.get(vid_id, 0):
            track_title = target_video_titles.get(vid_id, vid_id)
            print(f"[+] Inserting new track into YouTube at position {pos} ({vid_id})...")
            try:
                insert_res = youtube.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": vid_id},
                            "position": pos
                        }
                    }
                ).execute()
                item_info = {
                    "playlistItemId": insert_res["id"],
                    "videoId": vid_id,
                    "title": insert_res.get("snippet", {}).get("title", track_title),
                    "position": pos
                }
                current_list.insert(pos, item_info)
                active_counts[vid_id] = active_counts.get(vid_id, 0) + 1
                inserted_count += 1
                inserted_details.append(f"+ Inserted: '{vid_id}' | {item_info['title']} (pos {pos})")
                print(f"    [+] Inserted '{item_info['title']}'")
            except HttpError as e:
                if "quotaExceeded" in str(e):
                    _handle_quota_exceeded("insertion phase")
                    return False, {}
                print(f"    [!] Error inserting {vid_id}: {e}")
                sync_failed = True

    if sync_failed:
        print(f"\n[!] Push for '{playlist_name}' stopped before reordering because one or more delete/insert operations failed.")
        return False, {}

    # 3. Reorder tracks using LIS minimal moves algorithm
    available_by_vid = {}
    for item in current_list:
        available_by_vid.setdefault(item["videoId"], []).append(item["playlistItemId"])

    target_item_ids = []
    for vid in target_video_ids:
        if vid in available_by_vid and available_by_vid[vid]:
            target_item_ids.append(available_by_vid[vid].pop(0))

    curr_item_ids = [item["playlistItemId"] for item in current_list]
    reorder_moves = compute_minimal_moves(curr_item_ids, target_item_ids)

    for item_id, target_pos in reorder_moves:
        curr_ids = [it["playlistItemId"] for it in current_list]
        from_idx = curr_ids.index(item_id)
        item_info = current_list[from_idx]
        vid_id = item_info["videoId"]
        short_title = item_info['title'][:35]
        print(f"[*] Moving '{short_title}...' -> position {target_pos} (from position {from_idx})")
        try:
            youtube.playlistItems().update(
                part="snippet",
                body={
                    "id": item_id,
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": vid_id},
                        "position": target_pos
                    }
                }
            ).execute()
            moved_item = current_list.pop(from_idx)
            moved_item["position"] = target_pos
            current_list.insert(target_pos, moved_item)
            moved_count += 1
            moved_details.append(f"~ Reordered: '{vid_id}' | {item_info['title']} (pos {from_idx} -> pos {target_pos})")
        except HttpError as e:
            if "quotaExceeded" in str(e):
                _handle_quota_exceeded("reordering phase")
                return False, {}
            print(f"[!] Error moving track {vid_id}: {e}")

    # Calculate exact API quota units used
    list_quota = list_units * 1
    delete_quota = deleted_count * 50
    insert_quota = inserted_count * 50
    update_quota = moved_count * 50
    total_quota = list_quota + delete_quota + insert_quota + update_quota

    print("\n" + "=" * 60)
    print(f" Synchronization Summary ({playlist_name})")
    print("=" * 60)
    print(f"  * {'OAuth Client:':<22} {oauth_client}")
    print(f"  * {'Deleted:':<22} {deleted_count:>4d} track(s)     ({delete_quota:>5d} quota units)")
    print(f"  * {'Inserted:':<22} {inserted_count:>4d} track(s)     ({insert_quota:>5d} quota units)")
    print(f"  * {'Reordered:':<22} {moved_count:>4d} track(s)     ({update_quota:>5d} quota units)")
    print(f"  * {'Read/List:':<22} {list_units:>4d} request(s)   ({list_quota:>5d} quota units)")
    print("-" * 60)
    print(f"  * {'Total Quota Used:':<22} {total_quota:>4d} unit(s)")
    print("=" * 60)
    print(f"[+] '{playlist_name}' synchronization complete!\n")

    record_activity(playlist_data, effective_log_name, op_name)
    diff_details = deleted_details + inserted_details + moved_details
    log_playlist_event(
        settings,
        effective_log_name,
        op_name,
        oauth_client,
        summary_lines=[
            f"{inserted_count} inserted, {deleted_count} deleted, {moved_count} reordered ({total_quota} quota units)"
        ],
        detail_lines=diff_details if diff_details else ["No changes required (already in sync)"],
        playlist_data=playlist_data
    )

    current_video_map = {item["videoId"]: item for item in current_list if item.get("videoId")}
    resolved_titles = {}
    for vid_id in target_video_ids:
        if vid_id in current_video_map and current_video_map[vid_id].get("title"):
            resolved_titles[vid_id] = current_video_map[vid_id]["title"]
        elif target_video_titles.get(vid_id):
            resolved_titles[vid_id] = target_video_titles[vid_id]
        else:
            resolved_titles[vid_id] = "Untitled Video"

    return True, resolved_titles


def command_push(args, settings, playlist_data):
    """Pushes the local text file track order and changes to YouTube, then normalizes the local file."""
    from .config import load_playlist_settings

    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, playlist_data)
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        print(f"    Run 'python main.py pull {target_name}' first to download the playlist.")
        return

    # Safety checks and Pull Reminders
    if settings.get("safety_check_before_push", True):
        confirm = input(f"[?] Have you pulled recent YouTube additions for '{target_name}' before pushing? (y/N): ").strip().lower()
        if confirm != 'y':
            print("[!] Push aborted by user. Run 'python main.py pull <name>' first to avoid overwriting recent changes.")
            return

    # Parse local text file (supports IDs, URLs, and ID|Title formats)
    print(f"[*] Reading and validating local file '{file_path}'...")
    target_video_ids, target_video_titles, skipped, blank_above, sections_data = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the local text file. Push aborted.")
        return

    print(f"[+] Parsed {len(target_video_ids)} valid tracks from local file.")

    # Load push mode setting from playlist-settings.toml
    pl_settings = load_playlist_settings(playlist_name)
    # Normalize like the boolean settings - a stray case/whitespace difference here
    # (e.g. "Sections_Only" or "sections_only " typed by hand) would silently fail
    # the `in (...)` checks below and fall through to pushing the main playlist.
    push_mode = str(pl_settings.get("push_mode", "all") or "all").strip().lower()
    if push_mode not in ("all", "main_only", "sections_only"):
        print(f"[!] Warning: Unrecognized push_mode '{push_mode}' for '{playlist_name}' - defaulting to 'all'.")
        push_mode = "all"

    # Resolve OAuth client - prefer a playlist-specific default (see 'oauth_client'
    # in playlist-settings.toml) so you don't have to pick it every time.
    oauth_client, account = _resolve_command_client(args, default_client=pl_settings.get("oauth_client"), default_account=pl_settings.get("account"))
    youtube = get_youtube_service(oauth_client, account=account)

    is_sectioned = sections_data.get("is_sectioned", False)
    sections = sections_data.get("sections", [])

    if is_sectioned and push_mode == "sections_only":
        print(f"[*] push_mode = 'sections_only' -> the main playlist ('{playlist_id}') will NOT be pushed to.")

    any_section_pushed = False
    # 1. If sectioned and push_mode in ("all", "sections_only"), push each linked section
    if is_sectioned and push_mode in ("all", "sections_only"):
        for sec in sections:
            if sec.get("is_implicit"):
                continue
            sec_id = sec.get("playlist_id")
            sec_title = sec.get("title", "Section")
            sec_vids = sec.get("video_ids", [])
            if not sec_id:
                print(f"\n[*] Section '{sec_title}' has no linked playlist ID (skipped section push).")
                continue
            if sec_id == playlist_id:
                # Safety guard: a section header accidentally pointing at the SAME
                # playlist ID as the main playlist would otherwise silently push
                # section changes straight into the main playlist.
                print(f"\n[!] Warning: Section '{sec_title}' is linked to the SAME playlist ID as the main "
                      f"playlist ('{playlist_id}'). Skipping this section to avoid overwriting the main playlist - "
                      f"check the '## ' header for '{sec_title}' in '{file_path}'.")
                continue

            # Safety guard: it's very easy to paste a mistyped, truncated, or
            # otherwise-wrong link when hand-writing a section header. A link that
            # LOOKS valid but points at the wrong playlist would still push real
            # inserts/deletes/reorders to it, so pause here and make sure the user
            # actually means it before sending anything.
            link_issue = playlist_link_format_issue(sec)
            if link_issue and settings.get("warn_on_malformed_section_links", True):
                print("\n" + "=" * 60)
                print(f" [!] Section '{sec_title}' has a possibly-wrong playlist link")
                print("=" * 60)
                print(f"  Problem: {link_issue}")
                print(f"  Expected format: https://www.youtube.com/playlist?list=PLHd4hClFlvuw...")
                print(f"  ypm would push local changes to Playlist ID: '{sec_id}'")
                print("=" * 60)
                confirm_link = input("  Continue pushing to this playlist anyway? (y/N): ").strip().lower()
                if confirm_link != 'y':
                    print(f"[!] Skipped pushing section '{sec_title}' - fix the '## ' header link in "
                          f"'{file_path}' and try again.")
                    continue

            print(f"\n[+] Pushing section '{sec_title}' -> YouTube Playlist: {sec_id}")
            _sync_single_playlist_to_youtube(
                youtube,
                sec_id,
                f"{playlist_name} [{sec_title}]",
                sec_vids,
                target_video_titles,
                oauth_client,
                settings,
                playlist_data,
                log_playlist_name=playlist_name,
                section_title=sec_title
            )
            any_section_pushed = True

    # 2. Push main playlist if push_mode in ("all", "main_only")
    resolved_titles = {}
    if not is_sectioned or push_mode in ("all", "main_only"):
        if is_sectioned and push_mode == "sections_only":
            # Should be unreachable (this branch only runs when not is_sectioned in
            # sections_only mode) - but if it's ever hit, make sure it's not silent.
            print(f"[!] Warning: push_mode is 'sections_only' but no explicit sections were found in "
                  f"'{file_path}' - falling back to pushing the main playlist so nothing is lost.")
        print(f"\n[+] Pushing main playlist '{playlist_name}' -> YouTube Playlist: {playlist_id}")
        ok, res_titles = _sync_single_playlist_to_youtube(
            youtube,
            playlist_id,
            playlist_name,
            target_video_ids,
            target_video_titles,
            oauth_client,
            settings,
            playlist_data,
            file_path=file_path,
            blank_above=blank_above,
            sections_data=sections_data
        )
        if ok:
            resolved_titles = res_titles
    elif is_sectioned and push_mode == "sections_only" and not any_section_pushed:
        print(f"\n[!] push_mode is 'sections_only' but no section has a valid linked playlist ID - "
              f"nothing was pushed to YouTube for '{playlist_name}'.")

    # Normalize and update local text file by running the full format command.
    # This ensures all formatting logic is applied consistently, including
    # include_playlist_name_in_sections, metadata enrichment, and entry formatting.
    print(f"\n[*] Running format on '{file_path}' to normalize local file...")
    command_format(args, settings, playlist_data)


def command_format(args, settings, playlist_data):
    """
    Formats and cleans a local playlist file: converts any URLs/raw IDs to the layout defined by the
    playlist's playlist_entry_format (e.g. '<video_id> | <title> | <channel>') and normalizes section headers.
    Missing details (title/channel/duration) are fetched with yt-dlp, falling back to the YouTube API.
    """
    from .downloader import find_missing_metadata, fill_missing_metadata, fetch_playlist_title_ytdlp
    from .config import load_playlist_settings

    target_name = args.target.strip()
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        return

    pl_settings = load_playlist_settings(playlist_name)
    entry_format = pl_settings.get("playlist_entry_format")
    entry_fields = playlist_entry_format_fields(entry_format)

    print(f"[*] Reading and formatting '{file_path}'...")
    print(f"[*] Entry format: {entry_format}")
    target_video_ids, target_video_titles, _, blank_above, sections_data = parse_playlist_file(file_path, entry_format)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the file.")
        return

    # Check and format main header if needed
    if sections_data.get("main_header"):
        m_hdr = sections_data["main_header"]
        if m_hdr.get("playlist_id") and not m_hdr.get("title"):
            m_title = fetch_playlist_title_ytdlp(m_hdr["playlist_id"], settings)
            if m_title:
                m_hdr["title"] = m_title
                print(f"[+] Formatted main header title: '{m_title}'")

    # Check and format section headers if any have playlist link without title
    include_pl_name = pl_settings.get("include_playlist_name_in_sections", False)
    if sections_data.get("is_sectioned"):
        for sec in sections_data.get("sections", []):
            if sec.get("is_implicit"):
                continue
            if sec.get("playlist_id") and (not sec.get("title") or sec.get("title") == sec.get("playlist_id")):
                print(f"[*] Fetching section title for '{sec['playlist_id']}' using yt-dlp...")
                sec_title = fetch_playlist_title_ytdlp(sec["playlist_id"], settings)
                if sec_title:
                    sec["title"] = sec_title
                    print(f"[+] Formatted section title: '{sec_title}' (0 API quota)")
            # Optional: also record the section's *actual* linked-playlist title
            # alongside your own custom section name, e.g.
            # "## <url> | Chill Vibes | My Actual YouTube Playlist Title"
            if include_pl_name and sec.get("playlist_id") and not sec.get("playlist_name"):
                real_title = fetch_playlist_title_ytdlp(sec["playlist_id"], settings)
                if real_title and real_title != sec.get("title"):
                    sec["playlist_name"] = real_title
                    print(f"[+] Added linked playlist name to section header: '{real_title}' (0 API quota)")

            link_issue = playlist_link_format_issue(sec)
            if link_issue and settings.get("warn_on_malformed_section_links", True):
                print(f"[!] Heads up: section '{sec.get('title') or 'Section'}' has a possibly-wrong playlist "
                      f"link ({link_issue}). This won't stop 'format', but 'push' will pause and ask before "
                      f"sending anything to it.")

    video_metadata = sections_data.setdefault("video_metadata", {})
    missing_ids = find_missing_metadata(target_video_ids, video_metadata, entry_fields)
    quota_units = 0
    oauth_client = None
    method_used = "Local (nothing missing)"

    if missing_ids:
        print(f"[*] {len(missing_ids)} track(s) are missing details for the entry format.")
        answered = fill_missing_metadata(
            target_video_ids,
            video_metadata,
            entry_fields,
            settings,
            playlist_id=resolve_playlist_id(target_name, playlist_data),
        )
        method_used = "yt-dlp (0 API quota)" if answered else "none"

        # Only videos yt-dlp could not answer for at all (yt-dlp missing, private, deleted) go to the API
        unreachable = [v for v in missing_ids if v not in answered]
        if unreachable:
            print(f"[*] Falling back to YouTube Data API for {len(unreachable)} remaining track(s)...")
            oauth_client, account = _resolve_command_client(args, default_client=pl_settings.get("oauth_client"), default_account=pl_settings.get("account"))
            method_used = "yt-dlp + YouTube API" if answered else "YouTube Data API"
            try:
                youtube = get_youtube_service(oauth_client, account=account)
                quota_units += _enrich_metadata_via_api(youtube, unreachable, video_metadata, entry_fields)
            except Exception as e:
                print(f"[!] Warning: YouTube API fallback failed: {e}")

    resolved_titles = {}
    for vid_id in target_video_ids:
        resolved_titles[vid_id] = (video_metadata.get(vid_id) or {}).get("title") or "Untitled Video"

    if save_playlist_file(
        file_path, target_video_ids, resolved_titles,
        blank_above=blank_above, sections_data=sections_data, playlist_entry_format=entry_format,
    ):
        print(f"[+] Successfully formatted '{file_path}' ({len(target_video_ids)} tracks normalized).")

    still_missing = find_missing_metadata(target_video_ids, video_metadata, entry_fields)
    filled = len(missing_ids) - len(still_missing)

    print("\n" + "=" * 60)
    print(" Format Summary")
    print("=" * 60)
    print(f"  * {'Method:':<24} {method_used}")
    if oauth_client:
        print(f"  * {'OAuth Client:':<24} {oauth_client}")
    print(f"  * {'Total Tracks:':<24} {len(target_video_ids):>4d} track(s)")
    if missing_ids:
        print(f"  * {'Metadata Enriched:':<24} {filled:>4d} track(s)")
        if still_missing:
            print(f"  * {'Unavailable / Deleted:':<24} {len(still_missing):>4d} track(s) (no data on YouTube)")
    else:
        print(f"  * {'Metadata Status:':<24} Up-to-date (all details present)")
    print(f"  * {'API Quota Used:':<24} {quota_units:>4d} unit(s)")
    print("=" * 60 + "\n")
    record_activity(playlist_data, playlist_name, "format")
    log_playlist_event(
        settings,
        playlist_name,
        "format",
        oauth_client or method_used,
        summary_lines=[
            f"Normalized {len(target_video_ids)} track(s), completed {filled} of {len(missing_ids)} incomplete entries ({quota_units} quota units via {method_used})"
        ],
        playlist_data=playlist_data
    )
