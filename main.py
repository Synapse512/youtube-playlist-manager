import os
import sys
import json
import glob
import re
import argparse
import shutil
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# Configure safe utf-8 stdout/stderr where possible on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import google_auth_oauthlib.flow
    import googleapiclient.discovery
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.auth.exceptions import RefreshError
    from googleapiclient.errors import HttpError
except ImportError:
    google_auth_oauthlib = None
    googleapiclient = None
    Request = None
    Credentials = None
    RefreshError = None
    HttpError = Exception

SETTINGS_FILE = "settings.json"
TOKEN_FILE = "token.json"
CLIENT_SECRET_FILE = "client_secret.json"
PLAYLISTS_DIR = "playlists"

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# --- CONFIG & AUTH HELPERS ---

def load_settings():
    """Loads settings.json safely, recreating with defaults if missing or corrupted."""
    default_settings = {
        "safety_check_before_push": True,
        "playlists": {}
    }
    if not os.path.exists(SETTINGS_FILE):
        print(f"[*] Settings file not found. Creating default '{SETTINGS_FILE}'...")
        save_settings(default_settings)
        return default_settings

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
            if not isinstance(settings, dict):
                raise ValueError("Settings file must contain a JSON object.")
            settings.setdefault("safety_check_before_push", True)
            settings.setdefault("playlists", {})
            return settings
    except (json.JSONDecodeError, ValueError, OSError) as e:
        backup_file = f"{SETTINGS_FILE}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        print(f"[!] Warning: '{SETTINGS_FILE}' is invalid or corrupted ({e}).")
        print(f"[*] Backing up damaged settings to '{backup_file}' and resetting to defaults.")
        try:
            shutil.copyfile(SETTINGS_FILE, backup_file)
        except OSError:
            pass
        save_settings(default_settings)
        return default_settings


def save_settings(settings):
    """Saves settings dictionary to settings.json atomically."""
    temp_file = f"{SETTINGS_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
        if os.path.exists(SETTINGS_FILE):
            os.replace(temp_file, SETTINGS_FILE)
        else:
            os.rename(temp_file, SETTINGS_FILE)
    except OSError as e:
        print(f"[!] Error saving settings to '{SETTINGS_FILE}': {e}")


def find_client_secret_file():
    """Checks for client_secret.json or any matching client secret file in the directory."""
    if os.path.exists(CLIENT_SECRET_FILE):
        return CLIENT_SECRET_FILE

    # Check for downloaded files like client_secret_*.json or client_secrets.json
    candidates = glob.glob("client_secret*.json") + glob.glob("*client*secret*.json")
    # Exclude token files
    candidates = [c for c in candidates if "token" not in c.lower()]

    if candidates:
        candidate = candidates[0]
        print(f"[*] Using detected client secret file: '{candidate}'")
        return candidate

    return None


def get_youtube_service():
    """Authenticates user and returns an initialized YouTube API service client."""
    if googleapiclient is None or google_auth_oauthlib is None:
        print("\n[!] ERROR: Missing required Google API libraries.")
        print("    Please install dependencies with: pip install -r requirements.txt\n")
        sys.exit(1)

    creds = None

    # Step 1: Check existing cached token
    if os.path.exists(TOKEN_FILE):
        print(f"[*] Checking cached credentials in '{TOKEN_FILE}'...")
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"[!] Warning: Could not read '{TOKEN_FILE}' ({e}). Re-authenticating...")
            creds = None

    # Step 2: Validate or refresh credentials
    if creds and creds.valid:
        print("[+] Session token is valid.")
    else:
        if creds and creds.expired and creds.refresh_token:
            print("[*] Session token has expired. Attempting to refresh with Google OAuth...")
            try:
                creds.refresh(Request())
                print("[+] Session token successfully refreshed.")
            except RefreshError as e:
                print(f"[!] Token refresh failed ({e}). Starting fresh authentication flow...")
                creds = None
            except Exception as e:
                print(f"[!] Unexpected error during token refresh ({e}). Re-authenticating...")
                creds = None

        if not creds or not creds.valid:
            secret_file = find_client_secret_file()
            if not secret_file:
                print("\n" + "=" * 65)
                print("[!] ERROR: OAuth Client Secret file not found!")
                print("=" * 65)
                print(f"Expected file: '{CLIENT_SECRET_FILE}' in the current working directory.\n")
                print("Setup Instructions:")
                print("  1. Go to Google Cloud Console: https://console.cloud.google.com/")
                print("  2. Create a project and enable 'YouTube Data API v3'.")
                print("  3. Navigate to APIs & Services -> Credentials.")
                print("  4. Click 'Create Credentials' -> 'OAuth client ID'.")
                print("  5. Select Application Type: 'Desktop App'.")
                print("  6. Download the JSON credentials file and rename it to:")
                print(f"     '{CLIENT_SECRET_FILE}' in this folder.")
                print("=" * 65 + "\n")
                sys.exit(1)

            print(f"[*] Found client secret file: '{secret_file}'")
            print("[*] Launching browser for Google OAuth authorization...")
            print("    (Please log in and grant YouTube permissions in your browser window...)")

            try:
                flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(
                    secret_file, SCOPES
                )
                creds = flow.run_local_server(port=0)
                print("[+] OAuth authentication successful!")
            except Exception as e:
                print(f"\n[!] Authentication failed: {e}")
                sys.exit(1)

        # Save credentials for subsequent runs
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as token:
                token.write(creds.to_json())
            print(f"[+] Saved updated session token to '{TOKEN_FILE}'.")
        except OSError as e:
            print(f"[!] Warning: Could not save session token to '{TOKEN_FILE}': {e}")

    print("[*] Initializing YouTube Data API v3 client...")
    try:
        service = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
        print("[+] YouTube API client connected successfully.")
        return service
    except Exception as e:
        print(f"[!] Error building YouTube service: {e}")
        sys.exit(1)


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


