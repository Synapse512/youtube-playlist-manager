# YouTube Playlist Manager `(ypm)`

**CLI tool for managing yt-playlists both online and locally**

### Why use ypm?
You can use ypm management systems to control both online and local versions of your Youtube playlists at the same time without any extra work. Also, it makes it easier to do bulk operations of reordering, removing, and inserting playlist content.

<img width="800" src="https://github.com/user-attachments/assets/2d367699-7dd9-49e2-9407-03436da0188a" alt="ypm-preview-image" />

---

### 1. Installation

#### Requirements

* **Python 3.6+** (tested with **3.14.2**)
* **yt-dlp** (optional)
* **ffmpeg** (optional)

#### Clone the repository

```bash
git clone https://github.com/Synapse512/youtube-playlist-manager
cd youtube-playlist-manager
```

#### Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Google Cloud Setup (*only if you manage playlists online*)
1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (or select an existing one).
3. Enable the [YouTube Data API v3](https://console.cloud.google.com/marketplace/product/google/youtube.googleapis.com).
4. Go to **APIs & Services > Credentials**:
   - Click **Create Credentials** -> **OAuth client ID**.
   - Set Application type to **Desktop App**.
5. Download your credentials JSON file, rename it to something recognizable (e.g. `project-a.json`), and place it in the `oauth-clients/` folder.
6. Go to **OAuth consent screen -> Audience -> Test Users**
   - Add the Google account email(s) tied to the playlists you want to manage

#### OAuth Clients & Quota

An oauth-client JSON is just an OAuth client secret tied to a Google Cloud project's API quota - it is **not** a fixed user account. Each project gets its own daily quota, and any oauth-client file can be used to log into *any* Google account. Do not share these files, people can exploit your Google Cloud projects with the file info. 

- You can create multiple oauth-client files under the **same** Google Cloud project - they all draw from that project's shared quota.
- You can also create oauth-client files under **different** projects to get separate, independent quota pools.
- If you only have **one** oauth-client file, it's auto-selected for every command.
- If you have **multiple** oauth-client files, you can specify which one to use in commands with the `--client` param. If you do not, you will be prompted to choose which file to select before running the command.

**No session tokens are cached.** Every command that talks to YouTube (`link`, `pull`, `push`, `format`) opens a fresh browser window so you can log in with whichever Google account you want, using whichever oauth-client's quota you want. Nothing is remembered between runs - if one project's quota runs out, just re-run the command with a different `--client` pointed at a project that still has quota left.

**Directory layout:**
```
youtube-playlist-manager/
├── main.py              <- CLI entry point
├── core/
│   ├── config.py        <- Settings & data persistence
│   ├── auth.py          <- OAuth login & oauth-client selection
│   ├── parser.py        <- URL/ID regex & playlist file I/O
│   ├── sync.py          <- LIS minimal-moves reordering engine
│   ├── commands.py      <- Command handlers (pull, push, etc.)
│   └── ui.py            <- Dashboard menu & terminal formatting
├── oauth-clients/       <- OAuth client secrets (do not duplicate file or file name)
│   ├── project-a.json
│   └── project-b.json
├── data/
│   └── playlists.json   <- linked playlists and activity history
├── playlists/
│   ├── chill.txt        <- tracklist file
│   └── instrumental.txt
├── settings.toml        <- user configuration preferences
└── logs/
    ├── chill.log        <- track changelog and operation history
    └── instrumental.log
```

### 3. Usage

| Command | Syntax | Description |
| --- | --- | --- |
| **menu** | `python main.py` | Opens menu to show a quick glance at recently edited playlists and command usage |
| **link** | `python main.py link <id_or_url> [--client <name>]` | Connects a YouTube Playlist ID or URL using the title fetched from YouTube |
| **unlink** | `python main.py unlink <name>` | Removes a linked playlist. |
| **list** | `python main.py list` | Displays all configured playlists and last CLI edit timestamp/command |
| **pull** | `python main.py pull [<name>] [--client <name>]` | Downloads the live YouTube playlist into `playlists/<name>.txt` |
| **push** | `python main.py push [<name>] [--client <name>]` | Pushes local `.txt` additions, deletions, and track order to YouTube and automatically formats URLs/IDs to `<video_id> \| <video_title>` format |
| **format** | `python main.py format [<name>] [--client <name>]` | Normalizes URLs/IDs into `<video_id> \| <title>` format |
| **download** | `python main.py download [<name>] [--format audio\|video]` | Downloads playlist as audio or video using `yt-dlp`. Audits disk and reconciles missing tracks automatically |
| **help** | `python main.py help` | Displays help information with all command usages |

For commands operating on a playlist (`pull`, `push`, `format`, `download`), the `<name>` param is optional. If omitted, it will be auto-selected if only 1 playlist exists, or you will be prompted with an interactive selection menu if multiple playlists exist. For `download`, if `--format` is omitted, you will be prompted to choose audio or video.

`--client` / `-c` picks which oauth-client JSON to use for that single run. If omitted, it's auto-selected when only one exists, or you'll be prompted to choose when there are several. This choice is never saved - you pick fresh every time.

### 4. Managing Playlists via Text Files

The core workflow of `ypm` revolves around editing local `.txt` files in `playlists/<name>.txt`:

1. **Link the Playlist**:
   First, link your remote playlist using its YouTube URL or ID:
   ```bash
   python main.py link <id_or_url>
   ```
   This fetches the playlist's title from YouTube and registers it in `playlists/` as a text file. It will not have any video ids or titles stored in it yet.

2. **Pull Tracks Locally**:
   Download the live track order into your local text file:
   ```bash
   python main.py pull
   ```
   This populates the file with each video from your playlist in the correct order, formatted as `<video_id> | <video_title>`.

3. **Rearrange & Reorder**:
   Open `playlists/<name>.txt` in any text editor. Simply cut and paste lines to rearrange tracks into whatever order you want.

4. **Insert & Delete Tracks**:
   - **Insertions**: Paste a YouTube video URL or ID anywhere on a new line, it will automatically be formatted by the format or push command.
   - **Deletions**: Simply delete the track line from the file.

5. **Organize Sections with Blank Lines**:
   You can add spaces between different videos to organize them. Blank lines are preserved by `ypm` across syncs and pulls so your visual formatting stays intact.

6. **Push Changes to YouTube**:
   When you're happy with your text file changes, sync everything back to YouTube:
   ```bash
   python main.py push
   ```
   The engine computes the minimal number of API calls needed to rearrange items, inserts any added tracks, deletes removed ones, and formats raw URLs/IDs into clean `<video_id> | <title>` lines.

#### Managing Downloads Locally

Once a playlist is linked and pulled, you can also download it to your machine:

```bash
python main.py download
```

You will be prompted to choose **audio** or **video** if you don't specify `--format audio|video`.

- **Filesystem-as-Truth & Self-Healing**: YPM audits the physical download folder before running. If you ever delete a track file from Windows Explorer or re-add a track to your text file, running `download` immediately detects that the file is missing on disk and downloads that track right back into place!
- **Incremental downloads**: Only tracks not yet on disk are downloaded. Valid, existing tracks are skipped automatically, so re-running the command is always instant and safe.
- **Track ordering**: When `"number_files": true` in `_setting.json`, every file is prefixed with its playlist position (e.g. `01 - Song.mp3`, `02 - Song.mp3`). This forces the correct sort order in your OS file manager or media player without relying on ID3/metadata. If you want to remove the numbered order, simply toggle `"number_files": false` in `_setting.json` and re-run `download` to instantly rename them without redownloading.
- **Per-Playlist Settings**: Each playlist download directory contains `_setting.json`, storing that playlist's preferred format (`audio` or `video`), thumbnail options, numbering preference (`number_files`), and a track manifest mapping video IDs to filenames.

### 5. Quota Information
YouTube Data API v3 has a daily default quota of **10,000 units per Google Cloud project**, which amounts to about 200 operations of inserting, deleting, or reordering in a playlist. For more information go to [here](https://developers.google.com/youtube/v3/determine_quota_cost)

| Operation | API Endpoint | Quota Cost |
| --- | --- | --- |
| **Read / List** | `playlistItems.list` | **1 unit** per 50-track page |
| **Title Fetch** | `videos.list` | **1 unit** per 50-track batch |
| **Insert Track** | `playlistItems.insert` | **50 units** per added video |
| **Delete Track** | `playlistItems.delete` | **50 units** per removed video |
| **Reorder Track** | `playlistItems.update` | **50 units** per shifted position |

---

Note: I wrote the code in this project with Google Antigravity, mostly because I was in a rush to get this into a usable state, but also because Python is not my specialty. I have run many different tests using real oauth clients and large playlists (400+ videos), and did not encounter any issues, and all safety fallbacks worked as they should. 
