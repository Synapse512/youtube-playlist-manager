# YouTube Playlist Manager `(ypm)`

**CLI tool for managing yt-playlists both online and locally**

### Why use ypm?
You can use ypm management systems to control both online and local versions of your Youtube playlists at the same time without any extra work. Also, it makes it easier to do bulk operations of reordering, removing, and inserting playlist content.

It also works as a playlist backup - since the `push` command can rebuild your entire playlist, whether online or locally. Simply just run the `push` and/or `download` command when you need your playlist(s) back. 

<img width="800" src="https://github.com/user-attachments/assets/2d367699-7dd9-49e2-9407-03436da0188a" alt="ypm-preview-image" />

---

## Installation

**Requirements:**
- Python 3.11+ (tested on 3.14.2)
- `yt-dlp` - needed for `pull`, `link`, `format`, and `download` on public/unlisted playlists
- `ffmpeg` - needed for audio conversion in `download`

```bash
git clone https://github.com/Synapse512/youtube-playlist-manager
cd youtube-playlist-manager
pip install -r requirements.txt
```

No Google Cloud setup required yet if you are only looking to download and manage your playlists locally. Link a playlist and start pulling/downloading right away:

```bash
python main.py link https://www.youtube.com/playlist?list=<id>
python main.py pull
python main.py download
```

Google Cloud only comes into play later, if you want to do the two things `yt-dlp` can't do on its own: push local edits back to YouTube, or work with a private playlist. That's covered later.

## Project layout

```
youtube-playlist-manager/
├── main.py                  # CLI entry point
├── settings.toml            # user configuration (commented)
├── core/
│   ├── config.py            # settings & data persistence
│   ├── auth.py              # OAuth login & client selection
│   ├── parser.py            # URL/ID parsing & playlist file I/O
│   ├── sync.py              # LIS minimal-moves reorder engine
│   ├── commands.py          # command handlers (pull, push, etc.)
│   └── ui.py                # dashboard menu & terminal formatting
├── oauth-clients/           # OAuth client secrets (do not share)
│   └── project-a.json
├── playlists/
│   ├── _playlists.json      # linked playlists & activity history
│   └── my-playlist.txt      # tracklist file (one per playlist)
├── playlist-downloads/
│   └── my-playlist/
│       ├── _setting.json    # per-playlist download preferences
│       └── 01 - Song.opus
└── logs/
    └── my-playlist.log      # operation history & change log
```

## Commands

| Command | Syntax | What it does |
| --- | --- | --- |
| `menu` | `python main.py` | Interactive menu with recent playlists and a quick command reference |
| `link` | `python main.py link <id_or_url>` | Registers a playlist by URL or ID, creates its local text file and download folder |
| `unlink` | `python main.py unlink <name>` | Removes a linked playlist |
| `list` | `python main.py list` | Lists linked playlists with their last command and timestamp |
| `pull` | `python main.py pull [<name>]` | Fetches the live track order into `playlists/<name>.txt` |
| `push` | `python main.py push [<name>]` | Syncs local edits (order, additions, deletions) back to YouTube *(needs Google Cloud)* |
| `format` | `python main.py format [<name>]` | Normalizes raw URLs/IDs in the text file to `<video_id> \| <title>` |
| `download` | `python main.py download [<name>] [--format audio\|video]` | Downloads the playlist as audio or video via yt-dlp |
| `help` | `python main.py help` | Shows all commands and usage |

Anywhere `[<name>]` appears, it's optional - ypm auto-selects it if you only have one playlist linked, and prompts you otherwise.

## Typical workflow

```bash
# Register the playlist
python main.py link https://www.youtube.com/playlist?list=<id>

# Pull the current track order
python main.py pull

# Edit playlists/<name>.txt in any text editor:
#   - cut/paste lines to reorder
#   - paste a YouTube URL on a new line to add a track
#   - delete a line to remove a track

# Push your edits to YouTube - this is the one step that needs Google Cloud
python main.py push
```

Downloading works the same way, no Google Cloud involved:

```bash
python main.py download
```

- On first run it asks whether you want audio or video, then remembers your choice in `_setting.json`.
- It's self-healing: delete a file from the download folder and re-run `download`, and it'll notice the track is missing and grab it again.
- It's also incremental - only missing tracks get downloaded, so re-running is fast and safe.
- With `"number_files": true` in `_setting.json`, files get prefixed with their playlist position (`01 - Song.opus`, `02 - Song.opus`) so they sort correctly in file managers and media players.

## Setting up Google Cloud (only if you need `push`, or a private playlist)

If you're happy pulling, downloading, linking, and formatting public/unlisted playlists, you can skip this section entirely - none of that touches Google Cloud.

You'll need it if you want to:
- **Push** local edits (reorders, adds, deletes) back to YouTube, or
- Pull from or link a **private** playlist

Here's how to set it up:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create a project (or use one you already have).
2. Enable the [YouTube Data API v3](https://console.cloud.google.com/marketplace/product/google/youtube.googleapis.com).
3. Under **APIs & Services > Credentials**, click **Create Credentials > OAuth client ID**, and set the application type to **Desktop App**.
4. Download the resulting credentials JSON, give it a name you'll recognize (e.g. `project-a.json`), and drop it in the `oauth-clients/` folder.
5. Under **OAuth consent screen > Audience > Test Users**, add the Google account email(s) for the playlists you want to manage.

### A note on OAuth clients and quota

An oauth-client JSON is a credential tied to a Google Cloud project's API quota - it's not tied to a specific user account. Any oauth-client file can authenticate any Google account, but the quota it draws from belongs to whichever project it came from.

- Multiple oauth-client files from the *same* project share that project's quota.
- Files from *different* projects have entirely separate quota pools.
- With one oauth-client file, it's used automatically. With more than one, pass `--client <name>` (or `-c <name>`), or you'll be prompted to choose.

No session tokens are cached - every API command opens a fresh browser login each time. If one project's quota runs out, just re-run the command with `--client` pointed at a different project.

### Quota info

YouTube Data API v3 gives you 10,000 units/day per project, which works out to roughly 200 insert/delete/reorder operations. `pull`, `link`, `download`, and `format` all go through `yt-dlp` and use zero quota - this only applies to `push` and private-playlist operations.

| Operation | Endpoint | Cost |
| --- | --- | --- |
| Read / List | `playlistItems.list` | 1 unit per 50-track page |
| Insert track | `playlistItems.insert` | 50 units per track |
| Delete track | `playlistItems.delete` | 50 units per track |
| Reorder track | `playlistItems.update` | 50 units per move |

See the [quota documentation](https://developers.google.com/youtube/v3/determine_quota_cost) for more detail.

---

Note: I wrote the code in this project with Google Antigravity, mostly because I was in a rush to get this into a usable state, but also because Python is not my specialty. I have run many different tests using real oauth clients and large playlists (400+ videos), and did not encounter any issues, and all safety fallbacks worked as they should.