"""
yt-dlp integration for downloading playlists as audio or video with incremental caching,
automatic title resolution, deletion synchronization, per-playlist download settings,
JavaScript runtime auto-detection, and collision-free renumbering.
"""

import os
import re
import json
import shutil
import subprocess
import tempfile
import unicodedata
import uuid

from .config import (
    DOWNLOADS_DIR,
    PLAYLISTS_DIR,
    record_activity,
    log_playlist_event,
)
from .parser import (
    sanitize_filename,
    get_playlist_name_for_target,
    parse_playlist_file,
    save_playlist_file,
)

DOWNLOAD_SETTINGS_FILENAME = "_setting.json"

_IGNORED_FILENAMES = {
    DOWNLOAD_SETTINGS_FILENAME,
    "_settings.json",
    "_ypm-download-settings.json",
    "ypm-download-settings.json",
    "_ytdlp_archive.txt",
    ".ytdlp_archive.txt",
}


def _migrate_legacy_files(playlist_download_dir):
    """Migrates legacy setting filenames to _setting.json and cleans up deprecated archive files."""
    new_settings = os.path.join(playlist_download_dir, DOWNLOAD_SETTINGS_FILENAME)
    for old_name in ("_settings.json", "_ypm-download-settings.json", "ypm-download-settings.json"):
        old_path = os.path.join(playlist_download_dir, old_name)
        if os.path.isfile(old_path) and not os.path.isfile(new_settings):
            try:
                os.rename(old_path, new_settings)
                break
            except OSError:
                pass

    # Clean up deprecated yt-dlp archive files
    for arch_name in ("_ytdlp_archive.txt", ".ytdlp_archive.txt"):
        arch_path = os.path.join(playlist_download_dir, arch_name)
        if os.path.isfile(arch_path):
            try:
                os.remove(arch_path)
            except OSError:
                pass


def find_ytdlp(settings=None):
    """
    Locates the yt-dlp executable.
    Checks:
      1. Custom path in settings.toml ('ytdlp_path')
      2. Project root directory ('./yt-dlp.exe' or './yt-dlp')
      3. System PATH ('shutil.which')
    Returns the executable path or None if not found.
    """
    if settings:
        custom_path = settings.get("ytdlp_path", "").strip()
        if custom_path and os.path.isfile(custom_path):
            return os.path.abspath(custom_path)

    for candidate in ("yt-dlp.exe", "yt-dlp"):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    which_exe = shutil.which("yt-dlp.exe") or shutil.which("yt-dlp")
    if which_exe:
        return os.path.abspath(which_exe)

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


def find_js_runtime():
    """
    Locates an available JavaScript runtime for yt-dlp (Node.js, Deno, Bun, QuickJS).
    Returns the runtime name (e.g. 'node', 'deno', 'bun') or None if none found.
    """
    for rt in ("deno", "node", "bun", "qjs"):
        if shutil.which(rt) or shutil.which(f"{rt}.exe"):
            return rt
    return None


