"""
yt-dlp integration for downloading playlists as audio or video with incremental caching,
automatic title resolution, deletion synchronization, per-playlist download settings,
JavaScript runtime auto-detection, and collision-free renumbering.
"""

import os
import sys
import re
import json
import shutil
import subprocess
import tempfile
import time
import traceback
import unicodedata
import uuid

from .config import (
    DOWNLOADS_DIR,
    PLAYLISTS_DIR,
    DATA_DIR,
    record_activity,
    log_playlist_event,
    load_playlist_settings,
    save_playlist_settings,
)
from .parser import (
    sanitize_filename,
    get_playlist_name_for_target,
    resolve_playlist_id,
    parse_playlist_file,
    save_playlist_file,
)
from .config import playlist_entry_format_fields

DOWNLOAD_MANIFEST_FILENAME = "_manifest.json"
DOWNLOAD_SETTINGS_FILENAME = DOWNLOAD_MANIFEST_FILENAME

_IGNORED_FILENAMES = {
    DOWNLOAD_MANIFEST_FILENAME,
    "_manifest.json",
    "_setting.json",
    "_settings.json",
    "_ypm-download-settings.json",
    "ypm-download-settings.json",
    "_ytdlp_archive.txt",
    ".ytdlp_archive.txt",
}




def find_ytdlp(settings=None):
    """
    Locates the yt-dlp executable.
    Checks:
      1. Custom path in settings.toml ('ytdlp_path')
      2. Project root directory ('./yt-dlp.exe' or './yt-dlp')
      3. System PATH ('shutil.which')
    Returns a list representing the command prefix (e.g. ['path/to/yt-dlp']), or None if not found.

    Deliberately does NOT fall back to running yt-dlp as a Python module
    ('python -m yt_dlp'). A sectioned download of a large playlist means dozens of
    separate yt-dlp invocations (the full playlist, every section, plus retries);
    spinning up a fresh Python interpreter and re-importing yt_dlp for each one is
    meaningfully slower than calling a compiled binary, and it silently ties
    download reliability to whatever yt-dlp version happens to be pip-installed
    instead of the actively-maintained standalone release. Install the real
    executable instead (see README).
    """
    if settings:
        custom_path = settings.get("ytdlp_path", "").strip()
        if custom_path and os.path.isfile(custom_path):
            return [os.path.abspath(custom_path)]

    for candidate in ("yt-dlp.exe", "yt-dlp"):
        if os.path.isfile(candidate):
            return [os.path.abspath(candidate)]

    which_exe = shutil.which("yt-dlp.exe") or shutil.which("yt-dlp")
    if which_exe:
        return [os.path.abspath(which_exe)]

    return None


def find_ffmpeg(settings=None):
    """
    Locates ffmpeg executable or directory.
    Checks:
      1. Custom path in settings.toml ('ffmpeg_path')
      2. Project root directory ('./ffmpeg.exe' or './ffmpeg')
      3. System PATH ('shutil.which')
    Returns the executable or directory path, or None if not found.
    """
    if settings:
        custom_path = settings.get("ffmpeg_path", "").strip()
        if custom_path:
            if os.path.isfile(custom_path) or os.path.isdir(custom_path):
                return os.path.abspath(custom_path)

    for candidate in ("ffmpeg.exe", "ffmpeg"):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    which_exe = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if which_exe:
        return os.path.abspath(which_exe)

    return None


