"""
Google OAuth authentication, credentials management, and oauth-client selection.

Each JSON file in oauth-clients/ is just an OAuth client secret tied to a Google
Cloud project's API quota - it is NOT a fixed user identity. Multiple client
files can share the same project (and therefore the same quota), and any of
them can be used to log into ANY Google account.

Tokens are cached in data/tokens/ as '<client>_<email>.token.json', one file per
(oauth client, Google account) pair. The email is detected from Google right after
login (not typed by hand), so the filename always matches the account that actually
signed in. Each token file also records its client and email inside, which is how the
account picker lists the accounts cached for a client.

Choosing an account for a run (highest priority first):
  1. --account <email> on the command line
  2. 'account' key for the playlist in playlist-settings.toml
  3. Interactive picker: cached accounts for the chosen client, plus "log in with a
     different account".
"""

import json
import os
import sys

# oauthlib raises if Google returns scopes in a different order/set than requested
# (e.g. it adds 'openid'). We only care that the YouTube scope was granted.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from .config import OAUTH_CLIENTS_DIR, DATA_DIR, TOKENS_DIR, SCOPES, load_settings

try:
    import google.auth.transport.requests
    from google.oauth2.credentials import Credentials
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


def _normalize_account(account):
    return (account or "").strip().lower()


def get_token_path(client_name, account):
    """Returns the path of the cached token file '<client>_<email>.token.json' in data/tokens/."""
    os.makedirs(TOKENS_DIR, exist_ok=True)
    safe_account = _normalize_account(account)
    for ch in ("/", "\\", ":"):
        safe_account = safe_account.replace(ch, "-")
    return os.path.join(TOKENS_DIR, f"{client_name}_{safe_account}.token.json")


def _read_token_meta(path):
    """Returns (client, account) recorded inside a token file, or (None, None) if unreadable/legacy."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("ypm_client"), _normalize_account(data.get("ypm_account")) or None
    except Exception:
        return None, None


def list_cached_accounts(client_name):
    """Returns a sorted list of Google account emails that have a cached token for this oauth client."""
    if not os.path.isdir(TOKENS_DIR):
        return []
    accounts = set()
    for fname in os.listdir(TOKENS_DIR):
        if not fname.endswith(".token.json"):
            continue
        client, account = _read_token_meta(os.path.join(TOKENS_DIR, fname))
        if client == client_name and account:
            accounts.add(account)
    return sorted(accounts)


def _save_token(creds, client_name, account, cache_enabled=True):
    """Writes the credentials plus (client, account) metadata to the token file."""
    path = get_token_path(client_name, account)
    try:
        data = json.loads(creds.to_json())
        data["ypm_client"] = client_name
        data["ypm_account"] = _normalize_account(account)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path
    except OSError as e:
        print(f"[!] Warning: Could not cache token to '{path}': {e}")
        return None


def _detect_account_email(creds):
    """Asks Google which account these credentials belong to. Returns a lowercase email or None."""
    try:
        session = google.auth.transport.requests.AuthorizedSession(creds)
        resp = session.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=15)
        if resp.status_code == 200:
            return _normalize_account(resp.json().get("email")) or None
        print(f"[!] Warning: could not read the account email (HTTP {resp.status_code}).")
    except Exception as e:
        print(f"[!] Warning: could not read the account email: {e}")
    return None


def _prompt_for_account(client_name, cached):
    """
    Lets the user pick a cached account or choose to log in with a new one.
    Returns the chosen email, or None for 'log in with a different account'.
    """
    print(f"\n[?] Which Google account do you want to use with oauth client '{client_name}'?")
    for idx, acct in enumerate(cached, 1):
        print(f"    [{idx}] {acct}  (cached login)")
    new_idx = len(cached) + 1
    print(f"    [{new_idx}] Log in with a different account")
    print()
    while True:
        try:
            choice = input(f"Select (1-{new_idx}), type an email, or press Enter for 1: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[!] Operation cancelled by user.")
            sys.exit(130)
        if not choice:
            return cached[0]
        if choice.isdigit():
            val = int(choice)
            if 1 <= val <= len(cached):
                return cached[val - 1]
            if val == new_idx:
                return None
        elif choice.lower() in cached:
            return choice.lower()
        elif "@" in choice:
            return choice.lower()   # new account typed directly: used as login hint
        print(f"[!] Invalid selection '{choice}'.")


def _load_cached_credentials(client_name, account):
    """Loads and (if needed) refreshes the cached token. Returns valid creds or None."""
    token_path = get_token_path(client_name, account)
    if not os.path.exists(token_path):
        return None
    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            _save_token(creds, client_name, account)
            print(f"[+] Refreshed cached login for {account} ('{client_name}').")
        elif creds.valid:
            print(f"[+] Using cached login for {account} ('{client_name}').")
        return creds if creds.valid else None
    except Exception as e:
        print(f"[*] Cached login for {account} could not be refreshed ({e}). Re-authorizing...")
        return None


def _browser_login(client_name, secret_file, login_hint=None):
    """Runs the browser OAuth flow. Returns credentials."""
    print(f"[*] Using oauth client '{client_name}': '{secret_file}'")
    if login_hint:
        print(f"[*] Expected Google account: {login_hint}")
    print("[*] Launching browser for Google OAuth authorization...")
    print("    (Please log in and grant YouTube permissions in your browser window...)")
    flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
    kwargs = {"login_hint": login_hint, "prompt": "consent"} if login_hint else {"prompt": "select_account consent"}
    creds = flow.run_local_server(port=0, **kwargs)
    print("[+] OAuth authentication successful!")
    return creds


def get_youtube_service(client_name, account=None, allow_prompt=True):
    """
    Initializes a YouTube Data API service using the given oauth-client secret.

    account: Google account email to use (from --account or the playlist's 'account'
             setting). If omitted, cached accounts for this client are offered in a picker.
    Tokens are cached per (client, account email) in data/tokens/ when
    cache_oauth_tokens is enabled (default), so switching accounts never needs a new
    login for an account you've already used.
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

    settings = load_settings()
    cache_enabled = settings.get("cache_oauth_tokens", True)
    account = _normalize_account(account)

    if not account and cache_enabled and allow_prompt:
        cached = list_cached_accounts(client_name)
        if cached:
            account = _prompt_for_account(client_name, cached) or ""

    creds = None
    if account and cache_enabled:
        creds = _load_cached_credentials(client_name, account)

    if not creds:
        try:
            creds = _browser_login(client_name, secret_file, login_hint=account or None)
        except Exception as e:
            print(f"\n[!] Authentication failed: {e}")
            sys.exit(1)
        if cache_enabled:
            actual = _detect_account_email(creds)
            if actual:
                if account and actual != account:
                    print(f"[!] Warning: you signed in as {actual}, not {account}. Caching the login under {actual}.")
                saved = _save_token(creds, client_name, actual)
                if saved:
                    print(f"[+] Signed in as {actual}. Saved login to '{saved}'.")
            else:
                print("[!] Could not determine the account email, so this login was not cached.")

    print("[*] Initializing YouTube Data API v3 client...")
    try:
        service = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
        print("[+] YouTube API client connected successfully.")
        return service
    except Exception as e:
        print(f"[!] Error building YouTube service: {e}")
        sys.exit(1)