def load_playlist_download_settings(playlist_download_dir):
    """Loads per-playlist download settings (format, number_files, embed_thumbnail) from the playlist download directory."""
    _migrate_legacy_files(playlist_download_dir)
    settings_file = os.path.join(playlist_download_dir, DOWNLOAD_SETTINGS_FILENAME)
    if os.path.isfile(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def save_playlist_download_settings(playlist_download_dir, settings_dict):
    """Saves per-playlist download settings (format, number_files, embed_thumbnail) to the playlist download directory."""
    os.makedirs(playlist_download_dir, exist_ok=True)
    settings_file = os.path.join(playlist_download_dir, DOWNLOAD_SETTINGS_FILENAME)
    try:
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump(settings_dict, f, indent=4)
    except Exception as e:
        print(f"    [!] Warning: Could not save playlist download settings: {e}")


def fetch_video_titles_ytdlp(video_ids, settings=None):
    """
    Fetches video titles for a list of YouTube video IDs or URLs using yt-dlp.
    Returns a dict {video_id: title}.
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

    cmd = [
        ytdlp_bin,
        "--encoding", "utf-8",
        "--no-warnings",
        "--print", "%(id)s\t%(title)s",
        "--no-download",
    ]

    js_rt = find_js_runtime()
    if js_rt:
        cmd += ["--js-runtimes", js_rt]

    cmd += ["--batch-file", batch_path]

    titles = {}
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
            line = line.strip()
            if "\t" in line:
                v_id, v_title = line.split("\t", 1)
                v_id = v_id.strip()
                v_title = v_title.strip()
                if v_id and v_title:
                    titles[v_id] = v_title
    except Exception as e:
        print(f"[!] Warning: Failed to fetch titles with yt-dlp: {e}")
    finally:
        try:
            os.remove(batch_path)
        except OSError:
            pass

    return titles


def fetch_playlist_title_ytdlp(playlist_id, settings=None):
    """
    Fetches the title of a YouTube playlist using yt-dlp.
    Returns the title string or None if failed.
    """
    ytdlp_bin = find_ytdlp(settings)
    if not ytdlp_bin:
        return None

    cmd = [
        ytdlp_bin,
        "--encoding", "utf-8",
        "--no-warnings",
        "--print", "%(playlist_title)s",
        "--no-download",
        "--playlist-items", "1",
    ]
    js_rt = find_js_runtime()
    if js_rt:
        cmd += ["--js-runtimes", js_rt]
    cmd.append(f"https://www.youtube.com/playlist?list={playlist_id}")

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
        if res.returncode == 0:
            lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
            if lines and lines[0] and lines[0].lower() != "na":
                return lines[0]
    except Exception:
        pass
    return None


def fetch_playlist_tracks_ytdlp(playlist_id, settings=None):
    """
    Fetches the live list of tracks for a YouTube playlist using yt-dlp.
    Returns (video_ids, video_titles) ordered exactly as on YouTube (1..N).
    Returns (None, None) if yt-dlp fails or playlist is private.
    """
    ytdlp_bin = find_ytdlp(settings)
    if not ytdlp_bin:
        return None, None

    cmd = [
        ytdlp_bin,
        "--encoding", "utf-8",
        "--no-warnings",
        "--flat-playlist",
        "--print", "%(playlist_index)s\t%(id)s\t%(title)s",
        "--no-download",
    ]
    js_rt = find_js_runtime()
    if js_rt:
        cmd += ["--js-runtimes", js_rt]
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
        if res.returncode != 0:
            return None, None

        for line in res.stdout.splitlines():
            line = line.strip()
            parts = line.split("\t")
            if len(parts) >= 3:
                idx_str, vid_id, title = parts[0].strip(), parts[1].strip(), parts[2].strip()
                try:
                    idx = int(idx_str)
                except ValueError:
                    idx = len(raw_items) + 1
                if vid_id:
                    raw_items.append((idx, vid_id, title or "Untitled Video"))

        if not raw_items:
            return None, None

        raw_items.sort(key=lambda x: x[0])
        video_ids = [x[1] for x in raw_items]
        video_titles = {x[1]: x[2] for x in raw_items}
        return video_ids, video_titles
    except Exception:
        return None, None


_AUDIO_EXTS = {'.opus', '.m4a', '.mp3', '.aac', '.flac', '.ogg', '.wav'}
_VIDEO_EXTS = {'.mp4', '.mkv', '.mov', '.avi'}


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


def _sync_deletions(playlist_download_dir, target_video_ids, target_video_titles):
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

    try:
        entries = os.listdir(playlist_download_dir)
    except OSError:
        entries = []

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
    for t in targets:
        for idx, cf in enumerate(candidate_files):
            if idx not in assigned_candidates and t["norm"] and t["norm"] == cf["norm"]:
                assigned_candidates.add(idx)
                present_video_ids.add(t["vid"])
                id_to_file_map[t["vid"]] = cf["fname"]
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
                break

    missing_video_ids = [v for v in target_video_ids if v not in present_video_ids]
    return present_video_ids, missing_video_ids, id_to_file_map


def _sync_file_numbering(playlist_download_dir, target_video_ids, target_video_titles, number_files):
    """
    Renames existing downloaded files to match the 'number_files' setting
    and the exact order of tracks in the target playlist.
    Uses multi-pass matching and a collision-free two-phase rename.

    Returns a (renamed, skipped, errors) tuple.
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
    except OSError:
        return 0, 0, 0

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
    unmatched_files = []
    assigned_target_indices = set()

    # Pass 1: exact normalized match
    for mf in media_files:
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

    # Pass 3: if remaining unmatched files match remaining unassigned targets 1-to-1
    unassigned_targets = [t for t in targets if t["idx"] not in assigned_target_indices]
    if len(still_unmatched) == len(unassigned_targets) and still_unmatched:
        for mf, t in zip(still_unmatched, unassigned_targets):
            matched_pairs.append((mf, t))
            assigned_target_indices.add(t["idx"])
        still_unmatched = []

    planned_renames = []
    skipped = len(still_unmatched)
    updated_manifest = {}

    for mf, t in matched_pairs:
        target_idx = t["idx"]
        clean_title = t["clean_title"]
        ext = mf["ext"]
        fpath = mf["fpath"]

        if number_files:
            new_name = f"{target_idx:0{pad_width}d} - {clean_title}{ext}"
        else:
            new_name = f"{clean_title}{ext}"

        updated_manifest[t["vid_id"]] = new_name

        new_path = os.path.join(playlist_download_dir, new_name)
        if os.path.abspath(fpath) == os.path.abspath(new_path):
            continue

        planned_renames.append((fpath, new_path))

    if not planned_renames:
        return 0, skipped, 0, updated_manifest

    # Collision-free two-phase rename
    token = uuid.uuid4().hex[:8]
    temp_renames = []
    errors = 0

    for idx, (curr_path, final_path) in enumerate(planned_renames):
        temp_path = f"{curr_path}.__ypm_{token}_{idx}__"
        try:
            os.rename(curr_path, temp_path)
            temp_renames.append((temp_path, final_path))
        except OSError as exc:
            print(f"    [!] Could not stage rename for '{os.path.basename(curr_path)}': {exc}")
            errors += 1

    renamed = 0
    for temp_path, final_path in temp_renames:
        try:
            if os.path.exists(final_path):
                os.remove(final_path)
            os.rename(temp_path, final_path)
            renamed += 1
        except OSError as exc:
            print(f"    [!] Could not finalize rename to '{os.path.basename(final_path)}': {exc}")
            errors += 1
            try:
                orig_path = temp_path.split(".__ypm_")[0]
                os.rename(temp_path, orig_path)
            except OSError:
                pass

    return renamed, skipped, errors, updated_manifest


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


def command_download(args, settings, playlist_data, fmt=None):
    """
    Downloads all tracks from a local playlist text file using yt-dlp as audio or video.
    Maintains an archive file in the playlist download folder so subsequent runs
    only download newly added tracks without redownloading existing ones.
    Persists format, number_files, and thumbnail preferences per playlist in _ypm-download-settings.json.
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
    target_video_ids, target_video_titles, skipped, blank_above = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the local text file.")
        return

    # Auto-format: If any tracks lack titles (e.g. user pasted raw URLs/IDs), resolve them via yt-dlp now
    missing_titles = [v for v in target_video_ids if not target_video_titles.get(v)]
    if missing_titles:
        print(f"[*] Resolving titles for {len(missing_titles)} unformatted track(s) with yt-dlp...")
        fetched = fetch_video_titles_ytdlp(missing_titles, settings)
        for vid, title in fetched.items():
            target_video_titles[vid] = title
        save_playlist_file(file_path, target_video_ids, target_video_titles, blank_above=blank_above)
        print(f"[+] Automatically formatted '{file_path}' with video titles.")

    # Resolve output directory
    downloads_root = settings.get("downloads_dir", DOWNLOADS_DIR)
    playlist_download_dir = os.path.join(downloads_root, safe_name)
    os.makedirs(playlist_download_dir, exist_ok=True)

    # Migrate any legacy filenames (.ytdlp_archive.txt -> _ytdlp_archive.txt, etc.)
    _migrate_legacy_files(playlist_download_dir)

    # Load per-playlist download settings
    saved_dl_settings = load_playlist_download_settings(playlist_download_dir)

    # Determine format
    if fmt:
        fmt = resolve_download_format(fmt, allow_prompt=False)
        saved_dl_settings["format"] = fmt
        save_playlist_download_settings(playlist_download_dir, saved_dl_settings)
    elif "format" in saved_dl_settings:
        fmt = saved_dl_settings["format"]
        print(f"[*] Using saved playlist format: '{fmt}' (from {DOWNLOAD_SETTINGS_FILENAME})")
    else:
        fmt = resolve_download_format(None, allow_prompt=True)
        saved_dl_settings["format"] = fmt
        saved_dl_settings.setdefault("embed_thumbnail", True)
        saved_dl_settings.setdefault("number_files", True)
        save_playlist_download_settings(playlist_download_dir, saved_dl_settings)

    # Determine numbering preference from per-playlist settings (defaults to True)
    if "number_files" in saved_dl_settings:
        number_files = bool(saved_dl_settings["number_files"])
    else:
        number_files = True
        saved_dl_settings["number_files"] = True
        save_playlist_download_settings(playlist_download_dir, saved_dl_settings)

    embed_thumbnail = saved_dl_settings.get("embed_thumbnail", True)

    existing_fmt = _detect_folder_format(playlist_download_dir)
    if existing_fmt and existing_fmt != fmt:
        print(f"[*] Folder contains {existing_fmt} files — switching to {fmt}. Removing old files...")
        removed = 0
        for fname in os.listdir(playlist_download_dir):
            fpath = os.path.join(playlist_download_dir, fname)
            if os.path.isfile(fpath) and not fname.startswith('.') and fname not in _IGNORED_FILENAMES:
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError as exc:
                    print(f"    [!] Could not remove '{fname}': {exc}")
        print(f"    Removed {removed} old file(s). Starting fresh download.")

    manifest_tracks = saved_dl_settings.get("tracks", {})

    # Account for deletions in playlist.txt by cleaning orphaned files
    deleted_files = _sync_deletions(
        playlist_download_dir, target_video_ids, target_video_titles
    )
    if deleted_files > 0:
        print(f"[*] Cleaned up {deleted_files} deleted track(s) from download directory.")
        target_id_set = set(target_video_ids)
        manifest_tracks = {vid: fn for vid, fn in manifest_tracks.items() if vid in target_id_set}

    # Audit physical files on disk
    present_video_ids, missing_video_ids, id_to_file_map = _audit_tracks_on_disk(
        playlist_download_dir, target_video_ids, target_video_titles, manifest_tracks
    )

    to_download = missing_video_ids
    already_cached = [v for v in target_video_ids if v in present_video_ids]

    print(f"[*] Found {len(target_video_ids)} track(s) in playlist:")
    print(f"    - Already downloaded / cached: {len(already_cached)}")
    print(f"    - To download:                 {len(to_download)}")

    if not to_download:
        # Sync numbering across all existing files in case of reordering, deletions, or preference change
        ren, skipped_ren, ren_errors, updated_manifest = _sync_file_numbering(
            playlist_download_dir, target_video_ids, target_video_titles, number_files
        )
        if ren:
            print(f"[*] Renumbered {ren} file(s) to match playlist order.")

        saved_dl_settings["tracks"] = updated_manifest
        save_playlist_download_settings(playlist_download_dir, saved_dl_settings)

        print(f"\n[+] All {len(target_video_ids)} track(s) are already downloaded in '{playlist_download_dir}'.")
        print("=" * 60)
        print(" Download Summary")
        print("=" * 60)
        print(f"  * {'Playlist:':<22} {playlist_name}")
        print(f"  * {'Mode:':<22} {'Audio (best quality)' if fmt == 'audio' else 'Video (MP4)'}")
        print(f"  * {'Destination:':<22} {playlist_download_dir}")
        print(f"  * {'Total Tracks:':<22} {len(target_video_ids):>4d} track(s)")
        print(f"  * {'Newly Downloaded:':<22} {0:>4d} track(s)")
        print(f"  * {'Already Up-to-Date:':<22} {len(already_cached):>4d} track(s)")
        if deleted_files > 0:
            print(f"  * {'Deleted Tracks:':<22} {deleted_files:>4d} track(s)")
        print("=" * 60 + "\n")
        return

    # Write URLs to a temporary batch file to avoid Windows command length limits
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as batch_f:
        for vid_id in to_download:
            batch_f.write(f"https://www.youtube.com/watch?v={vid_id}\n")
        batch_path = batch_f.name

    output_template = os.path.join(playlist_download_dir, "%(title)s.%(ext)s")

    ffmpeg_bin = find_ffmpeg(settings)
    js_rt = find_js_runtime()

    cmd = [
        ytdlp_bin,
        "--encoding", "utf-8",
    ]
    if js_rt:
        cmd += ["--js-runtimes", js_rt]

    if fmt == "audio":
        cmd += [
            "--format", "bestaudio/best",
            "--extract-audio",
        ]
        if ffmpeg_bin and embed_thumbnail:
            cmd += ["--embed-thumbnail"]
    else:  # video
        cmd += [
            "--format", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b",
            "--merge-output-format", "mp4",
        ]

    if ffmpeg_bin:
        cmd += ["--ffmpeg-location", ffmpeg_bin]
    else:
        if fmt == "video":
            print("  [i] Note: ffmpeg was not detected. MP4 video merging may not work without it.")
            print("      Place ffmpeg.exe in this project's folder or define 'ffmpeg_path' in settings.toml.\n")

    cmd += [
        "--no-overwrites",
        "--output", output_template,
        "--batch-file", batch_path,
    ]

    print(f"[*] Starting yt-dlp ({'Audio' if fmt == 'audio' else 'Video'})...\n")
    interrupted = False
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        subprocess.run(cmd, env=env, check=False)
    except KeyboardInterrupt:
        interrupted = True
        print("\n\n[!] Download paused/cancelled by user.")
    except Exception as e:
        print(f"[!] Error running yt-dlp: {e}")
    finally:
        try:
            os.remove(batch_path)
        except OSError:
            pass

    # Sync file numbering across all files in playlist directory after download
    ren, skipped_ren, ren_errors, updated_manifest = _sync_file_numbering(
        playlist_download_dir, target_video_ids, target_video_titles, number_files
    )
    if ren:
        print(f"[*] Synchronized numbering for {ren} file(s) in download directory.")

    newly_downloaded = len(set(updated_manifest.keys()) - set(present_video_ids))
    saved_dl_settings["tracks"] = updated_manifest
    save_playlist_download_settings(playlist_download_dir, saved_dl_settings)

    if interrupted:
        print(f"[!] Partial download summary: {newly_downloaded} new track(s) completed before cancellation.\n")
        return

    print("\n" + "=" * 60)
    print(" Download Summary")
    print("=" * 60)
    print(f"  * {'Playlist:':<22} {playlist_name}")
    print(f"  * {'Mode:':<22} {'Audio (best quality)' if fmt == 'audio' else 'Video (MP4)'}")
    print(f"  * {'Destination:':<22} {playlist_download_dir}")
    print(f"  * {'Total Tracks:':<22} {len(target_video_ids):>4d} track(s)")
    print(f"  * {'Newly Downloaded:':<22} {newly_downloaded:>4d} track(s)")
    print(f"  * {'Already Up-to-Date:':<22} {len(already_cached):>4d} track(s)")
    if deleted_files > 0:
        print(f"  * {'Deleted Tracks:':<22} {deleted_files:>4d} track(s)")
    print("=" * 60 + "\n")

    record_activity(playlist_data, playlist_name, f"download-{fmt}")
    log_playlist_event(
        settings,
        playlist_name,
        f"download-{fmt}",
        "yt-dlp",
        summary_lines=[
            f"Downloaded {newly_downloaded} new track(s) ({'audio' if fmt == 'audio' else 'video'}) to '{playlist_download_dir}'"
        ],
        playlist_data=playlist_data
    )
