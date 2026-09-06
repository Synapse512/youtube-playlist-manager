"""
Google OAuth authentication, credentials management, and oauth-client selection.

Each JSON file in oauth-clients/ is just an OAuth client secret tied to a Google
Cloud project's API quota - it is NOT a fixed user identity. Multiple client
files can share the same project (and therefore the same quota), and any of
them can be used to log into ANY Google account. Because of this, no session
tokens are cached: every operation launches a fresh browser login so you can
pick whichever Google account you want for that run.
"""

import os
import sys

from .config import OAUTH_CLIENTS_DIR, SCOPES

try:
    import google_auth_oauthlib.flow
    import googleapiclient.discovery
    from googleapiclient.errors import HttpError
except ImportError:
    google_auth_oauthlib = None
    googleapiclient = None
    HttpError = Exception


def get_oauth_clients():
    """Returns sorted list of available oauth-client names from oauth-clients/ (filenames without .json extension)."""
    if not os.path.isdir(OAUTH_CLIENTS_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(OAUTH_CLIENTS_DIR)
        if f.endswith(".json") and not f.startswith(".")
    )


def resolve_oauth_client(client_name=None, allow_prompt=False):
    """
    Resolves which oauth-client JSON to use for this operation.
    - If client_name is provided, verifies that oauth-clients/<client_name>.json exists.
    - If only one oauth-client exists in oauth-clients/, auto-selects it.
    - If multiple oauth-clients exist and allow_prompt is True, interactively prompts
      the user to select one for this operation (nothing is remembered afterward).
    - If multiple oauth-clients exist and allow_prompt is False, exits with an error.
    """
    clients = get_oauth_clients()
    if not clients:
        print("\n" + "=" * 65)
        print(" ERROR: No OAuth client credentials found in 'oauth-clients/' directory.")
        print("=" * 65)
        print(f"  No .json credential files found in '{OAUTH_CLIENTS_DIR}/'.\n")
        print("Setup Instructions:")
        print("  1. Go to Google Cloud Console: https://console.cloud.google.com/")
        print("  2. Create a project and enable 'YouTube Data API v3'.")
        print("  3. Navigate to APIs & Services -> Credentials.")
        print("  4. Click 'Create Credentials' -> 'OAuth client ID'.")
        print("  5. Select Application Type: 'Desktop App'.")
        print(f"  6. Download the JSON file, rename it to '<name>.json',")
        print(f"     and place it inside the '{OAUTH_CLIENTS_DIR}/' folder.")
        print("=" * 65 + "\n")
        sys.exit(1)

    if client_name:
        secret_path = get_client_secret_path(client_name)
        if not os.path.exists(secret_path):
            print(f"[!] Error: OAuth client '{client_name}' not found.")
            print(f"    Expected file: '{secret_path}'")
            print(f"    Available oauth clients: {', '.join(clients)}")
            sys.exit(1)
        return client_name

    if len(clients) == 1:
        print(f"[*] Auto-selected oauth client: '{clients[0]}'")
        return clients[0]

    if allow_prompt:
        print(f"\n[?] Multiple oauth clients found. Which one do you want to use for this operation?")
        for idx, c in enumerate(clients, 1):
            print(f"    [{idx}] {c}")
        print()
        while True:
            try:
                choice = input(f"Select an oauth client (1-{len(clients)}) or type its name: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[!] Operation cancelled by user.")
                sys.exit(130)

            if not choice:
                continue

            if choice.isdigit():
                val = int(choice)
                if 1 <= val <= len(clients):
                    selected = clients[val - 1]
                    print(f"[+] Selected oauth client: '{selected}'")
                    return selected
            elif choice in clients:
                print(f"[+] Selected oauth client: '{choice}'")
                return choice

            print(f"[!] Invalid selection '{choice}'. Please enter a number between 1 and {len(clients)} or a valid client name.")

    print(f"\n[!] Error: Multiple oauth clients found but none specified.")
    print(f"    Available oauth clients: {', '.join(clients)}")
    print(f"    Use --client <name> to pick one for this run.")
    sys.exit(1)


def get_client_secret_path(client_name):
    """Returns the path to the OAuth client secret file for a given oauth-client name."""
    return os.path.join(OAUTH_CLIENTS_DIR, f"{client_name}.json")


def get_youtube_service(client_name):
    """
    Runs a fresh OAuth login using the given oauth-client secret and returns an
    initialized YouTube API service client.

    No session token is cached or reused - every call opens a browser window so
    you can log in with whichever Google account you want to use with this
    client's quota.
    """
    if googleapiclient is None or google_auth_oauthlib is None:
        print("\n[!] ERROR: Missing required Google API libraries.")
        print("    Please install dependencies with: pip install -r requirements.txt\n")
        sys.exit(1)

    secret_file = get_client_secret_path(client_name)

    if not os.path.exists(secret_file):
        print(f"\n[!] Error: Client secret file not found for oauth client '{client_name}'.")
        print(f"    Expected: '{secret_file}'")
        print(f"    Place the downloaded OAuth JSON in the '{OAUTH_CLIENTS_DIR}/' folder,")
        print(f"    named as '{client_name}.json'.")
        sys.exit(1)

    print(f"[*] Using oauth client '{client_name}': '{secret_file}'")
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

    print("[*] Initializing YouTube Data API v3 client...")
    try:
        service = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
        print("[+] YouTube API client connected successfully.")
        return service
    except Exception as e:
        print(f"[!] Error building YouTube service: {e}")
        sys.exit(1)
