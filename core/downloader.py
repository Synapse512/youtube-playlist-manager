"""
yt-dlp integration for downloading playlists as audio or video with incremental caching.
"""

import os
import re
import shutil
import subprocess
import tempfile

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
)


def find_ytdlp(settings=None):
    """
    Locates the yt-dlp executable.
    Checks:
      1. Custom path in settings.json ('ytdlp_path')
      2. Project root directory ('./yt-dlp.exe' or './yt-dlp')
      3. System PATH ('shutil.which')
    Returns the executable path or None if not found.
    """
    if settings:
        custom_path = settings.get("ytdlp_path", "").strip()
        if custom_path and os.path.isfile(custom_path):
            return os.path.abspath(custom_path)

    # Check project root directory
    for candidate in ("yt-dlp.exe", "yt-dlp"):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    # Check system PATH
    which_exe = shutil.which("yt-dlp.exe") or shutil.which("yt-dlp")
    if which_exe:
        return os.path.abspath(which_exe)

def find_ffmpeg(settings=None):
    """
    Locates ffmpeg executable or directory.
    Checks:
      1. Custom path in settings.json ('ffmpeg_path')
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



def _read_archive_ids(archive_path):
    """Reads the set of YouTube video IDs recorded in a yt-dlp archive file."""
    ids = set()
    if os.path.exists(archive_path):
        try:
            with open(archive_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        ids.add(parts[1])
                    elif parts:
                        ids.add(parts[0])
        except OSError:
            pass
    return ids


_AUDIO_EXTS = {'.opus', '.m4a', '.mp3', '.aac', '.flac', '.ogg', '.wav'}
_VIDEO_EXTS = {'.mp4', '.mkv', '.mov', '.avi'}


def _detect_folder_format(folder):
    """Returns 'audio', 'video', or None if the folder is empty or has no recognisable media."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return None
    for fname in entries:
        if fname.startswith('.'):
            continue
        ext = os.path.splitext(fname)[1].lower()
        if ext in _AUDIO_EXTS:
            return 'audio'
        if ext in _VIDEO_EXTS:
            return 'video'
    return None


_NUM_PREFIX_RE = re.compile(r'^(\d+)\s+-\s+(.+)$')

