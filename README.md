# YouTube Playlist Manager `(ypm)`

**CLI tool for advanced management of local and online YouTube playlists**

### Why use ypm?
YouTube's web interface makes managing large playlists painful - reordering hundreds of tracks requires endless scrolling and dragging, bulk-editing doesn't exist, and organizing by genre, artist, mood, etc. takes hours of manual work.

**ypm solves this by letting you edit playlists through text files** You can simply use the `link` command + a YT playlist id to pull every video from a playlist into a text file, and from there let `ypm` sync any changes made to it bidirectionally between your local file downloads and YouTube. 

Some examples of using ypm include:
- running the `ai-format` command to organize your **big playlist** into **smaller playlists/sections**
- using the `push` command to **update your online playlists** with a new order without manual dragging
- running the `download` command to automatically **download the latest changes** on an online YT playlist
- using the auto-generated text files as backups to **easily reconstruct entire YouTube playlists** online and/or locally
- easily **customizing `ypm` and playlist management** through settings, with things like download layouts, formats, storage locations, etc.

---

- [INSTALLATION](#installation)
  - [Requirements](#requirements)
  - [Quick start](#quick-start)
- [PROJECT LAYOUT](#project-layout)
- [COMMANDS](#commands)
- [TYPICAL WORKFLOW](#typical-workflow)
  - [Managing a playlist](#managing-a-playlist)
  - [Organizing and Formatting with AI](#organizing-and-formatting-with-ai)
  - [Downloading a playlist](#downloading-a-playlist)
  - [Bypassing YouTube Bot Detection & Cookies](#bypassing-youtube-bot-detection--cookies)
- [CONFIGURATION](#configuration)
  - [Managing Settings (`config` command)](#managing-settings-config-command)
  - [Global settings - `settings.toml`](#global-settings--settingstoml)
  - [Playlist settings - `playlist-settings.toml`](#playlist-settings--playlist-settingstoml)
    - [All keys](#all-keys)
    - [`download_mode` explained](#download_mode-explained)
    - [Using `oauth_client` and `account`](#using-oauth_client-and-account)
- [GOOGLE CLOUD SETUP](#google-cloud-setup)
  - [Setup steps](#setup-steps)
  - [OAuth clients and quota](#oauth-clients-and-quota)
  - [Quota info](#quota-info)

---

## INSTALLATION

### Requirements

- Python 3.11+ (tested on 3.14.2)
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) - needed for `pull`, `link`, `format`, and `download` on public/unlisted playlists
- [`ffmpeg`](https://ffmpeg.org/) - needed for audio conversion in `download`

### Quick start

```bash
git clone https://github.com/Synapse512/youtube-playlist-manager
cd youtube-playlist-manager
pip install -r requirements.txt
```

No Google Cloud setup is required if you only want to download and manage playlists locally. Link a playlist and go:

```bash
python main.py link https://www.youtube.com/playlist?list=<id>
python main.py pull
python main.py download
```

Google Cloud only comes into play if you want to push local edits back to YouTube or work with a private playlist. See [Google Cloud Setup](#google-cloud-setup).

---

## PROJECT LAYOUT

```text
youtube-playlist-manager/
├── main.py                  # CLI entry point
├── settings.toml            # global user configuration
├── playlist-settings.toml   # per-playlist preferences & sync modes
├── core/                    # application modules
│   ├── config.py            # settings & data persistence
│   ├── config_cmd.py        # CLI interactive settings editor
│   ├── auth.py              # OAuth login & client selection
│   ├── parser.py            # URL/ID parsing & playlist file I/O
│   ├── sync.py              # LIS minimal-moves reorder engine
│   ├── commands.py          # command handlers (pull, push, format, etc.)
│   ├── downloader.py        # yt-dlp downloading & metadata extraction
│   ├── ai.py                # AI reorganization & title cleanup engine
│   └── ui.py                # interactive menu, help & terminal formatting
├── oauth-clients/           # OAuth client secrets (do not share)
│   └── project-a.json       # example OAuth client file
├── data/                    # saved data
│   ├── playlist-data.json   # linked playlists & activity history
│   ├── cookies.txt          # optional cookies file (auto-detected)
│   └── tokens/              # cached OAuth tokens (one per account)
│       └── project-a_user@gmail.com.token.json
├── playlists/               # playlist files
│   ├── my-playlist.txt      # playlist file (one per playlist)
│   └── archive/             # automatic timestamped backups before AI edits
├── playlist-downloads/      # downloaded audio & video
│   └── my-playlist/         # one folder per playlist
│       ├── _manifest.json   # download cache & track file mapping
│       └── 01 - Song.opus   # downloaded track
└── logs/                    # operation logs
    └── my-playlist.log      # operation history & change log
```

---

## COMMANDS

| Command | Syntax | What it does |
| --- | --- | --- |
| `menu` | `python main.py` | Opens the interactive menu with recent playlists, OAuth clients, and command reference. |
| `link` | `python main.py link <id_or_url>` | Registers a playlist by URL or ID and creates its playlist file and download folder. |
| `unlink` | `python main.py unlink <name>` | Removes a linked playlist. |
| `list` | `python main.py list` | Lists linked playlists with their last command and timestamp. |
| `pull` | `python main.py pull [<name>]` | Fetches the live track order into `playlists/<name>.txt`. |
| `push` | `python main.py push [<name>]` | Syncs local edits (reorders, additions, deletions) back to YouTube *(needs Google Cloud)*. |
| `format` | `python main.py format [<name>]` | Normalizes raw URLs/IDs in the playlist file and syncs local downloaded track numbering. |
| `ai-format` | `python main.py ai-format [<name>] [-p <prompt>]` | Reorganizes tracks and creates logical sections (genre, mood, artist) with AI *(alias: `ai-organize`)*. |
| `download` | `python main.py download [<name>] [--format audio\|video]` | Downloads the playlist as audio or video via `yt-dlp` with incremental caching. |
| `config` | `python main.py config [<key>] [<val>] [-p <playlist>]` | View or modify global and playlist settings directly from the terminal *(aliases: `settings`, `set`)*. |
| `help` | `python main.py help` | Shows all commands, options, and usage. |

Anywhere `[<name>]` appears, it's optional - ypm auto-selects if you only have one playlist linked, and prompts you otherwise.

---

## TYPICAL WORKFLOW

### Managing a playlist

```bash
# Register the playlist
python main.py link https://www.youtube.com/playlist?list=<id>

# Pull the current track order
python main.py pull

# Edit playlists/<name>.txt in any text editor:
#   - cut/paste lines to reorder
#   - paste a YouTube URL on a new line to add a track
#   - delete a line to remove a track

# Push your edits to YouTube (needs Google Cloud)
python main.py push
```

### Organizing into Sections with AI

Organize playlist tracks into genre/mood sections, group by game/area/theme, or re-sequence using your preferred AI model:

```bash
# Organize interactively (prompts for what you want the AI to do)
python main.py ai-format <name>

# Organize with explicit prompt instructions
python main.py ai-format <name> -p "Group into sections by genre: Hip Hop, R&B, Rock, Ambient"

# Preview proposed AI changes safely without saving to disk
python main.py ai-format <name> --dry-run
```

- **Open to Any AI**: Works with OpenAI, Google Gemini, Groq, OpenRouter, Anthropic Claude, or 100% free local models with Ollama.
- **Context-Aware**: `ypm` enriches tracks with full artist/channel and duration metadata via yt-dlp before sending to the AI so it has complete context to categorize accurately.
- **Zero Data Loss Guarantee**: Validates that all video IDs are preserved 1:1, filters out any hallucinations, and saves an automatic timestamped backup in `playlists/archive/` before updating the file.

### Downloading a playlist

```bash
python main.py download
```

- **Incremental** - only tracks not already on disk are downloaded; re-running is always safe and fast.
- **Self-healing** - delete a file and re-run `download`; ypm notices it's missing and grabs it again.
- **Numbered files** - with `number_files = true` in `playlist-settings.toml`, files are prefixed by playlist position (`01 - Song.opus`) so they sort correctly in file managers and media players.
- **Failed tracks** - tracks that are deleted, age-restricted, or copyright-blocked are recorded and skipped on subsequent runs. Set `retry_failed_downloads` in `settings.toml` or use `--retry-failed` to re-attempt them. Use `--clear-failed` to reset the failed cache.

### Bypassing YouTube Bot Detection & Cookies

YouTube frequently challenges automated requests with:
`Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies for the authentication.`

You can resolve this using either a cookie text file or a browser:

#### Method 1: Drop `cookies.txt` into `data/` (Recommended for Chrome / Edge)
Chromium-based browsers (Google Chrome, Microsoft Edge, Brave) lock their cookie databases on Windows while the browser is running. The easiest solution that works while your browser stays open:
1. Install a browser extension such as **Get cookies.txt LOCALLY** (or any Netscape-format cookie exporter).
2. Log into YouTube in your browser.
3. Open YouTube, open the extension, and click **Export**.
4. Drop the exported text file into `data/cookies.txt` (or `data/youtube_cookies.txt`).
5. `ypm` will automatically detect and use `data/cookies.txt` on every download run-no extra commands or flags needed!

#### Method 2: Use Firefox (`cookies_from_browser = "firefox"`)
Unlike Chromium browsers, **Firefox does not lock its cookie database while running on Windows**. If you have Firefox installed and have signed into YouTube:
1. In `settings.toml`, set:
   ```toml
   cookies_from_browser = "firefox"
   ```
   (Or run: `python main.py config cookies_from_browser firefox` / pass flag `--cookies-from-browser firefox`)
2. yt-dlp will read your YouTube session cookies directly from Firefox in real time even while Firefox is actively open and running.

#### Method 3: Using Edge / Chrome directly
You can set `cookies_from_browser = "edge"` or `"chrome"`, but on Windows you **must completely close your browser** before running `download` so Windows releases the file lock.

---

## CONFIGURATION

### Managing Settings (`config` command)

You can view or change any setting directly from the command line without opening a text editor:

```bash
# List all global settings and their current values
python main.py config

# View a specific setting
python main.py config cookies_from_browser

# Change a global setting
python main.py config cookies_from_browser firefox
python main.py config menu_show_commands false   # Hide commands list from menu for a minimal dashboard

# View or change playlist-specific settings (-p / --playlist)
python main.py config -p "Forsaken OST (Roblox)"
python main.py config download-format video -p fors
```

### Global settings - `settings.toml`

These settings apply across all playlists.

| Key | Default | Description |
| --- | --- | --- |
| `safety_check_before_push` | `true` | Prompt to confirm you've pulled before pushing. |
| `menu_show_playlists` | `true` | Show recent playlists on the dashboard menu. |
| `menu_playlist_count` | `"all"` | Maximum number of playlists shown in the interactive menu (`"all"` or a number, `0` to hide). |
| `menu_show_clients` | `true` | Show OAuth clients on the dashboard menu. |
| `menu_client_count` | `"all"` | Maximum number of OAuth clients shown in the interactive menu (`"all"` or a number, `0` to hide). |
| `menu_show_commands` | `true` | Show available commands help list on the dashboard (`false` for minimal menu). |
| `enable_logging` | `true` | Save log files for all operations to `logs/`. |
| `downloads_dir` | `"playlist-downloads"` | Root folder for all downloads. |
| `clickable_links_in_playlist_files` | `true` | Save track entries as full URLs instead of raw IDs. Run `format` to apply to existing playlist files. |
| `pull_method` | `"auto"` | How to fetch playlists: `"auto"` = `yt-dlp` when possible with the API as fallback, `"ytdlp"` = `yt-dlp` only, `"api"` = API only. |
| `link_method` | `"auto"` | How to link playlists (same options as `pull_method`). |
| `ytdlp_path` | `""` | Custom path to the `yt-dlp` executable. Leave blank to auto-detect. |
| `ffmpeg_path` | `""` | Custom path to the `ffmpeg` executable. Leave blank to auto-detect. |
| `cookies_file` | `""` | Custom path to Netscape-format cookies.txt. Leave blank to auto-detect `data/cookies.txt`. |
| `cookies_from_browser` | `"firefox"` | Browser to extract cookies from (e.g. `"firefox"`, `"edge"`, `"chrome"`). |
| `retry_failed_downloads` | `false` | What to do with tracks that previously failed: `false` = skip silently, `true` = always retry, `"ask"` = prompt each time. |
| `[ai]` | table | AI configuration for `ai-format` (`provider`, `api_key`, `model`, `base_url`, `timeout`). |

### Playlist settings - `playlist-settings.toml`

Every playlist can have its own settings block. The bracketed header is the playlist's **alias** - the name shown in the interactive menu (usually the playlist title or the name you gave it when linking).

```toml
["My Playlist"]
download-format = "audio"
embed_thumbnail = true
number_files = true
push_mode = "all"
download_mode = "main_only"
playlist_entry_format = "%(id)s | %(title)s"
download_path = ""
folder_name_source = "alias"
atomic_writes = true
number_section_folders = false
oauth_client = ""
include_playlist_name_in_sections = false
```

#### All keys

| Key | Values | Description |
| --- | --- | --- |
| `download-format` | `"audio"` \| `"video"` | Whether `download` fetches audio only (`.opus`) or video (`.mp4`). |
| `embed_thumbnail` | `true` \| `false` | Embed album art into the audio file (requires `ffmpeg`). |
| `number_files` | `true` \| `false` | Prefix filenames with their playlist position (`01 - Song.opus`). |
| `push_mode` | `"all"` \| `"main_only"` \| `"sections_only"` | Which playlists are synced on `push`: `"all"` = main playlist and sections, `"main_only"` = main playlist only, `"sections_only"` = section sub-playlists only. |
| `download_mode` | `"all"` \| `"main_only"` \| `"sections_only"` | Download layout - see [`download_mode` explained](#download_mode-explained). |
| `playlist_entry_format` | format string | How each track line is written in the playlist file. Supported fields: `%(id)s`, `%(title)s`, `%(channel)s`, `%(duration)s`. Shorthand like `"id, title"` also works. Run `pull` after changing this. |
| `download_path` | path string | Custom absolute path for downloads. Leave blank to use `<downloads_dir>/<playlist name>`. |
| `folder_name_source` | `"alias"` \| `"header"` | Which name to use for the download folder when `download_path` is blank: `"alias"` = playlist key, `"header"` = title from the `##` header in the playlist file. |
| `atomic_writes` | `true` \| `false` | Write playlist files atomically (safe, default). `false` is slightly faster but can corrupt the file if interrupted. |
| `number_section_folders` | `true` \| `false` | Prefix section download folders with their order (`01 - Chill`, `02 - Hype`). |
| `oauth_client` | `"project-a"` \| `""` | OAuth client to use automatically - see [Using `oauth_client`](#using-oauth_client). |
| `account` | `"user@gmail.com"` \| `""` | Google account email to authenticate as. Keys the cached token in `data/tokens/` so multiple accounts can each have their own token without overwriting each other. Leave blank to pick an account in the browser each time. |
| `cookies_file` | path string | Custom cookie file path for this playlist. |
| `cookies_from_browser` | browser string | Custom browser for this playlist (e.g. `"firefox"`). |
| `ai_prompt` | prompt string | Custom instructions to automatically use for `ai-format` on this playlist without prompting. |
| `include_playlist_name_in_sections` | `true` \| `false` | When `format` fills in a section header, also append the section playlist's real YouTube title: `## <url> \| <your name> \| <real title>`. |

#### `download_mode` explained

| Mode | Behavior |
| --- | --- |
| `"all"` | Downloads both the main folder and each section sub-folder. Tracks exist in both places. |
| `"main_only"` | Downloads everything into a single flat folder. Good for playlists without sections. |
| `"sections_only"` | Only downloads section sub-folders (defined by `##` section headers in the playlist file). The top-level folder is skipped. |

#### Using `oauth_client` and `account`

These two keys work together to fully automate authentication for a playlist.

**`oauth_client`** pins which credential file (from `oauth-clients/`) to use - this controls which Google Cloud project's quota is drawn from.

**`account`** pins which Google account to authenticate as. It's used to key the cached token file in `data/tokens/`, so each `(client, account)` pair gets its own independent token:

| Scenario | Token file |
| --- | --- |
| `oauth_client = "project-a"`, `account = ""` | `data/tokens/project-a.token.json` |
| `oauth_client = "project-a"`, `account = "dalton@gmail.com"` | `data/tokens/project-a_dalton@gmail.com.token.json` |
| `oauth_client = "project-a"`, `account = "john@gmail.com"` | `data/tokens/project-a_john@gmail.com.token.json` |

This means Dalton and John can each manage their own playlists with the same OAuth client, and their cached logins never overwrite each other.

Example - two playlists, two accounts, one client:

```toml
["Dalton's Playlist"]
oauth_client = "project-a"
account = "dalton@gmail.com"

["John's Playlist"]
oauth_client = "project-a"
account = "john@gmail.com"
```

Leave `account` blank if you only use one Google account - the token is stored as `project-a.token.json` and reused silently on every run.

> **Note:** `account` is just a label for token-file naming. When the browser opens for login, ypm prints a reminder of which account it expects, but you can technically log in as any account - the token will just be saved under the label you provided.

> See [Google Cloud Setup](#google-cloud-setup) for how to create an OAuth client JSON.

---

## GOOGLE CLOUD SETUP

If you're happy pulling, downloading, linking, and formatting public/unlisted playlists, you can skip this section entirely - none of that touches Google Cloud.

You'll need it only if you want to:

- **Push** local edits (reorders, additions, deletions) back to YouTube, or
- **Pull from or link** a private playlist

### Setup steps

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project (or use an existing one).
2. Enable the [YouTube Data API v3](https://console.cloud.google.com/marketplace/product/google/youtube.googleapis.com).
3. Under **APIs & Services > Credentials**, click **Create Credentials > OAuth client ID** and set the application type to **Desktop App**.
4. Download the resulting credentials JSON, give it a recognizable name (e.g. `project-a.json`), and drop it in the `oauth-clients/` folder.
5. Under **OAuth consent screen > Audience > Test Users**, add the Google account email(s) for the playlists you want to manage.

### OAuth clients and quota

An OAuth client JSON is a credential tied to a Google Cloud project's API quota - it's not tied to a specific user account. Any client file can authenticate any Google account, but quota is drawn from whichever project it came from.

- Multiple client files from the *same* project share that project's quota.
- Files from *different* projects have entirely separate quota pools.
- With one client file it's used automatically. With more than one, pass `--client <name>` (or `-c <name>`), or you'll be prompted to choose.

No session tokens are cached - every API command opens a fresh browser login. If one project's quota runs out, re-run with `--client` pointing at a different project.

### Quota info

`pull`, `link`, `download`, and `format` all go through `yt-dlp` and use **zero quota**. Quota only applies to `push` and private-playlist operations. YouTube Data API v3 gives you 10,000 units/day per project (~200 insert/delete/reorder operations).

| Operation | Endpoint | Cost |
| --- | --- | --- |
| Read / List | `playlistItems.list` | 1 unit per 50-track page |
| Insert track | `playlistItems.insert` | 50 units per track |
| Delete track | `playlistItems.delete` | 50 units per track |
| Reorder track | `playlistItems.update` | 50 units per move |

See the [quota documentation](https://developers.google.com/youtube/v3/determine_quota_cost) for more detail.

---

Note: I wrote the code in this project with Google Antigravity, mostly because I was in a rush to get this into a usable state, but also because Python is not my specialty. I have run many different tests using real oauth clients and large playlists (700+ videos), and did not encounter any issues, all features and safety fallbacks work as they should.