"""
AI-assisted playlist formatting, reorganization, and title cleanup.
Supports OpenAI, Google Gemini, Groq, OpenRouter, Anthropic, Ollama, and custom endpoints.
"""

import os
import sys
import json
import re
import shutil
import urllib.request
import urllib.error
import ssl
import time
from datetime import datetime

from .config import (
    PLAYLISTS_DIR,
    PLAYLISTS_DATA_FILE,
    load_playlist_settings,
    save_playlist_settings,
    normalize_playlist_entry_format,
    playlist_entry_format_fields,
    load_all_playlist_settings,
    load_playlist_data,
    save_playlist_data,
    record_activity,
    VERSION,
)
from .parser import (
    parse_playlist_file,
    get_playlist_name_for_target,
    sanitize_filename,
    extract_video_id,
    extract_playlist_id,
    parse_header_line,
    _format_playlist_entry_line,
    _playlist_name_from_file_path,
)
from .downloader import (
    find_missing_metadata,
    fill_missing_metadata,
)

PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "env_key": "OPENAI_API_KEY",
        "api_type": "openai",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-3.6-flash",
        "env_key": "GEMINI_API_KEY",
        "api_type": "openai",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
        "api_type": "openai",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-3.3-70b-instruct",
        "env_key": "OPENROUTER_API_KEY",
        "api_type": "openai",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "default_model": "claude-3-5-haiku-latest",
        "env_key": "ANTHROPIC_API_KEY",
        "api_type": "anthropic",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
        "api_type": "openai",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-small-latest",
        "env_key": "MISTRAL_API_KEY",
        "api_type": "openai",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3.2",
        "env_key": None,
        "api_type": "openai",
    },
    "custom": {
        "base_url": "",
        "default_model": "",
        "env_key": "AI_API_KEY",
        "api_type": "openai",
    },
}

DEFAULT_USER_INSTRUCTION = (
    "Organize the playlist into broad genres, putting songs into big groups as much as possible where all songs in each group are related "
    "(e.g. 'Hip-Hop & Rap', 'Rock, Nu-Metal & Alternative Rock', 'R&B, Soul & Neo-Soul'). "
    "If an unorganized section exists, sort all its songs into their proper matching groups and do not keep the unorganized section."
)

SYSTEM_PROMPT = """You are an expert music curator and playlist organization assistant for YouTube Playlist Manager (ypm).
Your task is to organize, categorize, and sequence a playlist text file according to the user's instructions.

File Format Rules:
1. Main Playlist Header (Line 1, optional):
   - Starts with three hashes: '### <Playlist_URL_or_ID> | <Playlist Title>' (or '### <Playlist Title>')
   - If present in the input, you MUST preserve it at the very top.
   - If NOT present in the input, you should still organize and sequence all tracks into sections below.

2. Section Headers:
   - Starts with two hashes: '## <Section Name>'
   - Or if linked to a YouTube playlist: '## <Playlist_ID_or_URL> | <Section Name>' (e.g. '## https://... | Hip-Hop & Rap | conyay whecst')
   - If an existing section header already has a playlist link or ID, you MUST preserve that exact link, ID, and header line!
   - For any new sections created, simply use: '## Section Name'
   - If the input has NO section headers at all (a flat track list), you MUST group all tracks into broad genre sections from scratch.

3. Track Lines:
   - Input track lines are provided with full rich context in the format:
     <video_id> | <title> | <channel> | <duration>
   - Preserve all track titles and metadata intact. Focus your effort on sectioning and sequencing tracks.
   - Format each track line in your output as:
     <video_id> | <title> | <channel> | <duration>

ORGANIZATION & CURATION GUIDELINES:
1. Broad Genre Grouping (Big Groups of Related Songs):
   - Put songs into broad genres, making groups as big as possible while ensuring all songs in each group are genuinely related and similar to each other.
   - Do NOT make divisions too small: Never split a major genre into narrow micro-genres, eras, or sub-subgenres (e.g., do NOT split Hip-Hop into 10 separate buckets for 90s, trap, boom-bap, cloud rap, etc. Keep all Hip-Hop and Rap united together in one big group!).
   - Do NOT make divisions too big: Never combine fundamentally distinct musical genres into clumsy catch-all buckets (e.g., NEVER combine Pop, R&B, and Electronic together, and NEVER combine Rock and Rap).
   - Standard broad, cohesive genre families include:
     • Hip-Hop & Rap (keeps all rap, trap, boom-bap, and hip-hop together)
     • R&B, Soul & Neo-Soul
     • Rock, Nu-Metal & Alternative Rock
     • Indie, Alternative & Psychedelic
     • Pop, Dance & Electronic
     • Jazz, Instrumental & Soundtracks

2. Eliminate "Unorganized" / "Unsorted" Sections:
   - If the input contains a section like '## Unorganized', '## Unsorted', '## Inbox', or '## New Tracks', DO NOT KEEP THAT SECTION in your output under any circumstances!
   - Every single track inside an unorganized section MUST be sorted into its proper matching broad genre section above.
   - The output file must NEVER contain an unorganized or inbox section.

3. Respect and Maintain Existing Defined Sections:
   - When the playlist already has established sections (especially linked sections with playlist URLs/IDs like '## <url> | <name>'), treat those existing sections as the primary categories.
   - Maintain the exact headers, IDs, and titles of those existing sections.
   - Preserve already-organized tracks: Unless the user explicitly asks to "reorganize", "re-sort", or "restructure" the whole playlist, keep songs that are already in established sections in their current sections. Do not shuffle them around between sections.
   - Focus your sorting primarily on tracks from unorganized/unsorted sections, placing each one into its proper matching broad section above.
   - Only create a new section if there are tracks that genuinely cannot belong to any existing section.

CRITICAL INTEGRITY CONSTRAINTS:
1. EVERY video ID from the input MUST be present in the output. DO NOT DROP ANY TRACKS.
2. DO NOT hallucinate, fabricate, or change ANY 11-character video IDs.
3. DO NOT invent new songs or add tracks that were not in the input.
4. Output ONLY the raw playlist text. Do not include markdown conversational banter, introductions, or closing remarks.
"""


