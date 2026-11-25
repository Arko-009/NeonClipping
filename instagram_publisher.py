import json
import logging
import os
import re
import subprocess
import time
import threading
import uuid
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional
import requests
from fastapi import HTTPException
from pydantic import BaseModel

logger = logging.getLogger("instagram_publisher")

CREDENTIALS_FILE = Path(__file__).parent / "instagram_credentials.json"
CLOUDFLARED_BIN = Path(__file__).parent / "cloudflared.exe"
GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

ig_tasks: Dict[str, Dict[str, Any]] = {}
ig_batch_tasks: Dict[str, Dict[str, Any]] = {}

class InstagramPublisher:
    def __init__(self, credentials_path: Optional[Path] = None):
        self.creds_path = credentials_path or CREDENTIALS_FILE
        self._tunnel_proc: Optional[subprocess.Popen] = None
        self._tunnel_url: Optional[str] = None
        self._tunnel_lock = threading.Lock()

    def get_credentials(self) -> Optional[Dict[str, Any]]:
        if not self.creds_path.exists():
            return None
        try:
            with open(self.creds_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read Instagram credentials: {e}")
            return None

    def save_credentials(self, data: Dict[str, Any]) -> None:
        with open(self.creds_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def is_configured(self) -> bool:
        creds = self.get_credentials()
        return bool(creds and creds.get("access_token") and creds.get("instagram_account_id"))

    def get_status(self) -> Dict[str, Any]:
        creds = self.get_credentials()
        has_bin = CLOUDFLARED_BIN.exists()
        if not creds:
            return {
                "configured": False,
                "is_connected": False,
                "username": None,
                "account_id": None,
                "cloudflared_ready": has_bin
            }
        return {
            "configured": True,
            "is_connected": True,
            "username": creds.get("username", "techmedia09"),
            "account_id": creds.get("instagram_account_id"),
            "page_name": creds.get("page_name"),
            "cloudflared_ready": has_bin,
            "connected_at": creds.get("connected_at"),
            "is_permanent": creds.get("expires_at") == "NEVER",
            "expires_at": creds.get("expires_at", "Permanent")
        }

    def connect_account(self, access_token: str, app_id: str = "", app_secret: str = "") -> Dict[str, Any]:
        """
        Takes a User Access Token, discovers the connected Instagram Business/Creator account,
        optionally exchanges it for a 60-day long-lived token, and saves credentials.
        """
        token = access_token.strip()
        if not token:
            raise ValueError("Access token cannot be empty")

        # 1. Optionally exchange for a 60-day long-lived token if App ID & Secret are provided
        if app_id and app_secret:
            try:
                ex_url = f"{GRAPH_API_BASE}/oauth/access_token"
                ex_params = {
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id.strip(),
                    "client_secret": app_secret.strip(),
                    "fb_exchange_token": token
                }
                ex_res = requests.get(ex_url, params=ex_params, timeout=15)
                if ex_res.status_code == 200:
                    ex_data = ex_res.json()
                    token = ex_data.get("access_token", token)
                    logger.info("Successfully exchanged for a 60-day long-lived token")
            except Exception as e:
                logger.warning(f"Long-lived token exchange failed, using provided token: {e}")

        # 2. Try multiple discovery paths for Instagram Business/Creator Account
        ig_account = None
        linked_page = None
        page_token = token

        # Path A: Token might be a Page Access Token directly
        try:
            me_res = requests.get(f"{GRAPH_API_BASE}/me", params={
                "fields": "id,name,instagram_business_account{id,username,name}",
                "access_token": token
            }, timeout=15)
            if me_res.status_code == 200:
                me_data = me_res.json()
                if me_data.get("instagram_business_account"):
                    ig_account = me_data["instagram_business_account"]
                    linked_page = {"id": me_data.get("id"), "name": me_data.get("name", "Page")}
                    page_token = token
                    logger.info("Found Instagram account via direct Page token")
        except Exception:
            pass

        # Path B: User Token -> /me/accounts
        if not ig_account:
            accounts_url = f"{GRAPH_API_BASE}/me/accounts"
            params = {
                "fields": "id,name,access_token,instagram_business_account{id,username,name}",
                "access_token": token
            }
            res = requests.get(accounts_url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json().get("data", [])
                for page in data:
                    ig = page.get("instagram_business_account")
                    if ig and ig.get("id"):
                        ig_account = ig
                        linked_page = page
                        page_token = page.get("access_token", token)
                        logger.info("Found Instagram account via /me/accounts")
                        break

        # Path C: User Token -> /me/businesses (Meta Business Suite Portfolio)
        if not ig_account:
            try:
                biz_url = f"{GRAPH_API_BASE}/me/businesses"
                biz_params = {
                    "fields": "id,name,owned_pages{id,name,access_token,instagram_business_account{id,username,name}}",
                    "access_token": token
                }
                b_res = requests.get(biz_url, params=biz_params, timeout=15)
                if b_res.status_code == 200:
                    for b in b_res.json().get("data", []):
                        for page in b.get("owned_pages", {}).get("data", []):
                            ig = page.get("instagram_business_account")
                            if ig and ig.get("id"):
                                ig_account = ig
                                linked_page = page
                                page_token = page.get("access_token", token)
                                logger.info("Found Instagram account via /me/businesses")
                                break
                        if ig_account:
                            break
            except Exception:
                pass

        if not ig_account:
            raise RuntimeError(
                "No Instagram Creator/Business account found linked to your Facebook Pages. "
                "In Graph API Explorer, make sure you also add the 'business_management' permission, "
                "or select your Page ('Tech Media') under the 'Get Token' dropdown."
            )

        ig_id = ig_account["id"]
        ig_username = ig_account.get("username", "techmedia09")
        page_name = linked_page.get("name", "Tech Media") if linked_page else "Tech Media"

        creds = {
            "access_token": page_token or token,
            "user_token": token,
            "instagram_account_id": ig_id,
            "username": ig_username,
            "page_id": linked_page.get("id") if linked_page else None,
            "page_name": page_name,
            "connected_at": time.time()
        }
        self.save_credentials(creds)
        logger.info(f"Successfully connected Instagram: @{ig_username} (ID: {ig_id})")
        gh_synced = _sync_github_ig_secrets(creds["access_token"], ig_id)
        return {
            "status": "connected",
            "username": ig_username,
            "account_id": ig_id,
            "page_name": page_name,
            "github_secrets_synced": gh_synced,
        }

    def disconnect(self) -> None:
        if self.creds_path.exists():
            self.creds_path.unlink()
        logger.info("Instagram disconnected")

    def start_tunnel(self, local_port: int = 8770, timeout_seconds: int = 35) -> str:
        """
        Starts an ephemeral Cloudflare Quick Tunnel to make the local port publicly reachable for Meta.
        Waits until the tunnel connection is registered on Cloudflare's edge so Meta can reach it.
        Returns the public HTTPS URL (e.g. https://xxx.trycloudflare.com).
        """
        with self._tunnel_lock:
            if self._tunnel_url and self._tunnel_proc and self._tunnel_proc.poll() is None:
                return self._tunnel_url

            if not CLOUDFLARED_BIN.exists():
                raise FileNotFoundError(f"cloudflared.exe not found at {CLOUDFLARED_BIN}")

            cmd = [str(CLOUDFLARED_BIN), "tunnel", "--url", f"http://127.0.0.1:{local_port}"]
            logger.info(f"Launching ephemeral cloudflare tunnel: {cmd}")
            self._tunnel_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace"
            )

            tunnel_url = None
            start = time.time()
            registered = False
            while time.time() - start < timeout_seconds:
                line = self._tunnel_proc.stderr.readline()
                if not line and self._tunnel_proc.poll() is not None:
                    break
                if line:
                    if not tunnel_url:
                        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                        if match:
                            tunnel_url = match.group(0)
                    if "Registered tunnel connection" in line:
                        registered = True
                        break

            if not tunnel_url:
                self.stop_tunnel()
                raise RuntimeError("Failed to obtain ephemeral trycloudflare tunnel URL within timeout")

            # Drain remaining tunnel process output in daemon thread so Windows pipe buffer never blocks
            def _drain(proc):
                try:
                    for _ in iter(proc.stderr.readline, ""):
                        pass
                except Exception:
                    pass
            threading.Thread(target=_drain, args=(self._tunnel_proc,), daemon=True).start()

            # Allow 2 seconds for edge route propagation
            time.sleep(2)

            self._tunnel_url = tunnel_url
            logger.info(f"Ephemeral tunnel active and registered at: {tunnel_url}")
            return tunnel_url

    def stop_tunnel(self) -> None:
        """Terminates the ephemeral tunnel immediately."""
        with self._tunnel_lock:
            if self._tunnel_proc:
                try:
                    self._tunnel_proc.terminate()
                    self._tunnel_proc.wait(timeout=5)
                except Exception as e:
                    logger.warning(f"Error terminating tunnel: {e}")
                    try:
                        self._tunnel_proc.kill()
                    except Exception:
                        pass
                self._tunnel_proc = None
            self._tunnel_url = None
            logger.info("Ephemeral tunnel closed")

# Singleton instance
instagram = InstagramPublisher()
SAVED_DIR_GLOBAL = Path(__file__).parent / "Saved"

def optimize_for_reels(input_path: Path) -> Path:
    """
    Normalizes video/audio to strict Meta Instagram Reels specs:
    - Codec: H.264 High profile, yuv420p
    - Audio: AAC 48,000 Hz, stereo, 128 kbps
    - Framerate: 30 fps CFR, GOP 60 (2s keyframe interval)
    - Container: +faststart moov atom at beginning
    - Duration: -shortest to prevent audio/video stream length mismatch
    Caches the normalized file alongside the original as `_ig_norm_<stem>.mp4`.
    """
    norm_filename = f"_ig_norm_{input_path.stem}.mp4"
    output_path = input_path.parent / norm_filename
    if output_path.exists() and output_path.stat().st_mtime >= input_path.stat().st_mtime and output_path.stat().st_size > 1000:
        logger.info(f"Reusing existing normalized Reels video: {output_path.name}")
        return output_path

    logger.info(f"Normalizing {input_path.name} for Instagram Reels...")
    tmp_path = input_path.parent / f"_tmp_norm_{input_path.stem}.mp4"
    if tmp_path.exists():
        tmp_path.unlink(missing_ok=True)

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart",
        str(tmp_path)
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if proc.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size < 1000:
        logger.warning(f"FFmpeg Reels normalization failed: {proc.stderr[-300:]}, using original file")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return input_path

    try:
        tmp_path.replace(output_path)
    except Exception:
        return tmp_path

    logger.info(f"Normalized Reels video created: {output_path.name} ({output_path.stat().st_size} bytes)")
    return output_path

def extract_clip_transcript(filename: str, saved_dir: Path, data_dir: Path) -> str:
    """Extracts spoken transcript from .srt files in Saved/ or data/ directories."""
    p = saved_dir / filename
    stem = p.stem
    srt1 = saved_dir / f"{stem}.srt"
    if srt1.exists():
        try:
            lines = [l.strip() for l in srt1.read_text(encoding='utf-8', errors='ignore').splitlines() if l.strip() and not l.strip().isdigit() and '-->' not in l]
            return " ".join(lines)
        except Exception:
            pass
    if ' - ' in stem:
        short_id = stem.rsplit(' - ', 1)[1]
        matches = list(data_dir.glob(f"{short_id}*.srt"))
        if matches:
            try:
                lines = [l.strip() for l in matches[0].read_text(encoding='utf-8', errors='ignore').splitlines() if l.strip() and not l.strip().isdigit() and '-->' not in l]
                return " ".join(lines)
            except Exception:
                pass
    return ""

def generate_viral_caption(title: str, transcript: str = "") -> dict:
    """
    Generates a viral, Gen-Z meme style Instagram Reels caption with precision hashtags using local Ollama.
    Includes robust fallback if Ollama is busy or offline.
    """
    clean_title = title or "Epic Moment"
    snippet = transcript[:1500] if transcript else clean_title

    prompt = f"""You are an expert Instagram Reels creator who writes viral Gen-Z meme-style captions that blow up on Explore and Reels.
Given the clip title and transcript below, write a short, funny, relatable caption for Instagram Reels.

Clip Title: {clean_title}
Transcript: "{snippet}"

Follow this exact structure:
Line 1: A punchy, hilarious Gen-Z meme hook with emojis (e.g. using "Bro really thought...", "Tell me why...", "He was NOT ready for...", with emojis like 💀, 😭, 🤣).
Line 2: A short relatable line or comment-bait question (e.g. "Bro really risked it all 😭", "Who else would do this? Tag them below 👇", "Nah he actually went there 💀").
Line 3: 4 to 6 hyper-targeted hashtags matching the actual theme (mix of gaming, funny meme, and creator hashtags; NO generic #viral or #trending).

Reply ONLY with JSON:
{{
  "hook": "...",
  "comment_bait": "...",
  "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4", "#tag5"]
}}"""

    try:
        req = urllib.request.Request(
            'http://127.0.0.1:11434/api/generate',
            data=json.dumps({
                'model': 'llama3.2:3b',
                'prompt': prompt,
                'format': 'json',
                'stream': False,
                'options': {'temperature': 0.7}
            }).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=14) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            res = json.loads(data.get('response', '{}'))
            hook = res.get('hook', '').strip()
            comment_bait = res.get('comment_bait', '').strip()
            tags = res.get('hashtags', [])
            if isinstance(tags, list):
                hashtags_str = " ".join([t if t.startswith('#') else f"#{t}" for t in tags[:6]])
            else:
                hashtags_str = "#gamingmemes #funnyclips #reelsindia #gamerlife"
            
            if hook:
                full_caption = f"{hook}\n\n{comment_bait}\n\n{hashtags_str}\n\n🔥 Created with NeonClipping Studio".strip()
                return {
                    'ok': True,
                    'caption': full_caption,
                    'hook': hook,
                    'comment_bait': comment_bait,
                    'hashtags': hashtags_str
                }
    except Exception as e:
        logger.warning(f"Ollama viral caption generation fallback: {e}")

    # Fallback template (100% resilient)
    fallback_caption = f"Bro really thought this was a good idea 💀😭\n\nTag someone who needs to see this! 👇\n\n#gamingmemes #funnyreels #streamerclips #gaminglife #reelsindia\n\n🔥 Created with NeonClipping Studio"
    return {
        'ok': True,
        'caption': fallback_caption,
        'hook': "Bro really thought this was a good idea 💀😭",
        'comment_bait': "Tag someone who needs to see this! 👇",
        'hashtags': "#gamingmemes #funnyreels #streamerclips #gaminglife #reelsindia"
    }

def create_and_upload_reel_container(
    ig_user_id: str,
    token: str,
    video_path: Path,
    caption: str,
    share_to_feed: bool = True,
    max_retries: int = 2
) -> str:
    """
    Direct Resumable Upload to Meta Cloud (Zero-Tunnel, 100% Reliability):
    1. Initializes a resumable container via Graph API (upload_type=resumable).
    2. Directly streams raw binary MP4 bytes to rupload.facebook.com with OAuth header.
    3. Polls until Meta finishes server-side ingestion and returns status_code == 'FINISHED'.
    4. Returns verified container ID.
    Immune to error 2207082 (pull/crawler dropouts).
    """
    init_url = f"{GRAPH_API_BASE}/{ig_user_id}/media"
    init_payload = {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption or "Created with NeonClipping Studio 🔥 #Shorts #Reels",
        "share_to_feed": "true" if share_to_feed else "false",
        "access_token": token
    }
    res = requests.post(init_url, data=init_payload, timeout=30)
    if res.status_code != 200:
        err = res.json().get("error", {}).get("message", res.text)
        raise RuntimeError(f"Meta container initialization failed: {err}")

    c_data = res.json()
    container_id = c_data.get("id")
    upload_uri = c_data.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{GRAPH_API_VERSION}/{container_id}"

    file_size = os.path.getsize(video_path)
    headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(file_size),
        "Content-Type": "video/mp4"
    }

    with open(video_path, "rb") as f:
        video_bytes = f.read()

    upload_ok = False
    last_err = ""
    for attempt in range(max_retries):
        try:
            logger.info(f"Direct push {video_path.name} ({file_size} bytes) to {upload_uri} (attempt {attempt+1}/{max_retries})...")
            up_res = requests.post(upload_uri, headers=headers, data=video_bytes, timeout=180)
            if up_res.status_code == 200:
                upload_ok = True
                break
            else:
                last_err = f"HTTP {up_res.status_code}: {up_res.text}"
                logger.warning(f"rupload attempt {attempt+1} failed: {last_err}")
                time.sleep(2)
        except Exception as e:
            last_err = str(e)
            logger.warning(f"rupload attempt {attempt+1} error: {e}")
            time.sleep(2)

    if not upload_ok:
        raise RuntimeError(f"Direct binary push to Meta rupload failed: {last_err}")

    # Poll status until Meta transcode completes
    status_url = f"{GRAPH_API_BASE}/{container_id}"
    poll_start = time.time()
    max_wait = 180
    transcode_finished = False

    while time.time() - poll_start < max_wait:
        status_res = requests.get(
            status_url,
            params={"fields": "status_code,status", "access_token": token},
            timeout=15
        )
        if status_res.status_code == 200:
            s_data = status_res.json()
            s_code = s_data.get("status_code", "").upper()
            if s_code == "FINISHED":
                transcode_finished = True
                break
            elif s_code in ["ERROR", "EXPIRED"]:
                err_msg = s_data.get("status", f"Meta processing failed ({s_code})")
                raise RuntimeError(f"Meta processing failed: {err_msg}")
        time.sleep(3)

    if not transcode_finished:
        raise RuntimeError(f"Meta video processing timed out for container {container_id}")

    logger.info(f"Container {container_id} verified on Meta Cloud (FINISHED)")
    return container_id

def execute_ig_publish(task_id: str, video_filename: str, caption: str, share_to_feed: bool, saved_dir: Optional[Path] = None, scheduled_at: Optional[float] = None, scheduled_time_str: Optional[str] = None):
    task = ig_tasks.get(task_id)
    if not task:
        return

    base_saved = saved_dir or SAVED_DIR_GLOBAL
    creds = instagram.get_credentials()
    if not creds:
        task["status"] = "error"
        task["error"] = "Instagram not connected. Please enter your Access Token."
        return

    token = creds["access_token"]
    ig_user_id = creds["instagram_account_id"]

    try:
        # Step 0: Normalize for Reels
        task["status"] = "processing"
        task["message"] = "Optimizing video & audio for Instagram Reels (48kHz, CFR 30)..."
        task["progress"] = 15

        input_path = base_saved / video_filename
        final_video_path = optimize_for_reels(input_path) if input_path.exists() else input_path

        # Step 1: Direct Resumable Upload to Meta Cloud (Zero-Tunnel)
        task["status"] = "uploading"
        size_mb = round(final_video_path.stat().st_size / (1024 * 1024), 1)
        task["message"] = f"Pushing Reel directly to Meta Cloud ({size_mb} MB)..."
        task["progress"] = 35

        container_id = create_and_upload_reel_container(
            ig_user_id=ig_user_id,
            token=token,
            video_path=final_video_path,
            caption=caption or "Created with NeonClipping Studio 🔥 #Shorts #Reels",
            share_to_feed=share_to_feed
        )
        task["container_id"] = container_id

        if scheduled_at and scheduled_at > time.time() + 60:
            task["status"] = "done"
            task["progress"] = 100
            task["scheduled"] = True
            task["scheduled_at"] = scheduled_at
            task["scheduled_time_str"] = scheduled_time_str or time.ctime(scheduled_at)
            task["message"] = f"Pre-staged on Meta Cloud! Scheduled for {task['scheduled_time_str']}."

            try:
                import publisher_scheduler
                publisher_scheduler.scheduler.add_scheduled(
                    "instagram",
                    video_filename,
                    caption or video_filename,
                    scheduled_at,
                    task["scheduled_time_str"],
                    {
                        "container_id": container_id,
                        "ig_user_id": ig_user_id,
                        "access_token": token
                    }
                )
                publisher_scheduler.scheduler.prune_dual_scheduled(saved_dir)
            except Exception as e:
                logger.error(f"Failed to record scheduled Instagram post: {e}")
            return

        task["message"] = "Publishing Reel live to Instagram..."
        task["status"] = "publishing"
        task["progress"] = 90

        # Step 2: Publish live
        publish_url = f"{GRAPH_API_BASE}/{ig_user_id}/media_publish"
        pub_res = requests.post(
            publish_url,
            data={"creation_id": container_id, "access_token": token},
            timeout=25
        )
        if pub_res.status_code != 200:
            err = pub_res.json().get("error", {}).get("message", pub_res.text)
            task["status"] = "error"
            task["error"] = f"Meta publish error: {err}"
            return

        media_id = pub_res.json().get("id")
        permalink = f"https://www.instagram.com/reel/{media_id}/"
        try:
            p_res = requests.get(
                f"{GRAPH_API_BASE}/{media_id}",
                params={"fields": "permalink", "access_token": token},
                timeout=10
            )
            if p_res.status_code == 200:
                permalink = p_res.json().get("permalink", permalink)
        except Exception:
            pass

        task["status"] = "done"
        task["progress"] = 100
        task["media_id"] = media_id
        task["permalink"] = permalink
        task["message"] = "Reel successfully published!"

        try:
            import publisher_scheduler
            publisher_scheduler.scheduler.log_publish(
                "instagram", video_filename, caption or video_filename, permalink, status="published",
                meta={"media_id": media_id}
            )
            publisher_scheduler.scheduler.prune_dual_scheduled(saved_dir)
        except Exception:
            pass

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)

