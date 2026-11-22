import json
import logging
import os
import re
import subprocess
import time
import threading
import uuid
import urllib.parse
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
        return {"status": "connected", "username": ig_username, "account_id": ig_id, "page_name": page_name}

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
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart",
        str(output_path)
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    if proc.returncode != 0 or not output_path.exists():
        logger.warning(f"FFmpeg Reels normalization failed: {proc.stderr[-300:]}, using original file")
        return input_path
    logger.info(f"Normalized Reels video created: {output_path.name} ({output_path.stat().st_size} bytes)")
    return output_path

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
    tunnel_url = None

    try:
        # Step 0: Normalize for Reels
        task["status"] = "processing"
        task["message"] = "Optimizing video & audio for Instagram Reels (48kHz, CFR 30)..."
        task["progress"] = 15

        input_path = base_saved / video_filename
        final_video_path = optimize_for_reels(input_path) if input_path.exists() else input_path
        final_filename = final_video_path.name

        # Step 1: Tunnel
        task["status"] = "connecting"
        task["message"] = "Opening temporary secure Cloudflare Tunnel..."
        task["progress"] = 35
        tunnel_url = instagram.start_tunnel()

        task["status"] = "uploading"
        task["message"] = "Creating Reel container with Meta Graph API..."
        task["progress"] = 45

        quoted_fn = urllib.parse.quote(final_filename)
        video_public_url = f"{tunnel_url}/media-saved/{quoted_fn}"

        # Step 2: Create Reel Container
        container_url = f"{GRAPH_API_BASE}/{ig_user_id}/media"
        container_payload = {
            "media_type": "REELS",
            "video_url": video_public_url,
            "caption": caption or "Created with NeonClipping Studio 🔥 #Shorts #Reels",
            "share_to_feed": "true" if share_to_feed else "false",
            "access_token": token
        }
        res = requests.post(container_url, data=container_payload, timeout=25)
        if res.status_code != 200:
            err = res.json().get("error", {}).get("message", res.text)
            task["status"] = "error"
            task["error"] = f"Meta Container error: {err}"
            return

        container_id = res.json().get("id")
        task["container_id"] = container_id
        task["message"] = "Meta is transcoding the Reel..."
        task["status"] = "transcoding"
        task["progress"] = 60

        # Step 3: Poll Container status
        status_url = f"{GRAPH_API_BASE}/{container_id}"
        max_wait = 300
        poll_start = time.time()
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
                    err_msg = s_data.get("status", f"Meta video processing failed ({s_code})")
                    task["status"] = "error"
                    task["error"] = err_msg
                    return
            time.sleep(4)

        if not transcode_finished:
            task["status"] = "error"
            task["error"] = "Meta video processing timed out (exceeded 5 minutes)"
            return

        # Video ingestion complete; close tunnel immediately
        instagram.stop_tunnel()
        tunnel_url = None

        if scheduled_at and scheduled_at > time.time() + 60:
            task["status"] = "done"
            task["progress"] = 100
            task["scheduled"] = True
            task["scheduled_at"] = scheduled_at
            task["scheduled_time_str"] = scheduled_time_str or time.ctime(scheduled_at)
            task["container_id"] = container_id
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
            except Exception as e:
                logger.error(f"Failed to record scheduled Instagram post: {e}")
            return

        task["message"] = "Publishing Reel live to Instagram..."
        task["status"] = "publishing"
        task["progress"] = 90

        # Step 4: Publish
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
        except Exception:
            pass

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
    finally:
        if tunnel_url:
            instagram.stop_tunnel()

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
    tunnel_url = None

    try:
        task["status"] = "connecting"
        task["message"] = "Opening secure Cloudflare Tunnel for Meta Cloud pre-staging..."
        task["progress"] = 10
        tunnel_url = instagram.start_tunnel()

        containers_to_poll = []

        for idx, plan in enumerate(clip_plans):
            filename = plan["filename"]
            caption = plan.get("caption") or f"{Path(filename).stem}\n\n🔥 Created with NeonClipping #reels #gaming #gta6"
            scheduled_at = plan["scheduled_at"]
            scheduled_time_str = plan.get("scheduled_time_str") or time.ctime(scheduled_at)

            input_path = saved_dir / filename
            if not input_path.exists():
                continue

            task["message"] = f"Optimizing Reel {idx+1}/{total} for Instagram..."
            final_video_path = optimize_for_reels(input_path) if input_path.exists() else input_path
            final_filename = final_video_path.name

            quoted_fn = urllib.parse.quote(final_filename)
            video_public_url = f"{tunnel_url}/media-saved/{quoted_fn}"

            task["message"] = f"Ingesting Reel {idx+1}/{total} into Meta Cloud..."
            container_url = f"{GRAPH_API_BASE}/{ig_user_id}/media"
            container_payload = {
                "media_type": "REELS",
                "video_url": video_public_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": token
            }

            res = requests.post(container_url, data=container_payload, timeout=30)
            if res.status_code != 200:
                err = res.json().get("error", {}).get("message", res.text)
                task["status"] = "error"
                task["error"] = f"Meta Container error on {filename}: {err}"
                return

            container_id = res.json().get("id")
            containers_to_poll.append({
                "container_id": container_id,
                "filename": filename,
                "caption": caption,
                "scheduled_at": scheduled_at,
                "scheduled_time_str": scheduled_time_str
            })
            task["progress"] = int(10 + ((idx + 1) / total) * 35)

        task["message"] = "Waiting for Meta Cloud to verify & transcode all Reels..."
        max_wait = 360
        poll_start = time.time()
        verified_containers = []

        for idx, c_info in enumerate(containers_to_poll):
            cid = c_info["container_id"]
            transcode_finished = False
            status_url = f"{GRAPH_API_BASE}/{cid}"

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
                        task["status"] = "error"
                        task["error"] = f"Reel {c_info['filename']} failed on Meta Cloud: {err_msg}"
                        return
                time.sleep(3)

            if not transcode_finished:
                task["status"] = "error"
                task["error"] = f"Meta video processing timed out for {c_info['filename']}"
                return

            verified_containers.append(c_info)
            task["progress"] = int(45 + ((idx + 1) / total) * 45)

        instagram.stop_tunnel()
        tunnel_url = None

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

            task["completed_clips"].append({
                "filename": c_info["filename"],
                "container_id": c_info["container_id"],
                "scheduled_at": c_info["scheduled_at"],
                "scheduled_time_str": c_info["scheduled_time_str"]
            })

            cloud_queue.append({
                "container_id": c_info["container_id"],
                "scheduled_at": c_info["scheduled_at"],
                "scheduled_time_str": c_info["scheduled_time_str"],
                "title": c_info["filename"]
            })

        data_dir = root_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        queue_file = data_dir / "ig_cloud_queue.json"
        try:
            existing_queue = []
            if queue_file.exists():
                existing_queue = json.loads(queue_file.read_text(encoding="utf-8"))
            existing_queue.extend(cloud_queue)
            queue_file.write_text(json.dumps(existing_queue, indent=2), encoding="utf-8")
        except Exception:
            pass

        task["status"] = "done"
        task["progress"] = 100
        task["message"] = f"Successfully pre-staged and scheduled all {len(task['completed_clips'])} Reels on Meta Cloud!"

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
    finally:
        if tunnel_url:
            instagram.stop_tunnel()

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
        start_dt = (now_dt + datetime.timedelta(hours=gap_hours)).replace(minute=0, second=0, microsecond=0)
        if (start_dt - now_dt).total_seconds() < 3600:
            start_dt += datetime.timedelta(hours=1)

        for i in range(max_to_schedule):
            clip = unposted[i]
            target_dt = start_dt + datetime.timedelta(hours=i * gap_hours)
            target_ts = target_dt.timestamp()
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

        return {
            'quota': quota,
            'can_schedule': remaining_slots > 0 and len(unposted) > 0,
            'remaining_quota': remaining_slots,
            'unposted_total': len(unposted),
            'count': len(planned),
            'clips': planned
        }

    @app.post('/instagram/auto-schedule-batch')
    def ig_auto_schedule_batch(req: IgBatchScheduleReq):
        if not req.clips:
            raise HTTPException(400, "No clips provided for batch schedule")

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
