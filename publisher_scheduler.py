import json
import time
import os
import threading
import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import zoneinfo
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

DATA_DIR = Path(os.environ.get('NEONCLIPPING_DATA_DIR', 'data')).resolve()
HISTORY_FILE = DATA_DIR / 'publish_history.json'

try:
    PST_TZ = zoneinfo.ZoneInfo("America/Los_Angeles")
except Exception:
    PST_TZ = datetime.timezone(datetime.timedelta(hours=-8))

class PublisherScheduler:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DATA_DIR
        self.history_file = self.data_dir / 'publish_history.json'
        self.lock = threading.Lock()
        self._ensure_storage()
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._background_poll, daemon=True)
        self._worker_thread.start()

    def _ensure_storage(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump({"history": [], "scheduled": []}, f, indent=2)

    def _load_data(self) -> Dict[str, Any]:
        with self.lock:
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {"history": [], "scheduled": []}

    def _save_data(self, data: Dict[str, Any]):
        with self.lock:
            temp_file = self.history_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            temp_file.replace(self.history_file)

    # ---- QUOTA CALCULATIONS ----
    def get_youtube_quota(self) -> Dict[str, Any]:
        now_pst = datetime.datetime.now(PST_TZ)
        pst_midnight_start = now_pst.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        pst_next_midnight = (now_pst.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1)).timestamp()
        seconds_to_reset = max(0, int(pst_next_midnight - now_pst.timestamp()))
        hours_to_reset = seconds_to_reset // 3600
        mins_to_reset = (seconds_to_reset % 3600) // 60

        data = self._load_data()
        count = 0
        for item in data.get("history", []):
            if item.get("platform") == "youtube" and item.get("status") in ("published", "scheduled"):
                if item.get("created_at", 0) >= pst_midnight_start:
                    count += 1

        limit = 6
        return {
            "used": count,
            "limit": limit,
            "remaining": max(0, limit - count),
            "percentage": min(100, int((count / limit) * 100)),
            "reset_seconds": seconds_to_reset,
            "reset_in_text": f"{hours_to_reset}h {mins_to_reset}m",
            "reset_time_ist": "1:30 PM IST",
            "timezone": "PST (Pacific Standard Time)"
        }

    def get_instagram_quota(self) -> Dict[str, Any]:
        now = time.time()
        window_24h = now - 86400
        data = self._load_data()
        timestamps = []
        for item in data.get("history", []):
            if item.get("platform") == "instagram" and item.get("status") == "published":
                t = item.get("published_at") or item.get("created_at") or 0
                if t >= window_24h:
                    timestamps.append(t)

        count = len(timestamps)
        limit = 50
        refill_seconds = 0
        refill_str = "Full"
        sub_text = "Rolling 24-Hour Window"

        if timestamps:
            oldest_ts = min(timestamps)
            refill_seconds = max(0, int((oldest_ts + 86400) - now))
            h = refill_seconds // 3600
            m = (refill_seconds % 3600) // 60
            refill_at_str = time.strftime('%I:%M %p', time.localtime(oldest_ts + 86400))
            if h > 0:
                refill_str = f"{h}h {m}m"
                sub_text = f"Next refill in {h}h {m}m ({refill_at_str})"
            elif m > 0:
                refill_str = f"{m}m"
                sub_text = f"Next refill in {m}m ({refill_at_str})"
            else:
                refill_str = "< 1m"
                sub_text = f"Next refill in < 1m ({refill_at_str})"
        else:
            sub_text = "Rolling 24-Hour Window (100% Available)"

        return {
            "used": count,
            "limit": limit,
            "remaining": max(0, limit - count),
            "percentage": min(100, int((count / limit) * 100)),
            "window": "24-Hour Rolling Window",
            "refill_seconds": refill_seconds,
            "refill_str": refill_str,
            "refill_sub": sub_text
        }

    def get_all_quotas(self) -> Dict[str, Any]:
        return {
            "youtube": self.get_youtube_quota(),
            "instagram": self.get_instagram_quota()
        }

    # ---- RECORDING EVENTS ----
    def log_publish(self, platform: str, video_filename: str, title: str, url: str, status: str = "published", meta: Optional[dict] = None):
        data = self._load_data()
        item = {
            "id": os.urandom(8).hex(),
            "platform": platform,
            "video_filename": video_filename,
            "title": title,
            "url": url,
            "status": status,
            "created_at": time.time(),
            "published_at": time.time() if status == "published" else None,
            "meta": meta or {}
        }
        data.setdefault("history", []).append(item)
        self._save_data(data)
        return item

    def add_scheduled(self, platform: str, video_filename: str, title: str, scheduled_at: float, scheduled_time_str: str, payload: dict) -> dict:
        data = self._load_data()
        item = {
            "id": os.urandom(8).hex(),
            "platform": platform,
            "video_filename": video_filename,
            "title": title,
            "status": "scheduled",
            "created_at": time.time(),
            "scheduled_at": scheduled_at,
            "scheduled_time_str": scheduled_time_str,
            "payload": payload
        }
        data.setdefault("scheduled", []).append(item)
        self._save_data(data)
        return item

    def get_scheduled(self, platform: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._load_data()
        scheduled = data.get("scheduled", [])
        now = time.time()
        modified = False

        # Check for YouTube scheduled items whose time has passed
        for item in list(scheduled):
            if item.get("platform") == "youtube" and item.get("status") == "scheduled":
                if item.get("scheduled_at", 0) <= now:
                    item["status"] = "published"
                    item["published_at"] = item.get("scheduled_at")
                    scheduled.remove(item)
                    data.setdefault("history", []).append(item)
                    modified = True

        if modified:
            self._save_data(data)

        items = list(scheduled)
        if platform:
            items = [x for x in items if x.get("platform") == platform]
        items.sort(key=lambda x: x.get("scheduled_at", 0))
        return items

    def cancel_scheduled(self, schedule_id: str) -> bool:
        data = self._load_data()
        scheduled = data.get("scheduled", [])
        initial_len = len(scheduled)
        data["scheduled"] = [x for x in scheduled if x.get("id") != schedule_id]
        if len(data["scheduled"]) < initial_len:
            self._save_data(data)
            return True
        return False

    def _sync_remove_from_cloud_queue(self, container_id: str):
        try:
            cloud_queue_file = self.data_dir / "ig_cloud_queue.json"
            if cloud_queue_file.exists():
                items = json.loads(cloud_queue_file.read_text(encoding="utf-8"))
                new_items = [x for x in items if x.get("container_id") != container_id]
                if len(new_items) != len(items):
                    cloud_queue_file.write_text(json.dumps(new_items, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---- BACKGROUND DISPATCHER FOR PRE-STAGED INSTAGRAM POSTS ----
    def _background_poll(self):
        while self._worker_running:
            try:
                now = time.time()
                data = self._load_data()
                scheduled = data.get("scheduled", [])
                modified = False

                for item in list(scheduled):
                    if item.get("platform") == "instagram" and item.get("status") == "scheduled":
                        if item.get("scheduled_at", 0) <= now:
                            payload = item.get("payload", {})
                            container_id = payload.get("container_id")
                            ig_user_id = payload.get("ig_user_id")
                            token = payload.get("access_token")
                            
                            if container_id and ig_user_id and token:
                                print(f"[Scheduler] Firing scheduled Instagram publish for container {container_id}...")
                                try:
                                    pub_res = requests.post(
                                        f"https://graph.facebook.com/v21.0/{ig_user_id}/media_publish",
                                        data={"creation_id": container_id, "access_token": token},
                                        timeout=25
                                    )
                                    if pub_res.status_code == 200:
                                        media_id = pub_res.json().get("id")
                                        permalink = f"https://www.instagram.com/reel/{media_id}/"
                                        item["status"] = "published"
                                        item["published_at"] = time.time()
                                        item["permalink"] = permalink
                                        data.setdefault("history", []).append({
                                            "id": item["id"],
                                            "platform": "instagram",
                                            "video_filename": item["video_filename"],
                                            "title": item["title"],
                                            "url": permalink,
                                            "status": "published",
                                            "created_at": item["created_at"],
                                            "published_at": time.time(),
                                            "meta": {"scheduled": True}
                                        })
                                        scheduled.remove(item)
                                        modified = True
                                        print(f"[Scheduler] Scheduled Instagram publish SUCCESS: {permalink}")
                                        self._sync_remove_from_cloud_queue(container_id)
                                    else:
                                        err = pub_res.text
                                        err_lower = err.lower()
                                        if "already published" in err_lower or "has been published" in err_lower or "already been published" in err_lower:
                                            permalink = f"https://www.instagram.com/"
                                            item["status"] = "published"
                                            item["published_at"] = item.get("scheduled_at", time.time())
                                            item["permalink"] = permalink
                                            data.setdefault("history", []).append({
                                                "id": item["id"],
                                                "platform": "instagram",
                                                "video_filename": item["video_filename"],
                                                "title": item["title"],
                                                "url": permalink,
                                                "status": "published",
                                                "created_at": item["created_at"],
                                                "published_at": item.get("scheduled_at", time.time()),
                                                "meta": {"scheduled": True, "source": "cloud_dispatcher"}
                                            })
                                            scheduled.remove(item)
                                            modified = True
                                            print(f"[Scheduler] Container was already published by Cloud Dispatcher: {container_id}")
                                            self._sync_remove_from_cloud_queue(container_id)
                                        elif "expired" in err_lower:
                                            item["status"] = "expired"
                                            scheduled.remove(item)
                                            modified = True
                                            print(f"[Scheduler] Container expired on Meta Cloud: {container_id}")
                                            self._sync_remove_from_cloud_queue(container_id)
                                        else:
                                            item["status"] = "error"
                                            item["error"] = err
                                            modified = True
                                            print(f"[Scheduler] Scheduled Instagram publish failed: {err}")
                                except Exception as e:
                                    item["status"] = "error"
                                    item["error"] = str(e)
                                    modified = True
                                    print(f"[Scheduler] Exception firing Instagram publish: {e}")

                if modified:
                    self._save_data(data)

            except Exception as e:
                pass

            time.sleep(15)

scheduler = PublisherScheduler()

def attach(app: FastAPI, ROOT_DIR: Path, SAVED_DIR: Path):
    class CancelReq(BaseModel):
        id: str

    @app.get('/publishing/quotas')
    def get_quotas():
        return scheduler.get_all_quotas()

    @app.get('/publishing/scheduled')
    def get_scheduled(platform: Optional[str] = None):
        return {
            "scheduled": scheduler.get_scheduled(platform)
        }

    @app.post('/publishing/cancel-scheduled')
    def cancel_scheduled_post(req: CancelReq):
        ok = scheduler.cancel_scheduled(req.id)
        if not ok:
            raise HTTPException(404, "Scheduled post not found or already published")
        return {"ok": True, "id": req.id}