def find_js_runtime(settings=None):
    """
    Locates an available JavaScript runtime for yt-dlp (Node.js, Deno, Bun, QuickJS).
    Checks:
      1. Custom path in settings.toml ('js_runtime_path')
      2. PATH via shutil.which ('deno', 'node', 'bun', 'qjs')
      3. Standard Windows installation directories
    Returns the runtime string for --js-runtimes (e.g. 'node', 'deno', or 'node:C:\\...\\node.exe') or None.
    """
    if settings:
        custom = settings.get("js_runtime_path", "").strip()
        if custom and (os.path.isfile(custom) or shutil.which(custom)):
            base = os.path.splitext(os.path.basename(custom))[0].lower()
            rt_type = "node" if "node" in base else ("deno" if "deno" in base else ("bun" if "bun" in base else "qjs"))
            return f"{rt_type}:{os.path.abspath(custom)}"

    for rt in ("deno", "node", "bun", "qjs"):
        if shutil.which(rt) or shutil.which(f"{rt}.exe"):
            return rt

    # Check common Windows locations
    windows_candidates = [
        ("node", os.path.expandvars(r"%ProgramFiles%\nodejs\node.exe")),
        ("node", os.path.expandvars(r"%ProgramFiles(x86)%\nodejs\node.exe")),
        ("node", os.path.expandvars(r"%LOCALAPPDATA%\Programs\node.exe")),
        ("node", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\node.exe")),
        ("deno", os.path.expandvars(r"%USERPROFILE%\.deno\bin\deno.exe")),
        ("deno", os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\deno.exe")),
        ("bun", os.path.expandvars(r"%USERPROFILE%\.bun\bin\bun.exe")),
    ]
    for rt_name, path in windows_candidates:
        if os.path.isfile(path):
            return f"{rt_name}:{os.path.abspath(path)}"

    return None


def find_cookies_file(custom_path=None):
    """
    Locates an existing cookies text file (Netscape format).
    Checks:
      1. custom_path (if provided and exists)
      2. data/cookies.txt
      3. data/youtube_cookies.txt
      4. ./cookies.txt
      5. ./youtube_cookies.txt
    Returns the absolute path if found, or None.
    """
    if custom_path:
        cp = os.path.abspath(custom_path.strip())
        if os.path.isfile(cp):
            return cp
    candidates = [
        os.path.join(DATA_DIR, "cookies.txt"),
        os.path.join(DATA_DIR, "youtube_cookies.txt"),
        os.path.abspath("cookies.txt"),
        os.path.abspath("youtube_cookies.txt"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None


def resolve_cookies_args(settings=None, pl_settings=None, args=None):
    """
    Returns yt-dlp arguments for cookies, prioritizing cookie files over browser
    extraction (as files avoid Windows SQLite file-locking issues with running browsers).
    Checks:
      1. CLI --cookies <file>
      2. CLI --cookies-from-browser <browser>
      3. Playlist-specific 'cookies_file' in playlist-settings.toml
      4. Playlist-specific 'cookies_from_browser' in playlist-settings.toml
      5. Global 'cookies_file' in settings.toml
      6. Global 'ytdlp_cookies_from_browser' in settings.toml
      7. Auto-detected cookie file (e.g. data/cookies.txt or ./cookies.txt)
    Returns a list (e.g. ['--cookies', 'path/to/cookies.txt']), or [].
    """
    # 1. CLI flags
    cli_file = getattr(args, "cookies", None) if args else None
    if cli_file and os.path.isfile(cli_file):
        return ["--cookies", os.path.abspath(cli_file)]

    cli_browser = getattr(args, "cookies_from_browser", None) if args else None
    if cli_browser:
        return ["--cookies-from-browser", cli_browser.strip()]

    # 2. Playlist setting file
    if pl_settings:
        pl_file = (pl_settings.get("cookies_file") or pl_settings.get("cookies_path") or "").strip()
        if pl_file and os.path.isfile(pl_file):
            return ["--cookies", os.path.abspath(pl_file)]

    # 3. Global setting file
    if settings:
        st_file = (settings.get("cookies_file") or settings.get("ytdlp_cookies_file") or "").strip()
        if st_file and os.path.isfile(st_file):
            return ["--cookies", os.path.abspath(st_file)]

    # 4. Auto-detected cookie file in data/ or root
    auto_file = find_cookies_file()
    if auto_file:
        return ["--cookies", auto_file]

    # 5. Playlist setting browser
    if pl_settings:
        pl_browser = (pl_settings.get("cookies_from_browser") or "").strip()
        if pl_browser:
            return ["--cookies-from-browser", pl_browser]

    # 6. Global setting browser
    if settings:
        st_browser = (settings.get("ytdlp_cookies_from_browser") or settings.get("cookies_from_browser") or "").strip()
        if st_browser:
            return ["--cookies-from-browser", st_browser]

    return []


def is_bot_or_challenge_reason(text):
    """Returns True if the error text indicates a bot challenge, captcha, or rate-limiting block."""
    if not text:
        return False
    t = str(text).lower()
    return (
        "not a bot" in t
        or "confirm you're not a bot" in t
        or "confirm you’re not a bot" in t
        or "--cookies" in t
        or "http error 429" in t
        or "too many requests" in t
        or "captcha" in t
    )


def load_playlist_manifest(playlist_download_dir):
    """Loads per-playlist download cache (tracks mapping and failed tracks) from _manifest.json."""
    manifest_file = os.path.join(playlist_download_dir, DOWNLOAD_MANIFEST_FILENAME)
    if os.path.isfile(manifest_file):
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    if "tracks" not in data or not isinstance(data["tracks"], dict):
                        data["tracks"] = {}
                    if "failed" not in data or not isinstance(data["failed"], dict):
                        data["failed"] = {}

                    # Self-healing: purge transient bot detection / rate-limit / cookie errors
                    # from "failed" so previously-challenged playlists are not permanently stuck.
                    cleaned_failed = {}
                    dirty = False
                    for vid, reason in data["failed"].items():
                        if is_bot_or_challenge_reason(reason):
                            dirty = True
                            continue
                        cleaned_failed[vid] = reason
                    data["failed"] = cleaned_failed
                    if dirty:
                        try:
                            save_playlist_manifest(playlist_download_dir, data)
                        except Exception:
                            pass
                    return data
        except Exception:
            pass
    return {"tracks": {}, "failed": {}}


load_playlist_download_settings = load_playlist_manifest


def save_playlist_manifest(playlist_download_dir, manifest_dict):
    """Saves per-playlist download cache (tracks and failed mappings) to _manifest.json atomically."""
    os.makedirs(playlist_download_dir, exist_ok=True)
    manifest_file = os.path.join(playlist_download_dir, DOWNLOAD_MANIFEST_FILENAME)
    temp_file = f"{manifest_file}.__tmp_{uuid.uuid4().hex[:8]}__"
    try:
        existing = {}
        if os.path.isfile(manifest_file):
            try:
                with open(manifest_file, "r", encoding="utf-8") as ef:
                    existing = json.load(ef)
                    if not isinstance(existing, dict):
                        existing = {}
            except Exception:
                existing = {}

        tracks = manifest_dict.get("tracks") if "tracks" in manifest_dict else existing.get("tracks", {})
        failed = manifest_dict.get("failed") if "failed" in manifest_dict else existing.get("failed", {})
        data_to_save = {
            "tracks": tracks,
            "failed": failed,
        }
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, indent=4)
        if os.path.exists(manifest_file):
            os.replace(temp_file, manifest_file)
        else:
            os.rename(temp_file, manifest_file)
    except Exception as e:
        print(f"    [!] Warning: Could not save playlist manifest: {e}")
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass


save_playlist_download_settings = save_playlist_manifest



def _clean_ytdlp_value(value):
    value = (value or "").strip()
    return "" if value in ("NA", "N/A", "None", "null") else value


def format_duration(seconds):
    """Converts a duration in seconds (int, float or numeric string) to m:ss or h:mm:ss."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total < 0:
        return ""
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fetch_video_metadata_ytdlp(video_ids, settings=None):
    """
    Fetches id/title/channel/duration for YouTube video IDs using yt-dlp (one full
    extraction per video, so this is the slow path - prefer the playlist fetch when possible).
    Returns a dict {video_id: {"id", "title", "channel", "duration"}}.
    Requires no Google API quota and no OAuth credentials.
    """
    if not video_ids:
        return {}

    ytdlp_bin = find_ytdlp(settings)
    if not ytdlp_bin:
        return {}

    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as batch_f:
        for vid in video_ids:
            batch_f.write(f"https://www.youtube.com/watch?v={vid}\n")
        batch_path = batch_f.name

    cmd = list(ytdlp_bin) + [
        "--encoding", "utf-8",
        "--no-warnings",
        "--ignore-errors",
        "--no-download",
        "--print", "%(id)s\t%(title|)s\t%(channel,uploader|)s\t%(duration|)s",
    ]

    js_rt = find_js_runtime(settings)
    if js_rt:
        cmd += ["--js-runtimes", js_rt]

    cookie_args = resolve_cookies_args(settings=settings)
    if cookie_args:
        cmd += cookie_args

    cmd += ["--batch-file", batch_path]

    metadata = {}
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        for line in res.stdout.splitlines():
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 2:
                continue
            while len(parts) < 4:
                parts.append("")
            v_id, v_title, channel, duration = [_clean_ytdlp_value(p) for p in parts[:4]]
            if v_id:
                metadata[v_id] = {
                    "id": v_id,
                    "title": v_title,
                    "channel": channel,
                    "duration": format_duration(duration),
                }
    except Exception as e:
        print(f"[!] Warning: Failed to fetch video metadata with yt-dlp: {e}")
    finally:
        try:
            os.remove(batch_path)
        except OSError:
            pass

    return metadata


def fetch_video_titles_ytdlp(video_ids, settings=None):
    """Fetches video titles for a list of YouTube video IDs using yt-dlp."""
    return {
        vid: meta.get("title", "")
        for vid, meta in fetch_video_metadata_ytdlp(video_ids, settings).items()
        if meta.get("title")
    }


def find_missing_metadata(video_ids, video_metadata, required_fields):
    """Returns the video IDs (in order, no duplicates) that lack a value for any of required_fields."""
    required = [f for f in required_fields if f != "id"]
    seen = set()
    missing = []
    for vid in video_ids:
        if vid in seen:
            continue
        seen.add(vid)
        meta = video_metadata.get(vid) or {}
        if any(not meta.get(f) for f in required):
            missing.append(vid)
    return missing


def fill_missing_metadata(video_ids, video_metadata, required_fields, settings=None, playlist_id=None):
    """
    Fills in missing id/title/channel/duration values in video_metadata (in place) using yt-dlp.

    Strategy (cheapest first):
      1. One fast flat fetch of the whole playlist (if playlist_id is given).
      2. A per-video lookup only for videos that are still missing something.

    Only empty values are filled; anything already present is left untouched.
    Returns the set of video IDs yt-dlp was able to answer for. IDs that are missing
    metadata but NOT in that set are unreachable via yt-dlp (private/deleted/yt-dlp missing)
    and are the only ones worth retrying through the YouTube API.
    """
    answered = set()
    missing = find_missing_metadata(video_ids, video_metadata, required_fields)
    if not missing or not find_ytdlp(settings):
        return answered

    def _merge(vid, detail):
        base = video_metadata.setdefault(vid, {"id": vid})
        for key, value in detail.items():
            if value and not base.get(key):
                base[key] = value

    total_missing = len(missing)

    if playlist_id:
        print(f"[*] Reading playlist metadata using yt-dlp (0 Google API quota) for {total_missing} track(s) missing details...")
        flat_ids, _, flat_meta = fetch_playlist_tracks_ytdlp(playlist_id, settings)
        if flat_ids:
            for vid in missing:
                if vid in flat_meta:
                    _merge(vid, flat_meta[vid])
                    answered.add(vid)
            missing = find_missing_metadata(missing, video_metadata, required_fields)
            resolved_by_flat_fetch = total_missing - len(missing)
            if resolved_by_flat_fetch:
                print(f"[+] Resolved {resolved_by_flat_fetch} of {total_missing} track(s) from the playlist listing itself.")

    if missing:
        # These are tracks the playlist listing didn't have full details for (e.g. duration is
        # often missing from playlist listings), so each needs its own yt-dlp lookup.
        print(f"[*] {len(missing)} track(s) still need individual details - looking them up one-by-one "
              f"with yt-dlp (0 Google API quota)...")
        detailed = fetch_video_metadata_ytdlp(missing, settings)
        for vid, detail in detailed.items():
            _merge(vid, detail)
            answered.add(vid)
        still_unresolved = len(missing) - len(answered & set(missing))
        if still_unresolved:
            print(f"[*] {still_unresolved} track(s) could not be resolved via yt-dlp "
                  f"(likely private, deleted, or region-locked on YouTube).")

    return answered


LAST_YTDLP_ERROR = ""


def _run_playlist_title_ytdlp(ytdlp_bin, playlist_id, settings, flat):
    """Runs one yt-dlp probe. Returns (title, error_text)."""
    cmd = list(ytdlp_bin) + [
        "--encoding", "utf-8",
        "--no-warnings",
        "--print", "%(playlist_title|)s",
        "--no-download",
        "--playlist-items", "1",
    ]
    if flat:
        # Only reads the playlist page itself and never extracts the first video, so it
        # can't be tripped up by an unavailable/age-gated first video or a bot check.
        cmd.append("--flat-playlist")
    js_rt = find_js_runtime(settings)
    if js_rt:
        cmd += ["--js-runtimes", js_rt]
    cookie_args = resolve_cookies_args(settings=settings)
    if cookie_args:
        cmd += cookie_args
    cmd.append(f"https://www.youtube.com/playlist?list={playlist_id}")

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", env=env, check=False,
        )
    except Exception as e:
        return None, f"could not run yt-dlp: {e}"

    err = (res.stderr or "").strip().splitlines()
    err_text = err[-1].strip() if err else ""
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    if not lines:
        return None, err_text or f"yt-dlp exited with code {res.returncode} and printed nothing"

    title = lines[0].strip()
    if not title or title.lower() == "na":
        return None, err_text or "yt-dlp returned no playlist title"
    return title, ""


def fetch_playlist_title_ytdlp(playlist_id, settings=None):
    """
    Fetches the title of a YouTube playlist using yt-dlp. Returns the title or None.
    On failure, the reason is stored in LAST_YTDLP_ERROR so callers can show it.
    """
    global LAST_YTDLP_ERROR
    LAST_YTDLP_ERROR = ""
    ytdlp_bin = find_ytdlp(settings)
    if not ytdlp_bin:
        LAST_YTDLP_ERROR = "yt-dlp executable not found (set ytdlp_path in settings.toml, put it in the project folder, or add it to PATH)"
        return None

    title, err = _run_playlist_title_ytdlp(ytdlp_bin, playlist_id, settings, flat=True)
    if title:
        return title
    # Fallback: full extraction of item 1 (older behavior)
    title, err2 = _run_playlist_title_ytdlp(ytdlp_bin, playlist_id, settings, flat=False)
    if title:
        return title
    LAST_YTDLP_ERROR = err2 or err
    return None


def fetch_playlist_tracks_ytdlp(playlist_id, settings=None):
    """
    Fetches the live list of tracks for a YouTube playlist using yt-dlp (single fast call).
    Returns (video_ids, video_titles, video_metadata) ordered exactly as on YouTube (1..N).
    video_metadata values contain id/title/channel/duration (any of channel/duration may be
    empty if YouTube's playlist listing does not include them).
    Returns (None, None, None) if yt-dlp fails or playlist is private.
    """
    ytdlp_bin = find_ytdlp(settings)
    if not ytdlp_bin:
        return None, None, None

    cmd = list(ytdlp_bin) + [
        "--encoding", "utf-8",
        "--no-warnings",
        "--flat-playlist",
        "--no-download",
        "--print", "%(playlist_index|)s\t%(id)s\t%(title|)s\t%(channel,uploader|)s\t%(duration|)s",
    ]
    js_rt = find_js_runtime(settings)
    if js_rt:
        cmd += ["--js-runtimes", js_rt]
    cookie_args = resolve_cookies_args(settings=settings)
    if cookie_args:
        cmd += cookie_args
    cmd.append(f"https://www.youtube.com/playlist?list={playlist_id}")

    raw_items = []
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )
        if res.returncode != 0 and not res.stdout.strip():
            # Hard failure - yt-dlp produced nothing (playlist is truly private or inaccessible)
            return None, None, None
        if res.returncode != 0:
            # Partial failure - some videos unavailable but playlist is accessible;
            # log the warnings and continue parsing whatever was returned.
            for warn_line in res.stderr.splitlines():
                warn_line = warn_line.strip()
                if warn_line and ("ERROR" in warn_line or "WARNING" in warn_line):
                    print(f"    [yt-dlp] {warn_line}")

        for line in res.stdout.splitlines():
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 3:
                continue
            while len(parts) < 5:
                parts.append("")
            idx_str, vid_id, title, channel, duration = [_clean_ytdlp_value(p) for p in parts[:5]]
            try:
                idx = int(idx_str)
            except ValueError:
                idx = len(raw_items) + 1
            if vid_id:
                meta = {
                    "id": vid_id,
                    "title": title or "Untitled Video",
                    "channel": channel,
                    "duration": format_duration(duration),
                }
                raw_items.append((idx, vid_id, meta))

        if not raw_items:
            return None, None, None

        raw_items.sort(key=lambda x: x[0])
        video_ids = [x[1] for x in raw_items]
        video_metadata = {x[1]: x[2] for x in raw_items}
        video_titles = {vid: video_metadata[vid].get("title", "Untitled Video") for vid in video_ids}
        return video_ids, video_titles, video_metadata
    except Exception:
        return None, None, None


_AUDIO_EXTS = {'.opus', '.m4a', '.mp3', '.aac', '.flac', '.ogg', '.wav', '.webm'}
_VIDEO_EXTS = {'.mp4', '.mkv', '.mov', '.avi'}
# .webm is yt-dlp's raw, un-converted download container - it's what audio-mode
# downloads fall back to when ffmpeg isn't available to run --extract-audio. It's
# listed as an audio extension (rather than left unrecognized) so a folder that ends
# up with .webm files still gets tracked correctly in the manifest and numbered,
# instead of those files being invisible to both. See the ffmpeg check in
# _run_ytdlp_batch for the loud warning that should stop this from happening quietly.


def _is_media_filename(fname):
    ext = os.path.splitext(fname)[1].lower()
    return ext in _AUDIO_EXTS or ext in _VIDEO_EXTS


def _folder_has_media(folder):
    try:
        entries = os.listdir(folder)
    except OSError:
        return False
    return any(_is_media_filename(fname) for fname in entries)


def _move_flat_download_cache_to_subfolder(playlist_download_dir, subfolder_name):
    """
    Moves legacy flat playlist downloads into a layout subfolder.
    Used when a playlist that was previously downloaded as main_only is changed
    to a sectioned download mode that expects FULL_PLAYLIST.
    """
    if not os.path.isdir(playlist_download_dir):
        return 0

    subfolder_dir = os.path.join(playlist_download_dir, subfolder_name)
    root_manifest_path = os.path.join(playlist_download_dir, DOWNLOAD_MANIFEST_FILENAME)
    moved_count = 0

    try:
        entries = os.listdir(playlist_download_dir)
    except OSError:
        return 0

    media_files = []
    for fname in entries:
        fpath = os.path.join(playlist_download_dir, fname)
        if os.path.isfile(fpath) and _is_media_filename(fname):
            media_files.append(fname)

    has_manifest = os.path.isfile(root_manifest_path)
    if not media_files and not has_manifest:
        return 0

    os.makedirs(subfolder_dir, exist_ok=True)

    for fname in media_files:
        src = os.path.join(playlist_download_dir, fname)
        dst = os.path.join(subfolder_dir, fname)
        try:
            if not os.path.exists(dst):
                shutil.move(src, dst)
                moved_count += 1
        except OSError as exc:
            print(f"    [!] Could not move '{fname}' into '{subfolder_name}': {exc}")

    if has_manifest:
        root_manifest = load_playlist_manifest(playlist_download_dir)
        dest_manifest = load_playlist_manifest(subfolder_dir)
        merged_tracks = dest_manifest.get("tracks", {})
        merged_tracks.update(root_manifest.get("tracks", {}))
        save_playlist_manifest(subfolder_dir, {"tracks": merged_tracks})
        try:
            os.remove(root_manifest_path)
        except OSError:
            pass

    if moved_count:
        print(f"[*] Moved {moved_count} existing flat download(s) into '{subfolder_name}' for the new download layout.")
    return moved_count


def _detect_folder_format(folder):
    """Returns 'audio', 'video', or None if the folder is empty or has no recognisable media."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    for fname in entries:
        if fname.startswith('.') or fname in _IGNORED_FILENAMES:
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext in _AUDIO_EXTS:
            return 'audio'
        if ext in _VIDEO_EXTS:
            return 'video'
    return None


_NUM_PREFIX_RE = re.compile(r'^(\d+)\s+-\s+(.+)$')

_YTDLP_SUBS = str.maketrans({
    '\uff1a': ':',   # fullwidth colon ： → :
    '\ua789': ':',   # modifier letter colon ꞉ → :
    '\uff5c': '|',   # fullwidth vertical bar ｜ → |
    '\uff0a': '*',   # fullwidth asterisk ＊ → *
    '\uff1f': '?',   # fullwidth question mark ？ → ?
    '\uff02': '"',   # fullwidth quotation mark ＂ → "
    '\uff1c': '<',   # fullwidth less-than ＜ → <
    '\uff1e': '>',   # fullwidth greater-than ＞ → >
    '\uff3c': '\\',  # fullwidth reverse solidus ＼ → \
    '\uff0f': '/',   # fullwidth solidus ／ → /
})


def _norm_title(s: str) -> str:
    """
    Normalise a title for matching between playlist .txt entries and
    on-disk filenames. Reverses common yt-dlp filename-safe substitutions,
    normalizes Unicode to NFKC, unifies pipe/underscore separators,
    collapses whitespace, and lowercases.
    """
    s = s.translate(_YTDLP_SUBS)
    s = unicodedata.normalize('NFKC', s)
    # Treat '|', fullwidth '｜', and '_' all as neutral space separators
    s = s.replace('|', ' ').replace('\uff5c', ' ').replace('_', ' ')
    s = re.sub(r'[\s\u00a0]+', ' ', s)
    return s.strip().rstrip('. ').lower()


def _norm_alpha(s: str) -> str:
    """Extra-lenient alphanumeric normalization for fallback matching."""
    return re.sub(r'[\W_]+', '', _norm_title(s))


def _sync_deletions(playlist_download_dir, target_video_ids, target_video_titles, manifest_tracks=None):
    """
    Accounts for deletions in playlist.txt when synchronizing downloads:
    - Removes media files from playlist_download_dir that belonged to removed tracks.
    Returns deleted_files_count.
    """
    deleted_files_count = 0

    # Build set of current target normalized titles
    current_target_titles = set()
    for vid in target_video_ids:
        t = target_video_titles.get(vid, "").strip()
        if t:
            current_target_titles.add(_norm_title(t))
            current_target_titles.add(_norm_alpha(t))

    target_id_set = set(target_video_ids)
    # Media files known to belong to still-current target tracks via manifest
    kept_manifest_files = set()
    if manifest_tracks:
        for vid, fname in manifest_tracks.items():
            if vid in target_id_set:
                kept_manifest_files.add(fname)

    try:
        entries = os.listdir(playlist_download_dir)
    except OSError:
        entries = []

    for fname in entries:
        if fname.startswith('.') or fname in _IGNORED_FILENAMES or fname in kept_manifest_files:
            continue
        fpath = os.path.join(playlist_download_dir, fname)
        if not os.path.isfile(fpath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _AUDIO_EXTS and ext not in _VIDEO_EXTS:
            continue

        stem = os.path.splitext(fname)[0]
        m = _NUM_PREFIX_RE.match(stem)
        base_title = m.group(2).strip() if m else stem
        norm = _norm_title(base_title)
        alpha = _norm_alpha(base_title)

        # If this file's title does not match ANY current track in the playlist, delete it
        if norm not in current_target_titles and alpha not in current_target_titles:
            try:
                os.remove(fpath)
                deleted_files_count += 1
                print(f"    [-] Removed deleted track: '{fname}'")
            except OSError as exc:
                print(f"    [!] Could not remove '{fname}': {exc}")

    return deleted_files_count


def _audit_tracks_on_disk(playlist_download_dir, target_video_ids, target_video_titles, manifest_tracks=None):
    """
    Scans playlist_download_dir to determine which target tracks actually have
    a valid, non-empty media file on disk (> 0 bytes) and which are missing.
    Uses manifest_tracks if available, and falls back to normalized/alphanumeric title matching.

    Returns:
      present_video_ids: set of video IDs that have valid files on disk
      missing_video_ids: list of video IDs (in playlist order) that need to be downloaded
      id_to_file_map: dict of {vid_id: current_file_name_on_disk}
    """
    if manifest_tracks is None:
        manifest_tracks = {}

    present_video_ids = set()
    id_to_file_map = {}

    # Step 1: Check manifest matches
    for vid in target_video_ids:
        expected_fname = manifest_tracks.get(vid)
        if expected_fname:
            fpath = os.path.join(playlist_download_dir, expected_fname)
            if os.path.isfile(fpath):
                try:
                    if os.path.getsize(fpath) > 0:
                        present_video_ids.add(vid)
                        id_to_file_map[vid] = expected_fname
                    else:
                        # Corrupted 0-byte file: remove so it can be re-downloaded
                        os.remove(fpath)
                except OSError:
                    pass

    # If all tracks were found via manifest, fast return
    unresolved_vids = [v for v in target_video_ids if v not in present_video_ids]
    if not unresolved_vids:
        return present_video_ids, [], id_to_file_map

    # Step 2: Scan disk for existing media files to match unresolved tracks
    try:
        entries = os.listdir(playlist_download_dir)
    except OSError:
        entries = []

    claimed_files = set(id_to_file_map.values())
    candidate_files = []
    for fname in entries:
        if fname.startswith('.') or fname in _IGNORED_FILENAMES or fname in claimed_files:
            continue
        fpath = os.path.join(playlist_download_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            if os.path.getsize(fpath) == 0:
                os.remove(fpath)
                continue
        except OSError:
            continue

        ext = os.path.splitext(fname)[1].lower()
        if ext not in _AUDIO_EXTS and ext not in _VIDEO_EXTS:
            continue

        stem = os.path.splitext(fname)[0]
        m = _NUM_PREFIX_RE.match(stem)
        base_title = m.group(2).strip() if m else stem
        candidate_files.append({
            "fname": fname,
            "fpath": fpath,
            "norm": _norm_title(base_title),
            "alpha": _norm_alpha(base_title),
        })

    targets = []
    for vid in unresolved_vids:
        raw_title = target_video_titles.get(vid, "").strip()
        targets.append({
            "vid": vid,
            "norm": _norm_title(raw_title),
            "alpha": _norm_alpha(raw_title),
        })

    assigned_candidates = set()

    # Pass 1: exact normalized match
    newly_discovered = {}
    for t in targets:
        for idx, cf in enumerate(candidate_files):
            if idx not in assigned_candidates and t["norm"] and t["norm"] == cf["norm"]:
                assigned_candidates.add(idx)
                present_video_ids.add(t["vid"])
                id_to_file_map[t["vid"]] = cf["fname"]
                newly_discovered[t["vid"]] = cf["fname"]
                break

    # Pass 2: alphanumeric match
    for t in targets:
        if t["vid"] in present_video_ids:
            continue
        for idx, cf in enumerate(candidate_files):
            if idx not in assigned_candidates and t["alpha"] and t["alpha"] == cf["alpha"]:
                assigned_candidates.add(idx)
                present_video_ids.add(t["vid"])
                id_to_file_map[t["vid"]] = cf["fname"]
                newly_discovered[t["vid"]] = cf["fname"]
                break

    # Pass 3: alphanumeric substring / contains match (min length 4 to prevent false positives)
    for t in targets:
        if t["vid"] in present_video_ids:
            continue
        t_a = t["alpha"]
        if len(t_a) < 4:
            continue
        for idx, cf in enumerate(candidate_files):
            c_a = cf["alpha"]
            if idx not in assigned_candidates and len(c_a) >= 4 and (t_a in c_a or c_a in t_a):
                assigned_candidates.add(idx)
                present_video_ids.add(t["vid"])
                id_to_file_map[t["vid"]] = cf["fname"]
                newly_discovered[t["vid"]] = cf["fname"]
                break

    # Pass 4: prefix normalized match (min length 4)
    for t in targets:
        if t["vid"] in present_video_ids:
            continue
        t_n = t["norm"]
        if len(t_n) < 4:
            continue
        for idx, cf in enumerate(candidate_files):
            c_n = cf["norm"]
            if idx not in assigned_candidates and len(c_n) >= 4 and (t_n.startswith(c_n) or c_n.startswith(t_n)):
                assigned_candidates.add(idx)
                present_video_ids.add(t["vid"])
                id_to_file_map[t["vid"]] = cf["fname"]
                newly_discovered[t["vid"]] = cf["fname"]
                break

    # Persist any newly discovered matches into manifest_tracks immediately
    if newly_discovered:
        manifest_tracks.update(newly_discovered)
        save_playlist_manifest(playlist_download_dir, {"tracks": manifest_tracks})

    missing_video_ids = [v for v in target_video_ids if v not in present_video_ids]
    return present_video_ids, missing_video_ids, id_to_file_map


def _sync_file_numbering(playlist_download_dir, target_video_ids, target_video_titles, number_files, manifest_tracks=None):
    """
    Renames existing downloaded files to match the 'number_files' setting
    and the exact order of tracks in the target playlist.
    Uses multi-pass matching (manifest -> exact -> alphanumeric -> 1:1) and a collision-free two-phase rename.

    Returns a (renamed, skipped, errors, updated_manifest) 4-tuple.
    """
    pad_width = max(2, len(str(len(target_video_ids))))

    # Build target mappings: index -> (target_idx, clean_title, norm_title, alpha_title)
    targets = []
    for i, vid_id in enumerate(target_video_ids, 1):
        raw_title = target_video_titles.get(vid_id, "").strip()
        clean_title = sanitize_filename(raw_title) if raw_title else f"Track {i}"
        targets.append({
            "idx": i,
            "vid_id": vid_id,
            "clean_title": clean_title,
            "norm": _norm_title(raw_title),
            "alpha": _norm_alpha(raw_title),
        })

    try:
        entries = sorted(os.listdir(playlist_download_dir))
    except OSError as exc:
        # Don't let a transient/permission scan failure wipe out a manifest that
        # already had good data - fall back to what we knew before, rather than {}.
        print(f"    [!] Warning: could not list '{playlist_download_dir}' ({exc}); "
              f"keeping the existing manifest for this run.")
        return 0, 0, 0, dict(manifest_tracks or {})

    media_files = []
    for fname in entries:
        if fname.startswith('.') or fname in _IGNORED_FILENAMES:
            continue
        fpath = os.path.join(playlist_download_dir, fname)
        if not os.path.isfile(fpath):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext not in _AUDIO_EXTS and ext not in _VIDEO_EXTS:
            continue
        stem = os.path.splitext(fname)[0]
        m = _NUM_PREFIX_RE.match(stem)
        base_title = m.group(2).strip() if m else stem
        media_files.append({
            "fname": fname,
            "fpath": fpath,
            "ext": ext,
            "stem": stem,
            "base_title": base_title,
            "norm": _norm_title(base_title),
            "alpha": _norm_alpha(base_title),
            "has_number": m is not None,
            "num": int(m.group(1)) if m else None,
        })

    # Sort media files so numbered files retain their sequence
    media_files.sort(key=lambda x: (0, x["num"]) if x["has_number"] else (1, x["fname"].lower()))

    matched_pairs = []  # (media_file, target)
    assigned_target_indices = set()
    assigned_mf_paths = set()

    # Pass 0: match using existing manifest mapping if present and file is still on disk
    if manifest_tracks:
        mf_by_fname = {mf["fname"]: mf for mf in media_files}
        for t in targets:
            expected_fname = manifest_tracks.get(t["vid_id"])
            if expected_fname and expected_fname in mf_by_fname:
                mf = mf_by_fname[expected_fname]
                if mf["fpath"] not in assigned_mf_paths:
                    matched_pairs.append((mf, t))
                    assigned_target_indices.add(t["idx"])
                    assigned_mf_paths.add(mf["fpath"])

    remaining_media = [mf for mf in media_files if mf["fpath"] not in assigned_mf_paths]
    unmatched_files = []

    # Pass 1: exact normalized match
    for mf in remaining_media:
        matched = None
        for t in targets:
            if t["idx"] not in assigned_target_indices and t["norm"] and t["norm"] == mf["norm"]:
                matched = t
                assigned_target_indices.add(t["idx"])
                break
        if matched:
            matched_pairs.append((mf, matched))
        else:
            unmatched_files.append(mf)

    # Pass 2: alphanumeric match (handles stripped punctuation, accents, or separator differences)
    still_unmatched = []
    for mf in unmatched_files:
        matched = None
        for t in targets:
            if t["idx"] not in assigned_target_indices and t["alpha"] and t["alpha"] == mf["alpha"]:
                matched = t
                assigned_target_indices.add(t["idx"])
                break
        if matched:
            matched_pairs.append((mf, matched))
        else:
            still_unmatched.append(mf)

    # Pass 3: alphanumeric substring / contains match (min length 4)
    further_unmatched = []
    for mf in still_unmatched:
        matched = None
        m_a = mf["alpha"]
        if len(m_a) >= 4:
            for t in targets:
                t_a = t["alpha"]
                if t["idx"] not in assigned_target_indices and len(t_a) >= 4:
                    if m_a in t_a or t_a in m_a:
                        matched = t
                        assigned_target_indices.add(t["idx"])
                        break
        if matched:
            matched_pairs.append((mf, matched))
        else:
            further_unmatched.append(mf)

    # Pass 4: prefix normalized match (min length 4)
    still_unmatched = []
    for mf in further_unmatched:
        matched = None
        m_n = mf["norm"]
        if len(m_n) >= 4:
            for t in targets:
                t_n = t["norm"]
                if t["idx"] not in assigned_target_indices and len(t_n) >= 4:
                    if m_n.startswith(t_n) or t_n.startswith(m_n):
                        matched = t
                        assigned_target_indices.add(t["idx"])
                        break
        if matched:
            matched_pairs.append((mf, matched))
        else:
            still_unmatched.append(mf)

    planned_renames = []
    skipped = len(still_unmatched)
    updated_manifest = {}
    used_names = set()

    for mf, t in matched_pairs:
        target_idx = t["idx"]
        clean_title = t["clean_title"]
        ext = mf["ext"]
        fpath = mf["fpath"]

        if number_files:
            new_name = f"{target_idx:0{pad_width}d} - {clean_title}{ext}"
        else:
            cand_name = f"{clean_title}{ext}"
            if cand_name in used_names:
                dup_count = 2
                while f"{clean_title} ({dup_count}){ext}" in used_names:
                    dup_count += 1
                new_name = f"{clean_title} ({dup_count}){ext}"
            else:
                new_name = cand_name

        used_names.add(new_name)
        updated_manifest[t["vid_id"]] = new_name

        new_path = os.path.join(playlist_download_dir, new_name)
        if os.path.abspath(fpath) == os.path.abspath(new_path):
            continue

        planned_renames.append((fpath, new_path))

    if not planned_renames:
        return 0, skipped, 0, updated_manifest

    def _rename_with_retry(src, dst, max_attempts=5, delay=0.1):
        """Renames src to dst, retrying if transient Windows file locking (WinError 32/33) occurs."""
        for attempt in range(max_attempts):
            try:
                os.rename(src, dst)
                return
            except OSError as e:
                if attempt < max_attempts - 1 and getattr(e, "winerror", None) in (32, 33):
                    time.sleep(delay)
                else:
                    raise

    # Collision-free two-phase rename
    token = uuid.uuid4().hex[:8]
    temp_renames = []
    errors = 0

    for idx, (curr_path, final_path) in enumerate(planned_renames):
        temp_path = f"{curr_path}.__ypm_{token}_{idx}__"
        try:
            _rename_with_retry(curr_path, temp_path)
            temp_renames.append((temp_path, final_path))
        except OSError as exc:
            print(f"    [!] Could not stage rename for '{os.path.basename(curr_path)}': {exc}")
            errors += 1

    renamed = 0
    for temp_path, final_path in temp_renames:
        try:
            if os.path.exists(final_path):
                os.remove(final_path)
            _rename_with_retry(temp_path, final_path)
            renamed += 1
        except OSError as exc:
            print(f"    [!] Could not finalize rename to '{os.path.basename(final_path)}': {exc}")
            errors += 1
            try:
                orig_path = temp_path.split(".__ypm_")[0]
                _rename_with_retry(temp_path, orig_path)
            except OSError:
                pass

    return renamed, skipped, errors, updated_manifest


def _safe_sync_file_numbering(folder_dir, target_video_ids, target_video_titles, number_files, manifest_tracks=None):
    """
    Wraps _sync_file_numbering so a bug or unexpected condition in the numbering/
    matching logic can never silently wipe out or skip an entire folder's manifest.

    Previously, if anything in this step raised an exception, it would propagate
    all the way up and abort the whole 'download' run - which, for a folder reached
    partway through processing dozens of sections, could look exactly like "this
    folder randomly ended up with zero manifest entries and no numbering" while
    every subsequent folder in the same run went completely untouched. Now the
    error is always printed in full (never swallowed) and this folder falls back to
    whatever manifest data was already known-good, instead of losing it.
    """
    try:
        return _sync_file_numbering(folder_dir, target_video_ids, target_video_titles, number_files, manifest_tracks)
    except Exception:
        print(f"    [!] ERROR: Unexpected failure while numbering/updating the manifest for "
              f"'{os.path.basename(folder_dir)}':")
        traceback.print_exc()
        print(f"    [!] Falling back to the previous manifest for this folder so no data is lost. "
              f"This folder was NOT renumbered this run - please report this traceback, then re-run "
              f"'download' once it's fixed.")
        return 0, 0, 0, dict(manifest_tracks or {})


def resolve_download_format(fmt=None, allow_prompt=True):
    """
    Resolves whether to download as audio or video.
    If fmt is provided ('audio' or 'video'), validates and returns it.
    If fmt is not provided and allow_prompt is True, interactively prompts the user.
    """
    if fmt:
        f = fmt.strip().lower()
        if f in ("audio", "video"):
            return f
        if f in ("a", "mp3", "m4a", "sound"):
            return "audio"
        if f in ("v", "mp4", "vid"):
            return "video"

    if allow_prompt:
        print("\n[?] Select download format:")
        print("    [1] Audio")
        print("    [2] Video")
        print()
        import sys
        while True:
            try:
                choice = input("Select format (1 for audio, 2 for video): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n[!] Operation cancelled by user.")
                sys.exit(130)

            if not choice or choice in ("1", "audio", "a", "mp3"):
                print("[+] Selected download format: 'audio'")
                return "audio"
            elif choice in ("2", "video", "v", "mp4"):
                print("[+] Selected download format: 'video'")
                return "video"

            print("[!] Invalid selection. Please enter 1 for audio or 2 for video.")

    return "audio"


def _run_ytdlp_single_chunk(video_ids, folder_dir, fmt, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt, on_track_downloaded=None, on_track_failed=None, pl_settings=None, args=None, total_tracks=None, start_offset=0):
    """
    Runs a single yt-dlp invocation for a chunk of video_ids into folder_dir.
    Streams progress and captures successfully downloaded video IDs in real time.
    Returns True if a bot challenge was encountered, False otherwise.
    """
    if not video_ids:
        return False
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as batch_f:
        for vid_id in video_ids:
            batch_f.write(f"https://www.youtube.com/watch?v={vid_id}\n")
        batch_path = batch_f.name

    output_template = os.path.join(folder_dir, "%(title)s.%(ext)s")
    cmd = list(ytdlp_bin) + [
        "--encoding", "utf-8",
        "--print", "before_dl:__START__\t%(id)s\t%(title)s",
        "--print", "after_move:__DONE__\t%(id)s\t%(filepath)s",
        "--progress",
    ]
    if js_rt:
        cmd += ["--js-runtimes", js_rt]

    if fmt == "audio":
        # Exclude multi-channel ec-3/ac-3 surround streams that cause ffmpeg M4A container errors
        cmd += [
            "--format", "ba[acodec!=ec-3][acodec!=ac-3]/bestaudio/best",
            "--extract-audio",
        ]
        if ffmpeg_bin:
            cmd += ["--embed-metadata"]
            if embed_thumbnail:
                cmd += ["--embed-thumbnail"]
    else:  # video
        cmd += [
            "--format", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b",
            "--merge-output-format", "mp4",
        ]
        if ffmpeg_bin:
            cmd += ["--embed-metadata"]

    if ffmpeg_bin:
        cmd += ["--ffmpeg-location", ffmpeg_bin]
    else:
        if fmt == "video":
            print("  [i] Note: ffmpeg was not detected. MP4 video merging may not work without it.")
            print("      Place ffmpeg.exe in this project's folder or define 'ffmpeg_path' in settings.toml.\n")
        else:
            print("  [!] WARNING: ffmpeg was not detected, but audio mode needs it to convert/extract audio.")
            print("      Without ffmpeg, yt-dlp keeps the raw downloaded stream (typically '.webm') instead")
            print("      of converting it to .opus/.m4a/etc. ypm will still track these raw files so they")
            print("      aren't lost, but quality/compatibility will suffer. Place ffmpeg.exe in this")
            print("      project's folder or define 'ffmpeg_path' in settings.toml to fix this properly.\n")

    cmd += [
        "--ignore-errors",
        "--no-overwrites",
        "--retries", "3",
        "--fragment-retries", "3",
        "--extractor-retries", "2",
        "--retry-sleep", "linear=1:3:1",
        "--newline",  # Force \n-terminated progress so readline() sees updates in real time
    ]

    # Player client & extractor args configuration
    extractor_args = (pl_settings.get("ytdlp_extractor_args") if pl_settings else "") or (settings.get("ytdlp_extractor_args") if settings else "")
    player_client = (pl_settings.get("ytdlp_player_client") if pl_settings else "") or (settings.get("ytdlp_player_client") if settings else "")
    if extractor_args:
        cmd += ["--extractor-args", extractor_args.strip()]
    elif player_client:
        cmd += ["--extractor-args", f"youtube:player_client={player_client.strip()}"]

    cmd += [
        "--output", output_template,
        "--batch-file", batch_path,
    ]

    # Cookies resolution: prioritize cookie files over browser extraction
    cookie_args = resolve_cookies_args(settings=settings, pl_settings=pl_settings, args=args)
    if cookie_args:
        cmd += cookie_args
        if cookie_args[0] == "--cookies":
            try:
                c_disp = os.path.relpath(cookie_args[1], os.getcwd())
            except Exception:
                c_disp = cookie_args[1]
            print(f"[*] Authenticating with cookie file: '{c_disp}'")
        elif cookie_args[0] == "--cookies-from-browser":
            print(f"[*] Authenticating with browser cookies: '{cookie_args[1]}'")

    # Sleep settings: default to 0 for sleep_requests to avoid stalling thumbnail/format checks
    sleep_requests = settings.get("ytdlp_sleep_requests", 0) if settings else 0
    try:
        sleep_requests = float(sleep_requests)
    except (TypeError, ValueError):
        sleep_requests = 0
    if sleep_requests > 0:
        cmd += ["--sleep-requests", str(sleep_requests)]

    sleep_interval = settings.get("ytdlp_sleep_interval", 0) if settings else 0
    try:
        sleep_interval = float(sleep_interval)
    except (TypeError, ValueError):
        sleep_interval = 0
    if sleep_interval > 0:
        cmd += ["--sleep-interval", str(sleep_interval)]

    print(f"[*] Starting yt-dlp for '{os.path.basename(folder_dir)}' ({'Audio' if fmt == 'audio' else 'Video'}, {len(video_ids)} track(s))...\n")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    start_pattern = re.compile(r"^__START__\t([A-Za-z0-9_-]{11})\t(.*)$")
    done_pattern = re.compile(r"^__DONE__\t([A-Za-z0-9_-]{11})\t(.*)$")
    error_pattern = re.compile(r"^ERROR:\s*(?:\[[^\]]+\]\s*)?([A-Za-z0-9_-]{11}):\s*(.+)$")
    progress_re = re.compile(r'^\[download\]\s+\d+\.?\d*%')

    bot_detected = False
    consecutive_bot_errors = 0
    db_locked_warned = False
    _mid_progress = False

    current_vid = None
    current_title = ""
    current_index = start_offset
    total_tracks_count = total_tracks or len(video_ids)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            bufsize=1,
        )
        if proc.stdout:
            for raw_line in iter(proc.stdout.readline, ''):
                line = raw_line.rstrip("\r\n")
                low_line = line.lower()

                # Detect Windows locked Chromium cookie database
                if not db_locked_warned and ("could not copy chrome cookie database" in low_line or "cookie database is locked" in low_line):
                    db_locked_warned = True
                    print("\n" + "=" * 68)
                    print(" [!] Browser Cookie Database Locked by Windows")
                    print("=" * 68)
                    print("  Chromium browsers (Edge, Chrome, Brave) lock their cookie database")
                    print("  while the browser is running.")
                    print("  Solutions:")
                    print("    1. Close your browser completely before running the download.")
                    print("    2. Or drop 'cookies.txt' into the 'data/' folder using the")
                    print("       'Get cookies.txt LOCALLY' extension (works while browser is open!).")
                    print("    3. Or use Firefox: cookies_from_browser = \"firefox\" (never locks).")
                    print("=" * 68 + "\n")

                # Detect cookie rotation / expiration notice
                if "cookies are no longer valid" in low_line or "rotated in the browser" in low_line:
                    prefix = "\n" if _mid_progress else ""
                    print(f"{prefix}  [!] YouTube indicated that session cookies were rotated or expired.")
                    print(f"      Next batch chunk will automatically re-read fresh cookies from browser.")
                    _mid_progress = False
                    continue

                # Track starting download
                sm = start_pattern.match(line)
                if sm:
                    current_vid = sm.group(1)
                    current_title = sm.group(2).strip() or current_vid
                    current_index += 1
                    if _mid_progress:
                        print()
                        _mid_progress = False
                    print(f"  [*] [{current_index}/{total_tracks_count}] Downloading: '{current_title}'...")
                    continue

                # Track completed download and move
                dm = done_pattern.match(line)
                if dm:
                    consecutive_bot_errors = 0
                    vid = dm.group(1)
                    filepath = dm.group(2).strip()
                    fname = os.path.basename(filepath)
                    idx = current_index if current_vid == vid else (start_offset + video_ids.index(vid) + 1 if vid in video_ids else current_index)
                    prefix = "\n" if _mid_progress else ""
                    print(f"{prefix}  [+] [{idx}/{total_tracks_count}] Completed:   '{fname}'")
                    _mid_progress = False
                    if on_track_downloaded:
                        on_track_downloaded(vid, filepath)
                    continue

                # Track-specific error
                em = error_pattern.match(line)
                if em:
                    failed_vid = em.group(1)
                    failed_reason = em.group(2).strip()
                    if is_bot_or_challenge_reason(failed_reason):
                        consecutive_bot_errors += 1
                        # Do NOT call on_track_failed for bot challenge errors to avoid poisoning manifest!
                        if consecutive_bot_errors >= 3:
                            bot_detected = True
                            if _mid_progress:
                                print()
                                _mid_progress = False
                            print("\n" + "=" * 68)
                            print(" [!] YouTube Bot Detection / Rate-Limit Challenge Encountered")
                            print("=" * 68)
                            print("  YouTube is requesting bot confirmation for this network session:")
                            print("    'Sign in to confirm you’re not a bot'")
                            print()
                            print("  Stopping download batch early to avoid redundant failed requests")
                            print("  and protect against temporary IP blocks.")
                            print()
                            print("  HOW TO RESOLVE:")
                            print("   1. (Recommended) Drop 'cookies.txt' into 'data/cookies.txt':")
                            print("      • Install 'Get cookies.txt LOCALLY' extension in your browser.")
                            print("      • Log into YouTube, click the extension, and export in Netscape format.")
                            print("      • Save as 'data/cookies.txt' (ypm auto-detects it).")
                            print("   2. Use Firefox cookies (which does not lock while open):")
                            print("      • In settings.toml or playlist-settings.toml, set: cookies_from_browser = \"firefox\"")
                            print("   3. If using Edge/Chrome: close the browser completely before running.")
                            print("   4. Switch to a mobile hotspot or different network connection.")
                            print("=" * 68 + "\n")
                            try:
                                proc.terminate()
                                proc.wait(timeout=2)
                            except Exception:
                                pass
                            break
                    else:
                        consecutive_bot_errors = 0
                        idx = current_index if current_vid == failed_vid else (start_offset + video_ids.index(failed_vid) + 1 if failed_vid in video_ids else current_index)
                        prefix = "\n" if _mid_progress else ""
                        print(f"{prefix}  [-] [{idx}/{total_tracks_count}] Failed:      '{failed_vid}' - {failed_reason}")
                        _mid_progress = False
                        if on_track_failed:
                            on_track_failed(failed_vid, failed_reason)
                    continue

                if is_bot_or_challenge_reason(line):
                    consecutive_bot_errors += 1
                    if consecutive_bot_errors >= 3:
                        bot_detected = True
                        if _mid_progress:
                            print()
                            _mid_progress = False
                        print("\n" + "=" * 68)
                        print(" [!] YouTube Bot Detection Encountered: Stopping batch early.")
                        print("=" * 68 + "\n")
                        try:
                            proc.terminate()
                            proc.wait(timeout=2)
                        except Exception:
                            pass
                        break
                    continue

                if line:
                    if progress_re.match(line):
                        if '100%' not in line:
                            print(f"\r      {line}", end='', flush=True)
                            _mid_progress = True
                        else:
                            print(f"\r      {line}", flush=True)
                            _mid_progress = False
                    elif line.startswith("ERROR:") or line.startswith("WARNING:"):
                        prefix = "\n" if _mid_progress else ""
                        print(f"{prefix}  [!] {line}", flush=True)
                        _mid_progress = False
                    elif "has already been downloaded" in line:
                        prefix = "\n" if _mid_progress else ""
                        print(f"{prefix}  [=] {line}", flush=True)
                        _mid_progress = False
        proc.wait()
    except KeyboardInterrupt:
        try:
            if 'proc' in locals() and proc:
                proc.terminate()
                proc.wait(timeout=2)
        except Exception:
            pass
        raise
    except Exception as e:
        print(f"[!] Error running yt-dlp: {e}")
    finally:
        try:
            os.remove(batch_path)
        except OSError:
            pass

    return bot_detected


def _run_ytdlp_batch(video_ids, folder_dir, fmt, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt, on_track_downloaded=None, on_track_failed=None, pl_settings=None, args=None):
    """
    Runs yt-dlp against the given video_ids into folder_dir in manageable chunks (e.g. 25 tracks).
    Chunking refreshes cookies from the browser at each chunk boundary, preventing stale session timeouts
    on large playlists, streams progress, and aborts early if anti-bot challenges occur.
    Returns True if a bot challenge was encountered, False otherwise.
    """
    if not video_ids:
        return False

    chunk_size = 25
    if settings:
        try:
            chunk_size = max(5, int(settings.get("ytdlp_batch_size", 25)))
        except (TypeError, ValueError):
            chunk_size = 25

    total_tracks = len(video_ids)
    if total_tracks <= chunk_size:
        return _run_ytdlp_single_chunk(
            video_ids, folder_dir, fmt, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
            on_track_downloaded=on_track_downloaded,
            on_track_failed=on_track_failed,
            pl_settings=pl_settings,
            args=args,
            total_tracks=total_tracks,
            start_offset=0
        )

    print(f"[*] Processing {total_tracks} track(s) in chunks of {chunk_size} to keep browser cookies fresh...\n")
    for start_idx in range(0, total_tracks, chunk_size):
        chunk = video_ids[start_idx:start_idx + chunk_size]
        bot_detected = _run_ytdlp_single_chunk(
            chunk, folder_dir, fmt, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
            on_track_downloaded=on_track_downloaded,
            on_track_failed=on_track_failed,
            pl_settings=pl_settings,
            args=args,
            total_tracks=total_tracks,
            start_offset=start_idx
        )
        if bot_detected:
            return True
        time.sleep(1)

    return False


def _collect_playlist_cache_dirs(playlist_root_dir, folder_dir):
    """
    Finds all potential cache folders for this playlist (root and all subdirectories),
    excluding folder_dir itself.
    """
    cache_dirs = []
    if not playlist_root_dir or not os.path.isdir(playlist_root_dir):
        return cache_dirs

    curr_abs = os.path.abspath(folder_dir)
    root_abs = os.path.abspath(playlist_root_dir)

    if root_abs != curr_abs:
        cache_dirs.append(playlist_root_dir)

    try:
        for entry in sorted(os.listdir(playlist_root_dir)):
            sub = os.path.join(playlist_root_dir, entry)
            if os.path.isdir(sub) and not entry.startswith('.') and entry != '__pycache__':
                if os.path.abspath(sub) != curr_abs:
                    cache_dirs.append(sub)
    except OSError:
        pass

    return cache_dirs


def _cleanup_orphaned_thumbnails(folder_dir):
    """
    Removes standalone image files (.webp, .jpg, .jpeg, .png) left over from
    interrupted or failed audio/video downloads. yt-dlp saves thumbnails to disk
    before downloading audio/video; when the audio download fails or is rate-limited,
    the thumbnail is left stranded on disk.
    Preserves standard album/folder cover files (cover.jpg, folder.jpg, etc.).
    """
    if not os.path.isdir(folder_dir):
        return 0
    protected = {"cover.jpg", "cover.png", "folder.jpg", "folder.png", "albumart.jpg", "albumart.png", "thumb.jpg", "thumb.png"}
    image_exts = {".webp", ".jpg", ".jpeg", ".png", ".meta"}
    removed = 0
    try:
        for fname in os.listdir(folder_dir):
            if fname.lower() in protected or fname.startswith('.'):
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext in image_exts or ".temp." in fname.lower():
                fpath = os.path.join(folder_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        os.remove(fpath)
                        removed += 1
                    except OSError:
                        pass
    except OSError:
        pass
    if removed > 0:
        print(f"[*] Cleaned up {removed} orphaned thumbnail/temporary file(s) in '{os.path.basename(folder_dir)}'.")
    return removed


def _populate_from_playlist_cache(folder_dir, target_video_ids, target_video_titles, fmt, cache_dirs, manifest_tracks, manifest_failed=None):
    """
    Copies tracks that are already downloaded in other folders of the playlist into folder_dir.
    Avoids re-downloading existing media files across different folders/sections.
    """
    copied_count = 0
    valid_exts = _AUDIO_EXTS if fmt == "audio" else _VIDEO_EXTS

    # Pre-load manifests from cache dirs
    cache_manifests = {}
    for c_dir in cache_dirs:
        if os.path.isdir(c_dir):
            c_mf = load_playlist_manifest(c_dir).get("tracks", {})
            cache_manifests[c_dir] = c_mf

    for vid in target_video_ids:
        if vid in manifest_tracks:
            existing_path = os.path.join(folder_dir, manifest_tracks[vid])
            if os.path.isfile(existing_path) and os.path.getsize(existing_path) > 0:
                continue

        # Look for this vid in cache_dirs via manifest
        found_src = None
        for c_dir, c_tracks in cache_manifests.items():
            if vid in c_tracks:
                cand_file = os.path.join(c_dir, c_tracks[vid])
                if os.path.isfile(cand_file) and os.path.getsize(cand_file) > 0:
                    ext = os.path.splitext(cand_file)[1].lower()
                    if ext in valid_exts:
                        found_src = cand_file
                        break

        # If not found via manifest, audit files on disk in cache_dirs
        if not found_src:
            raw_title = target_video_titles.get(vid, "").strip()
            if raw_title:
                norm = _norm_title(raw_title)
                alpha = _norm_alpha(raw_title)
                for c_dir in cache_dirs:
                    if not os.path.isdir(c_dir):
                        continue
                    try:
                        for fname in os.listdir(c_dir):
                            fpath = os.path.join(c_dir, fname)
                            if os.path.isfile(fpath) and not fname.startswith('.'):
                                ext = os.path.splitext(fname)[1].lower()
                                if ext in valid_exts:
                                    stem = os.path.splitext(fname)[0]
                                    m = _NUM_PREFIX_RE.match(stem)
                                    base = m.group(2).strip() if m else stem
                                    if (norm and _norm_title(base) == norm) or (alpha and _norm_alpha(base) == alpha):
                                        found_src = fpath
                                        break
                    except OSError:
                        pass
                    if found_src:
                        break

        if found_src:
            raw_title = target_video_titles.get(vid, "").strip()
            if raw_title:
                clean_title = sanitize_filename(raw_title)
            else:
                clean_stem = _NUM_PREFIX_RE.sub("", os.path.splitext(os.path.basename(found_src))[0]).strip()
                clean_title = sanitize_filename(clean_stem) or f"track_{vid}"
            ext = os.path.splitext(found_src)[1].lower()
            dst_name = f"{clean_title}{ext}"
            dst_path = os.path.join(folder_dir, dst_name)
            try:
                if not os.path.isfile(dst_path) or os.path.abspath(found_src) != os.path.abspath(dst_path):
                    shutil.copy2(found_src, dst_path)
                manifest_tracks[vid] = dst_name
                if manifest_failed is not None:
                    manifest_failed.pop(vid, None)
                copied_count += 1
            except Exception as exc:
                print(f"    [!] Failed to copy cached track '{dst_name}': {exc}")

    if copied_count > 0:
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed if manifest_failed is not None else {}})
        print(f"[*] Reused {copied_count} track(s) from existing playlist download folders (no re-downloading needed).")

    return copied_count


def _execute_folder_download(folder_dir, target_video_ids, target_video_titles, fmt, number_files, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt, parent_cache_dir=None, retry_failed_state=None, playlist_root_dir=None, pl_settings=None, args=None):
    """
    Downloads and synchronizes tracks for a single directory (main playlist or section subfolder).
    Reuses already-downloaded tracks from any other folders/sections in the playlist to avoid re-downloading.
    Returns (newly_downloaded, already_cached_count, deleted_count).
    """
    os.makedirs(folder_dir, exist_ok=True)
    manifest_data = load_playlist_manifest(folder_dir)
    manifest_tracks = manifest_data.get("tracks", {})
    manifest_failed = dict(manifest_data.get("failed", {}))

    def handle_track_downloaded(vid, filepath):
        fname = os.path.basename(filepath)
        manifest_tracks[vid] = fname
        # Remove from failed if it succeeded this time
        manifest_failed.pop(vid, None)
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed})

    def handle_track_failed(vid, reason):
        manifest_failed[vid] = reason
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed})

    existing_fmt = _detect_folder_format(folder_dir)
    if existing_fmt and existing_fmt != fmt:
        print(f"[*] Folder contains {existing_fmt} files — switching to {fmt}. Removing old files...")
        removed = 0
        for fname in os.listdir(folder_dir):
            fpath = os.path.join(folder_dir, fname)
            if os.path.isfile(fpath) and not fname.startswith('.') and fname not in _IGNORED_FILENAMES:
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError as exc:
                    print(f"    [!] Could not remove '{fname}': {exc}")
        print(f"    Removed {removed} old file(s). Starting fresh download.")
        manifest_tracks = {}
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks})

    # Clean up any leftover orphaned thumbnail images from previous interrupted/failed runs
    _cleanup_orphaned_thumbnails(folder_dir)

    # Re-use already downloaded files across the playlist's download tree
    if not playlist_root_dir:
        playlist_root_dir = parent_cache_dir if (parent_cache_dir and os.path.isdir(parent_cache_dir)) else os.path.dirname(os.path.abspath(folder_dir))
    all_cache_dirs = _collect_playlist_cache_dirs(playlist_root_dir, folder_dir)
    if parent_cache_dir and os.path.isdir(parent_cache_dir) and parent_cache_dir not in all_cache_dirs and os.path.abspath(parent_cache_dir) != os.path.abspath(folder_dir):
        all_cache_dirs.append(parent_cache_dir)

    _populate_from_playlist_cache(
        folder_dir, target_video_ids, target_video_titles, fmt,
        all_cache_dirs, manifest_tracks, manifest_failed
    )

    # Sync deletions
    deleted_files = _sync_deletions(folder_dir, target_video_ids, target_video_titles, manifest_tracks)
    if deleted_files > 0:
        print(f"[*] Cleaned up {deleted_files} deleted track(s) from '{folder_dir}'.")
        target_id_set = set(target_video_ids)
        manifest_tracks = {vid: fn for vid, fn in manifest_tracks.items() if vid in target_id_set}
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks})

    # Audit physical files on disk
    present_video_ids, missing_video_ids, id_to_file_map = _audit_tracks_on_disk(
        folder_dir, target_video_ids, target_video_titles, manifest_tracks
    )

    # Remove tracks from manifest_failed that are now present on disk or no longer in the playlist
    target_id_set = set(target_video_ids)
    manifest_failed = {v: r for v, r in manifest_failed.items() if v in target_id_set and v not in present_video_ids}

    # Split missing into brand-new vs previously-failed
    brand_new_missing = [v for v in missing_video_ids if v not in manifest_failed]
    previously_failed = [v for v in missing_video_ids if v in manifest_failed]

    already_cached = [v for v in target_video_ids if v in present_video_ids]

    # Determine whether to retry previously-failed tracks
    # retry_failed_state is a shared dict so decisions made for one folder (e.g. "all"/"none")
    # can carry over to subsequent folders in the same run.
    if previously_failed:
        # Check if a global decision was already made this run
        global_decision = (retry_failed_state or {}).get("decision") if retry_failed_state is not None else None

        if global_decision is True:
            do_retry = True
        elif global_decision is False:
            do_retry = False
        else:
            # Determine mode from CLI flag or settings
            mode = (retry_failed_state or {}).get("mode") if retry_failed_state is not None else None
            if mode is True:
                do_retry = True
            elif mode is False:
                do_retry = False
            else:
                # mode is None or "ask" — check the setting
                setting_val = settings.get("retry_failed_downloads", False) if settings else False
                if setting_val is True:
                    do_retry = True
                elif setting_val == "ask":
                    # Prompt the user
                    folder_label = os.path.basename(folder_dir)
                    print(f"\n[?] '{folder_label}' has {len(previously_failed)} previously-failed track(s):")
                    for pf_vid in previously_failed:
                        reason = manifest_failed.get(pf_vid, "unknown reason")
                        title = target_video_titles.get(pf_vid, "Untitled")
                        print(f"    [-] {pf_vid} | {title}")
                        print(f"        Reason: {reason}")
                    print("    Retry these tracks? [y/N/(a)ll sections/(n)one of the remaining sections]: ", end="", flush=True)
                    try:
                        answer = input().strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        answer = ""
                    if answer in ("a", "all"):
                        do_retry = True
                        if retry_failed_state is not None:
                            retry_failed_state["decision"] = True
                    elif answer in ("n", "none"):
                        do_retry = False
                        if retry_failed_state is not None:
                            retry_failed_state["decision"] = False
                    elif answer == "y":
                        do_retry = True
                    else:
                        do_retry = False
                else:
                    # False (default) — silently skip
                    do_retry = False
    else:
        do_retry = True  # no previously-failed tracks, so this is a no-op

    if previously_failed and not do_retry:
        skipped_failed = previously_failed
        to_download = brand_new_missing
    else:
        skipped_failed = []
        to_download = brand_new_missing + [v for v in previously_failed if do_retry]

    skip_msg = f", Skipped (prev. failed): {len(skipped_failed)}" if skipped_failed else ""
    print(f"[*] Folder '{os.path.basename(folder_dir)}': {len(target_video_ids)} track(s) (Cached: {len(already_cached)}, To download: {len(to_download)}{skip_msg})")

    if not to_download:
        ren, skipped_ren, ren_errors, updated_manifest = _safe_sync_file_numbering(
            folder_dir, target_video_ids, target_video_titles, number_files, manifest_tracks
        )
        if ren:
            print(f"[*] Renumbered {ren} file(s) in '{os.path.basename(folder_dir)}'.")
        if updated_manifest:
            manifest_tracks.update(updated_manifest)
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed})
        if skipped_failed:
            print(f"[*] Skipped {len(skipped_failed)} previously-failed track(s) in '{os.path.basename(folder_dir)}' "
                  f"(set 'retry_failed_downloads = \"ask\"' in settings.toml to be prompted, or use --retry-failed to force).")
        return 0, len(already_cached), deleted_files

    bot_detected = _run_ytdlp_batch(
        to_download, folder_dir, fmt, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
        on_track_downloaded=handle_track_downloaded,
        on_track_failed=handle_track_failed,
        pl_settings=pl_settings,
        args=args,
    )

    ren, skipped_ren, ren_errors, updated_manifest = _safe_sync_file_numbering(
        folder_dir, target_video_ids, target_video_titles, number_files, manifest_tracks
    )
    if ren:
        print(f"[*] Synchronized numbering for {ren} file(s) in '{os.path.basename(folder_dir)}'.")
    if updated_manifest:
        manifest_tracks.update(updated_manifest)
    save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed})

    still_missing = [v for v in to_download if v not in manifest_tracks]

    # Automatic retry pass: only for tracks that truly did not download in pass 1,
    # and only if we were NOT stopped by a bot-detection / rate-limit challenge.
    if still_missing and not bot_detected:
        print(f"[*] {len(still_missing)} track(s) didn't come through on the first pass - waiting 5s then retrying...")
        time.sleep(5)
        bot_detected = _run_ytdlp_batch(
            still_missing, folder_dir, fmt, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
            on_track_downloaded=handle_track_downloaded,
            on_track_failed=handle_track_failed,
            pl_settings=pl_settings,
            args=args,
        )
        ren2, _, _, updated_manifest_2 = _safe_sync_file_numbering(
            folder_dir, target_video_ids, target_video_titles, number_files, manifest_tracks
        )
        if ren2:
            print(f"[*] Synchronized numbering for {ren2} more file(s) in '{os.path.basename(folder_dir)}'.")
        if updated_manifest_2:
            manifest_tracks.update(updated_manifest_2)
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed})
        still_missing = [v for v in to_download if v not in manifest_tracks]

    if still_missing and not bot_detected:
        print(f"\n[!] {len(still_missing)} of {len(to_download)} track(s) in '{os.path.basename(folder_dir)}' "
              f"could not be downloaded (likely private, deleted, or region-locked on YouTube):")
        for mv in still_missing:
            m_title = target_video_titles.get(mv, "Untitled Video")
            if mv not in manifest_failed:
                manifest_failed[mv] = "Download failed (video unavailable, blocked, or stream unextractable)"
            reason = manifest_failed.get(mv, "unknown")
            print(f"    [-] {mv} | {m_title}")
            print(f"        Reason: {reason}")
        print("    These will be skipped on the next run (set 'retry_failed_downloads' in settings.toml to change this).\n")
        save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed})
    elif still_missing and bot_detected:
        print(f"[*] Note: {len(still_missing)} track(s) were uncompleted due to YouTube bot verification. They are NOT marked as permanently failed.\n")

    if skipped_failed:
        print(f"[*] Skipped {len(skipped_failed)} previously-failed track(s) in '{os.path.basename(folder_dir)}' "
              f"(set 'retry_failed_downloads = \"ask\"' in settings.toml to be prompted, or use --retry-failed to force).")

    # Clean up any leftover orphaned thumbnail images from failed downloads in this run
    _cleanup_orphaned_thumbnails(folder_dir)

    newly_downloaded = len(set(manifest_tracks.keys()) - set(present_video_ids))
    save_playlist_manifest(folder_dir, {"tracks": manifest_tracks, "failed": manifest_failed})
    return newly_downloaded, len(already_cached), deleted_files


