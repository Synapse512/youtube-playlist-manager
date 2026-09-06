# YouTube Playlist Manager

A CLI tool to manage, reorder, backup, and synchronize YouTube playlists locally using plain text files.

### 1. Installation  
install Python 3.6+ (tested on 3.14.2)
```bash
git clone https://github.com/Synapse512/youtube-playlist-manager
```
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
5. Download your credentials JSON file, rename it to something recognizable (e.g. `project-a.json`), and place it in the `oauth-clients/` folder.
6. Go to **OAuth consent screen -> Audience -> Test Users**
   - Add the Google account email(s) tied to the playlists you want to manage

### 3. OAuth Clients & Quota

An oauth-client JSON is just an OAuth client secret tied to a Google Cloud project's API quota — it is **not** a fixed user account. Each project gets its own daily quota, and any oauth-client file can be used to log into *any* Google account.

- You can create multiple oauth-client files under the **same** Google Cloud project — they all draw from that project's shared quota.
- You can also create oauth-client files under **different** projects to get separate, independent quota pools.
- If you only have **one** oauth-client file, it's auto-selected for every command.
- If you have **multiple** oauth-client files, you can specify which one to use in commands with the `--client` param. If you do not, you will be prompted to choose which file to select before running the command.

**No session tokens are cached.** Every command that talks to YouTube (`link`, `pull`, `push`, `format`) opens a fresh browser window so you can log in with whichever Google account you want, using whichever oauth-client's quota you want. Nothing is remembered between runs - if one project's quota runs out, just re-run the command with a different `--client` pointed at a project that still has quota left.

**Directory layout:**
```
youtube-playlist-manager/
├── main.py              <- CLI entry point
├── core/                <- Modular application logic
│   ├── config.py        <- Settings & data persistence
│   ├── auth.py          <- OAuth login & oauth-client selection
│   ├── parser.py        <- URL/ID regex & playlist file I/O
│   ├── sync.py          <- LIS minimal-moves reordering engine
│   ├── commands.py      <- Command handlers (pull, push, etc.)
│   └── ui.py            <- Dashboard menu & terminal formatting
├── oauth-clients/
│   ├── project-a.json   <- OAuth client secret (rename however you like)
│   └── project-b.json
├── data/
│   └── playlists.json   <- linked playlists and activity history
├── playlists/
│   ├── chill.txt        <- tracklist file
│   └── instrumental.txt
├── settings.json        <- user configuration preferences
└── logs/
    ├── chill.log        <- track changelog and operation history
    └── instrumental.log
```

### 4. Usage

| Command | Syntax | Description |
| --- | --- | --- |
| **menu** | `python main.py` | Opens menu to show a quick glance at recently edited playlists and command usage |
| **link** | `python main.py link <id_or_url> [--client <name>]` | Connects a YouTube Playlist ID or URL using the title fetched from YouTube |
| **unlink** | `python main.py unlink <name>` | Removes a linked playlist. |
| **list** | `python main.py list` | Displays all configured playlists and last CLI edit timestamp/command. |
| **pull** | `python main.py pull <name> [--client <name>]` | Downloads the live YouTube playlist into `playlists/<name>.txt`. |
| **push** | `python main.py push <name> [--client <name>]` | Pushes local `.txt` additions, deletions, and track order to YouTube and automatically formats URLs/IDs to `<video_id> \| <video_title>` format. |
| **format** | `python main.py format <name> [--client <name>]` | Normalizes URLs/IDs into `<video_id> \| <title>` format for readability. |
| **help** | `python main.py help` | Displays help information with all command usages. |

`--client` / `-c` picks which oauth-client JSON to use for that single run. If omitted, it's auto-selected when only one exists, or you'll be prompted to choose when there are several. This choice is never saved — you pick fresh every time.

### 5. Configuration (`settings.json`)

You can edit `settings.json` directly in any text editor to configure defaults:
- `"safety_check_before_push"`: `true` / `false` - Prompts for confirmation before pushing to YouTube to prevent overwriting recent changes.
- `"menu_playlist_count"`: `<number>` / `"all"` - How many recent playlists to show in the menu, or `"all"` to list all.
- `"menu_client_count"`: `<number>` / `"all"` - How many oauth clients to show in the menu, or `"all"` to list all.
- `"enable_logging"`: `true` / `false` - Records per-playlist operation history in `logs/<name>.log` tracking additions, removals, and changes.

### 6. Quota Information
YouTube Data API v3 has a daily default quota of **10,000 units per Google Cloud project**, which amounts to about 200 operations of inserting, deleting, or reordering in a playlist. For more information go to [here](https://developers.google.com/youtube/v3/determine_quota_cost)

| Operation | API Endpoint | Quota Cost |
| --- | --- | --- |
| **Read / List** | `playlistItems.list` | **1 unit** per 50-track page |
| **Title Fetch** | `videos.list` | **1 unit** per 50-track batch |
| **Insert Track** | `playlistItems.insert` | **50 units** per added video |
| **Delete Track** | `playlistItems.delete` | **50 units** per removed video |
| **Reorder Track** | `playlistItems.update` | **50 units** per shifted position |

---

Note: I made this project using Google Antigravity, since I basically know nothing about Python. Though, I figured since this is actually useful I'd upload it.