def resolve_ai_config(settings, args=None):
    """
    Resolves the active AI provider, API key, model, and base URL from settings, CLI args, and env vars.
    Returns (config_dict, error_message).
    """
    ai_settings = settings.get("ai", {}) if isinstance(settings.get("ai"), dict) else {}
    
    # 1. Provider
    cli_provider = getattr(args, "provider", None) if args else None
    provider = (cli_provider or ai_settings.get("provider") or "openai").strip().lower()
    
    provider_info = PROVIDERS.get(provider, PROVIDERS["custom"])
    api_type = provider_info.get("api_type", "openai")
    
    # 2. Base URL
    custom_base_url = (ai_settings.get("base_url") or "").strip()
    base_url = custom_base_url or provider_info.get("base_url", "")
    base_url = base_url.rstrip("/")
    
    # 3. Model
    cli_model = getattr(args, "model", None) if args else None
    model = (cli_model or ai_settings.get("model") or provider_info.get("default_model", "")).strip()
    
    # 4. Timeout
    timeout = ai_settings.get("timeout", 120)
    try:
        timeout = float(timeout)
    except (ValueError, TypeError):
        timeout = 120.0
        
    # 5. API Key
    api_key = (ai_settings.get("api_key") or "").strip()
    env_key_name = provider_info.get("env_key")
    if not api_key and env_key_name:
        api_key = os.environ.get(env_key_name, "").strip()
    if not api_key:
        api_key = os.environ.get("AI_API_KEY", "").strip()

    # Ollama doesn't require an API key
    if provider == "ollama":
        api_key = api_key or "ollama"
        if not base_url:
            base_url = "http://localhost:11434/v1"

    if not base_url:
        return None, f"No base_url configured for AI provider '{provider}'."

    if not api_key and provider != "ollama":
        env_hint = f"${env_key_name}" if env_key_name else "$AI_API_KEY"
        return None, (
            f"No API key found for AI provider '{provider}'.\n"
            f"  • Configure 'api_key' in settings.toml under [ai]:\n"
            f"      [ai]\n"
            f"      provider = \"{provider}\"\n"
            f"      api_key = \"your-key-here\"\n"
            f"  • Or set the environment variable: {env_hint}\n"
            f"  • Tip: To use a 100% free local AI with zero API keys, run Ollama and set provider = \"ollama\"."
        )

    return {
        "provider": provider,
        "api_type": api_type,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "timeout": timeout,
    }, None


