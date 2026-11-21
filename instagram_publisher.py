import json
import logging
import os
import re
import subprocess
import time
import threading
import uuid
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
            "username": creds.get("username", "Arko007"),
            "account_id": creds.get("instagram_account_id"),
            "page_name": creds.get("page_name"),
            "cloudflared_ready": has_bin,
            "connected_at": creds.get("connected_at")
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

        # 2. Query Facebook Pages to discover linked Instagram Business/Creator Account
        accounts_url = f"{GRAPH_API_BASE}/me/accounts"
        params = {
            "fields": "id,name,access_token,instagram_business_account{id,username,name}",
            "access_token": token
        }
        res = requests.get(accounts_url, params=params, timeout=15)
        if res.status_code != 200:
            err = res.json().get("error", {}).get("message", res.text)
            raise RuntimeError(f"Meta Graph API error: {err}")

        data = res.json().get("data", [])
        if not data:
            raise RuntimeError(
                "No Facebook Pages found for this account. "
                "Please link your Instagram Creator account to a Facebook Page in Instagram Settings."
            )

        ig_account = None
        linked_page = None
        for page in data:
            ig = page.get("instagram_business_account")
            if ig and ig.get("id"):
                ig_account = ig
                linked_page = page
                break

        if not ig_account:
            raise RuntimeError(
                "No Instagram Creator/Business account linked to your Facebook Pages. "
                "Ensure your Instagram account is switched to Professional/Creator and linked to a Facebook Page."
            )

        ig_id = ig_account["id"]
        ig_username = ig_account.get("username", "Arko007")
        page_name = linked_page.get("name", "Connected Page")

        creds = {
            "access_token": token,
            "instagram_account_id": ig_id,
            "username": ig_username,
            "page_id": linked_page.get("id"),
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

    def start_tunnel(self, local_port: int = 8770, timeout_seconds: int = 25) -> str:
        """
        Starts an ephemeral Cloudflare Quick Tunnel to make the local port publicly reachable for Meta.
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
            while time.time() - start < timeout_seconds:
                line = self._tunnel_proc.stderr.readline()
                if not line and self._tunnel_proc.poll() is not None:
                    break
                if line:
                    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                    if match:
                        tunnel_url = match.group(0)
                        break

            if not tunnel_url:
                self.stop_tunnel()
                raise RuntimeError("Failed to obtain ephemeral trycloudflare tunnel URL within timeout")

            self._tunnel_url = tunnel_url
            logger.info(f"Ephemeral tunnel active at: {tunnel_url}")
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

def execute_ig_publish(task_id: str, video_filename: str, caption: str, share_to_feed: bool):
    task = ig_tasks.get(task_id)
    if not task:
        return

    task["status"] = "connecting"
    task["message"] = "Opening temporary secure Cloudflare Tunnel..."
    task["progress"] = 15

    creds = instagram.get_credentials()
    if not creds:
        task["status"] = "error"
        task["error"] = "Instagram not connected. Please enter your Access Token."
        return

    token = creds["access_token"]
    ig_user_id = creds["instagram_account_id"]
    tunnel_url = None

    try:
        # Step 1: Tunnel
        tunnel_url = instagram.start_tunnel()
        task["message"] = "Creating Reel container with Meta Graph API..."
        task["progress"] = 30
        task["status"] = "uploading"

        video_public_url = f"{tunnel_url}/media-saved/{video_filename}"

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
        task["progress"] = 50

        # Step 3: Poll Container status
        status_url = f"{GRAPH_API_BASE}/{container_id}"
        max_wait = 120
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
                    task["status"] = "error"
                    task["error"] = f"Meta video processing failed ({s_code})"
                    return
            time.sleep(4)

        if not transcode_finished:
            task["status"] = "error"
            task["error"] = "Meta video processing timed out"
            return

        # Video ingestion complete; close tunnel immediately
        instagram.stop_tunnel()
        tunnel_url = None

        task["message"] = "Publishing Reel live to Instagram..."
        task["status"] = "publishing"
        task["progress"] = 85

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

    except Exception as e:
        task["status"] = "error"
        task["error"] = str(e)
    finally:
        if tunnel_url:
            instagram.stop_tunnel()

def attach(app, ROOT_DIR: Path, SAVED_DIR: Path):
    class ConnectReq(BaseModel):
        access_token: str
        app_id: str = ""
        app_secret: str = ""

    class PublishReq(BaseModel):
        filename: str
        caption: str = None
        share_to_feed: bool = True

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
            args=(task_id, req.filename, req.caption, req.share_to_feed),
            daemon=True
        )
        th.start()
        return {"task_id": task_id, "status": "starting"}

    @app.get('/instagram/task/{task_id}')
    def task_status(task_id: str):
        if task_id not in ig_tasks:
            raise HTTPException(404, "Task not found")
        return ig_tasks[task_id]
