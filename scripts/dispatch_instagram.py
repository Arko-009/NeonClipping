import os
import json
import time
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = ROOT / "data" / "ig_cloud_queue.json"
GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

def main():
    if not QUEUE_FILE.exists():
        print("No IG cloud queue file found. Nothing to dispatch.")
        return

    token = os.environ.get("IG_ACCESS_TOKEN")
    ig_user_id = os.environ.get("IG_USER_ID")

    creds_file = ROOT / "instagram_credentials.json"
    if creds_file.exists():
        try:
            creds = json.loads(creds_file.read_text(encoding="utf-8"))
            token = token or creds.get("access_token")
            ig_user_id = ig_user_id or creds.get("instagram_account_id")
        except Exception:
            pass

    if not token or not ig_user_id:
        print("Notice: IG_ACCESS_TOKEN and IG_USER_ID not provided. Please set them as GitHub Secrets.")
        return

    try:
        queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error reading queue: {e}")
        return

    now = time.time()
    remaining = []
    published_count = 0

    for item in queue:
        scheduled_at = item.get("scheduled_at", 0)
        container_id = item.get("container_id")

        if scheduled_at <= now and container_id:
            print(f"[Dispatcher] Publishing scheduled container {container_id} (scheduled for {item.get('scheduled_time_str')})...")
            try:
                res = requests.post(
                    f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
                    data={"creation_id": container_id, "access_token": token},
                    timeout=30
                )
                if res.status_code == 200:
                    media_id = res.json().get("id")
                    print(f"[Dispatcher] SUCCESS! Published media ID: {media_id}")
                    published_count += 1
                else:
                    err = res.text
                    print(f"[Dispatcher] FAILED to publish container {container_id}: {err}")
                    if "expired" in err.lower() or "already published" in err.lower():
                        continue
                    remaining.append(item)
            except Exception as e:
                print(f"[Dispatcher] Exception publishing container {container_id}: {e}")
                remaining.append(item)
        else:
            remaining.append(item)

    if published_count > 0 or len(remaining) != len(queue):
        QUEUE_FILE.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        print(f"[Dispatcher] Queue updated: {published_count} published, {len(remaining)} remaining.")
    else:
        print(f"[Dispatcher] No due containers at this time ({len(queue)} pending in queue).")

if __name__ == "__main__":
    main()
