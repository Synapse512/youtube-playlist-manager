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
    """Links a YouTube playlist to data/playlists.json using the title fetched from YouTube."""
    raw_input = getattr(args, "target", None) or getattr(args, "name", None) or getattr(args, "id", None)
    if not raw_input:
        print("[!] Error: Missing Playlist ID or URL. Syntax: python main.py link <id_or_url> [--client <name>]\n")
        from .ui import print_help
        print_help()
        return

    # Resolve which oauth-client JSON to use for this operation (prompts if multiple exist and --client is omitted)
    oauth_client = _resolve_command_client(args)

    raw_id = raw_input.strip()
    playlist_id = extract_playlist_id(raw_id)
    if not playlist_id:
        print(f"[!] Error: Could not extract a valid Playlist ID from '{raw_id}'.")
        return

    print(f"[*] Fetching playlist title from YouTube ({playlist_id})...")
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

    print(f"[+] Successfully linked '{playlist_name}' -> Playlist ID: '{playlist_id}'")
    record_activity(playlist_data, playlist_name, "link")
    log_playlist_event(
        settings,
        playlist_name,
        "link",
        oauth_client,
        summary_lines=[f"Linked '{playlist_name}' -> Playlist ID '{playlist_id}'"],
        playlist_data=playlist_data
    )


def command_unlink(args, settings, playlist_data):
    """Removes a linked playlist from data/playlists.json."""
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
    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, playlist_data)
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)

    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    # Resolve which oauth-client JSON to use for this operation
    oauth_client = _resolve_command_client(args)

    if playlist_id != target_name:
        print(f"[*] Target playlist '{target_name}' resolved to Playlist ID: {playlist_id}")
    else:
        print(f"[*] Using Playlist ID: {playlist_id}")

    youtube = get_youtube_service(oauth_client)
    print(f"[*] Fetching live track list from YouTube for playlist '{playlist_id}'...")

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
                    raw_items.append((pos, f"{v_id} | {title}"))

            next_page_token = res.get("nextPageToken")
            page_num += 1
            if not next_page_token:
                break

    except HttpError as e:
        status_code = e.resp.status if hasattr(e, "resp") else "Unknown"
        if status_code == 404:
            print(f"[!] Error: Playlist '{playlist_id}' not found (404). Check the ID or playlist name.")
        elif status_code == 403:
            print(f"[!] Error: Access forbidden (403). The playlist might be private or API quota was exceeded.\n    Details: {e}")
        else:
            print(f"[!] YouTube API Error ({status_code}): {e}")
        return

    # Ensure items are ordered by their actual position in the playlist
    raw_items.sort(key=lambda x: x[0])
    lines_to_write = ["# PULL BEFORE MAKING CHANGES", ""] + [x[1] for x in raw_items]

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines_to_write) + "\n")
    except OSError as e:
        print(f"[!] Error writing playlist file '{file_path}': {e}")
        return

    print("\n" + "=" * 55)
    print(" Pull Summary")
    print("=" * 55)
    print(f"  * OAuth Client:       {oauth_client}")
    print(f"  * Tracks Fetched:  {len(raw_items):4d}")
    print(f"  * Pages Read:      {page_num - 1:4d} request(s)")
    print(f"  * API Quota Used:  {quota_units:4d} unit(s) (1 unit/page)")
    print("=" * 55)
    print(f"[+] Successfully pulled {len(raw_items)} tracks to '{file_path}'!\n")
    record_activity(playlist_data, playlist_name, "pull")
    log_playlist_event(
        settings,
        playlist_name,
        "pull",
        oauth_client,
        summary_lines=[
            f"Fetched {len(raw_items)} track(s) across {page_num - 1} page(s) ({quota_units} quota units)"
        ],
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
    target_video_ids, target_video_titles, skipped = parse_playlist_file(file_path)

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
        print(f"[!] YouTube API Error while fetching current playlist: {e}")
        return

    # Critical: Sort current items by remote position so current_list reflects true sequence
    current_items.sort(key=lambda x: x["position"])
    print(f"[+] Retrieved {len(current_items)} tracks currently on YouTube.")

    current_list = list(current_items)
    target_counts = {}
    for vid in target_video_ids:
        target_counts[vid] = target_counts.get(vid, 0) + 1

    # 1. Delete videos removed locally (iterate backwards to keep list indices valid)
    deleted_count = 0
    deleted_details = []
    inserted_details = []
    moved_details = []
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
                print(f"[!] Error deleting track {vid}: {e}")
                sync_failed = True

    # 2. Add new tracks present locally at their exact target positions
    inserted_count = 0
    # Track existing counts in current list
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
                    print("\n[!] YouTube API daily quota limit reached (~200 updates/day).")
                    print("Run this script again tomorrow to continue!")
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
    moved_count = 0

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
                print("\n[!] YouTube API daily quota limit reached")
                print("Wait for your quota to reset before running again")
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

    if save_playlist_file(file_path, target_video_ids, resolved_titles):
        print(f"[+] Automatically updated and formatted local file '{file_path}' (replaced links with video IDs and titles).")

    # Calculate exact API quota units used
    # playlistItems.list: 1 unit | delete: 50 units | insert: 50 units | update: 50 units
    list_quota = list_units * 1
    delete_quota = deleted_count * 50
    insert_quota = inserted_count * 50
    update_quota = moved_count * 50
    total_quota = list_quota + delete_quota + insert_quota + update_quota

    print("\n" + "=" * 58)
    print(" Synchronization Summary")
    print("=" * 58)
    print(f"  * OAuth Client:   {oauth_client}")
    print(f"  * Deleted:     {deleted_count:4d} track(s)     ({delete_quota:5d} quota units)")
    print(f"  * Inserted:    {inserted_count:4d} track(s)     ({insert_quota:5d} quota units)")
    print(f"  * Reordered:   {moved_count:4d} track(s)     ({update_quota:5d} quota units)")
    print(f"  * Read/List:   {list_units:4d} request(s)   ({list_quota:5d} quota units)")
    print("-" * 58)
    print(f"  * Total Quota Used: {total_quota:5d} units")
    print("=" * 58)
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
    target_name = args.target.strip()
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        return

    print(f"[*] Reading and formatting '{file_path}'...")
    target_video_ids, target_video_titles, _ = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the file.")
        return

    # Resolve which oauth-client JSON to use for this operation
    oauth_client = _resolve_command_client(args)

    # Check for missing titles and fetch them from YouTube API in batches of 50
    missing_ids = [v for v in target_video_ids if not target_video_titles.get(v)]
    quota_units = 0
    if missing_ids:
        print(f"[*] Fetching titles for {len(missing_ids)} tracks from YouTube API...")
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

    if save_playlist_file(file_path, target_video_ids, resolved_titles):
        print(f"[+] Successfully formatted '{file_path}' ({len(target_video_ids)} tracks normalized).")

    print("\n" + "=" * 55)
    print(" Format Summary")
    print("=" * 55)
    print(f"  * OAuth Client:         {oauth_client}")
    print(f"  * Tracks Normalized: {len(target_video_ids):4d}")
    print(f"  * Titles Fetched:    {len(missing_ids):4d}")
    print(f"  * API Quota Used:    {quota_units:4d} unit(s) (1 unit/batch of 50)")
    print("=" * 55 + "\n")
    record_activity(playlist_data, playlist_name, "format")
    log_playlist_event(
        settings,
        playlist_name,
        "format",
        oauth_client,
        summary_lines=[
            f"Normalized {len(target_video_ids)} track(s), fetched {len(missing_ids)} missing title(s) ({quota_units} quota units)"
        ],
        playlist_data=playlist_data
    )
