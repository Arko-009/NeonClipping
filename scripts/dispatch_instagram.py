import os
import json
import time
import subprocess
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_FILE = ROOT / "data" / "ig_cloud_queue.json"
GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
MAX_RUNNER_DURATION_SECONDS = 5 * 3600  # 5 hours max before spawning handoff runner (GitHub limit is 6h)

def load_queue():
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[Dispatcher] Error reading queue: {e}")
        return []

def save_and_push_queue(queue, message="auto-published due Reel [skip ci]", removed_cids=None):
    QUEUE_FILE.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    try:
        subprocess.run(["git", "add", "data/ig_cloud_queue.json"], cwd=str(ROOT), capture_output=True, text=True, timeout=15)
        subprocess.run(["git", "commit", "-m", f"chore(instagram): {message}"], cwd=str(ROOT), capture_output=True, text=True, timeout=15)
        rb = subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        if rb.returncode != 0 and removed_cids:
            # Re-apply removal cleanly onto latest origin/main if PC pushed new scheduled Reels while runner slept
            subprocess.run(["git", "rebase", "--abort"], cwd=str(ROOT), capture_output=True, timeout=10)
            subprocess.run(["git", "fetch", "origin", "main"], cwd=str(ROOT), capture_output=True, timeout=20)
            subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=str(ROOT), capture_output=True, timeout=15)
            latest_q = [x for x in load_queue() if x.get("container_id") not in removed_cids]
            QUEUE_FILE.write_text(json.dumps(latest_q, indent=2), encoding="utf-8")
            subprocess.run(["git", "add", "data/ig_cloud_queue.json"], cwd=str(ROOT), capture_output=True, timeout=15)
            subprocess.run(["git", "commit", "-m", f"chore(instagram): {message}"], cwd=str(ROOT), capture_output=True, timeout=15)
        push_res = subprocess.run(["git", "push", "origin", "main"], cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        if push_res.returncode == 0:
            print(f"[Dispatcher] Git sync successful: {message}")
        else:
            print(f"[Dispatcher] Git push failed: {push_res.stderr.strip()}")
    except Exception as e:
        print(f"[Dispatcher] Git sync warning: {e}")

def spawn_next_runner(pat_token, repo_name):
    if not pat_token or not repo_name:
        print("[Dispatcher] Notice: GH_PAT or GITHUB_REPOSITORY not set. Cannot spawn handoff runner.")
        return False
    try:
        url = f"https://api.github.com/repos/{repo_name}/actions/workflows/instagram_dispatcher.yml/dispatches"
        res = requests.post(
            url,
            json={"ref": "main"},
            headers={
                "Authorization": f"token {pat_token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Autonomous-Cloud-Dispatcher"
            },
            timeout=20
        )
        if res.status_code == 204:
            print(f"[Dispatcher] Successfully dispatched next handoff runner for {repo_name}!")
            return True
        else:
            print(f"[Dispatcher] Failed to dispatch next runner: {res.status_code} {res.text}")
            return False
    except Exception as e:
        print(f"[Dispatcher] Exception dispatching next runner: {e}")
        return False

def publish_container(container_id, ig_user_id, token, item):
    print(f"[Dispatcher] Publishing container {container_id} (scheduled: {item.get('scheduled_time_str')})...")
    try:
        res = requests.post(
            f"{GRAPH_API_BASE}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=35
        )
        if res.status_code == 200:
            media_id = res.json().get("id")
            print(f"[Dispatcher] >>> SUCCESS! Reel published live! Media ID: {media_id}")
            return True
        else:
            err = res.text
            print(f"[Dispatcher] Meta API response: {err}")
            err_lower = err.lower()
            if "already published" in err_lower or "has been published" in err_lower or "already been published" in err_lower:
                print(f"[Dispatcher] Reel was already published. Treating as success.")
                return True
            elif "expired" in err_lower:
                print(f"[Dispatcher] Container expired. Skipping.")
                return True
            return False
    except Exception as e:
        print(f"[Dispatcher] Exception publishing container {container_id}: {e}")
        return False

def _sleep_with_heartbeats(wait_seconds, label, check_remote_updates=True):
    """Sleeps up to wait_seconds in 60s chunks, pulling origin/main every 15m to detect newly scheduled earlier items."""
    print(f"[Dispatcher] Entering precision cloud sleep for {wait_seconds} seconds ({wait_seconds//60} mins)...")
    remaining_sleep = max(0, int(wait_seconds))
    slept = 0
    while remaining_sleep > 0:
        step = min(60, remaining_sleep)
        time.sleep(step)
        remaining_sleep -= step
        slept += step
        mins_left = remaining_sleep // 60
        if mins_left % 15 == 0 and remaining_sleep > 60:
            print(f"[Dispatcher Heartbeat] {mins_left}m remaining until '{label}' drops...")
            if check_remote_updates:
                prev_q = load_queue()
                pull = subprocess.run(["git", "pull", "--ff-only", "origin", "main"], cwd=str(ROOT), capture_output=True, timeout=20)
                if pull.returncode == 0:
                    new_q = load_queue()
                    if len(new_q) != len(prev_q):
                        print("[Dispatcher] Remote queue updated during sleep; re-evaluating schedule immediately.")
                        return True
    return False


def main():
    start_time = time.time()
    token = os.environ.get("IG_ACCESS_TOKEN")
    ig_user_id = os.environ.get("IG_USER_ID")
    gh_pat = os.environ.get("GH_PAT")
    repo_name = os.environ.get("GITHUB_REPOSITORY", "Arko-009/NeonClipping")

    creds_file = ROOT / "instagram_credentials.json"
    if creds_file.exists():
        try:
            creds = json.loads(creds_file.read_text(encoding="utf-8"))
            token = token or creds.get("access_token")
            ig_user_id = ig_user_id or creds.get("instagram_account_id")
        except Exception:
            pass

    if not token or not ig_user_id:
        print("[Dispatcher] Error: IG_ACCESS_TOKEN and IG_USER_ID must be provided.")
        return

    print("=======================================================")
    print("🚀 NEONCLIPPING AUTONOMOUS CLOUD DISPATCHER DAEMON")
    print(f"Started at: {time.ctime(start_time)}")
    print(f"Target Account: {ig_user_id}")
    print("=======================================================")

    while True:
        if time.time() - start_time > 30:
            subprocess.run(["git", "pull", "--ff-only", "origin", "main"], cwd=str(ROOT), capture_output=True, timeout=20)
        queue = load_queue()
        now = time.time()

        if not queue:
            print("[Dispatcher] 🎉 Queue is empty! All scheduled Reels have been published. Exiting.")
            break

        # 1. Process any due clips immediately
        due_items = [x for x in queue if x.get("scheduled_at", 0) <= now]
        if due_items:
            successful_cids = set()
            for item in due_items:
                cid = item.get("container_id")
                if cid:
                    ok = publish_container(cid, ig_user_id, token, item)
                    if ok:
                        successful_cids.add(cid)
                    else:
                        print(f"[Dispatcher] ⚠️ Failed to publish container {cid}. Keeping in queue for retry.")
            if successful_cids:
                queue = [x for x in queue if x.get("container_id") not in successful_cids]
                save_and_push_queue(queue, f"auto-published {len(successful_cids)} Reel(s) [skip ci]", removed_cids=successful_cids)

        if not queue:
            print("[Dispatcher] 🎉 All clips published! Queue empty. Exiting.")
            break

        # 2. Check next scheduled clip
        queue.sort(key=lambda x: x.get("scheduled_at", 0))
        next_item = queue[0]
        scheduled_at = next_item.get("scheduled_at", 0)
        wait_seconds = max(0, int(scheduled_at - time.time()))
        elapsed_job = time.time() - start_time

        print(f"[Dispatcher] Pending queue: {len(queue)} clips.")
        print(f"[Dispatcher] Next Reel: '{next_item.get('title')}' scheduled for {next_item.get('scheduled_time_str')} (in {wait_seconds/60:.1f} mins)")

        # 3. Check if waiting for this clip would exceed 5 hours runner runtime.
        #    Sleep up to our remaining budget BEFORE handing off so >5h waits never tight-loop spawn runners.
        if elapsed_job + wait_seconds > MAX_RUNNER_DURATION_SECONDS:
            budget_left = max(0, int(MAX_RUNNER_DURATION_SECONDS - elapsed_job - 120))
            if budget_left > 180:
                print(f"[Dispatcher] Next Reel exceeds remaining runner budget; sleeping {budget_left//60}m before handoff...")
                if _sleep_with_heartbeats(budget_left, next_item.get("title", "Reel")):
                    continue
            print(f"[Dispatcher] Job approaching 5-hour safety limit ({(time.time()-start_time)/3600:.1f}h elapsed).")
            print("[Dispatcher] Triggering handoff to fresh cloud runner...")
            spawn_next_runner(gh_pat, repo_name)
            print("[Dispatcher] Handoff complete. Terminating current runner cleanly.")
            break

        # 4. Sleep with 60-second heartbeats until the exact scheduled second
        if wait_seconds > 0:
            if _sleep_with_heartbeats(wait_seconds, next_item.get("title", "Reel")):
                continue

        print(f"[Dispatcher] Scheduled timestamp reached for '{next_item.get('title')}'! Proceeding to publish...")

if __name__ == "__main__":
    main()