def _execute_folder_download_safe(folder_dir, *args, **kwargs):
    """
    Runs _execute_folder_download for one folder, catching any unexpected exception
    so a single folder's failure can never abort the rest of a multi-section download
    run. Without this, one bad folder partway through a large sectioned playlist
    (the full-playlist folder itself, or any one of dozens of section folders) would
    crash the whole 'download' command, leaving every folder after it completely
    untouched - which is exactly what "some sections randomly have zero manifest
    entries and no numbering" looks like from the outside.
    Returns (newly_downloaded, already_cached_count, deleted_count); a caught
    failure counts as (0, 0, 0) for that folder rather than stopping everything else.
    """
    try:
        return _execute_folder_download(folder_dir, *args, **kwargs)
    except Exception:
        print(f"\n[!] ERROR: Unexpected failure while downloading '{os.path.basename(folder_dir)}':")
        traceback.print_exc()
        print(f"[!] Skipping this folder for now and continuing with the rest of the playlist. "
              f"Please report this traceback, then re-run 'download' to pick this folder back up.\n")
        return 0, 0, 0


def _resolve_full_playlist_folder_name(playlist_name, sections_data, pl_settings):
    """
    Names the folder used for the complete, unsectioned download in download_mode =
    'all' (previously always the literal string 'FULL_PLAYLIST'). Controlled by the
    'folder_name_source' playlist setting:
      - 'header': the title from the playlist's main '### ' header, if one is set
      - 'alias' (default), or 'header' with no header title available: the
        playlist's own name/alias
    """
    if pl_settings.get("folder_name_source") == "header":
        header_title = ((sections_data or {}).get("main_header") or {}).get("title") or ""
        if header_title.strip():
            return sanitize_filename(header_title.strip())
    return sanitize_filename(playlist_name)