def _sync_github_ig_secrets(ig_token: str, ig_user_id: str, repo_name: str = "Arko-009/NeonClipping") -> bool:
    """Best-effort sync of IG_ACCESS_TOKEN and IG_USER_ID to GitHub Actions secrets when account connects."""
    try:
        from nacl import encoding as nacl_encoding, public as nacl_public
        import base64
        cred_p = subprocess.Popen(
            ['git', 'credential', 'fill'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        cred_out, _ = cred_p.communicate('protocol=https\nhost=github.com\n\n')
        pw_lines = [l.split('=', 1)[1].strip() for l in cred_out.splitlines() if l.startswith('password=')]
        if not pw_lines:
            return False
        headers = {
            "Authorization": f"token {pw_lines[0]}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "NeonClipping-Autopilot"
        }
        pk_res = requests.get(
            f"https://api.github.com/repos/{repo_name}/actions/secrets/public-key",
            headers=headers, timeout=15
        )
        if pk_res.status_code != 200:
            logger.warning(f"GitHub secrets public-key fetch failed: {pk_res.status_code}")
            return False
        pk_data = pk_res.json()
        pub_key = nacl_public.PublicKey(pk_data["key"].encode("utf-8"), nacl_encoding.Base64Encoder())
        box = nacl_public.SealedBox(pub_key)

        for secret_name, secret_val in (("IG_ACCESS_TOKEN", ig_token), ("IG_USER_ID", ig_user_id)):
            enc = base64.b64encode(box.encrypt(secret_val.encode("utf-8"))).decode("utf-8")
            put_res = requests.put(
                f"https://api.github.com/repos/{repo_name}/actions/secrets/{secret_name}",
                json={"encrypted_value": enc, "key_id": pk_data["key_id"]},
                headers=headers, timeout=15
            )
            if put_res.status_code not in (201, 204):
                logger.warning(f"Failed updating GitHub secret {secret_name}: {put_res.status_code}")
                return False
        logger.info("Synced IG_ACCESS_TOKEN & IG_USER_ID to GitHub Actions secrets")
        return True
    except Exception as e:
        logger.warning(f"GitHub secret sync skipped: {e}")
        return False


def _sync_git_and_push_cloud_queue(root_dir: Path, cloud_queue: list) -> Dict[str, Any]:
    """
    Safely merges newly staged containers with the latest origin/main queue
    (and any unpushed local commits), pushes data/ig_cloud_queue.json to GitHub
    without rebase conflicts, and dispatches the autonomous GitHub Actions runner.
    """
    data_dir = root_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    queue_file = data_dir / "ig_cloud_queue.json"

    result: Dict[str, Any] = {"git_pushed": False, "dispatched": False, "error": None}
    try:
        # 1. Fetch latest remote state so we never merge against stale local queue edits
        fetch_res = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=str(root_dir), capture_output=True, text=True, timeout=25
        )
        if fetch_res.returncode != 0:
            logger.warning(f"git fetch origin main failed: {fetch_res.stderr.strip()}")

        # 2. Read authoritative queue from origin/main AND preserve any unpushed local HEAD batches
        base_queue = []
        show_res = subprocess.run(
            ["git", "show", "origin/main:data/ig_cloud_queue.json"],
            cwd=str(root_dir), capture_output=True, text=True, timeout=10
        )
        if show_res.returncode == 0 and show_res.stdout.strip():
            try:
                base_queue = json.loads(show_res.stdout)
            except Exception:
                base_queue = []
        elif queue_file.exists():
            try:
                base_queue = json.loads(queue_file.read_text(encoding="utf-8"))
            except Exception:
                base_queue = []

        unpushed_res = subprocess.run(
            ["git", "rev-list", "--count", "origin/main..HEAD"],
            cwd=str(root_dir), capture_output=True, text=True, timeout=10
        )
        if unpushed_res.returncode == 0 and int((unpushed_res.stdout or "0").strip() or "0") > 0:
            head_show = subprocess.run(
                ["git", "show", "HEAD:data/ig_cloud_queue.json"],
                cwd=str(root_dir), capture_output=True, text=True, timeout=10
            )
            if head_show.returncode == 0 and head_show.stdout.strip():
                try:
                    cutoff = time.time() - 3600
                    for item in json.loads(head_show.stdout):
                        if item.get("scheduled_at", 0) >= cutoff:
                            base_queue.append(item)
                except Exception:
                    pass

        # 3. Cleanly fast-forward local branch before writing, discarding stale local queue diff
        st_res = subprocess.run(
            ["git", "status", "--porcelain", "data/ig_cloud_queue.json"],
            cwd=str(root_dir), capture_output=True, text=True, timeout=10
        )
        if st_res.returncode == 0 and st_res.stdout.strip():
            subprocess.run(
                ["git", "checkout", "origin/main", "--", "data/ig_cloud_queue.json"],
                cwd=str(root_dir), capture_output=True, text=True, timeout=10
            )
        ff_res = subprocess.run(
            ["git", "merge", "--ff-only", "origin/main"],
            cwd=str(root_dir), capture_output=True, text=True, timeout=15
        )
        if ff_res.returncode != 0:
            rb_res = subprocess.run(
                ["git", "pull", "--rebase", "origin", "main"],
                cwd=str(root_dir), capture_output=True, text=True, timeout=25
            )
            if rb_res.returncode != 0:
                logger.warning(f"git pull --rebase failed, aborting rebase: {rb_res.stderr.strip()}")
                subprocess.run(["git", "rebase", "--abort"], cwd=str(root_dir), capture_output=True, timeout=10)

        # 4. Merge deduplicated by container_id and sort by scheduled_at
        seen_cids = set()
        merged_queue = []
        for item in base_queue + cloud_queue:
            cid = item.get("container_id")
            if cid and cid in seen_cids:
                continue
            if cid:
                seen_cids.add(cid)
            merged_queue.append(item)
        merged_queue.sort(key=lambda x: x.get("scheduled_at", 0))

        queue_file.write_text(json.dumps(merged_queue, indent=2), encoding="utf-8")

        subprocess.run(
            ["git", "add", "data/ig_cloud_queue.json"],
            cwd=str(root_dir), capture_output=True, text=True, timeout=10
        )
        diff_res = subprocess.run(
            ["git", "diff", "--cached", "--quiet", "--", "data/ig_cloud_queue.json"],
            cwd=str(root_dir), capture_output=True, timeout=10
        )
        if diff_res.returncode != 0:
            commit_res = subprocess.run(
                ["git", "commit", "-m", "chore(instagram): sync scheduled cloud queue [skip ci]"],
                cwd=str(root_dir), capture_output=True, text=True, timeout=15
            )
            if commit_res.returncode != 0:
                result["error"] = f"git commit failed: {commit_res.stderr.strip()}"
                logger.error(result["error"])
                return result

        push_res = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(root_dir), capture_output=True, text=True, timeout=25
        )
        if push_res.returncode != 0:
            result["error"] = f"git push failed: {push_res.stderr.strip()}"
            logger.error(result["error"])
            return result
        result["git_pushed"] = True
        logger.info(f"Synced {len(merged_queue)} item(s) to origin/main data/ig_cloud_queue.json")

        # 5. Kick off the autonomous GitHub Cloud Dispatcher runner immediately
        try:
            cred_p = subprocess.Popen(
                ['git', 'credential', 'fill'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            cred_out, _ = cred_p.communicate('protocol=https\nhost=github.com\n\n')
            pw_lines = [l.split('=', 1)[1].strip() for l in cred_out.splitlines() if l.startswith('password=')]
            if pw_lines:
                disp_res = requests.post(
                    "https://api.github.com/repos/Arko-009/NeonClipping/actions/workflows/instagram_dispatcher.yml/dispatches",
                    json={"ref": "main"},
                    headers={
                        "Authorization": f"token {pw_lines[0]}",
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": "NeonClipping-Autopilot"
                    },
                    timeout=15
                )
                result["dispatched"] = disp_res.status_code == 204
                if not result["dispatched"]:
                    logger.warning(f"GitHub workflow dispatch returned {disp_res.status_code}: {disp_res.text}")
        except Exception as e:
            logger.warning(f"GitHub workflow dispatch failed: {e}")
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Cloud queue sync failed: {e}")
    return result


def execute_ig_batch_upload(batch_id: str, root_dir: Path, saved_dir: Path, clip_plans: list):
    task = ig_batch_tasks[batch_id]
    task["status"] = "uploading"
    total = len(clip_plans)
    task["total"] = total
    task["completed_clips"] = []

    creds = instagram.get_credentials()
    if not creds:
        task["status"] = "error"
        task["error"] = "Instagram not connected. Please enter your Access Token first."
        return

    token = creds["access_token"]
    ig_user_id = creds["instagram_account_id"]

    try:
        verified_containers = []
        failed_clips = []

        for idx, plan in enumerate(clip_plans):
            filename = plan["filename"]
            raw_title = plan.get("title") or Path(filename).stem
            if ' - ' in raw_title:
                raw_title = raw_title.rsplit(' - ', 1)[0]
            if plan.get("caption"):
                caption = plan["caption"]
            else:
                t_text = extract_clip_transcript(filename, saved_dir, root_dir / "data")
                caption = generate_viral_caption(raw_title, t_text)["caption"]
            scheduled_at = plan["scheduled_at"]
            scheduled_time_str = plan.get("scheduled_time_str") or time.ctime(scheduled_at)

            input_path = saved_dir / filename
            if not input_path.exists():
                continue

            base_pct = int((idx / total) * 90)

            # Step 1: Normalize for Reels
            task["message"] = f"Optimizing Reel {idx+1}/{total} for Instagram specs..."
            task["progress"] = max(5, base_pct)
            final_video_path = optimize_for_reels(input_path) if input_path.exists() else input_path

            # Step 2: Direct push to rupload.facebook.com (Zero-Tunnel)
            size_mb = round(final_video_path.stat().st_size / (1024 * 1024), 1)
            task["message"] = f"Pushing Reel {idx+1}/{total} directly to Meta Cloud ({size_mb} MB)..."
            task["progress"] = max(5, base_pct + int(40 / total))

            try:
                container_id = create_and_upload_reel_container(
                    ig_user_id=ig_user_id,
                    token=token,
                    video_path=final_video_path,
                    caption=caption,
                    share_to_feed=True
                )
            except Exception as e:
                logger.warning(f"Reel {filename} failed during batch upload: {e}")
                failed_clips.append({"filename": filename, "error": str(e)})
                continue

            c_info = {
                "container_id": container_id,
                "filename": filename,
                "caption": caption,
                "scheduled_at": scheduled_at,
                "scheduled_time_str": scheduled_time_str
            }
            verified_containers.append(c_info)

            task["completed_clips"].append({
                "filename": filename,
                "container_id": container_id,
                "scheduled_at": scheduled_at,
                "scheduled_time_str": scheduled_time_str
            })

            task["progress"] = int(((idx + 1) / total) * 90)
            task["message"] = f"Reel {idx+1}/{total} verified & cached on Meta Cloud!"

        if not verified_containers:
            task["status"] = "error"
            err_details = "; ".join([f"{f['filename']}: {f['error']}" for f in failed_clips])
            task["error"] = f"Failed to pre-stage Reels on Meta Cloud: {err_details}"
            return

        import publisher_scheduler
        cloud_queue = []

        for c_info in verified_containers:
            publisher_scheduler.scheduler.add_scheduled(
                "instagram",
                c_info["filename"],
                c_info["caption"],
                c_info["scheduled_at"],
                c_info["scheduled_time_str"],
                {
                    "container_id": c_info["container_id"],
                    "ig_user_id": ig_user_id,
                    "access_token": token
                }
            )

            cloud_queue.append({
                "container_id": c_info["container_id"],
                "scheduled_at": c_info["scheduled_at"],
                "scheduled_time_str": c_info["scheduled_time_str"],
                "title": c_info.get("caption", "").splitlines()[0] if c_info.get("caption") else c_info["filename"]
            })

        sync_res = _sync_git_and_push_cloud_queue(root_dir, cloud_queue)
        task["cloud_sync"] = sync_res

        task["status"] = "done"
        task["progress"] = 100
        if failed_clips:
            task["message"] = f"Pre-staged {len(task['completed_clips'])}/{total} Reels on Meta Cloud ({len(failed_clips)} failed)!"
        elif not sync_res.get("git_pushed"):
            task["message"] = (
                f"Pre-staged {len(task['completed_clips'])} Reels on Meta Cloud, "
                f"but GitHub cloud sync failed ({sync_res.get('error')})."
            )
        else:
            task["message"] = f"Successfully pre-staged and scheduled all {len(task['completed_clips'])} Reels on Meta Cloud!"

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)

def attach(app, ROOT_DIR: Path, SAVED_DIR: Path):
    global SAVED_DIR_GLOBAL
    SAVED_DIR_GLOBAL = SAVED_DIR

    class ConnectReq(BaseModel):
        access_token: str
        app_id: str = ""
        app_secret: str = ""

    class PublishReq(BaseModel):
        filename: str
        caption: str = None
        share_to_feed: bool = True
        scheduled_at: float = None
        scheduled_time_str: str = None

    @app.get('/instagram/status')
    def get_status():
        return instagram.get_status()

    @app.post('/instagram/connect')
    def connect(req: ConnectReq):
        try:
            return instagram.connect_account(req.access_token, req.app_id, req.app_secret)
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post('/instagram/disconnect')
    def disconnect():
        instagram.disconnect()
        return {"status": "disconnected"}

    @app.post('/instagram/publish')
    def publish(req: PublishReq):
        if not instagram.is_configured():
            raise HTTPException(400, "Instagram is not connected. Please enter your Access Token first.")

        target = (SAVED_DIR / req.filename).resolve()
        if not target.exists() or not target.is_file():
            raise HTTPException(404, "Saved clip file not found")

        task_id = str(uuid.uuid4())[:8]
        ig_tasks[task_id] = {
            "task_id": task_id,
            "filename": req.filename,
            "status": "starting",
            "progress": 0,
            "message": "Initializing Instagram Reel publish...",
            "created_at": time.time()
        }

        th = threading.Thread(
            target=execute_ig_publish,
            args=(task_id, req.filename, req.caption, req.share_to_feed, SAVED_DIR, req.scheduled_at, req.scheduled_time_str),
            daemon=True
        )
        th.start()
        return {"task_id": task_id, "status": "starting"}

    @app.get('/instagram/task/{task_id}')
    def task_status(task_id: str):
        if task_id not in ig_tasks:
            raise HTTPException(404, "Task not found")
        return ig_tasks[task_id]

    class GenerateCaptionReq(BaseModel):
        filename: str
        title: Optional[str] = None

    @app.post('/instagram/generate-caption')
    def ig_generate_caption(req: GenerateCaptionReq):
        t_text = extract_clip_transcript(req.filename, SAVED_DIR, ROOT_DIR / "data")
        clean_title = req.title or Path(req.filename).stem
        if ' - ' in clean_title:
            clean_title = clean_title.rsplit(' - ', 1)[0]
        return generate_viral_caption(clean_title, t_text)

    class IgBatchScheduleReq(BaseModel):
        clips: list

    @app.get('/instagram/auto-schedule-preview')
    def ig_auto_schedule_preview(gap_hours: float = 2.0, count: int = 10):
        import publisher_scheduler
        quota = publisher_scheduler.scheduler.get_instagram_quota()
        remaining_slots = quota.get('remaining', 0)

        data = publisher_scheduler.scheduler._load_data()
        posted = set()
        for x in data.get('history', []):
            if x.get('platform') == 'instagram':
                posted.add(x.get('video_filename'))
        for x in data.get('scheduled', []):
            if x.get('platform') == 'instagram':
                posted.add(x.get('video_filename'))

        unposted = []
        for p in SAVED_DIR.glob('*.mp4'):
            if p.name.startswith('_') or p.name in posted:
                continue
            st = p.stat()
            stem = p.stem
            raw_title = stem.rsplit(' - ', 1)[0] if ' - ' in stem else stem
            unposted.append({
                'filename': p.name,
                'title': raw_title,
                'size_bytes': st.st_size,
                'created': st.st_mtime
            })

        unposted.sort(key=lambda x: x['created'], reverse=True)
        max_to_schedule = min(remaining_slots, len(unposted), count)

        planned = []
        import datetime
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        now_ts = now_dt.timestamp()
        max_container_ttl_seconds = 23 * 3600  # Meta REELS containers expire at 24h; enforce 23h safety cap

        latest_ig_ts = max(
            (
                x.get('scheduled_at', 0)
                for x in data.get('scheduled', [])
                if x.get('platform') == 'instagram'
                and x.get('status') == 'scheduled'
                and x.get('scheduled_at', 0) > now_ts
            ),
            default=0,
        )
        if latest_ig_ts > now_ts:
            start_dt = datetime.datetime.fromtimestamp(latest_ig_ts, tz=datetime.timezone.utc) + datetime.timedelta(hours=gap_hours)
        else:
            start_dt = (now_dt + datetime.timedelta(hours=gap_hours)).replace(minute=0, second=0, microsecond=0)
            if (start_dt - now_dt).total_seconds() < 3600:
                start_dt += datetime.timedelta(hours=1)

        ttl_capped = False
        for i in range(max_to_schedule):
            clip = unposted[i]
            target_dt = start_dt + datetime.timedelta(hours=i * gap_hours)
            target_ts = target_dt.timestamp()
            if target_ts - now_ts > max_container_ttl_seconds:
                ttl_capped = True
                break
            local_dt = target_dt.astimezone()
            local_str = local_dt.strftime('%b %d, %I:%M %p')
            size_mb = round(clip['size_bytes'] / (1024 * 1024), 1)

            planned.append({
                'order': i + 1,
                'filename': clip['filename'],
                'title': clip['title'],
                'size_mb': size_mb,
                'scheduled_at': target_ts,
                'scheduled_time_str': local_str
            })

        ttl_blocked = ttl_capped and len(planned) == 0 and len(unposted) > 0 and remaining_slots > 0
        return {
            'quota': quota,
            'can_schedule': remaining_slots > 0 and len(planned) > 0,
            'remaining_quota': remaining_slots,
            'unposted_total': len(unposted),
            'ttl_capped': ttl_capped,
            'ttl_blocked': ttl_blocked,
            'count': len(planned),
            'clips': planned
        }

    @app.post('/instagram/auto-schedule-batch')
    def ig_auto_schedule_batch(req: IgBatchScheduleReq):
        if not req.clips:
            raise HTTPException(400, "No clips provided for batch schedule")

        now_ts = time.time()
        for clip in req.clips:
            sched_at = clip.get('scheduled_at') or 0
            if sched_at - now_ts > 23.5 * 3600:
                raise HTTPException(
                    400,
                    f"Clip '{clip.get('filename', 'Reel')}' is scheduled more than 23.5h ahead; Meta containers expire after 24h."
                )

        import publisher_scheduler
        quota = publisher_scheduler.scheduler.get_instagram_quota()
        if quota.get('remaining', 0) < len(req.clips):
            raise HTTPException(400, f"Quota exceeded: only {quota.get('remaining', 0)} uploads remaining in 24h")

        batch_id = str(uuid.uuid4())[:8]
        ig_batch_tasks[batch_id] = {
            'batch_id': batch_id,
            'status': 'starting',
            'progress': 0,
            'total': len(req.clips),
            'message': 'Initializing batch Meta Cloud pre-staging...',
            'created_at': time.time(),
            'completed_clips': []
        }

        th = threading.Thread(
            target=execute_ig_batch_upload,
            args=(batch_id, ROOT_DIR, SAVED_DIR, req.clips),
            daemon=True
        )
        th.start()
        return {'ok': True, 'batch_id': batch_id}

    @app.get('/instagram/batch-status/{batch_id}')
    def ig_batch_status(batch_id: str):
        task = ig_batch_tasks.get(batch_id)
        if not task:
            raise HTTPException(404, "Batch task not found")
        return task