# Characters yt-dlp substitutes or strips in filenames on various platforms.
# We normalize both the playlist title and the on-disk stem to the same form
# before comparing, so sanitization differences never cause a missed match.
_YTDLP_SUBS = str.maketrans({
    '\uff1a': ':',   # fullwidth colon ： → :
    '\ua789': ':',   # modifier letter colon ꞉ → :  (yt-dlp Windows substitute)
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
    Normalise a title for fuzzy matching between playlist .txt entries and
    on-disk filenames.  Reverses common yt-dlp filename-safe substitutions,
    then lowercases and collapses whitespace.
    """
    s = s.translate(_YTDLP_SUBS)
    # Collapse any run of whitespace (including non-breaking spaces) to one space
    s = re.sub(r'[\s\u00a0]+', ' ', s)
    return s.strip().lower()


def _sync_file_numbering(playlist_download_dir, target_video_ids, target_video_titles, number_files):
    """
    Renames existing downloaded files to match the current 'number_downloaded_files' setting.
    - If number_files is True:  adds/corrects numeric prefixes (e.g. '03 - Title.mp3').
    - If number_files is False: strips numeric prefixes (e.g. 'Title.mp3').
    Matches files to playlist entries by their bare title (case-insensitive, yt-dlp-normalised).

    Returns a (renamed, skipped, errors) tuple.
    """
    pad_width = max(2, len(str(len(target_video_ids))))

    # Build normalised-title → playlist-index map from the ordered ID list
    title_to_index = {}
    for i, vid_id in enumerate(target_video_ids, 1):
        raw_title = target_video_titles.get(vid_id, "").strip()
        if raw_title:
            title_to_index[_norm_title(raw_title)] = i

    renamed = skipped = errors = 0

    try:
        entries = os.listdir(playlist_download_dir)
    except OSError:
        return renamed, skipped, errors

    for fname in entries:
        if fname.startswith('.') or fname == '.ytdlp_archive.txt':
            continue

        fpath = os.path.join(playlist_download_dir, fname)
        if not os.path.isfile(fpath):
            continue

        stem, ext = os.path.splitext(fname)
        m = _NUM_PREFIX_RE.match(stem)
        has_number = m is not None

        if number_files:
            if has_number:
                existing_num = int(m.group(1))
                base_title   = m.group(2).strip()
                correct_idx  = title_to_index.get(_norm_title(base_title))
                # Unknown title or number already correct — leave it alone
                if correct_idx is None or existing_num == correct_idx:
                    continue
                new_name = f"{correct_idx:0{pad_width}d} - {base_title}{ext}"
            else:
                correct_idx = title_to_index.get(_norm_title(stem))
                if correct_idx is None:
                    skipped += 1
                    continue
                new_name = f"{correct_idx:0{pad_width}d} - {stem}{ext}"
        else:
            if not has_number:
                continue  # Already unnumbered — nothing to do
            base_title = m.group(2).strip()
            new_name   = f"{base_title}{ext}"

        new_path = os.path.join(playlist_download_dir, new_name)
        if os.path.abspath(new_path) == os.path.abspath(fpath):
            continue
        if os.path.exists(new_path):
            skipped += 1
            continue

        try:
            os.rename(fpath, new_path)
            renamed += 1
        except OSError as exc:
            print(f"    [!] Could not rename '{fname}': {exc}")
            errors += 1

    return renamed, skipped, errors


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
    fmt: 'audio' (best quality audio in native format) or 'video' (best quality MP4).
    If fmt is None, prompts user to select.
    """
    fmt = resolve_download_format(fmt, allow_prompt=True)

    ytdlp_bin = find_ytdlp(settings)
    if not ytdlp_bin:
        print("\n" + "=" * 60)
        print(" [!] yt-dlp Not Found")
        print("=" * 60)
        print("  yt-dlp is required for download commands.")
        print("  Please download 'yt-dlp.exe' from:")
        print("    https://github.com/yt-dlp/yt-dlp/releases")
        print("  and place it in this project's root directory, or add it to your PATH.")
        print("  (You can also define a custom path in settings.json under 'ytdlp_path').")
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
    target_video_ids, target_video_titles, skipped, _ = parse_playlist_file(file_path)

    if not target_video_ids:
        print("[!] Error: No valid video IDs or URLs found in the local text file.")
        return

    # Resolve output directory — all formats share the same flat folder.
    # If the folder already contains files from a different format they are
    # deleted and the archive is cleared so yt-dlp re-downloads everything.
    downloads_root = settings.get("downloads_dir", DOWNLOADS_DIR)
    playlist_download_dir = os.path.join(downloads_root, safe_name)
    os.makedirs(playlist_download_dir, exist_ok=True)

    existing_fmt = _detect_folder_format(playlist_download_dir)
    archive_file = os.path.join(playlist_download_dir, ".ytdlp_archive.txt")
    if existing_fmt and existing_fmt != fmt:
        print(f"[*] Folder contains {existing_fmt} files — switching to {fmt}. Removing old files...")
        removed = 0
        for fname in os.listdir(playlist_download_dir):
            fpath = os.path.join(playlist_download_dir, fname)
            if os.path.isfile(fpath) and not fname.startswith('.'):
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError as exc:
                    print(f"    [!] Could not remove '{fname}': {exc}")
        # Reset archive so yt-dlp treats everything as new
        try:
            os.remove(archive_file)
        except OSError:
            pass
        print(f"    Removed {removed} old file(s). Starting fresh download.")

    initial_archived = _read_archive_ids(archive_file)

    already_cached = [v for v in target_video_ids if v in initial_archived]
    to_download = [v for v in target_video_ids if v not in initial_archived]

    print(f"[*] Found {len(target_video_ids)} track(s) in playlist:")
    print(f"    - Already downloaded / cached: {len(already_cached)}")
    print(f"    - To download:                 {len(to_download)}")

    # Determine numbering preference early so rename sync can run even on a full cache hit
    number_files = settings.get("number_downloaded_files", True)
    pad_width = max(2, len(str(len(target_video_ids))))

    # Sync existing file names to the current numbering preference
    ren, skipped_ren, ren_errors = _sync_file_numbering(
        playlist_download_dir, target_video_ids, target_video_titles, number_files
    )
    if ren:
        print(f"[*] Renamed {ren} existing file(s) to match numbering setting.")
    if skipped_ren:
        print(f"    [i] {skipped_ren} file(s) skipped (title not matched or destination already exists).")

    if not to_download:
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
        print("=" * 60 + "\n")
        return

    # Write URLs to a temporary batch file to avoid Windows command length limits
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as batch_f:
        for vid_id in to_download:
            batch_f.write(f"https://www.youtube.com/watch?v={vid_id}\n")
        batch_path = batch_f.name

    # Build yt-dlp output template based on numbering preference (already resolved above)
    if number_files:
        # Use yt-dlp autonumbering matching the exact order from the playlist text file
        output_template = os.path.join(playlist_download_dir, f"%(autonumber)0{pad_width}d - %(title)s.%(ext)s")
    else:
        output_template = os.path.join(playlist_download_dir, "%(title)s.%(ext)s")

    ffmpeg_bin = find_ffmpeg(settings)
    cmd = [ytdlp_bin]
    if fmt == "audio":
        # Download best quality audio in its native format.
        # When ffmpeg is available, embed the video's thumbnail as album art.
        cmd += [
            "--format", "bestaudio/best",
            "--extract-audio",
        ]
        if ffmpeg_bin:
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
            print("      Place ffmpeg.exe in this project's folder or define 'ffmpeg_path' in settings.json.\n")

    cmd += [
        "--download-archive", archive_file,
        "--no-overwrites",
        "--output", output_template,
        "--batch-file", batch_path,
    ]

    print(f"[*] Starting yt-dlp ({'Audio' if fmt == 'audio' else 'Video'})...\n")
    interrupted = False
    try:
        subprocess.run(cmd, check=False)
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

    # Read archive again to calculate newly downloaded
    final_archived = _read_archive_ids(archive_file)
    newly_downloaded = len(final_archived - initial_archived)
    total_up_to_date = len([v for v in target_video_ids if v in final_archived])

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
    print(f"  * {'Already Up-to-Date:':<22} {total_up_to_date:>4d} track(s)")
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