def _urlopen_with_ssl_fallback(req, timeout):
    """
    Attempts to execute a URL request. If Windows certificate validation fails due to
    missing local root CA certificates in Python, retries using an unverified SSL context.
    """
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        # An HTTP response was received (e.g. 400, 404, 503) — SSL handshake succeeded.
        raise
    except urllib.error.URLError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e.reason):
            ctx = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


def _format_ai_error(code, provider, model, raw_msg):
    """Formats raw API error responses into clear, actionable advice across all providers."""
    try:
        parsed = json.loads(raw_msg)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            parsed = parsed[0]
        msg = (
            parsed.get("error", {}).get("message")
            if isinstance(parsed.get("error"), dict)
            else (parsed.get("detail") or parsed.get("message") or parsed.get("error") or raw_msg)
        )
        if not isinstance(msg, str):
            msg = str(msg)
    except Exception:
        msg = raw_msg

    msg = msg.strip()

    if code == 401:
        tip = f"The API key for provider '{provider}' is invalid, missing, or expired. Check 'api_key' in settings.toml."
    elif code == 403:
        tip = f"Access was denied by provider '{provider}'. Check your account permissions, project setup, or billing tier."
    elif code == 404:
        tip = f"The model '{model}' was not found or is no longer available on provider '{provider}'. Update via: python main.py config ai.model <model_name>"
    elif code == 429:
        tip = f"Rate limit or quota exceeded on '{provider}'. You may need to wait a minute, upgrade quota, or switch providers."
    elif code == 503:
        tip = f"The AI provider '{provider}' is currently experiencing high demand on '{model}'. Wait a moment and try again, or switch to another model via: python main.py config ai.model <model_name>"
    else:
        tip = None

    if tip:
        return f"HTTP {code} from {provider}: {msg}\n  • Tip: {tip}"
    return f"HTTP {code} from {provider}: {msg}"


def _call_ai_api(ai_config, system_prompt, user_message):
    """
    Sends a chat completion request to the configured AI endpoint.
    Uses standard library urllib.request for zero external dependencies.
    Includes automatic retries with exponential backoff for transient server busy (503) or rate limit (429) errors.
    """
    api_type = ai_config["api_type"]
    base_url = ai_config["base_url"]
    model = ai_config["model"]
    api_key = ai_config["api_key"]
    timeout = ai_config["timeout"]

    if api_type == "anthropic":
        url = f"{base_url}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": f"YouTube-Playlist-Manager/{VERSION}",
        }
        payload = {
            "model": model,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.2,
        }
    else:
        # Standard OpenAI-compatible endpoint (OpenAI, Gemini, Groq, OpenRouter, Ollama)
        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"YouTube-Playlist-Manager/{VERSION}",
        }
        if api_key and api_key != "ollama":
            headers["Authorization"] = f"Bearer {api_key}"
        if "openrouter.ai" in base_url:
            headers["HTTP-Referer"] = "https://github.com/Synapse512/youtube-playlist-manager"
            headers["X-Title"] = "YouTube Playlist Manager"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": 0.2,
        }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    max_retries = 4
    retry_delay = 5.0

    for attempt in range(1, max_retries + 1):
        try:
            with _urlopen_with_ssl_fallback(req, timeout=timeout) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
                res_json = json.loads(resp_body)
                break
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            # If temporary server high-demand (503) or rate limit (429), retry with backoff
            if e.code in (503, 429) and attempt < max_retries:
                print(f"[*] Provider '{ai_config['provider']}' returned HTTP {e.code} (temporary high demand). Retrying in {retry_delay:.0f}s (attempt {attempt}/{max_retries})...")
                time.sleep(retry_delay)
                retry_delay *= 2.0
                continue
            formatted = _format_ai_error(e.code, ai_config["provider"], model, err_msg)
            raise RuntimeError(formatted)
        except urllib.error.URLError as e:
            if "11434" in base_url:
                raise RuntimeError(f"Could not connect to Ollama at {base_url}. Make sure Ollama is running ('ollama serve').")
            raise RuntimeError(f"Network error connecting to {base_url}: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {e}")

    # Extract assistant message content
    if api_type == "anthropic":
        content_blocks = res_json.get("content", [])
        text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        result_text = "\n".join(text_parts).strip()
    else:
        choices = res_json.get("choices", [])
        if not choices:
            raise RuntimeError(f"No response choices returned by {ai_config['provider']}.")
        result_text = choices[0].get("message", {}).get("content", "").strip()

    # Strip any enclosing markdown code fences
    fence_match = re.search(r"^```(?:text|markdown)?\s*\n(.*?)\n```\s*$", result_text, re.DOTALL)
    if fence_match:
        result_text = fence_match.group(1).strip()

    return result_text


