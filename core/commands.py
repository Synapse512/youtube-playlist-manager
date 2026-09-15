"""
Command handlers for link, unlink, list, pull, push, and format.
"""

import os

try:
    from googleapiclient.errors import HttpError
except ImportError:
    HttpError = Exception

from .config import (
    PLAYLISTS_DIR,
    save_playlist_data,
    record_activity,
    log_playlist_event,
)
from .auth import resolve_oauth_client, get_youtube_service
from .parser import (
    extract_playlist_id,
    sanitize_filename,
    resolve_playlist_id,
    get_playlist_name_for_target,
    parse_playlist_file,
    save_playlist_file,
)
from .sync import compute_minimal_moves


def _resolve_command_client(args):
    """
    Resolves which oauth-client JSON to use for this operation.
    Nothing is remembered between runs - the oauth-client is picked fresh every
    time (unless --client is passed or only one oauth-client exists).
    """
    explicit_client = getattr(args, "client", None)
    return resolve_oauth_client(explicit_client, allow_prompt=True)


def command_link(args, settings, playlist_data):
    """Links a YouTube playlist to playlists/_playlists.json using the title fetched from YouTube."""
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
            print(f"[*] yt-dlp could not access playlist title (it may be private). Falling back to YouTube API...")

    if not playlist_name:
        oauth_client = _resolve_command_client(args)
        method_used = f"YouTube API ({oauth_client})"
        print(f"[*] Fetching playlist title from YouTube API ({playlist_id})...")
        youtube = get_youtube_service(oauth_client)
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

    # Create initial playlist file with a reminder to pull before editing
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{playlist_name}.txt")
    if not os.path.exists(file_path):
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("# PULL BEFORE MAKING CHANGES\n\n")
            print(f"[+] Created initial playlist file '{file_path}'.")
        except OSError as e:
            print(f"[!] Warning: Could not create initial file '{file_path}': {e}")

    # Initialize the playlist-downloads folder and default _setting.json
    downloads_root = settings.get("downloads_dir", DOWNLOADS_DIR)
    playlist_download_dir = os.path.join(downloads_root, playlist_name)
    os.makedirs(playlist_download_dir, exist_ok=True)
    settings_path = os.path.join(playlist_download_dir, DOWNLOAD_SETTINGS_FILENAME)
    if not os.path.exists(settings_path):
        existing = load_playlist_download_settings(playlist_download_dir)
        if not existing:
            default_settings = {
                "embed_thumbnail": True,
                "number_files": True,
                "tracks": {}
            }
            save_playlist_download_settings(playlist_download_dir, default_settings)
            print(f"[+] Initialized download folder and settings: '{playlist_download_dir}/'")
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
        playlist_data=playlist_data
    )


def command_unlink(args, settings, playlist_data):
    """Removes a linked playlist from playlists/_playlists.json."""
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

        print(f"  {name}  [{terminal_link(pid, url)}]{last_info}")

def command_pull(args, settings, playlist_data):
    """Pulls a remote YouTube playlist into a local text file."""
    from .downloader import fetch_playlist_tracks_ytdlp

    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, playlist_data)
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)

    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    # Check if local file already exists to preserve custom blank line spacing and compute diff
    existing_blank_above = set()
    existing_video_ids = []
    existing_titles = {}
    if os.path.exists(file_path):
        try:
            existing_video_ids, existing_titles, _, existing_blank_above = parse_playlist_file(file_path)
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
    method_used = "yt-dlp"
    oauth_client = None
    pages_read = 0
    quota_units = 0

    if pull_method in ("auto", "ytdlp"):
        print(f"[*] Fetching live track list from YouTube for playlist '{playlist_id}' using yt-dlp (0 Google API quota)...")
        ytdlp_ids, ytdlp_titles = fetch_playlist_tracks_ytdlp(playlist_id, settings)
        if ytdlp_ids is not None:
            pulled_video_ids = ytdlp_ids
            pulled_titles = ytdlp_titles
            method_used = "yt-dlp"
        else:
            if pull_method == "ytdlp":
                print(f"[!] Error: Could not pull playlist tracks via yt-dlp. Make sure the playlist is public/unlisted.")
                return
            print(f"[*] yt-dlp could not access playlist tracks (it may be private). Falling back to YouTube API...")

    if pulled_video_ids is None:
        # Resolve which oauth-client JSON to use for this operation
        oauth_client = _resolve_command_client(args)
        youtube = get_youtube_service(oauth_client)
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
                    pos = snippet.get("position", len(raw_items))
                    if v_id:
                        raw_items.append((pos, v_id, title))

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
        pulled_titles = {x[1]: x[2] for x in raw_items}
        pages_read = page_num - 1
        method_used = "api"

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

    if not save_playlist_file(file_path, pulled_video_ids, pulled_titles, blank_above=existing_blank_above):
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
        playlist_data=playlist_data
    )