def resolve_playlist_id(name_or_id, settings):
    """Resolves an alias name or URL/ID to the actual YouTube Playlist ID."""
    name_or_id = name_or_id.strip()
    playlists = settings.get("playlists", {})
    resolved = playlists.get(name_or_id, name_or_id)
    return extract_playlist_id(resolved)


def parse_playlist_file(file_path):
    """
    Parses a local playlist text file.
    Supports lines formatted as:
      - '<video_id> | <title>'
      - '<video_url> | <title>'
      - '<video_id>'
      - '<video_url>'
    Ignores empty lines and comments (starting with '#').
    Returns (target_video_ids, target_video_titles, skipped_lines).
    """
    target_video_ids = []
    target_video_titles = {}
    skipped_lines = 0

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
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


# --- CORE COMMANDS ---

def command_link(args, settings):
    """Links a short name alias to a YouTube Playlist ID in settings.json."""
    alias = args.name.strip()
    raw_id = args.id.strip()
    playlist_id = extract_playlist_id(raw_id)

    if not alias:
        print("[!] Error: Alias name cannot be empty.")
        return

    if not playlist_id:
        print("[!] Error: Playlist ID cannot be empty.")
        return

    playlists = settings.setdefault("playlists", {})
    if alias in playlists:
        existing_id = playlists[alias]
        print(f"[!] Error: Alias '{alias}' is already linked to Playlist ID: '{existing_id}'.")
        print(f"    If you wish to change it, unlink it first with: python main.py unlink {alias}")
        return

    settings["playlists"][alias] = playlist_id
    save_settings(settings)

    # Create initial playlist file with the reminder header if it does not exist
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    safe_name = sanitize_filename(alias)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")
    if not os.path.exists(file_path):
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("# PULL BEFORE MAKING CHANGES\n\n")
            print(f"[+] Created initial playlist file '{file_path}'.")
        except OSError as e:
            print(f"[!] Warning: Could not create initial file '{file_path}': {e}")

    print(f"[+] Successfully linked alias '{alias}' -> Playlist ID: '{playlist_id}'")


def command_unlink(args, settings):
    """Removes a linked playlist alias from settings.json."""
    alias = args.name.strip()
    playlists = settings.get("playlists", {})

    if alias in playlists:
        del playlists[alias]
        save_settings(settings)
        print(f"[+] Unlinked alias '{alias}'.")
    else:
        print(f"[!] Alias '{alias}' not found in settings.")


def command_list(args, settings):
    """Lists all configured playlist aliases and current settings."""
    playlists = settings.get("playlists", {})
    safety_check = settings.get("safety_check_before_push", True)

    print("\n" + "=" * 50)
    print(" YouTube Playlist Manager - Configuration")
    print("=" * 50)
    print(f" Safety check before push: {'Enabled (True)' if safety_check else 'Disabled (False)'}")
    print("-" * 50)
    print(" Configured Aliases:")

    if not playlists:
        print("  (No aliases linked yet. Use 'python main.py link <alias> <playlist_id>')")
    else:
        for alias, pid in playlists.items():
            local_file = os.path.join(PLAYLISTS_DIR, f"{sanitize_filename(alias)}.txt")
            status = f"Local file: {local_file}" if os.path.exists(local_file) else "Local file: Not pulled yet"
            print(f"  * {alias} -> {pid}")
            print(f"    - {status}")
    print("=" * 50 + "\n")