def test_ai_connection(ai_config):
    """
    Performs a lightweight 1-token pre-flight test against the configured AI model/endpoint
    before doing any heavy yt-dlp metadata enrichment or data transmission.
    Returns (True, None) on success, or (False, error_message) on failure.
    """
    test_config = dict(ai_config)
    test_config["timeout"] = min(ai_config.get("timeout", 30), 20.0)
    api_type = test_config["api_type"]
    base_url = test_config["base_url"]
    model = test_config["model"]
    api_key = test_config["api_key"]
    timeout = test_config["timeout"]

    if api_type == "anthropic":
        url = f"{base_url}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": f"YouTube-Playlist-Manager/{VERSION}",
        }
        payload = {
            "model": model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    else:
        url = f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"YouTube-Playlist-Manager/{VERSION}",
        }
        if api_key and api_key != "ollama":
            headers["Authorization"] = f"Bearer {api_key}"
        if "openrouter.ai" in base_url:
            headers["HTTP-Referer"] = "https://github.com/Synapse512/youtube-playlist-manager"
            headers["X-Title"] = "YouTube Playlist Manager"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    max_retries = 2
    retry_delay = 1.5

    for attempt in range(1, max_retries + 1):
        try:
            with _urlopen_with_ssl_fallback(req, timeout=timeout) as resp:
                resp.read()
                return True, None
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="replace")
            if e.code in (503, 429) and attempt < max_retries:
                time.sleep(retry_delay)
                retry_delay *= 2.0
                continue
            formatted = _format_ai_error(e.code, test_config["provider"], model, err_msg)
            return False, formatted
        except urllib.error.URLError as e:
            if "11434" in base_url:
                return False, f"Could not connect to Ollama at {base_url}. Make sure Ollama is running ('ollama serve')."
            return False, f"Network error connecting to {base_url}: {e.reason}"
        except Exception as e:
            return False, f"Connection failed: {e}"


def _parse_ai_output(ai_text, original_ids, original_metadata):
    """
    Parses the raw AI text output, validates video ID integrity, cleans/updates metadata,
    and returns (parsed_sections, missing_ids, hallucinated_ids, cleaned_title_count).
    """
    original_id_set = set(original_ids)
    returned_ids = []
    seen_returned = set()
    cleaned_titles = {}
    cleaned_title_count = 0

    lines = ai_text.splitlines()
    main_header_line = None
    sections = []
    current_sec = {
        "title": "Default",
        "playlist_id": None,
        "playlist_name": "",
        "video_ids": [],
        "is_implicit": True,
    }

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        # Main Header (###)
        if line_str.startswith("###"):
            if not main_header_line:
                main_header_line = line_str
            continue

        # Section Header (##)
        if line_str.startswith("##"):
            if current_sec["video_ids"] or not current_sec.get("is_implicit"):
                sections.append(current_sec)

            hdr = parse_header_line(line_str)
            sec_title = hdr.get("title", "").strip() or "Section"
            sec_id = hdr.get("playlist_id")
            sec_pl_name = hdr.get("playlist_name", "").strip()

            current_sec = {
                "title": sec_title,
                "playlist_id": sec_id,
                "playlist_name": sec_pl_name,
                "video_ids": [],
                "is_implicit": False,
            }
            continue

        # Track line
        parts = [p.strip() for p in line_str.split("|")]
        if not parts:
            continue
        raw_vid = parts[0]
        vid = extract_video_id(raw_vid)
        if not vid or len(vid) != 11:
            continue

        # Capture cleaned title if present
        if len(parts) >= 2 and parts[1]:
            new_title = parts[1].strip()
            old_meta = original_metadata.get(vid, {})
            old_title = old_meta.get("title", "")
            if new_title and new_title != old_title:
                cleaned_titles[vid] = new_title
                cleaned_title_count += 1
            if len(parts) >= 3 and parts[2]:
                old_meta["channel"] = parts[2].strip()
            if len(parts) >= 4 and parts[3]:
                old_meta["duration"] = parts[3].strip()

        current_sec["video_ids"].append(vid)
        returned_ids.append(vid)
        seen_returned.add(vid)

    if current_sec["video_ids"] or not current_sec.get("is_implicit"):
        sections.append(current_sec)

    returned_id_set = set(returned_ids)
    missing_ids = [v for v in original_ids if v not in returned_id_set]
    hallucinated_ids = [v for v in returned_ids if v not in original_id_set]

    # Filter out hallucinated IDs from sections
    if hallucinated_ids:
        halluc_set = set(hallucinated_ids)
        for s in sections:
            s["video_ids"] = [v for v in s["video_ids"] if v not in halluc_set]

    # Rescue any missing IDs by putting them into an Unsorted section at the end
    if missing_ids:
        sections.append({
            "title": "Unsorted Tracks (Retained)",
            "playlist_id": None,
            "playlist_name": "",
            "video_ids": list(missing_ids),
            "is_implicit": False,
        })

    return main_header_line, sections, missing_ids, hallucinated_ids, cleaned_titles, cleaned_title_count