def command_push(args, settings, playlist_data):
    """Pushes the local text file track order and changes to YouTube, then normalizes the local file."""
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
    target_video_ids, target_video_titles, skipped, blank_above = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the local text file. Push aborted.")
        return

    print(f"[+] Parsed {len(target_video_ids)} valid tracks from local file.")

    # Resolve which oauth-client JSON to use for this operation
    oauth_client = _resolve_command_client(args)

    youtube = get_youtube_service(oauth_client)
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
            list_units += 1  # 1 unit per page request

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
            print("\n[!] YouTube API daily quota limit reached while reading playlist.")
            record_activity(playlist_data, playlist_name, "push (quota exceeded)")
            log_playlist_event(
                settings,
                playlist_name,
                "push (ABANDONED - QUOTA EXCEEDED)",
                oauth_client,
                summary_lines=[
                    "Push abandoned: YouTube API daily quota limit reached during initial track fetch",
                    f"{list_units} list request(s) completed before quota exhaustion"
                ],
                detail_lines=[f"Error: {e}"],
                playlist_data=playlist_data
            )
            return
        print(f"[!] YouTube API Error while fetching current playlist: {e}")
        return

    # Critical: Sort current items by remote position so current_list reflects true sequence
    current_items.sort(key=lambda x: x["position"])
    print(f"[+] Retrieved {len(current_items)} tracks currently on YouTube.")

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

    def _handle_quota_exceeded(phase_name):
        list_q = list_units * 1
        del_q = deleted_count * 50
        ins_q = inserted_count * 50
        upd_q = moved_count * 50
        total_q = list_q + del_q + ins_q + upd_q

        print("\n" + "=" * 60)
        print(" [!] YouTube API Daily Quota Exceeded")
        print("=" * 60)
        print(f"  * Operation abandoned during: {phase_name}")
        print(f"  * Total quota consumed:       {total_q} unit(s)")
        print(f"  * Successfully deleted:       {deleted_count} track(s)")
        print(f"  * Successfully inserted:      {inserted_count} track(s)")
        print(f"  * Successfully reordered:     {moved_count} track(s)")
        print("=" * 60)
        print("  Wait for your daily quota to reset or use another --client.")
        print(f"  (This abandoned operation has been logged to logs/{sanitize_filename(playlist_name)}.log)\n")

        # Update local file to preserve YouTube's partial state if any changes occurred
        if deleted_count > 0 or inserted_count > 0 or moved_count > 0:
            partial_vids = [it["videoId"] for it in current_list if it.get("videoId")]
            partial_titles = {it["videoId"]: it.get("title", "") for it in current_list if it.get("videoId")}
            save_playlist_file(file_path, partial_vids, partial_titles, blank_above=blank_above)
            print(f"[*] Updated local file '{file_path}' to match YouTube's current state.")

        record_activity(playlist_data, playlist_name, "push (quota exceeded)")
        diff_d = deleted_details + inserted_details + moved_details
        log_playlist_event(
            settings,
            playlist_name,
            "push (ABANDONED - QUOTA EXCEEDED)",
            oauth_client,
            summary_lines=[
                f"Push abandoned: YouTube API daily quota limit reached during {phase_name}",
                f"Partial progress: {inserted_count} inserted, {deleted_count} deleted, {moved_count} reordered ({total_q} quota units used)"
            ],
            detail_lines=diff_d if diff_d else [f"Quota limit reached during {phase_name} before any modifications were made."],
            playlist_data=playlist_data
        )

    # 1. Delete videos removed locally (iterate backwards to keep list indices valid)
    sync_failed = False
    curr_counts = {}
    for item in current_list:
        v = item["videoId"]
        curr_counts[v] = curr_counts.get(v, 0) + 1

    for i in range(len(current_list) - 1, -1, -1):
        item = current_list[i]
        vid = item["videoId"]
        target_allowed = target_counts.get(vid, 0)
        # If video is not in target or exceeds count in target
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
                    return
                print(f"[!] Error deleting track {vid}: {e}")
                sync_failed = True

    # 2. Add new tracks present locally at their exact target positions
    active_counts = {}
    for item in current_list:
        v = item["videoId"]
        active_counts[v] = active_counts.get(v, 0) + 1

    target_seen_counts = {}
    for pos, vid_id in enumerate(target_video_ids):
        target_seen_counts[vid_id] = target_seen_counts.get(vid_id, 0) + 1
        # If this occurrence is beyond what's currently in YouTube, insert it
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
                    return
                print(f"    [!] Error inserting {vid_id}: {e}")
                sync_failed = True

    if sync_failed:
        print("\n[!] Push stopped before reordering because one or more delete/insert operations failed.")
        print("    Pull the playlist again before retrying so the local file reflects YouTube's current state.")
        return

    # 3. Reorder tracks using LIS minimal moves algorithm to minimize quota units
    # Map target video IDs to specific playlistItemIds in current_list
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
            # Update local list state to match YouTube's new sequence
            moved_item = current_list.pop(from_idx)
            moved_item["position"] = target_pos
            current_list.insert(target_pos, moved_item)
            moved_count += 1
            moved_details.append(f"~ Reordered: '{vid_id}' | {item_info['title']} (pos {from_idx} -> pos {target_pos})")
        except HttpError as e:
            if "quotaExceeded" in str(e):
                _handle_quota_exceeded("reordering phase")
                return
            print(f"[!] Error moving track {vid_id}: {e}")

    # 4. Normalize and update local text file (<video_id> | <video_title>)
    current_video_map = {item["videoId"]: item for item in current_list if item.get("videoId")}
    resolved_titles = {}
    for vid_id in target_video_ids:
        if vid_id in current_video_map and current_video_map[vid_id].get("title"):
            resolved_titles[vid_id] = current_video_map[vid_id]["title"]
        elif target_video_titles.get(vid_id):
            resolved_titles[vid_id] = target_video_titles[vid_id]
        else:
            resolved_titles[vid_id] = "Untitled Video"

    if save_playlist_file(file_path, target_video_ids, resolved_titles, blank_above=blank_above):
        print(f"[+] Automatically updated and formatted local file '{file_path}' (replaced links with video IDs and titles).")

    # Calculate exact API quota units used
    # playlistItems.list: 1 unit | delete: 50 units | insert: 50 units | update: 50 units
    list_quota = list_units * 1
    delete_quota = deleted_count * 50
    insert_quota = inserted_count * 50
    update_quota = moved_count * 50
    total_quota = list_quota + delete_quota + insert_quota + update_quota

    print("\n" + "=" * 60)
    print(" Synchronization Summary")
    print("=" * 60)
    print(f"  * {'OAuth Client:':<22} {oauth_client}")
    print(f"  * {'Deleted:':<22} {deleted_count:>4d} track(s)     ({delete_quota:>5d} quota units)")
    print(f"  * {'Inserted:':<22} {inserted_count:>4d} track(s)     ({insert_quota:>5d} quota units)")
    print(f"  * {'Reordered:':<22} {moved_count:>4d} track(s)     ({update_quota:>5d} quota units)")
    print(f"  * {'Read/List:':<22} {list_units:>4d} request(s)   ({list_quota:>5d} quota units)")
    print("-" * 60)
    print(f"  * {'Total Quota Used:':<22} {total_quota:>4d} unit(s)")
    print("=" * 60)
    print("[+] Playlist synchronization complete!\n")
    record_activity(playlist_data, playlist_name, "push")
    diff_details = deleted_details + inserted_details + moved_details
    log_playlist_event(
        settings,
        playlist_name,
        "push",
        oauth_client,
        summary_lines=[
            f"{inserted_count} inserted, {deleted_count} deleted, {moved_count} reordered ({total_quota} quota units)"
        ],
        detail_lines=diff_details if diff_details else ["No changes required (already in sync)"],
        playlist_data=playlist_data
    )


