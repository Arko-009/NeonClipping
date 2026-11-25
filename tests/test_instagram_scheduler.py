import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import requests

import instagram_publisher
from publisher_scheduler import PublisherScheduler


class InstagramSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.saved_dir = Path(self.tmp.name) / "Saved"
        self.saved_dir.mkdir(parents=True, exist_ok=True)
        self.sched = PublisherScheduler(data_dir=self.data_dir, saved_dir=self.saved_dir)
        self.sched._worker_running = False
        self.addCleanup(setattr, self.sched, "_worker_running", False)

    def test_transient_network_error_keeps_scheduled_for_retry(self):
        item = self.sched.add_scheduled(
            "instagram",
            "clip1.mp4",
            "Bro really tried 💀",
            time.time() - 10,
            "Nov 25, 02:00 PM",
            {"container_id": "111", "ig_user_id": "222", "access_token": "tok"},
        )
        with patch("publisher_scheduler.requests.get", side_effect=requests.exceptions.ConnectionError("getaddrinfo failed")):
            self.sched._poll_once()

        scheduled = self.sched.get_scheduled("instagram")
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0]["id"], item["id"])
        self.assertEqual(scheduled[0]["status"], "scheduled")
        self.assertIn("getaddrinfo failed", scheduled[0]["last_error"])
        self.assertGreater(scheduled[0]["retry_after"], time.time())

    def test_reconciles_errored_item_already_published_by_cloud_dispatcher(self):
        data = {
            "history": [],
            "scheduled": [
                {
                    "id": "err1",
                    "platform": "instagram",
                    "video_filename": "Alarm man - 99d847.mp4",
                    "title": "Bro really thought he could take on the WisCon cop's sass 😂\n\nBody",
                    "status": "error",
                    "created_at": 1795498260.0,
                    "scheduled_at": 1795532400.0,
                    "scheduled_time_str": "Nov 24, 08:30 PM",
                    "payload": {"container_id": "18077360891590647", "ig_user_id": "222", "access_token": "tok"},
                    "error": "HTTPSConnectionPool(host='graph.facebook.com', port=443): Failed to resolve 'graph.facebook.com'",
                }
            ],
        }
        self.sched._save_data(data)
        queue_file = self.data_dir / "ig_cloud_queue.json"
        queue_file.write_text(json.dumps([{"container_id": "18077360891590647"}]), encoding="utf-8")

        st_resp = MagicMock(status_code=200)
        st_resp.json.return_value = {"id": "18077360891590647", "status_code": "PUBLISHED", "status": "Published"}
        media_resp = MagicMock(status_code=200)
        media_resp.json.return_value = {
            "data": [
                {
                    "id": "18087",
                    "caption": "Bro really thought he could take on the WisCon cop's sass 😂\n\nBody",
                    "permalink": "https://www.instagram.com/reel/DgHM-nVkjiC/",
                    "timestamp": "2026-11-24T15:00:03+0000",
                }
            ]
        }
        with patch("publisher_scheduler.requests.get", side_effect=[st_resp, media_resp]):
            self.sched._poll_once()

        self.assertEqual(self.sched.get_scheduled("instagram"), [])
        hist = self.sched._load_data()["history"]
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["status"], "published")
        self.assertEqual(hist[0]["url"], "https://www.instagram.com/reel/DgHM-nVkjiC/")
        self.assertEqual(json.loads(queue_file.read_text(encoding="utf-8")), [])

    def test_concurrent_add_scheduled_not_clobbered_during_publish(self):
        first = self.sched.add_scheduled(
            "instagram",
            "clip1.mp4",
            "First",
            time.time() - 5,
            "Now",
            {"container_id": "c1", "ig_user_id": "u1", "access_token": "tok"},
        )

        st_resp = MagicMock(status_code=200)
        st_resp.json.return_value = {"id": "c1", "status_code": "FINISHED"}
        pub_resp = MagicMock(status_code=200)
        pub_resp.json.return_value = {"id": "m1"}
        perm_resp = MagicMock(status_code=200)
        perm_resp.json.return_value = {"permalink": "https://www.instagram.com/reel/abc/"}

        def fake_post(*args, **kwargs):
            # Simulate concurrent batch scheduling adding another item during HTTP request
            self.sched.add_scheduled(
                "youtube",
                "clip2.mp4",
                "Second",
                time.time() + 3600,
                "Later",
                {"video_id": "v2"},
            )
            return pub_resp

        with patch("publisher_scheduler.requests.get", side_effect=[st_resp, perm_resp]), \
             patch("publisher_scheduler.requests.post", side_effect=fake_post):
            self.sched._poll_once()

        remaining = self.sched.get_scheduled()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["video_filename"], "clip2.mp4")
        self.assertEqual(self.sched._load_data()["history"][0]["id"], first["id"])

    def test_cancel_scheduled_cleans_cloud_queue(self):
        item = self.sched.add_scheduled(
            "instagram",
            "clip1.mp4",
            "Cancel Me",
            time.time() + 3600,
            "Later",
            {"container_id": "c999", "ig_user_id": "u1", "access_token": "tok"},
        )
        queue_file = self.data_dir / "ig_cloud_queue.json"
        queue_file.write_text(json.dumps([{"container_id": "c999"}, {"container_id": "c1000"}]), encoding="utf-8")

        self.assertTrue(self.sched.cancel_scheduled(item["id"]))
        self.assertEqual(json.loads(queue_file.read_text(encoding="utf-8")), [{"container_id": "c1000"}])

    def test_sync_git_and_push_cloud_queue_merges_remote_and_dedupes(self):
        root = Path(self.tmp.name)
        (root / "data").mkdir(parents=True, exist_ok=True)
        remote_items = [{"container_id": "c1", "scheduled_at": 100}]
        new_items = [
            {"container_id": "c1", "scheduled_at": 100},
            {"container_id": "c2", "scheduled_at": 200},
        ]

        def fake_run(cmd, **kwargs):
            res = MagicMock(returncode=0, stdout="", stderr="")
            if cmd[:3] == ["git", "show", "origin/main:data/ig_cloud_queue.json"]:
                res.stdout = json.dumps(remote_items)
            elif cmd[:3] == ["git", "diff", "--cached"]:
                res.returncode = 1
            return res

        cred_proc = MagicMock()
        cred_proc.communicate.return_value = ("password=ghp_test\n", "")
        disp_resp = MagicMock(status_code=204)

        with patch("instagram_publisher.subprocess.run", side_effect=fake_run), \
             patch("instagram_publisher.subprocess.Popen", return_value=cred_proc), \
             patch("instagram_publisher.requests.post", return_value=disp_resp):
            out = instagram_publisher._sync_git_and_push_cloud_queue(root, new_items)

        self.assertTrue(out["git_pushed"])
        self.assertTrue(out["dispatched"])
        written = json.loads((root / "data" / "ig_cloud_queue.json").read_text(encoding="utf-8"))
        self.assertEqual([x["container_id"] for x in written], ["c1", "c2"])


if __name__ == "__main__":
    unittest.main()
