#!/usr/bin/env python3
"""
Gengar DJ — Song Generation Orchestrator
=========================================
Automates Suno AI music generation using Camofox, downloads the completed tracks,
and POSTs them back to the Gengar DJ Discord bot's callback endpoint.
"""

import argparse
import glob
import json
import os
import sys
import time
import urllib.request
import re
from curl_cffi import requests

# Configuration
CAMOFOX_BASE = "http://127.0.0.1:9377"
GET_SESSION_URL = "https://clerk.suno.com/v1/client?_clerk_js_version=4.73.2"
BASE_URL = "https://studio-api.prod.suno.com"
BROWSER_VERSION = "chrome110"

# James's Banned Vocabulary (STRICT MODE)
BANNED_WORDS = [
    "neon", "echo", "shadow", "whisper", "veil", "tapestry", 
    "symphony", "labyrinth", "realm", "ignite", "unleash", "elevate"
]

def extract_cookies():
    profiles = glob.glob("/home/james/.camofox/profiles/*/storage-state.json")
    for profile_path in profiles:
        try:
            with open(profile_path, "r") as f:
                state = json.load(f)
            cookies = state.get("cookies", [])
            suno_cookies = []
            for c in cookies:
                if "suno.com" in c.get("domain", ""):
                    suno_cookies.append(f"{c['name']}={c['value']}")
            if suno_cookies:
                print(f"[Gengar DJ Generator] Extracted cookies from: {profile_path}")
                return "; ".join(suno_cookies)
        except Exception:
            pass
    return None

def run_strict_checks(style_tags, prompt):
    violations = []
    text_to_scan = f"{style_tags.lower()} {prompt.lower()}"
    for word in BANNED_WORDS:
        pattern = r"\b" + re.escape(word) + r"\b"
        if re.search(pattern, text_to_scan):
            violations.append(word)
    return violations