def _build_final_playlist_text(main_header_line, sections, original_metadata, cleaned_titles, entry_format, clickable_links):
    """
    Renders the sections and track lines back into the user's configured playlist_entry_format.
    """
    lines = []
    if main_header_line:
        lines.append(main_header_line)
        lines.append("")

    for sec in sections:
        if not sec.get("is_implicit"):
            pid = sec.get("playlist_id")
            title = sec.get("title", "Section")
            pl_name = sec.get("playlist_name", "")
            if pid:
                link_target = f"https://www.youtube.com/playlist?list={pid}" if clickable_links else pid
                if pl_name:
                    sec_hdr = f"## {link_target} | {title} | {pl_name}"
                else:
                    sec_hdr = f"## {link_target} | {title}"
            else:
                if pl_name:
                    sec_hdr = f"## {title} | {pl_name}"
                else:
                    sec_hdr = f"## {title}"
            lines.append(sec_hdr)

        for vid in sec.get("video_ids", []):
            meta = dict(original_metadata.get(vid, {}))
            if vid in cleaned_titles:
                meta["title"] = cleaned_titles[vid]
            line_str = _format_playlist_entry_line(vid, meta, entry_format=entry_format, clickable_links=clickable_links)
            lines.append(line_str)
        lines.append("")

    # Clean up trailing blank lines
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)


