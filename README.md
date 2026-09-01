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
5. Download your credentials JSON file and save it in the project root directory as `client_secret.json`.
6. Go to **OAuth consent screen -> Audience -> Test Users**
   - Add your google email tied to the account with the playlists you want

### 3. Usage

| Command | Syntax | Description |
| --- | --- | --- |
| **link** | `python main.py link <alias> <id_or_url>` | Connects a short alias name to a YouTube Playlist ID. |
| **unlink** | `python main.py unlink <alias>` | Removes a linked playlist alias from settings. |
| **list** | `python main.py list` | Displays all configured aliases and local file statuses. |
| **pull** | `python main.py pull <alias>` | Downloads the live YouTube playlist into `playlists/<alias>.txt`. |
| **push** | `python main.py push <alias>` | Pushes local `.txt` additions, deletions, and track order to YouTube and automatically formats URLs/IDs to `<video_id> \| <video_title>` format. |
| **format** | `python main.py format <alias>` | Normalizes URLs/IDs into `<video_id> \| <title>` format for readability. |

### 4. Quota Information
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