def command_format(args, settings, playlist_data):
    """Formats and cleans a local playlist file, converting any URLs/raw IDs to '<video_id> | <title>'."""
    from .downloader import find_ytdlp, fetch_video_titles_ytdlp

    target_name = args.target.strip()
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        return

    print(f"[*] Reading and formatting '{file_path}'...")
    target_video_ids, target_video_titles, _, blank_above = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the file.")
        return

    missing_ids = [v for v in target_video_ids if not target_video_titles.get(v)]
    quota_units = 0
    oauth_client = None
    method_used = "Local (all titles present)"

    if missing_ids:
        ytdlp_bin = find_ytdlp(settings)
        if ytdlp_bin:
            print(f"[*] Fetching titles for {len(missing_ids)} track(s) using yt-dlp (0 Google API quota)...")
            fetched = fetch_video_titles_ytdlp(missing_ids, settings)
            for vid, title in fetched.items():
                target_video_titles[vid] = title
            method_used = "yt-dlp (0 API quota)"

            # If any are still missing (e.g. yt-dlp couldn't extract some), check for cloud fallback
            still_missing = [v for v in missing_ids if not target_video_titles.get(v)]
            if still_missing:
                print(f"[*] Falling back to YouTube Data API for {len(still_missing)} remaining title(s)...")
                oauth_client = _resolve_command_client(args)
                method_used = "yt-dlp + YouTube API"
                try:
                    youtube = get_youtube_service(oauth_client)
                    for i in range(0, len(still_missing), 50):
                        batch_ids = still_missing[i:i + 50]
                        res = youtube.videos().list(part="snippet", id=",".join(batch_ids)).execute()
                        quota_units += 1
                        for item in res.get("items", []):
                            target_video_titles[item["id"]] = item.get("snippet", {}).get("title", "").strip()
                except Exception as e:
                    print(f"[!] Warning: YouTube API fallback failed: {e}")
        else:
            # yt-dlp not available, use YouTube Data API directly
            oauth_client = _resolve_command_client(args)
            method_used = "YouTube Data API"
            print(f"[*] yt-dlp not found. Fetching titles for {len(missing_ids)} tracks from YouTube API...")
            try:
                youtube = get_youtube_service(oauth_client)
                for i in range(0, len(missing_ids), 50):
                    batch_ids = missing_ids[i:i + 50]
                    res = youtube.videos().list(part="snippet", id=",".join(batch_ids)).execute()
                    quota_units += 1  # 1 unit per videos.list batch
                    for item in res.get("items", []):
                        v_id = item["id"]
                        v_title = item.get("snippet", {}).get("title", "").strip()
                        if v_title:
                            target_video_titles[v_id] = v_title
            except Exception as e:
                print(f"[!] Warning: Could not fetch some video titles from YouTube API: {e}")

    resolved_titles = {}
    for vid_id in target_video_ids:
        resolved_titles[vid_id] = target_video_titles.get(vid_id) or "Untitled Video"

    if save_playlist_file(file_path, target_video_ids, resolved_titles, blank_above=blank_above):
        print(f"[+] Successfully formatted '{file_path}' ({len(target_video_ids)} tracks normalized).")

    print("\n" + "=" * 60)
    print(" Format Summary")
    print("=" * 60)
    print(f"  * {'Method:':<22} {method_used}")
    if oauth_client:
        print(f"  * {'OAuth Client:':<22} {oauth_client}")
    print(f"  * {'Tracks Normalized:':<22} {len(target_video_ids):>4d} track(s)")
    print(f"  * {'Titles Fetched:':<22} {len(missing_ids):>4d} track(s)")
    print(f"  * {'API Quota Used:':<22} {quota_units:>4d} unit(s)")
    print("=" * 60 + "\n")
    record_activity(playlist_data, playlist_name, "format")
    log_playlist_event(
        settings,
        playlist_name,
        "format",
        oauth_client or method_used,
        summary_lines=[
            f"Normalized {len(target_video_ids)} track(s), fetched {len(missing_ids)} missing title(s) ({quota_units} quota units via {method_used})"
        ],
        playlist_data=playlist_data
    )