def find_lis_indices(arr):
    """
    Computes the set of indices corresponding to a Longest Increasing Subsequence in arr.
    Uses O(N log N) patience sorting with predecessor tracking.
    """
    import bisect
    n = len(arr)
    if n == 0:
        return set()

    tails_val = []
    tails_idx = []
    parent = [-1] * n

    for i, x in enumerate(arr):
        idx = bisect.bisect_left(tails_val, x)
        if idx == len(tails_val):
            tails_val.append(x)
            tails_idx.append(i)
        else:
            tails_val[idx] = x
            tails_idx[idx] = i
        if idx > 0:
            parent[i] = tails_idx[idx - 1]

    lis_idx = set()
    curr = tails_idx[-1]
    while curr != -1:
        lis_idx.add(curr)
        curr = parent[curr]
    return lis_idx


def compute_minimal_moves(curr_list, target_list):
    """
    Calculates the sequence of minimal move operations to transform curr_list into target_list.
    curr_list and target_list must contain unique identifiers (e.g. playlistItemIds).
    Returns a list of tuples: (item_identifier, target_position).
    """
    curr = list(curr_list)
    t_pos = {v: i for i, v in enumerate(target_list)}
    moves = []

    while curr != target_list:
        arr = [t_pos[v] for v in curr]
        lis_idx = find_lis_indices(arr)
        candidates = [i for i in range(len(curr)) if i not in lis_idx]
        if not candidates:
            break

        best_i = candidates[0]
        best_gain = -1

        for i in candidates:
            elem = curr[i]
            des = t_pos[elem]
            temp = list(curr)
            temp.pop(i)
            temp.insert(des, elem)
            temp_arr = [t_pos[v] for v in temp]
            temp_lis_len = len(find_lis_indices(temp_arr))
            if temp_lis_len > best_gain:
                best_gain = temp_lis_len
                best_i = i

        elem = curr[best_i]
        des = t_pos[elem]
        curr.pop(best_i)
        curr.insert(des, elem)
        moves.append((elem, des))

    return moves


def command_pull(args, settings):
    """Pulls a remote YouTube playlist into a local text file."""
    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, settings)
    safe_name = sanitize_filename(target_name)

    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if playlist_id != target_name:
        print(f"[*] Target alias '{target_name}' resolved to Playlist ID: {playlist_id}")
    else:
        print(f"[*] Using Playlist ID: {playlist_id}")

    youtube = get_youtube_service()
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
                title = snippet.get("title", "Untitled Track").strip()
                pos = snippet.get("position", 0)
                if v_id:
                    raw_items.append((pos, f"{v_id} | {title}"))

            next_page_token = res.get("nextPageToken")
            page_num += 1
            if not next_page_token:
                break

    except HttpError as e:
        status_code = e.resp.status if hasattr(e, "resp") else "Unknown"
        if status_code == 404:
            print(f"[!] Error: Playlist '{playlist_id}' not found (404). Check the ID or alias.")
        elif status_code == 403:
            print(f"[!] Error: Access forbidden (403). The playlist might be private or API quota was exceeded.\n    Details: {e}")
        else:
            print(f"[!] YouTube API Error ({status_code}): {e}")
        return

    # Ensure items are ordered by their actual position in the playlist
    raw_items.sort(key=lambda x: x[0])
    lines_to_write = ["# PULL BEFORE MAKING CHANGES", ""] + [x[1] for x in raw_items]

    # Write safely
    temp_file = f"{file_path}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines_to_write) + "\n")
        if os.path.exists(file_path):
            os.replace(temp_file, file_path)
        else:
            os.rename(temp_file, file_path)
    except OSError as e:
        print(f"[!] Error saving to '{file_path}': {e}")
        return

    print("\n" + "=" * 55)
    print(" Pull Summary")
    print("=" * 55)
    print(f"  * Tracks Fetched:  {len(raw_items):4d}")
    print(f"  * Pages Read:      {page_num - 1:4d} request(s)")
    print(f"  * API Quota Used:  {quota_units:4d} unit(s) (1 unit/page)")
    print("=" * 55)
    print(f"[+] Successfully pulled {len(raw_items)} tracks to '{file_path}'!\n")


