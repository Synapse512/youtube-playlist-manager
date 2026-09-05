# YouTube Playlist Manager

A CLI tool to manage, reorder, backup, and synchronize YouTube playlists locally using plain text files.

### 1. Installation
```bash
pip install -r requirements.txt
```

### 2. Google Cloud Setup
1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Enable the [YouTube Data API v3](https://console.cloud.google.com/marketplace/product/google/youtube.googleapis.com).
4. Go to **APIs & Services > Credentials**:
   - Click **Create Credentials** -> **OAuth client ID**.
   - Set Application type to **Desktop App**.
5. Download your credentials JSON file, rename it to `<username>.json`, and place it in the `users/` folder (e.g. `users/alice.json`).
6. Go to **OAuth consent screen -> Audience -> Test Users**
   - Add the Google account email tied to the playlists you want to manage

### 3. Multiple Accounts

The tool supports any number of Google accounts simultaneously. Each account gets its own credential file in `users/` and its OAuth session token is cached separately in `data/tokens/`.

**Directory layout:**
```
youtube-playlist-manager/
├── users/
│   ├── john.json        ← renamed client_secret files (one per account)
│   └── dalton.json
├── data/
│   ├── tokens/
│   │   ├── john.json    ← cached OAuth tokens (auto-generated, never edit)
│   │   └── dalton.json
│   └── playlists.json   ← linked playlists and activity history
├── playlists/
│   ├── chill.txt        ← tracklist with user name header
│   └── instrumental.txt
├── settings.json        ← user configuration preferences
└── logs/
    ├── chill.log        ← track changelog and operation history
    └── instrumental.log
```

**How accounts are automatically used**   
The account a playlist is tied to is found through the `# user: <username>` header at the top of the playlist `.txt` file:
- If you only have **one user**, it is auto-selected when linking without needing `--user`.
- If you have **multiple users** and omit `--user` when linking, the CLI interactively prompts you to choose which account the playlist is connected to.

The `# user:` header is written automatically when you `link` or `pull` a playlist - you don't need to add it manually.

After linking, all `pull`, `push`, and `format` operations on that playlist will automatically use the correct account without needing `--user` again.

### 4. Usage

| Command | Syntax | Description |
| --- | --- | --- |
| **menu** | `python main.py` | Opens menu to show a quick glance at recently edited playlists and command usage |
| **link** | `python main.py link <id_or_url> [--user <username>]` | Connects a YouTube Playlist ID or URL using the title fetched from YouTube |
| **unlink** | `python main.py unlink <name>` | Removes a linked playlist. |
| **list** | `python main.py list` | Displays all configured playlists, associated accounts, and last CLI edit timestamp/command. |
| **pull** | `python main.py pull <name> [--user <username>]` | Downloads the live YouTube playlist into `playlists/<name>.txt`. |
| **push** | `python main.py push <name> [--user <username>]` | Pushes local `.txt` additions, deletions, and track order to YouTube and automatically formats URLs/IDs to `<video_id> \| <video_title>` format. |
| **format** | `python main.py format <name> [--user <username>]` | Normalizes URLs/IDs into `<video_id> \| <title>` format for readability. |
| **help** | `python main.py help` | Displays help information with all command usages. |

### 5. Configuration (`settings.json`)

You can edit `settings.json` directly in any text editor to configure defaults:
- `"safety_check_before_push"`: `true` / `false` — Prompts for confirmation before pushing to YouTube to prevent overwriting recent changes.
- `"menu_playlist_count"`: `<number>` / `"all"` — How many recent playlists to show in the menu, or `"all"` to list all.
- `"menu_user_count"`: `<number>` / `"all"` — How many user accounts to show in the menu, or `"all"` to list all.
- `"enable_logging"`: `true` / `false` — Records per-playlist operation history in `logs/<name>.log` tracking additions, removals, and changes.

### 6. Quota Information
YouTube Data API v3 has a daily default quota of **10,000 units**, which amounts to about 200 operations of inserting and deleting from a playlist every day.

| Operation | API Endpoint | Quota Cost |
| --- | --- | --- |
| **Read / List** | `playlistItems.list` | **1 unit** per 50-track page |
| **Title Fetch** | `videos.list` | **1 unit** per 50-track batch |
| **Insert Track** | `playlistItems.insert` | **50 units** per added video |
| **Delete Track** | `playlistItems.delete` | **50 units** per removed video |
| **Reorder Track** | `playlistItems.update` | **50 units** per shifted position |

---

Note: I made this project using Google Antigravity, since I basically know nothing about Python. Though, I figured since this is actually useful I'd upload it.
