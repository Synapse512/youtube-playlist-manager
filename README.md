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

#### Link an Alias:
```bash
python main.py link <alias_name> <playlist_id_or_url>
```

#### List Configured Playlists:
```bash
python main.py list
```

#### Pull Live Playlist to Text File:
```bash
python main.py pull <alias_or_id>
```
Saves to `playlists/<alias>.txt`.


#### Push Local Layout to YouTube:
```bash
python main.py push <alias_or_id>
```
Synchronizes all insertions, deletions, and reorders with YouTube. Upon completion, it **automatically normalizes the local text file**, replacing any pasted links with `<video_id> | <video_title>`.

#### Format Local File (Optional):
```bash
python main.py format <alias>
```
Normalizes the local text file (converting pasted links/IDs to `<video_id> | <video_title>` and fetching titles) without pushing changes to YouTube.

### 4. Quota Information
YouTube Data API v3 has a daily default quota of **10,000 units**:
- **Read / List (`playlistItems.list`)**: 1 unit per 50-track page.
- **Title Fetch (`videos.list`)**: 1 unit per 50-track batch.
- **Insert (`playlistItems.insert`)**: 50 units per added track.
- **Delete (`playlistItems.delete`)**: 50 units per deleted track.
- **Reorder (`playlistItems.update`)**: 50 units per moved track.

---

Note: I made this project using Google Antigravity, since I basically know nothing about Python. Though, I figured since this is actually useful I'd upload it.
