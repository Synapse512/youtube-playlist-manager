"""
Google OAuth authentication, credentials management, and multi-user switching.
"""

import os
import sys

from .config import USERS_DIR, TOKENS_DIR, SCOPES

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
    RefreshError = Exception
    HttpError = Exception


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