def command_download(args, settings, playlist_data, fmt=None):
    """
    Downloads all tracks from playlists/<name>.txt using yt-dlp.
    Persists format, number_files, thumbnails, and section download modes in playlist-settings.toml.
    Caches track mappings in <downloads_dir>/<name>/_manifest.json.
    """
    ytdlp_bin = find_ytdlp(settings)
    if not ytdlp_bin:
        print("\n" + "=" * 60)
        print(" [!] yt-dlp Not Found")
        print("=" * 60)
        print("  yt-dlp is required for download commands.")
        print("  Please download 'yt-dlp.exe' from:")
        print("    https://github.com/yt-dlp/yt-dlp/releases")
        print("  and place it in this project's root directory, or add it to your PATH.")
        print("  (You can also define a custom path in settings.toml under 'ytdlp_path').")
        print("=" * 60 + "\n")
        return

    target_name = args.target.strip()
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        print(f"    Run 'python main.py pull {target_name}' first to download the playlist.")
        return

    print(f"[*] Reading tracks from '{file_path}'...")
    target_video_ids, target_video_titles, skipped, blank_above, sections_data = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the local text file.")
        return

    # Handle --export-urls: dump list of video URLs to data/<name>_urls.txt and exit
    if getattr(args, "export_urls", False):
        os.makedirs(DATA_DIR, exist_ok=True)
        export_file = os.path.join(DATA_DIR, f"{safe_name}_urls.txt")
        with open(export_file, "w", encoding="utf-8") as f:
            for vid in target_video_ids:
                f.write(f"https://www.youtube.com/watch?v={vid}\n")
        print(f"[+] Exported {len(target_video_ids)} URL(s) to '{export_file}'.")
        return

    # Load playlist-specific settings from playlist-settings.toml
    pl_settings = load_playlist_settings(playlist_name)

    custom_dl_path = pl_settings.get("download_path", "").strip()
    if custom_dl_path:
        playlist_download_dir = os.path.abspath(custom_dl_path)
    else:
        downloads_root = settings.get("downloads_dir", DOWNLOADS_DIR)
        playlist_download_dir = os.path.join(downloads_root, safe_name)
    os.makedirs(playlist_download_dir, exist_ok=True)

    # Handle --clear-failed: reset all recorded failed tracks in playlist download manifests
    if getattr(args, "clear_failed", False):
        cleared_count = 0
        manifest_files = []
        for root, dirs, files in os.walk(playlist_download_dir):
            if DOWNLOAD_MANIFEST_FILENAME in files:
                manifest_files.append(os.path.join(root, DOWNLOAD_MANIFEST_FILENAME))
        for mf in manifest_files:
            try:
                with open(mf, "r", encoding="utf-8") as f:
                    m_data = json.load(f)
                if isinstance(m_data, dict) and m_data.get("failed"):
                    cleared_count += len(m_data["failed"])
                    m_data["failed"] = {}
                    with open(mf, "w", encoding="utf-8") as f:
                        json.dump(m_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[!] Warning: could not clear failed entries in '{mf}': {e}")
        print(f"[+] Cleared {cleared_count} failed track record(s) across {len(manifest_files)} folder manifest(s) for '{playlist_name}'.")
        return

    entry_fields = playlist_entry_format_fields(pl_settings.get("playlist_entry_format"))
    video_metadata = sections_data.setdefault("video_metadata", {})

    # Auto-format: resolve anything the playlist file is missing via yt-dlp.
    # Titles are always required for downloads (they name and match the files),
    # even if the playlist_entry_format leaves them out of the text file.
    missing_for_file = find_missing_metadata(target_video_ids, video_metadata, entry_fields)
    missing_for_download = find_missing_metadata(target_video_ids, video_metadata, entry_fields + ["title"])
    if missing_for_download:
        print(f"[*] Resolving details for {len(missing_for_download)} track(s) with yt-dlp...")
        fill_missing_metadata(
            target_video_ids, video_metadata, entry_fields + ["title"],
            settings, playlist_id=resolve_playlist_id(target_name, playlist_data),
        )
        for vid in target_video_ids:
            title = (video_metadata.get(vid) or {}).get("title")
            if title:
                target_video_titles[vid] = title
        if missing_for_file:
            resolved = {v: target_video_titles.get(v) or "Untitled Video" for v in target_video_ids}
            save_playlist_file(file_path, target_video_ids, resolved, blank_above=blank_above, sections_data=sections_data)
            print(f"[+] Automatically formatted '{file_path}'.")

    if fmt:
        fmt = resolve_download_format(fmt, allow_prompt=False)
        pl_settings["download-format"] = fmt
        save_playlist_settings(playlist_name, {"download-format": fmt})
    elif pl_settings.get("download-format"):
        fmt = pl_settings["download-format"]
        print(f"[*] Using saved playlist format: '{fmt}' (from playlist-settings.toml)")
    else:
        fmt = resolve_download_format(None, allow_prompt=True)
        pl_settings["download-format"] = fmt
        save_playlist_settings(playlist_name, {"download-format": fmt})

    number_files = pl_settings.get("number_files", True)
    embed_thumbnail = pl_settings.get("embed_thumbnail", True)
    download_mode = pl_settings.get("download_mode", "main_only")  # "main_only", "all", "sections_only"
    number_sections = pl_settings.get("number_section_folders", False)

    ffmpeg_bin = find_ffmpeg(settings)
    js_rt = find_js_runtime(settings)

    is_sectioned = sections_data.get("is_sectioned", False)
    sections = sections_data.get("sections", [])

    total_new = 0
    total_cached = 0
    total_deleted = 0

    # Build retry_failed_state dict — shared across all folder calls so a user's
    # "all sections" / "none" answer propagates automatically.
    retry_failed_state = {}
    if getattr(args, "retry_failed", False):
        retry_failed_state["mode"] = True   # CLI --retry-failed forces retry
    else:
        setting_val = settings.get("retry_failed_downloads", False)
        if setting_val is True:
            retry_failed_state["mode"] = True
        elif setting_val == "ask":
            retry_failed_state["mode"] = "ask"
        else:
            retry_failed_state["mode"] = False

    full_playlist_name = _resolve_full_playlist_folder_name(playlist_name, sections_data, pl_settings)

    if is_sectioned and download_mode in ("all", "sections_only"):
        explicit_sections = [s for s in sections if not s.get("is_implicit")]
        sec_pad_width = max(2, len(str(len(explicit_sections))))
        if download_mode == "all":
            full_playlist_dir = os.path.join(playlist_download_dir, full_playlist_name)
            _move_flat_download_cache_to_subfolder(playlist_download_dir, full_playlist_name)
            print(f"[*] Download mode: 'all' (downloading '{full_playlist_name}' folder + {len(explicit_sections)} section folders)...")
            new_dl, cached, deleted = _execute_folder_download_safe(
                full_playlist_dir, target_video_ids, target_video_titles,
                fmt, number_files, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
                retry_failed_state=retry_failed_state,
                playlist_root_dir=playlist_download_dir,
                pl_settings=pl_settings,
                args=args,
            )
            total_new += new_dl
            total_cached += cached
            total_deleted += deleted

            # Then download / organize explicit sections (reuse files from full_playlist_dir)
            for sec_idx, sec in enumerate(explicit_sections, 1):
                raw_sec_name = sec.get("title") or sec.get("playlist_id") or "Section"
                if number_sections:
                    sec_name = sanitize_filename(f"{sec_idx:0{sec_pad_width}d} - {raw_sec_name}")
                else:
                    sec_name = sanitize_filename(raw_sec_name)
                sec_dir = os.path.join(playlist_download_dir, sec_name)
                sec_vids = sec.get("video_ids", [])
                if not sec_vids:
                    continue
                s_new, s_cached, s_del = _execute_folder_download_safe(
                    sec_dir, sec_vids, target_video_titles,
                    fmt, number_files, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
                    parent_cache_dir=full_playlist_dir,
                    retry_failed_state=retry_failed_state,
                    playlist_root_dir=playlist_download_dir,
                    pl_settings=pl_settings,
                    args=args,
                )
                total_new += s_new
                total_cached += s_cached
                total_deleted += s_del
        else:  # sections_only
            flat_cache_dir = playlist_download_dir if _folder_has_media(playlist_download_dir) else None
            print(f"[*] Download mode: 'sections_only' (downloading {len(explicit_sections)} section folders)...")
            for sec_idx, sec in enumerate(explicit_sections, 1):
                raw_sec_name = sec.get("title") or sec.get("playlist_id") or "Section"
                if number_sections:
                    sec_name = sanitize_filename(f"{sec_idx:0{sec_pad_width}d} - {raw_sec_name}")
                else:
                    sec_name = sanitize_filename(raw_sec_name)
                sec_dir = os.path.join(playlist_download_dir, sec_name)
                sec_vids = sec.get("video_ids", [])
                if not sec_vids:
                    continue
                s_new, s_cached, s_del = _execute_folder_download_safe(
                    sec_dir, sec_vids, target_video_titles,
                    fmt, number_files, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
                    parent_cache_dir=flat_cache_dir,
                    retry_failed_state=retry_failed_state,
                    playlist_root_dir=playlist_download_dir,
                    pl_settings=pl_settings,
                    args=args,
                )
                total_new += s_new
                total_cached += s_cached
                total_deleted += s_del
    else:
        # Default / main_only download.
        # If the full-playlist subfolder already exists (from a prior 'all' run),
        # keep downloading there so cached files are recognised and nothing re-downloads.
        full_playlist_dir = os.path.join(playlist_download_dir, full_playlist_name)
        effective_dir = full_playlist_dir if os.path.isdir(full_playlist_dir) else playlist_download_dir
        new_dl, cached, deleted = _execute_folder_download_safe(
            effective_dir, target_video_ids, target_video_titles,
            fmt, number_files, embed_thumbnail, settings, ytdlp_bin, ffmpeg_bin, js_rt,
            retry_failed_state=retry_failed_state,
            playlist_root_dir=playlist_download_dir,
            pl_settings=pl_settings,
            args=args,
        )
        total_new += new_dl
        total_cached += cached
        total_deleted += deleted

    print("\n" + "=" * 60)
    print(" Download Summary")
    print("=" * 60)
    print(f"  * {'Playlist:':<22} {playlist_name}")
    print(f"  * {'Mode:':<22} {'Audio (best quality)' if fmt == 'audio' else 'Video (MP4)'}")
    _full_dir = os.path.join(playlist_download_dir, full_playlist_name)
    if is_sectioned and download_mode == "all":
        _dest_str = f"{playlist_download_dir}/{full_playlist_name}/ (+ section folders)"
    elif os.path.isdir(_full_dir):
        _dest_str = f"{playlist_download_dir}/{full_playlist_name}/"
    else:
        _dest_str = str(playlist_download_dir)
    print(f"  * {'Destination:':<22} {_dest_str}")
    print(f"  * {'Total Tracks:':<22} {len(target_video_ids):>4d} track(s)")
    print(f"  * {'Newly Downloaded:':<22} {total_new:>4d} track(s)")
    print(f"  * {'Already Up-to-Date:':<22} {total_cached:>4d} track(s)")
    if total_deleted > 0:
        print(f"  * {'Deleted Tracks:':<22} {total_deleted:>4d} track(s)")
    print("=" * 60 + "\n")

    record_activity(playlist_data, playlist_name, f"download-{fmt}")
    log_playlist_event(
        settings,
        playlist_name,
        f"download-{fmt}",
        "yt-dlp",
        summary_lines=[
            f"Downloaded {total_new} new track(s) ({'audio' if fmt == 'audio' else 'video'}) to '{playlist_download_dir}'"
        ],
        playlist_data=playlist_data
    )