def command_ai_format(args, settings, playlist_data):
    """
    Executes the 'ai-format' command to organize sections, clean titles, or categorize tracks using AI.
    """
    target_name = args.target.strip()
    playlist_name = get_playlist_name_for_target(target_name, playlist_data)
    safe_name = sanitize_filename(playlist_name)
    file_path = os.path.join(PLAYLISTS_DIR, f"{safe_name}.txt")

    if not os.path.exists(file_path):
        print(f"[!] Error: Local file '{file_path}' does not exist.")
        return

    pl_settings = load_playlist_settings(playlist_name)
    entry_format = pl_settings.get("playlist_entry_format")
    clickable_links = settings.get("clickable_links_in_playlist_files", False)

    print(f"[*] Reading '{file_path}'...")
    target_video_ids, target_video_titles, _, blank_above, sections_data = parse_playlist_file(file_path, entry_format)

    if not target_video_ids:
        print("[!] Error: No valid tracks found in playlist file.")
        return

    # 1. Resolve AI configuration
    ai_config, err = resolve_ai_config(settings, args)
    if err:
        print(f"\n[!] Configuration Error:\n{err}")
        return

    # Pre-flight check: Verify that the AI provider and model are accessible BEFORE doing heavy work or taking inputs
    print(f"[*] Verifying AI model connection to '{ai_config['provider']}' ({ai_config['model']})...")
    conn_ok, conn_err = test_ai_connection(ai_config)
    if not conn_ok:
        print(f"\n[!] AI Connection Test Failed: {conn_err}")
        print(f"    Check your model name ('{ai_config['model']}'), API key, or provider in settings.toml [ai].")
        return
    print(f"[+] AI model '{ai_config['model']}' is online and verified.")

    # 2. Get user instructions
    cli_prompt = getattr(args, "prompt", None)
    saved_playlist_prompt = (pl_settings.get("ai_prompt") or "").strip()

    if cli_prompt and cli_prompt.strip():
        user_instructions = cli_prompt.strip()
    elif saved_playlist_prompt:
        print(f"\n[*] Using saved prompt for '{playlist_name}':")
        print(f"    \"{saved_playlist_prompt}\"")
        user_instructions = saved_playlist_prompt
    else:
        preset_options = [
            ("Organize by genre", "Sort unorganized tracks into broad genre families, keeping existing sections intact"),
            ("Reorganize all tracks", "Re-evaluate and regroup all tracks across the entire playlist from scratch"),
            ("Organize by artist", "Group tracks by artist / discography into logical sections"),
        ]

        print(f"\n[*] Ready to organize '{playlist_name}' ({len(target_video_ids)} track(s)).")
        print("  [?] Choose an organization option or type a custom prompt:")
        for idx, (p_title, p_desc) in enumerate(preset_options, 1):
            default_tag = " (Default)" if idx == 1 else ""
            print(f"      [{idx}] {p_title}{default_tag} - {p_desc}")

        preset_titles = [po[0] for po in preset_options]
        recent_prompts = [
            p for p in playlist_data.get("recent_ai_prompts", [])
            if p not in preset_titles and p != DEFAULT_USER_INSTRUCTION
        ]
        if recent_prompts:
            print("  [*] Recent Custom Prompts:")
            for idx, p in enumerate(recent_prompts, len(preset_options) + 1):
                print(f"      [{idx}] {p}")

        max_choice = len(preset_options) + len(recent_prompts)
        print(f"      [Enter a number (1-{max_choice}), press Enter for default [1], or type a custom prompt]")

        try:
            user_input = input("      > ").strip()
        except EOFError:
            user_input = ""

        if user_input.isdigit() and 1 <= int(user_input) <= max_choice:
            choice = int(user_input)
            if choice <= len(preset_options):
                chosen_title = preset_options[choice - 1][0]
                if choice == 1:
                    user_instructions = DEFAULT_USER_INSTRUCTION
                elif choice == 2:
                    user_instructions = "Reorganize the entire playlist from scratch into broad cohesive genre families, re-evaluating all tracks."
                elif choice == 3:
                    user_instructions = "Organize tracks by artist and musical collaborators into logical sections."
                print(f"[+] Selected: {chosen_title}")
            else:
                user_instructions = recent_prompts[choice - len(preset_options) - 1]
                print(f"[+] Selected: \"{user_instructions}\"")
        elif user_input:
            user_instructions = user_input
        else:
            user_instructions = DEFAULT_USER_INSTRUCTION
            print("[+] Selected: Organize by genre (Default)")

    # Save prompt to recent history if custom
    preset_instructions = [
        DEFAULT_USER_INSTRUCTION,
        "Reorganize the entire playlist from scratch into broad cohesive genre families, re-evaluating all tracks.",
        "Organize tracks by artist and musical collaborators into logical sections.",
    ]
    if user_instructions and user_instructions not in preset_instructions:
        recents = playlist_data.setdefault("recent_ai_prompts", [])
        if user_instructions in recents:
            recents.remove(user_instructions)
        recents.insert(0, user_instructions)
        playlist_data["recent_ai_prompts"] = recents[:5]
        save_playlist_data(playlist_data)

    # 3. Enrich metadata to give the AI rich context (%(id)s | %(title)s | %(channel)s | %(duration)s)
    full_context_fields = ["id", "title", "channel", "duration"]
    video_metadata = sections_data.setdefault("video_metadata", {})
    missing_meta = find_missing_metadata(target_video_ids, video_metadata, full_context_fields)

    if missing_meta:
        print(f"[*] Enriching metadata for {len(missing_meta)} track(s) via yt-dlp to provide full artist/channel context...")
        main_hdr_id = (sections_data.get("main_header") or {}).get("playlist_id")
        fill_missing_metadata(target_video_ids, video_metadata, full_context_fields, settings=settings, playlist_id=main_hdr_id)

    # 4. Build rich context string for the AI
    context_lines = []
    m_hdr = sections_data.get("main_header")
    if m_hdr:
        hdr_pid = m_hdr.get("playlist_id")
        hdr_title = m_hdr.get("title") or playlist_name
        if hdr_pid and hdr_title:
            context_lines.append(f"### {hdr_pid} | {hdr_title}")
        elif hdr_title:
            context_lines.append(f"### {hdr_title}")
        context_lines.append("")

    if sections_data.get("is_sectioned") and sections_data.get("sections"):
        for sec in sections_data["sections"]:
            if not sec.get("is_implicit"):
                s_id = sec.get("playlist_id")
                s_url = sec.get("url")
                s_title = sec.get("title", "Section")
                s_pl_name = sec.get("playlist_name", "")
                link_id = s_url if (s_url and clickable_links) else (s_id or "")
                if link_id:
                    context_lines.append(f"## {link_id} | {s_title}" + (f" | {s_pl_name}" if s_pl_name else ""))
                else:
                    context_lines.append(f"## {s_title}" + (f" | {s_pl_name}" if s_pl_name else ""))
            for vid in sec.get("video_ids", []):
                meta = video_metadata.get(vid, {})
                t = meta.get("title") or target_video_titles.get(vid) or "Unknown Title"
                c = meta.get("channel") or ""
                d = meta.get("duration") or ""
                context_lines.append(f"{vid} | {t} | {c} | {d}")
            context_lines.append("")
    else:
        for vid in target_video_ids:
            meta = video_metadata.get(vid, {})
            t = meta.get("title") or target_video_titles.get(vid) or "Unknown Title"
            c = meta.get("channel") or ""
            d = meta.get("duration") or ""
            context_lines.append(f"{vid} | {t} | {c} | {d}")

    rich_text = "\n".join(context_lines)

    reorg_keywords = ["reorganize", "re-organize", "resort", "re-sort", "restructure", "from scratch", "regroup all"]
    is_reorganize = any(k in user_instructions.lower() for k in reorg_keywords)

    instructions_text = user_instructions
    instructions_text += (
        "\n\nCuration Rules:\n"
        "- Organize into broad genres: Put songs into big groups as much as possible where all songs in each group are related and similar.\n"
        "- Do not make divisions too small (do NOT split major genres like Hip-Hop into tiny subgenre fragments).\n"
        "- Do not make divisions too big (do NOT combine completely unrelated genres into slashed mega-buckets).\n"
        "- If an '## Unorganized', '## Unsorted', or inbox section exists, it MUST NOT be kept in the output: sort all of its songs into their matching broad genre sections above.\n"
        "- Preserve all existing linked section headers (## <url> | <name>) exactly as they are.\n"
    )
    has_existing_sections = bool(
        sections_data.get("is_sectioned")
        and any(not s.get("is_implicit") for s in sections_data.get("sections", []))
    )
    if not has_existing_sections:
        instructions_text += "- Create Section Headers: This playlist currently has no sections. Group all tracks into broad, cohesive genre family sections (e.g. '## Hip-Hop & Rap', '## Rock & Alternative', etc.).\n"
    elif is_reorganize:
        instructions_text += "- Full Reorganization: The user requested a full reorganization. You may re-evaluate and regroup all tracks across the entire playlist into cohesive broad genre families.\n"
    else:
        instructions_text += "- Preserve Already-Organized Tracks: Keep tracks that are already under established headers in their existing sections. Do NOT shuffle existing organized tracks around between sections; focus on sorting the unorganized / new tracks into their proper matching sections.\n"

    user_message = f"User Instructions:\n{instructions_text}\n\nPlaylist Content ({len(target_video_ids)} tracks):\n{rich_text}"

    # 5. Call AI API
    print(f"[*] Contacting AI provider '{ai_config['provider']}' (model: {ai_config['model']})...")
    try:
        ai_response = _call_ai_api(ai_config, SYSTEM_PROMPT, user_message)
    except Exception as e:
        print(f"\n[!] AI API Call Failed: {e}")
        return

    # 6. Parse and Validate AI output
    main_hdr, sections, missing_ids, hallucinated_ids, cleaned_titles, cleaned_count = _parse_ai_output(
        ai_response, target_video_ids, video_metadata
    )

    if hallucinated_ids:
        print(f"[!] Removed {len(hallucinated_ids)} hallucinated track(s) not present in the original playlist.")
    if missing_ids:
        print(f"[!] AI dropped {len(missing_ids)} track(s); preserved them under '## Unsorted Tracks (Retained)'.")

    # 7. Format back to user's configured format
    final_text = _build_final_playlist_text(
        main_hdr or (f"### {playlist_name}"),
        sections,
        video_metadata,
        cleaned_titles,
        entry_format,
        clickable_links
    )

    # 8. Dry-run preview or save
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("\n" + "=" * 60)
        print(f" [DRY RUN] Preview of AI Changes for '{playlist_name}'")
        print("=" * 60)
        print(" Sections Summary:")
        for idx, sec in enumerate(sections, 1):
            sec_title = sec.get("title") or "Untitled Section"
            count = len(sec.get("video_ids", []))
            print(f"   [{idx}] {sec_title} ({count} track{'s' if count != 1 else ''})")
        print("-" * 60)
        preview_lines = final_text.splitlines()[:25]
        for l in preview_lines:
            print(f"  {l}")
        if len(final_text.splitlines()) > 25:
            print(f"  ... ({len(final_text.splitlines()) - 25} more lines)")
        print("\n[*] Dry run complete. No files were modified.")
        return

    # 9. Create timestamped backup
    backup_dir = os.path.join(PLAYLISTS_DIR, "archive")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"{safe_name}_pre_ai_{timestamp}.txt")
    try:
        shutil.copyfile(file_path, backup_path)
    except OSError as e:
        print(f"[!] Warning: Could not create backup file: {e}")

    # 10. Write formatted playlist
    atomic = pl_settings.get("atomic_writes", True)
    try:
        if atomic:
            temp_path = f"{file_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(final_text)
            if os.path.exists(file_path):
                os.replace(temp_path, file_path)
            else:
                os.rename(temp_path, file_path)
        else:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(final_text)
    except OSError as e:
        print(f"[!] Error writing updated playlist to '{file_path}': {e}")
        return

    # 11. Summary
    explicit_sections = [s for s in sections if not s.get("is_implicit")]
    print("\n" + "=" * 60)
    print(" AI Organization Summary")
    print("=" * 60)
    print(f"  * {'Playlist:':<24} {playlist_name}")
    print(f"  * {'Provider / Model:':<24} {ai_config['provider']} ({ai_config['model']})")
    print(f"  * {'Total Tracks:':<24} {len(target_video_ids):>4d} track(s)")
    print(f"  * {'Sections:':<24} {len(explicit_sections):>4d} section(s)")
    if explicit_sections:
        for s in explicit_sections[:8]:
            print(f"      • {s['title']} ({len(s['video_ids'])} tracks)")
        if len(explicit_sections) > 8:
            print(f"      • ... and {len(explicit_sections) - 8} more section(s)")
    print(f"  * {'Backup Created:':<24} {backup_path}")
    print("=" * 60)
    record_activity(playlist_data, playlist_name, "ai-format")
    print(f"[+] Successfully organized and saved '{file_path}'!")

    # If the user has downloaded media files for this playlist on disk, synchronize their numbering and names
    try:
        from .downloader import sync_downloaded_playlist_files
        target_titles_map = {
            v: (cleaned_titles.get(v) or (video_metadata.get(v) or {}).get("title") or target_video_titles.get(v) or "Untitled Video")
            for v in target_video_ids
        }
        all_ordered_vids = []
        for s in sections:
            all_ordered_vids.extend(s.get("video_ids", []))
        renamed_dl = sync_downloaded_playlist_files(
            playlist_name, all_ordered_vids or target_video_ids, target_titles_map,
            {"sections": sections, "is_sectioned": bool(explicit_sections)},
            settings, pl_settings
        )
        if renamed_dl > 0:
            print(f"[+] Synchronized numbering for {renamed_dl} local downloaded track(s) to match the AI organization.")
    except Exception as exc:
        print(f"    [!] Note: could not sync downloaded files: {exc}")