def command_push(args, settings):
    """Pushes the local text file track order and changes to YouTube, then normalizes the local file."""
    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, settings)
    safe_name = sanitize_filename(target_name)
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

    youtube = get_youtube_service()
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
            print(f"[-] Deleting track from YouTube: '{item['title']}' ({vid})")
            try:
                youtube.playlistItems().delete(id=item["playlistItemId"]).execute()
                deleted_count += 1
                current_list.pop(i)
                curr_counts[vid] -= 1
            except HttpError as e:
                print(f"[!] Error deleting track {vid}: {e}")

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
                print(f"    [+] Inserted '{item_info['title']}'")
            except HttpError as e:
                if "quotaExceeded" in str(e):
                    print("\n[!] YouTube API daily quota limit reached (~200 updates/day).")
                    print("Run this script again tomorrow to continue!")
                    break
                print(f"    [!] Error inserting {vid_id}: {e}")

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
        except HttpError as e:
            if "quotaExceeded" in str(e):
                print("\n[!] YouTube API daily quota limit reached (~200 updates/day).")
                print("Run this script again tomorrow to continue!")
                break
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
    print(f"  * Deleted:     {deleted_count:4d} track(s)     ({delete_quota:5d} quota units)")
    print(f"  * Inserted:    {inserted_count:4d} track(s)     ({insert_quota:5d} quota units)")
    print(f"  * Reordered:   {moved_count:4d} track(s)     ({update_quota:5d} quota units)")
    print(f"  * Read/List:   {list_units:4d} request(s)   ({list_quota:5d} quota units)")
    print("-" * 58)
    print(f"  * Total Quota Used: {total_quota:5d} units (Daily limit: ~10,000)")
    print("=" * 58)
    print("[+] Playlist synchronization complete!\n")


def command_format(args, settings):
    """Formats and cleans a local playlist file, converting any URLs/raw IDs to '<video_id> | <title>'."""
    target_name = args.target.strip()
    safe_name = sanitize_filename(target_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        return

    print(f"[*] Reading and formatting '{file_path}'...")
    target_video_ids, target_video_titles, _ = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the file.")
        return

    # Check for missing titles and fetch them from YouTube API in batches of 50
    missing_ids = [v for v in target_video_ids if not target_video_titles.get(v)]
    quota_units = 0
    if missing_ids:
        print(f"[*] Fetching titles for {len(missing_ids)} tracks from YouTube API...")
        try:
            youtube = get_youtube_service()
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
    print(f"  * Tracks Normalized: {len(target_video_ids):4d}")
    print(f"  * Titles Fetched:    {len(missing_ids):4d}")
    print(f"  * API Quota Used:    {quota_units:4d} unit(s) (1 unit/batch of 50)")
    print("=" * 55 + "\n")


# --- CLI ENTRY POINT ---

def main():
    try:
        settings = load_settings()
        parser = argparse.ArgumentParser(
            description="YouTube Playlist CLI Manager - Reorder, sync, and manage YouTube playlists using local text files.",
            formatter_class=argparse.RawDescriptionHelpFormatter
        )
        subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

        # Command: link
        link_parser = subparsers.add_parser("link", help="Link a playlist alias to a YouTube Playlist ID or URL")
        link_parser.add_argument("name", help="Alias name (e.g. 'main', 'chill')")
        link_parser.add_argument("id", help="YouTube Playlist ID or full YouTube playlist URL")

        # Command: unlink
        unlink_parser = subparsers.add_parser("unlink", help="Unlink a playlist alias")
        unlink_parser.add_argument("name", help="Alias name to remove")

        # Command: list
        subparsers.add_parser("list", help="List all linked playlist aliases and configuration")

        # Command: pull
        pull_parser = subparsers.add_parser("pull", help="Pull remote YouTube playlist to local text file")
        pull_parser.add_argument("target", help="Playlist alias name or Playlist ID / URL")

        # Command: push
        push_parser = subparsers.add_parser("push", help="Push local text file layout to YouTube and normalize file")
        push_parser.add_argument("target", help="Playlist alias name or Playlist ID / URL")

        # Command: format
        format_parser = subparsers.add_parser("format", help="Format and normalize local playlist file (convert links to ID | Title)")
        format_parser.add_argument("target", help="Playlist alias name (file in playlists/ directory)")

        args = parser.parse_args()

        if not args.command:
            parser.print_help()
            sys.exit(0)

        if args.command == "link":
            command_link(args, settings)
        elif args.command == "unlink":
            command_unlink(args, settings)
        elif args.command == "list":
            command_list(args, settings)
        elif args.command == "pull":
            command_pull(args, settings)
        elif args.command == "push":
            command_push(args, settings)
        elif args.command == "format":
            command_format(args, settings)

    except KeyboardInterrupt:
        print("\n\n[!] Operation cancelled by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()