def main():
    parser = argparse.ArgumentParser(description="Gengar DJ Song Generator")
    parser.add_argument("--prompt", required=True, help="User prompt/theme")
    parser.add_argument("--style-tags", required=True, help="Suno style tags")
    parser.add_argument("--title", required=True, help="Song title")
    parser.add_argument("--callback-url", required=True, help="Bot callback URL")
    parser.add_argument("--guild-id", required=True, type=int, help="Discord Guild ID")
    parser.add_argument("--channel-id", required=True, type=int, help="Discord Channel ID")
    parser.add_argument("--user-id", required=True, type=int, help="Requesting User ID")
    parser.add_argument("--play-in-vc", action="store_true", help="Play immediately on completion")
    
    args = parser.parse_args()

    cookie_str = extract_cookies()
    if not cookie_str:
        print("[Gengar DJ Generator] Error: No Suno cookies found. Please run a login session via VNC first.")
        sys.exit(1)

    # Clean the prompt and style tags of any banned words
    style_tags = args.style_tags
    prompt = args.prompt
    violations = run_strict_checks(style_tags, prompt)
    if violations:
        print(f"[Gengar DJ Generator] Strict Mode: Found banned words {violations}. Cleaning them up...")
        # Replace banned words with safe, moody alternatives
        replacements = {
            "neon": "cyber",
            "echo": "reverb",
            "shadow": "spectre",
            "whisper": "murmur",
            "veil": "mist",
            "tapestry": "weave",
            "symphony": "harmony",
            "labyrinth": "maze",
            "realm": "zone",
            "ignite": "kindle",
            "unleash": "free",
            "elevate": "raise"
        }
        for word in violations:
            pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
            style_tags = pattern.sub(replacements[word.lower()], style_tags)
            prompt = pattern.sub(replacements[word.lower()], prompt)

    # Initialize API Session
    session = requests.Session()
    session.headers.update({
        "Accept-Encoding": "gzip, deflate, br",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "cookie": cookie_str
    })

    print("[Gengar DJ Generator] Logging into Clerk to retrieve API token...")
    res = session.get(GET_SESSION_URL, impersonate=BROWSER_VERSION)
    if res.status_code != 200:
        print(f"[Gengar DJ Generator] Clerk login failed: {res.status_code}")
        sys.exit(1)

    sessions = res.json().get("response", {}).get("sessions", [])
    jwt = None
    for s in sessions:
        if s.get("status") == "active":
            jwt = s.get("last_active_token", {}).get("jwt")
            break
    if not jwt and sessions:
        jwt = sessions[0].get("last_active_token", {}).get("jwt")

    if not jwt:
        print("[Gengar DJ Generator] Error: No active Clerk JWT found.")
        sys.exit(1)

    session.headers["Authorization"] = f"Bearer {jwt}"

    # Check credits before generating
    try:
        bill_res = session.get(f"{BASE_URL}/api/billing/info/", impersonate=BROWSER_VERSION)
        if bill_res.status_code == 200:
            credits = bill_res.json().get("total_credits_left", 0)
            print(f"[Gengar DJ Generator] Verified Suno Account. Credits left: {credits}")
            if credits < 10:
                print("[Gengar DJ Generator] Error: Insufficient credits to generate music.")
                sys.exit(1)
    except Exception as e:
        print(f"[Gengar DJ Generator] Warning: Could not verify billing credits: {e}")

    # Fetch baseline feed from API
    print("[Gengar DJ Generator] Fetching baseline feed from Suno API...")
    feed_res = session.get(f"{BASE_URL}/api/feed/?page=1", impersonate=BROWSER_VERSION)
    baseline_ids = set()
    if feed_res.status_code == 200:
        baseline_ids = {clip["id"] for clip in feed_res.json()}
    print(f"[Gengar DJ Generator] Baseline feed contains {len(baseline_ids)} clips.")

    # 1. Open Camofox tab
    print("[Gengar DJ Generator] Opening stealth Camofox tab...")
    tab_res = requests.post(f"{CAMOFOX_BASE}/tabs", json={
        "userId": "james",
        "url": "https://suno.com/create",
        "sessionKey": "suno"
    }, timeout=15)

    if tab_res.status_code != 200:
        print(f"[Gengar DJ Generator] Failed to create Camofox tab: {tab_res.text}")
        sys.exit(1)
        
    tab_id = tab_res.json().get("tabId")
    print(f"[Gengar DJ Generator] Opened tab: {tab_id}. Waiting 10 seconds for initial load...")
    time.sleep(10)

    # 2. Unified JS Form Hydration & Clicking Create
    js_payload = json.dumps({
        "style_tags": style_tags,
        "title": args.title,
        "weirdness": "60",
        "style_influence": "70"
    })
    
    fill_js = f"""
    (async () => {{
        const payload = {js_payload};
        
        // Dismiss cookie banner if present
        const allowAllBtn = document.querySelector('#accept-recommended-btn-handler');
        if (allowAllBtn) {{
            allowAllBtn.click();
            await new Promise(r => setTimeout(r, 1500));
        }}

        // Find and click "Advanced" button
        const advancedBtn = Array.from(document.querySelectorAll('button'))
            .find(el => el.innerText && el.innerText.trim() === 'Advanced');
        if (advancedBtn && !advancedBtn.className.includes('active')) {{
            advancedBtn.click();
            await new Promise(r => setTimeout(r, 2000));
        }}
        
        // Find and click "Sounds" tab
        const sounds = Array.from(document.querySelectorAll('button'))
            .find(el => el.innerText && el.innerText.trim() === 'Sounds');
        if (sounds) {{
            sounds.click();
            await new Promise(r => setTimeout(r, 2000));
        }}
        
        // Set sliders
        const w = document.querySelector('[aria-label="Weirdness"]');
        const si = document.querySelector('[aria-label="Style Influence"]');
        if (w) {{
            w.setAttribute('aria-valuenow', payload.weirdness);
            w.dispatchEvent(new Event('input', {{ bubbles: true }}));
            w.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        if (si) {{
            si.setAttribute('aria-valuenow', payload.style_influence);
            si.dispatchEvent(new Event('input', {{ bubbles: true }}));
            si.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }}
        
        // Get text areas
        const tas = Array.from(document.querySelectorAll('textarea'));
        let lyricsEl = tas.find(t => t.placeholder.toLowerCase().includes('lyrics') || t.placeholder.toLowerCase().includes('write'));
        if (!lyricsEl && tas.length > 0) lyricsEl = tas[0];
        
        let styleEl = tas.find(t => t !== lyricsEl && (t.placeholder.toLowerCase().includes('style') || t.placeholder.toLowerCase().includes('genre') || t.placeholder.toLowerCase().includes('groovy')));
        if (!styleEl && tas.length > 1) styleEl = tas[1];
        
        const titleEl = document.querySelector('input[placeholder*="title"], input[placeholder*="Title"]');
        const exclEl = document.querySelector('[placeholder*="exclude"], [placeholder*="Exclude"], [aria-label*="Exclude"]');
        
        // Use React prototype setters to bypass Virtual DOM state tracking
        const nts = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
        const nis = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
        
        if (lyricsEl) {{
            nts.call(lyricsEl, "[Instrumental]");
            lyricsEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
            lyricsEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
            lyricsEl.dispatchEvent(new Event('blur', {{ bubbles: true }}));
        }}
        if (styleEl) {{
            nts.call(styleEl, payload.style_tags);
            styleEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
            styleEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
            styleEl.dispatchEvent(new Event('blur', {{ bubbles: true }}));
        }}
        if (titleEl) {{
            nis.call(titleEl, payload.title);
            titleEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
            titleEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
            titleEl.dispatchEvent(new Event('blur', {{ bubbles: true }}));
        }}
        if (exclEl) {{
            nis.call(exclEl, "vocals, singing, rap, spoken word, voice");
            exclEl.dispatchEvent(new Event('input', {{ bubbles: true }}));
            exclEl.dispatchEvent(new Event('change', {{ bubbles: true }}));
            exclEl.dispatchEvent(new Event('blur', {{ bubbles: true }}));
        }}
        
        await new Promise(r => setTimeout(r, 1000));
        
        // Select submit button
        const btn = Array.from(document.querySelectorAll('button'))
            .find(el => el.innerText && el.innerText.trim().includes('Create') && 
                  (el.className.includes('bg-black') || el.className.includes('ei9tfkc0') || 
                   el.className.includes('button-background') || el.className.includes('css-x7k4s3')));
                   
        if (btn) {{
            btn.click();
            return {{ ok: true, msg: 'Create button clicked successfully!' }};
        }}
        return {{ ok: false, error: 'Create button not found inside the active DOM tree.' }};
    }})()
    """

    print("[Gengar DJ Generator] Submitting form to Suno.com inside browser...")
    eval_res = requests.post(f"{CAMOFOX_BASE}/tabs/{tab_id}/evaluate", json={
        "userId": "james",
        "expression": fill_js
    }, timeout=30)
    
    eval_data = eval_res.json()
    if not eval_data.get("ok") or not eval_data.get("result", {}).get("ok"):
        requests.delete(f"{CAMOFOX_BASE}/tabs/{tab_id}?userId=james")
        print(f"[Gengar DJ Generator] Error: Browser form filling failed: {eval_data}")
        sys.exit(1)
        
    print("[Gengar DJ Generator] Success: Form submitted. Waiting for backend to register clips...")
    time.sleep(8)
    
    # Poll API feed for newly registered clip IDs
    new_clip_ids = []
    print("[Gengar DJ Generator] Polling Suno feed to identify new clip IDs...")
    for attempt in range(1, 15):
        try:
            feed_now = session.get(f"{BASE_URL}/api/feed/?page=1", impersonate=BROWSER_VERSION)
            if feed_now.status_code == 200:
                current_ids = {clip["id"] for clip in feed_now.json()}
                new_ids = current_ids - baseline_ids
                if new_ids:
                    new_clip_ids = list(new_ids)
                    break
        except Exception as fe:
            print(f"[Gengar DJ Generator] Warning: Feed polling failed on attempt {attempt}: {fe}")
        time.sleep(4)

    # Close browser tab immediately to save memory
    requests.delete(f"{CAMOFOX_BASE}/tabs/{tab_id}?userId=james")

    if not new_clip_ids:
        print("[Gengar DJ Generator] Error: Timed out waiting for Suno to register new clips.")
        sys.exit(1)

    print(f"[Gengar DJ Generator] Found new clips: {new_clip_ids}")
    
    # 3. Poll for song rendering completion (up to 5 minutes)
    completed_clips = {}
    max_attempts = 30
    print("[Gengar DJ Generator] Waiting for tracks to finish rendering...")
    for attempt in range(1, max_attempts + 1):
        time.sleep(10)
        try:
            ids_str = ",".join(new_clip_ids)
            details_res = session.get(f"{BASE_URL}/api/feed/?ids={ids_str}", impersonate=BROWSER_VERSION)
            if details_res.status_code == 200:
                for clip in details_res.json():
                    cid = clip["id"]
                    status = clip.get("status")
                    audio_url = clip.get("audio_url")
                    
                    if status == "complete" and audio_url:
                        if cid not in completed_clips:
                            completed_clips[cid] = clip
                            print(f"[Gengar DJ Generator] Clip {cid} is ready!")
                            
                if len(completed_clips) == len(new_clip_ids):
                    print("[Gengar DJ Generator] All tracks rendered successfully!")
                    break
        except Exception as e:
            print(f"[Gengar DJ Generator] Polling warning: {e}")

    if not completed_clips:
        print("[Gengar DJ Generator] Error: Rendering timed out.")
        sys.exit(1)

    # 4. Download tracks locally and send callback to the Discord bot
    tmp_dir = "/tmp/gengar-dj"
    os.makedirs(tmp_dir, exist_ok=True)
    
    # We will pick the first completed track as our primary song
    primary_clip_id = list(completed_clips.keys())[0]
    clip = completed_clips[primary_clip_id]
    audio_url = clip["audio_url"]
    
    local_path = os.path.join(tmp_dir, f"suno_{primary_clip_id}.mp3")
    print(f"[Gengar DJ Generator] Downloading track from {audio_url}...")
    try:
        urllib.request.urlretrieve(audio_url, local_path)
        print(f"[Gengar DJ Generator] Saved to temporary path: {local_path}")
    except Exception as e:
        print(f"[Gengar DJ Generator] Error downloading track: {e}")
        sys.exit(1)

    # 5. POST back to the Discord bot's callback API
    # We post using multipart/form-data or JSON with download_url. 
    # Let's post the JSON containing download_url, which is easiest and cleanest!
    # Wait, the bot APIServer handles "download_url" and downloads it natively,
    # or accepts "file_path" if on a shared volume.
    # Since they run in the same host/K8s/homelab environment, let's pass both so the bot can choose!
    callback_payload = {
        "guild_id": args.guild_id,
        "channel_id": args.channel_id,
        "user_id": args.user_id,
        "title": clip.get("title", args.title),
        "file_path": local_path,
        "download_url": audio_url,
        "play_in_vc": args.play_in_vc,
        "style_tags": style_tags
    }

    print(f"[Gengar DJ Generator] Sending callback to bot API: {args.callback_url}")
    try:
        cb_res = requests.post(
            args.callback_url, 
            json=callback_payload,
            timeout=30
        )
        if cb_res.status_code == 200:
            print("[Gengar DJ Generator] Callback succeeded! Bot has received the song.")
        else:
            print(f"[Gengar DJ Generator] Callback failed with status {cb_res.status_code}: {cb_res.text}")
            sys.exit(1)
    except Exception as cb_err:
        print(f"[Gengar DJ Generator] Callback request failed: {cb_err}")
        sys.exit(1)

if __name__ == "__main__":
    main()
