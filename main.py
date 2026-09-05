import os
import sys
import json
import re
import argparse
import shutil
import bisect
from datetime import datetime
from urllib.parse import urlparse, parse_qs

# Configure safe utf-8 stdout/stderr and ANSI/OSC terminal support on Windows
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
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

VERSION = "1.0.0"

SETTINGS_FILE = "settings.json"
DATA_DIR = "data"
PLAYLISTS_DATA_FILE = os.path.join(DATA_DIR, "playlists.json")
USERS_DIR = "users"
TOKENS_DIR = os.path.join(DATA_DIR, "tokens")
PLAYLISTS_DIR = "playlists"
LOGS_DIR = "logs"

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# --- CONFIG & AUTH HELPERS ---

def load_settings():
    """Loads settings.json safely, recreating with defaults if missing or corrupted."""
    default_settings = {
        "safety_check_before_push": True,
        "menu_playlist_count": 3,
        "menu_user_count": 3,
        "enable_logging": True
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
            settings.setdefault("menu_playlist_count", 3)
            # Support migration from legacy show_accounts_in_menu
            if "menu_user_count" not in settings and "menu_account_count" not in settings:
                legacy_val = settings.get("show_accounts_in_menu")
                if legacy_val is False:
                    settings["menu_user_count"] = 0
                else:
                    settings["menu_user_count"] = 3
            settings.setdefault("menu_user_count", 3)
            settings.setdefault("enable_logging", True)
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
    # Ensure runtime data keys are not stored in settings.json
    clean_settings = {k: v for k, v in settings.items() if k not in ("playlists", "activity")}
    temp_file = f"{SETTINGS_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(clean_settings, f, indent=4)
        if os.path.exists(SETTINGS_FILE):
            os.replace(temp_file, SETTINGS_FILE)
        else:
            os.rename(temp_file, SETTINGS_FILE)
    except OSError as e:
        print(f"[!] Error saving settings to '{SETTINGS_FILE}': {e}")


def load_playlist_data(settings=None):
    """Loads data/playlists.json containing linked playlists and activity tracking, migrating from settings.json if needed."""
    os.makedirs(DATA_DIR, exist_ok=True)
    default_data = {
        "playlists": {},
        "activity": {}
    }

    # Auto-migration: if settings has legacy 'playlists' or 'activity', transfer them
    migrated = False
    legacy_playlists = {}
    legacy_activity = {}
    if settings is not None:
        if "playlists" in settings:
            legacy_playlists = settings.pop("playlists")
            migrated = True
        if "activity" in settings:
            legacy_activity = settings.pop("activity")
            migrated = True
        if migrated:
            save_settings(settings)

    if not os.path.exists(PLAYLISTS_DATA_FILE):
        default_data["playlists"].update(legacy_playlists)
        default_data["activity"].update(legacy_activity)
        save_playlist_data(default_data)
        return default_data

    try:
        with open(PLAYLISTS_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Playlist data file must contain a JSON object.")
            data.setdefault("playlists", {})
            data.setdefault("activity", {})
            if migrated:
                for k, v in legacy_playlists.items():
                    data["playlists"].setdefault(k, v)
                for k, v in legacy_activity.items():
                    data["activity"].setdefault(k, v)
                save_playlist_data(data)
            return data
    except (json.JSONDecodeError, ValueError, OSError) as e:
        backup_file = f"{PLAYLISTS_DATA_FILE}.corrupted.{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"
        print(f"[!] Warning: '{PLAYLISTS_DATA_FILE}' is invalid or corrupted ({e}).")
        print(f"[*] Backing up damaged playlist data to '{backup_file}' and resetting.")
        try:
            shutil.copyfile(PLAYLISTS_DATA_FILE, backup_file)
        except OSError:
            pass
        save_playlist_data(default_data)
        return default_data


def save_playlist_data(data):
    """Saves playlist data dictionary to data/playlists.json atomically."""
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_file = f"{PLAYLISTS_DATA_FILE}.tmp"
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        if os.path.exists(PLAYLISTS_DATA_FILE):
            os.replace(temp_file, PLAYLISTS_DATA_FILE)
        else:
            os.rename(temp_file, PLAYLISTS_DATA_FILE)
    except OSError as e:
        print(f"[!] Error saving playlist data to '{PLAYLISTS_DATA_FILE}': {e}")


def check_tokens_migration():
    """Migrates cached OAuth tokens from legacy 'tokens/' directory to 'data/tokens/'."""
    old_tokens_dir = "tokens"
    if os.path.isdir(old_tokens_dir) and os.path.abspath(old_tokens_dir) != os.path.abspath(TOKENS_DIR):
        os.makedirs(TOKENS_DIR, exist_ok=True)
        for fname in os.listdir(old_tokens_dir):
            if fname.endswith(".json") and not fname.startswith("."):
                old_path = os.path.join(old_tokens_dir, fname)
                new_path = os.path.join(TOKENS_DIR, fname)
                if not os.path.exists(new_path):
                    try:
                        shutil.move(old_path, new_path)
                    except OSError:
                        pass


def get_users():
    """Returns sorted list of available usernames from users/ (filenames without .json extension)."""
    if not os.path.isdir(USERS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(USERS_DIR)
        if f.endswith(".json") and not f.startswith(".")
    )


def resolve_user(username=None, allow_prompt=False):
    """
    Resolves which user account to use.
    - If username is provided, verifies that users/<username>.json exists.
    - If only one user exists in users/, auto-selects that user.
    - If multiple users exist and allow_prompt is True, interactively prompts the user to select an account.
    - If multiple users exist and allow_prompt is False, exits with an error and instructions.
    """
    users = get_users()
    if not users:
        print("\n" + "=" * 65)
        print(" ERROR: No user credentials found in 'users/' directory.")
        print("=" * 65)
        print(f"  No .json credential files found in '{USERS_DIR}/'.\n")
        print("Setup Instructions:")
        print("  1. Go to Google Cloud Console: https://console.cloud.google.com/")
        print("  2. Create a project and enable 'YouTube Data API v3'.")
        print("  3. Navigate to APIs & Services -> Credentials.")
        print("  4. Click 'Create Credentials' -> 'OAuth client ID'.")
        print("  5. Select Application Type: 'Desktop App'.")
        print(f"  6. Download the JSON file, rename it to '<username>.json',")
        print(f"     and place it inside the '{USERS_DIR}/' folder.")
        print("=" * 65 + "\n")
        sys.exit(1)

    if username:
        secret_path = get_client_secret_path(username)
        if not os.path.exists(secret_path):
            print(f"[!] Error: User '{username}' not found.")
            print(f"    Expected file: '{secret_path}'")
            print(f"    Available users: {', '.join(users)}")
            sys.exit(1)
        return username

    if len(users) == 1:
        print(f"[*] Auto-selected user account: '{users[0]}'")
        return users[0]

    if allow_prompt:
        print(f"\n[?] Multiple user accounts found. Which account is this playlist connected to?")
        for idx, u in enumerate(users, 1):
            print(f"    [{idx}] {u}")
        print()
        while True:
            try:
                choice = input(f"Select an account (1-{len(users)}) or type username: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[!] Operation cancelled by user.")
                sys.exit(130)

            if not choice:
                continue

            if choice.isdigit():
                val = int(choice)
                if 1 <= val <= len(users):
                    selected = users[val - 1]
                    print(f"[+] Selected account: '{selected}'")
                    return selected
            elif choice in users:
                print(f"[+] Selected account: '{choice}'")
                return choice

            print(f"[!] Invalid selection '{choice}'. Please enter a number between 1 and {len(users)} or a valid username.")

    print(f"\n[!] Error: Multiple user accounts found but no user specified.")
    print(f"    Available users: {', '.join(users)}")
    print(f"    Use --user <username>, or add '# user: <name>' to your playlist file.")
    sys.exit(1)


def get_token_path(username):
    """Returns the path to the cached OAuth token file for a given user."""
    return os.path.join(TOKENS_DIR, f"{username}.json")


def get_client_secret_path(username):
    """Returns the path to the OAuth client secret file for a given user."""
    return os.path.join(USERS_DIR, f"{username}.json")


def get_youtube_service(username):
    """Authenticates the given user and returns an initialized YouTube API service client."""
    if googleapiclient is None or google_auth_oauthlib is None:
        print("\n[!] ERROR: Missing required Google API libraries.")
        print("    Please install dependencies with: pip install -r requirements.txt\n")
        sys.exit(1)

    token_file = get_token_path(username)
    secret_file = get_client_secret_path(username)

    creds = None

    # Step 1: Check existing cached token
    if os.path.exists(token_file):
        print(f"[*] Checking cached credentials for '{username}' in '{token_file}'...")
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception as e:
            print(f"[!] Warning: Could not read '{token_file}' ({e}). Re-authenticating...")
            creds = None

    # Step 2: Validate or refresh credentials
    if creds and creds.valid:
        print(f"[+] Session token for '{username}' is valid.")
    else:
        if creds and creds.expired and creds.refresh_token:
            print(f"[*] Session token for '{username}' has expired. Attempting to refresh with Google OAuth...")
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
            if not os.path.exists(secret_file):
                print(f"\n[!] Error: Client secret file not found for user '{username}'.")
                print(f"    Expected: '{secret_file}'")
                print(f"    Place the downloaded OAuth JSON in the '{USERS_DIR}/' folder,")
                print(f"    named as '{username}.json'.")
                sys.exit(1)

            print(f"[*] Found client secret for '{username}': '{secret_file}'")
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
        os.makedirs(TOKENS_DIR, exist_ok=True)
        try:
            with open(token_file, "w", encoding="utf-8") as token:
                token.write(creds.to_json())
            print(f"[+] Saved updated session token to '{token_file}'.")
        except OSError as e:
            print(f"[!] Warning: Could not save session token to '{token_file}': {e}")

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


def record_activity(playlist_data, target, command_name):
    """Records CLI activity (last command, timestamp, interaction count) for a playlist."""
    if playlist_data is None:
        playlist_data = load_playlist_data()
    playlist_name = get_playlist_name_for_target(target, playlist_data)
    if not playlist_name:
        return
    activity = playlist_data.setdefault("activity", {})
    entry = activity.setdefault(playlist_name, {
        "last_command": command_name,
        "last_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": 0
    })
    entry["last_command"] = command_name
    entry["last_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry["count"] = entry.get("count", 0) + 1
    save_playlist_data(playlist_data)


def log_playlist_event(settings, target, operation, user, summary_lines=None, detail_lines=None, playlist_data=None):
    """Appends a structured log entry to logs/<name>.log if logging is enabled."""
    if not settings.get("enable_logging", True):
        return
    playlist_name = get_playlist_name_for_target(target, playlist_data)
    if not playlist_name:
        return

    safe_name = sanitize_filename(playlist_name)
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_path = os.path.join(LOGS_DIR, f"{safe_name}.log")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"=== [{timestamp}] {operation.upper()} (user: {user or 'unknown'}) ==="

    lines = [header]
    if summary_lines:
        for s in summary_lines:
            lines.append(f"  Summary: {s}")
    if detail_lines:
        for d in detail_lines:
            lines.append(f"  {d}")
    lines.append("")  # Blank line separator

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        print(f"[!] Warning: Could not write to log file '{log_path}': {e}")


def parse_playlist_file(file_path):
    """
    Parses a local playlist text file.
    Supports lines formatted as:
      - '<video_id> | <title>'
      - '<video_url> | <title>'
      - '<video_id>'
      - '<video_url>'
    Recognizes directive comments:
      - '# user: <username>'  — specifies which account credentials to use
    Ignores empty lines and all other comments (lines starting with '#').
    Returns (target_video_ids, target_video_titles, skipped_lines, file_user).
    """
    target_video_ids = []
    target_video_titles = {}
    skipped_lines = 0
    file_user = None

    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            # Detect special directive comments
            if line.lower().startswith("# user:"):
                file_user = line[7:].strip()
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

    return target_video_ids, target_video_titles, skipped_lines, file_user


def save_playlist_file(file_path, video_ids, video_titles, username=None):
    """
    Rewrites the local playlist text file atomically with normalized format:

    # user: <username>         (only when username is provided)
    # PULL BEFORE MAKING CHANGES

    <video_id> | <video_title>
    """
    lines = []
    if username:
        lines.append(f"# user: {username}")
    lines += ["# PULL BEFORE MAKING CHANGES", ""]
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


def read_playlist_user(file_path):
    """Reads only the '# user:' directive from a playlist file without full parsing."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("# user:"):
                    return line[7:].strip()
                # Stop early once we reach actual video data
                if line and not line.startswith("#"):
                    break
    except OSError:
        pass
    return None


# --- CORE COMMANDS ---

def command_link(args, settings, playlist_data):
    """Links a YouTube playlist to data/playlists.json using the title fetched from YouTube."""
    raw_input = getattr(args, "target", None) or getattr(args, "name", None) or getattr(args, "id", None)
    if not raw_input:
        print("[!] Error: Missing Playlist ID or URL. Syntax: python main.py link <id_or_url> [--user <username>]\n")
        print_help()
        return

    # Resolve user first (prompts to select an account if multiple users exist and --user is omitted)
    username = resolve_user(getattr(args, "user", None), allow_prompt=True)

    raw_id = raw_input.strip()
    playlist_id = extract_playlist_id(raw_id)
    if not playlist_id:
        print(f"[!] Error: Could not extract a valid Playlist ID from '{raw_id}'.")
        return

    print(f"[*] Fetching playlist title from YouTube ({playlist_id})...")
    youtube = get_youtube_service(username)
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

    # Create initial playlist file with user header and reminder
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{playlist_name}.txt")
    if not os.path.exists(file_path):
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"# user: {username}\n# PULL BEFORE MAKING CHANGES\n\n")
            print(f"[+] Created initial playlist file '{file_path}'.")
        except OSError as e:
            print(f"[!] Warning: Could not create initial file '{file_path}': {e}")

    print(f"[+] Successfully linked '{playlist_name}' -> Playlist ID: '{playlist_id}' (user: {username})")
    record_activity(playlist_data, playlist_name, "link")
    log_playlist_event(
        settings,
        playlist_name,
        "link",
        username,
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


def terminal_link(text, url):
    """Formats text as a clickable OSC 8 terminal hyperlink."""
    return f"\033]8;;{url}\033\\{text}\033]8;;\033\\"


def command_list(args, settings, playlist_data):
    """Lists all configured playlists with user account and last edit info."""
    playlists = playlist_data.get("playlists", {})
    activity = playlist_data.get("activity", {})

    if not playlists:
        print("No playlists configured. Use 'python main.py link <id_or_url>'")
        return

    print("Configured Playlists:")
    for name, pid in playlists.items():
        url = f"https://www.youtube.com/playlist?list={pid}"
        safe_name = sanitize_filename(name)
        file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")
        file_user = read_playlist_user(file_path) if os.path.exists(file_path) else None
        user_tag = f"  [{file_user}]" if file_user else ""

        act = activity.get(name, {})
        last_cmd = act.get("last_command")
        last_time = act.get("last_time")
        if last_cmd and last_time:
            last_info = f"  (last: {last_cmd} on {last_time})"
        else:
            last_info = "  (no CLI edits yet)"

        print(f"  {name}{user_tag}  [{terminal_link(pid, url)}]{last_info}")


def find_lis_indices(arr):
    """
    Computes the set of indices corresponding to a Longest Increasing Subsequence in arr.
    Uses O(N log N) patience sorting with predecessor tracking.
    """
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


def command_pull(args, settings, playlist_data):
    """Pulls a remote YouTube playlist into a local text file."""
    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, playlist_data)
    safe_name = sanitize_filename(target_name)

    os.makedirs(PLAYLISTS_DIR, exist_ok=True)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    # Resolve user: explicit --user flag > # user: header in existing file > auto-detect / interactive prompt
    file_user = read_playlist_user(file_path) if os.path.exists(file_path) else None
    username = resolve_user(getattr(args, "user", None) or file_user, allow_prompt=True)

    if playlist_id != target_name:
        print(f"[*] Target playlist '{target_name}' resolved to Playlist ID: {playlist_id}")
    else:
        print(f"[*] Using Playlist ID: {playlist_id}")

    youtube = get_youtube_service(username)
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
    lines_to_write = [f"# user: {username}", "# PULL BEFORE MAKING CHANGES", ""] + [x[1] for x in raw_items]

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines_to_write) + "\n")
    except OSError as e:
        print(f"[!] Error writing playlist file '{file_path}': {e}")
        return

    print("\n" + "=" * 55)
    print(" Pull Summary")
    print("=" * 55)
    print(f"  * User:               {username}")
    print(f"  * Tracks Fetched:  {len(raw_items):4d}")
    print(f"  * Pages Read:      {page_num - 1:4d} request(s)")
    print(f"  * API Quota Used:  {quota_units:4d} unit(s) (1 unit/page)")
    print("=" * 55)
    print(f"[+] Successfully pulled {len(raw_items)} tracks to '{file_path}'!\n")
    record_activity(playlist_data, target_name, "pull")
    log_playlist_event(
        settings,
        target_name,
        "pull",
        username,
        summary_lines=[
            f"Fetched {len(raw_items)} track(s) across {page_num - 1} page(s) ({quota_units} quota units)"
        ],
        playlist_data=playlist_data
    )


def command_push(args, settings, playlist_data):
    """Pushes the local text file track order and changes to YouTube, then normalizes the local file."""
    target_name = args.target.strip()
    playlist_id = resolve_playlist_id(target_name, playlist_data)
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
    target_video_ids, target_video_titles, skipped, file_user = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the local text file. Push aborted.")
        return

    print(f"[+] Parsed {len(target_video_ids)} valid tracks from local file.")

    # Resolve user: explicit --user flag > # user: header in file > auto-detect / interactive prompt
    username = resolve_user(getattr(args, "user", None) or file_user, allow_prompt=True)

    youtube = get_youtube_service(username)
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
            moved_details.append(f"~ Reordered: '{vid_id}' | {item_info['title']} (pos {from_idx} -> pos {target_pos})")
        except HttpError as e:
            if "quotaExceeded" in str(e):
                print("\n[!] YouTube API daily quota limit reached")
                print("Wait for your quota to reset before running again")
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

    if save_playlist_file(file_path, target_video_ids, resolved_titles, username):
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
    print(f"  * User:           {username}")
    print(f"  * Deleted:     {deleted_count:4d} track(s)     ({delete_quota:5d} quota units)")
    print(f"  * Inserted:    {inserted_count:4d} track(s)     ({insert_quota:5d} quota units)")
    print(f"  * Reordered:   {moved_count:4d} track(s)     ({update_quota:5d} quota units)")
    print(f"  * Read/List:   {list_units:4d} request(s)   ({list_quota:5d} quota units)")
    print("-" * 58)
    print(f"  * Total Quota Used: {total_quota:5d} units (Daily limit: ~10,000)")
    print("=" * 58)
    print("[+] Playlist synchronization complete!\n")
    record_activity(playlist_data, target_name, "push")
    diff_details = deleted_details + inserted_details + moved_details
    log_playlist_event(
        settings,
        target_name,
        "push",
        username,
        summary_lines=[
            f"{inserted_count} inserted, {deleted_count} deleted, {moved_count} reordered ({total_quota} quota units)"
        ],
        detail_lines=diff_details if diff_details else ["No changes required (already in sync)"],
        playlist_data=playlist_data
    )


def command_format(args, settings, playlist_data):
    """Formats and cleans a local playlist file, converting any URLs/raw IDs to '<video_id> | <title>'."""
    target_name = args.target.strip()
    safe_name = sanitize_filename(target_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        return

    print(f"[*] Reading and formatting '{file_path}'...")
    target_video_ids, target_video_titles, _, file_user = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the file.")
        return

    # Resolve user: explicit --user flag > # user: header in file > auto-detect / interactive prompt
    username = resolve_user(getattr(args, "user", None) or file_user, allow_prompt=True)

    # Check for missing titles and fetch them from YouTube API in batches of 50
    missing_ids = [v for v in target_video_ids if not target_video_titles.get(v)]
    quota_units = 0
    if missing_ids:
        print(f"[*] Fetching titles for {len(missing_ids)} tracks from YouTube API...")
        try:
            youtube = get_youtube_service(username)
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

    if save_playlist_file(file_path, target_video_ids, resolved_titles, username):
        print(f"[+] Successfully formatted '{file_path}' ({len(target_video_ids)} tracks normalized).")

    print("\n" + "=" * 55)
    print(" Format Summary")
    print("=" * 55)
    print(f"  * User:                 {username}")
    print(f"  * Tracks Normalized: {len(target_video_ids):4d}")
    print(f"  * Titles Fetched:    {len(missing_ids):4d}")
    print(f"  * API Quota Used:    {quota_units:4d} unit(s) (1 unit/batch of 50)")
    print("=" * 55 + "\n")
    record_activity(playlist_data, target_name, "format")
    log_playlist_event(
        settings,
        target_name,
        "format",
        username,
        summary_lines=[
            f"Normalized {len(target_video_ids)} track(s), fetched {len(missing_ids)} missing title(s) ({quota_units} quota units)"
        ],
        playlist_data=playlist_data
    )


# --- CLI MENU & HELP ---

def show_menu(settings, playlist_data, parser=None):
    """Displays the welcome menu with recent playlists and quick actions."""
    playlists = playlist_data.get("playlists", {})
    activity = playlist_data.get("activity", {})

    print("\n" + "=" * 70)
    print(f"ypm - Dashboard - V{VERSION}".center(70))
    print("=" * 70)

    raw_playlist_limit = settings.get("menu_playlist_count", 3)
    if isinstance(raw_playlist_limit, str) and raw_playlist_limit.strip().lower() == "all":
        menu_limit = None
    else:
        try:
            menu_limit = max(0, int(raw_playlist_limit))
        except (ValueError, TypeError):
            menu_limit = 3

    # Rank playlists by interaction count (descending), then by last_time (descending)
    ranked_playlists = sorted(
        playlists.keys(),
        key=lambda a: (
            activity.get(a, {}).get("count", 0),
            activity.get(a, {}).get("last_time", "")
        ),
        reverse=True
    )
    recent_playlists = ranked_playlists if menu_limit is None else ranked_playlists[:menu_limit]

    has_shown_section = False

    if menu_limit != 0:
        print("\n  [*] Recent Playlists:")
        has_shown_section = True
        if not playlists:
            print("      No playlists configured yet.\n")
        else:
            for idx, p_name in enumerate(recent_playlists, 1):
                pid = playlists[p_name]
                url = f"https://www.youtube.com/playlist?list={pid}"
                safe_name = sanitize_filename(p_name)
                file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")
                file_user = read_playlist_user(file_path) if os.path.exists(file_path) else None
                user_tag = f"  [{file_user}]" if file_user else ""

                act = activity.get(p_name, {})
                last_cmd = act.get("last_command")
                last_time = act.get("last_time")
                if last_cmd and last_time:
                    last_edit_str = f"{last_cmd} on {last_time}"
                else:
                    last_edit_str = "None recorded"

                print(f"    {idx}. {p_name} [{terminal_link(pid, url)}]{user_tag}")
                print(f"       • most recent edit: {last_edit_str}")
                print()

    raw_user_limit = settings.get("menu_user_count", settings.get("menu_account_count", settings.get("show_accounts_in_menu", 3)))

    if raw_user_limit is False or str(raw_user_limit).strip().lower() in ("false", "no"):
        user_limit = 0
    elif isinstance(raw_user_limit, str) and raw_user_limit.strip().lower() == "all":
        user_limit = None
    elif raw_user_limit is True or str(raw_user_limit).strip().lower() in ("true", "yes"):
        user_limit = 3
    else:
        try:
            user_limit = max(0, int(raw_user_limit))
        except (ValueError, TypeError):
            user_limit = 3

    users = get_users()
    if user_limit is not None:
        displayed_users = users[:user_limit]
    else:
        displayed_users = users

    if user_limit != 0:
        leading_newline = "" if has_shown_section else "\n"
        print(f"{leading_newline}  [*] Accounts:")
        has_shown_section = True
        if displayed_users:
            for u in displayed_users:
                print(f"      • {u}")
            print()
        else:
            print("      No accounts found in 'users/'.\n")

    cmd_leading_newline = "" if has_shown_section else "\n"
    print(f"{cmd_leading_newline}  [?] Available Commands:")
    print("      python main.py pull <name>        Download playlist to local file")
    print("      python main.py push <name>        Push changes and sync to YouTube")
    print("      python main.py format <name>      Normalize track IDs and titles")
    print("      python main.py list               List all configured playlists")
    print("      python main.py link <id_or_url>   Link a new playlist (uses YouTube title)")
    print("      python main.py unlink <name>      Remove a playlist link")
    print("      python main.py help               Show full documentation and flags")
    print("=" * 70 + "\n")


def print_help():
    """Prints complete CLI usage and all command descriptions."""
    help_text = f"""YouTube Playlist Manager v{VERSION}
A CLI tool to manage, reorder, backup, and synchronize YouTube playlists locally using plain text files.

Usage:
  python main.py <command> [arguments]

Multi-Account Setup:
  Place each user's OAuth client secret JSON in the 'users/' folder, renamed to '<username>.json'.
  Example: users/dalton.json, users/john.json
  Cached session tokens are stored automatically in 'data/tokens/<username>.json'.
  Playlist files record their account with a '# user: <username>' header line.

Configuration (settings.json):
  Edit 'settings.json' in any text editor to customize tool behavior:
  - "safety_check_before_push": true | false
      Prompts for confirmation before pushing to YouTube to prevent accidental overwrites. (Default: true)
  - "menu_playlist_count": <number> | "all"
      Number of playlists to show in the menu, or "all" to show all playlists. (Default: 3)
  - "menu_user_count": <number> | "all"
      Number of user accounts to show in the menu, or "all" to show all accounts. (Default: 3)
  - "enable_logging": true | false
      Records operation logs in 'logs/<name>.log' tracking additions, removals, and changes. (Default: true)

Commands:
  menu    python main.py
          Displays the welcome menu, recent playlists, and configured accounts.

  link    python main.py link <id_or_url> [--user <username>]
          Connects a YouTube Playlist ID or URL using the title fetched from YouTube.
          Creates a playlist file with '# user: <username>' header (prompts to choose account
          if multiple exist and --user is omitted).

  unlink  python main.py unlink <name>
          Removes a linked playlist.

  list    python main.py list
          Displays all configured playlists with their associated user accounts and last CLI edit info.

  pull    python main.py pull <name> [--user <username>]
          Downloads the live YouTube playlist into playlists/<name>.txt.
          User is read from the file's '# user:' header if not specified.

  push    python main.py push <name> [--user <username>]
          Pushes local .txt additions, deletions, and track order to YouTube and
          automatically formats URLs/IDs to <video_id> | <video_title> format.
          User is read from the file's '# user:' header if not specified.

  format  python main.py format <name> [--user <username>]
          Normalizes URLs/IDs into <video_id> | <title> format for readability.
          User is read from the file's '# user:' header if not specified.

  help    python main.py help
          Displays this help message with all command usages.

Options:
  --user, -u  Specify the username (must match a file in users/<username>.json).
              If omitted: auto-selected if 1 user exists, or prompted if multiple exist.
  -h, --help  Print help
"""
    print(help_text)


def dispatch_command(args, settings, playlist_data, parser=None):
    """Dispatches parsed arguments to the corresponding command handler."""
    if not args or not getattr(args, "command", None):
        return

    if getattr(args, "help", False) or args.command == "help":
        print_help()
        return

    if args.command == "menu":
        show_menu(settings, playlist_data, parser)
    elif args.command == "link":
        target = getattr(args, "target", None) or getattr(args, "name", None)
        if not target:
            print("[!] Error: Missing Playlist ID or URL. Syntax: python main.py link <id_or_url> [--user <username>]\n")
            print_help()
            sys.exit(1)
        command_link(args, settings, playlist_data)
    elif args.command == "unlink":
        if not args.name:
            print("[!] Error: Missing playlist name. Syntax: python main.py unlink <name>\n")
            print_help()
            sys.exit(1)
        command_unlink(args, settings, playlist_data)
    elif args.command == "list":
        command_list(args, settings, playlist_data)
    elif args.command == "pull":
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py pull <name> [--user <username>]\n")
            print_help()
            sys.exit(1)
        command_pull(args, settings, playlist_data)
    elif args.command == "push":
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py push <name> [--user <username>]\n")
            print_help()
            sys.exit(1)
        command_push(args, settings, playlist_data)
    elif args.command == "format":
        if not args.target:
            print("[!] Error: Missing target. Syntax: python main.py format <name> [--user <username>]\n")
            print_help()
            sys.exit(1)
        command_format(args, settings, playlist_data)


# --- ENTRY POINT ---

def main():
    try:
        settings = load_settings()
        playlist_data = load_playlist_data(settings)
        check_tokens_migration()

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
        link_parser.add_argument("--user", "-u", default=None, metavar="USERNAME")
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
        pull_parser.add_argument("--user", "-u", default=None, metavar="USERNAME")
        pull_parser.add_argument("-h", "--help", action="store_true")

        # Command: push
        push_parser = subparsers.add_parser("push", add_help=False)
        push_parser.add_argument("target", nargs="?")
        push_parser.add_argument("--user", "-u", default=None, metavar="USERNAME")
        push_parser.add_argument("-h", "--help", action="store_true")

        # Command: format
        format_parser = subparsers.add_parser("format", add_help=False)
        format_parser.add_argument("target", nargs="?")
        format_parser.add_argument("--user", "-u", default=None, metavar="USERNAME")
        format_parser.add_argument("-h", "--help", action="store_true")

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