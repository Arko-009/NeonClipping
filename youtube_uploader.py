import os
import json
import time
import threading
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from fastapi import HTTPException
from pydantic import BaseModel

try:
    import socket
    with socket.create_connection(('127.0.0.1', 18888), timeout=0.3):
        os.environ['HTTP_PROXY'] = 'http://127.0.0.1:18888'
        os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:18888'
        os.environ['NO_PROXY'] = '127.0.0.1,localhost'
except Exception:
    for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        os.environ.pop(_k, None)

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly'
]

upload_tasks = {}
active_flows = {}
batch_tasks = {}

def get_credentials(root_dir: Path):
    secrets_file = root_dir / 'client_secrets.json'
    token_file = root_dir / 'token.json'

    if not secrets_file.exists():
        return None, "client_secrets.json not found in project root"

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                with open(token_file, 'w', encoding='utf-8') as f:
                    f.write(creds.to_json())
            except Exception:
                creds = None
    return creds, None

def get_channel_info(creds):
    try:
        service = build('youtube', 'v3', credentials=creds)
        res = service.channels().list(part='snippet,statistics', mine=True).execute()
        items = res.get('items', [])
        if not items:
            return {
                'channel_id': 'default',
                'title': 'YouTube Channel (Active)',
                'description': '',
                'custom_url': '',
                'thumbnail': '',
                'subscribers': '0',
                'video_count': '0'
            }
        item = items[0]
        snippet = item.get('snippet', {})
        stats = item.get('statistics', {})
        return {
            'channel_id': item.get('id'),
            'title': snippet.get('title') or 'YouTube Channel',
            'description': snippet.get('description'),
            'custom_url': snippet.get('customUrl'),
            'thumbnail': snippet.get('thumbnails', {}).get('default', {}).get('url'),
            'subscribers': stats.get('subscriberCount', '0'),
            'video_count': stats.get('videoCount', '0')
        }
    except Exception as e:
        return {'channel_id': 'default', 'title': 'Connected Channel', 'error': str(e)}

def execute_upload(task_id: str, root_dir: Path, video_path: Path, title: str, description: str, tags: list, privacy: str, publish_at: str = None):
    task = upload_tasks[task_id]
    task['status'] = 'uploading'
    task['progress'] = 0

    try:
        creds, err = get_credentials(root_dir)
        if not creds or not creds.valid:
            task['status'] = 'error'
            task['error'] = 'Not authenticated. Please connect your YouTube account first.'
            return

        service = build('youtube', 'v3', credentials=creds)

        status_body = {
            'selfDeclaredMadeForKids': False
        }
        if publish_at:
            status_body['privacyStatus'] = 'private'
            status_body['publishAt'] = publish_at
        else:
            status_body['privacyStatus'] = privacy

        body = {
            'snippet': {
                'title': title[:100],
                'description': description[:5000],
                'tags': tags,
                'categoryId': '20'  # Gaming
            },
            'status': status_body
        }

        media = MediaFileUpload(
            str(video_path),
            chunksize=1024 * 1024 * 2,
            resumable=True,
            mimetype='video/mp4'
        )

        req = service.videos().insert(
            part='snippet,status',
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            upload_status, response = req.next_chunk()
            if upload_status:
                task['progress'] = int(upload_status.progress() * 100)
                task['message'] = f"Uploading: {task['progress']}%"

        video_id = response.get('id')
        task['status'] = 'done'
        task['progress'] = 100
        task['video_id'] = video_id
        task['shorts_url'] = f"https://youtube.com/shorts/{video_id}"
        task['watch_url'] = f"https://youtu.be/{video_id}"

        if publish_at:
            task['scheduled'] = True
            task['publish_at'] = publish_at
            task['message'] = f"Cloud Scheduled! Will go public automatically at {publish_at}."
            try:
                import publisher_scheduler
                import datetime
                dt = datetime.datetime.fromisoformat(publish_at.replace('Z', '+00:00'))
                local_dt = dt.astimezone()
                local_str = local_dt.strftime('%b %d, %I:%M %p')
                publisher_scheduler.scheduler.log_publish(
                    "youtube", video_path.name, title, task['shorts_url'], status="scheduled",
                    meta={"publish_at": publish_at, "video_id": video_id, "publish_at_local": local_str}
                )
                publisher_scheduler.scheduler.add_scheduled(
                    "youtube", video_path.name, title, dt.timestamp(), local_str,
                    {"video_id": video_id, "shorts_url": task['shorts_url'], "publish_at": publish_at}
                )
            except Exception:
                pass
        else:
            task['message'] = "Uploaded successfully!"
            try:
                import publisher_scheduler
                publisher_scheduler.scheduler.log_publish(
                    "youtube", video_path.name, title, task['shorts_url'], status="published",
                    meta={"video_id": video_id}
                )
            except Exception:
                pass
    except HttpError as e:
        task['status'] = 'error'
        try:
            err_details = json.loads(e.content.decode('utf-8'))
            task['error'] = err_details.get('error', {}).get('message', str(e))
        except Exception:
            task['error'] = str(e)
    except Exception as e:
        task['status'] = 'error'
        task['error'] = str(e)

def execute_batch_upload(batch_id: str, root_dir: Path, saved_dir: Path, clip_plans: list):
    task = batch_tasks[batch_id]
    task['status'] = 'uploading'
    total = len(clip_plans)
    task['total'] = total
    task['completed_clips'] = []
    task['failed_clips'] = []

    try:
        creds, err = get_credentials(root_dir)
        if not creds or not creds.valid:
            task['status'] = 'error'
            task['error'] = 'Not authenticated. Please connect your YouTube account first.'
            return

        service = build('youtube', 'v3', credentials=creds)

        for idx, plan in enumerate(clip_plans):
            task['current_index'] = idx + 1
            filename = plan['filename']
            task['current_filename'] = filename
            publish_at = plan['publish_at']
            video_path = saved_dir / filename
            if not video_path.exists():
                task['failed_clips'].append({'filename': filename, 'error': 'File not found on disk'})
                continue

            raw_title = plan.get('title') or video_path.stem
            if ' - ' in raw_title:
                raw_title = raw_title.rsplit(' - ', 1)[0]
            title = f"{raw_title} #shorts"[:100]

            description = f"{raw_title}\n\nAuto-scheduled with NeonClipping #shorts #gaming #gta6"
            tags = ["shorts", "gaming", "neonclipping", "clips", "gta6"]

            status_body = {
                'privacyStatus': 'private',
                'publishAt': publish_at,
                'selfDeclaredMadeForKids': False
            }
            body = {
                'snippet': {
                    'title': title,
                    'description': description,
                    'tags': tags,
                    'categoryId': '20'
                },
                'status': status_body
            }

            try:
                media = MediaFileUpload(
                    str(video_path),
                    chunksize=1024 * 1024 * 2,
                    resumable=True,
                    mimetype='video/mp4'
                )

                req = service.videos().insert(
                    part='snippet,status',
                    body=body,
                    media_body=media
                )

                response = None
                retries = 0
                while response is None:
                    try:
                        upload_status, response = req.next_chunk()
                        retries = 0
                        if upload_status:
                            pct = int(upload_status.progress() * 100)
                            overall = int(((idx + upload_status.progress()) / total) * 100)
                            task['progress'] = overall
                            task['message'] = f"Uploading Short {idx+1}/{total}: {pct}% ({title[:30]}...)"
                    except (HttpError, Exception) as chunk_err:
                        if isinstance(chunk_err, HttpError) and chunk_err.resp.status in (401, 403):
                            raise
                        retries += 1
                        if retries > 3:
                            raise chunk_err
                        time.sleep(retries * 2)

                video_id = response.get('id')
                shorts_url = f"https://youtube.com/shorts/{video_id}"

                import publisher_scheduler
                import datetime
                dt = datetime.datetime.fromisoformat(publish_at.replace('Z', '+00:00'))
                local_dt = dt.astimezone()
                local_str = local_dt.strftime('%b %d, %I:%M %p')
                publisher_scheduler.scheduler.log_publish(
                    "youtube", filename, title, shorts_url, status="scheduled",
                    meta={"publish_at": publish_at, "video_id": video_id, "publish_at_local": local_str}
                )
                publisher_scheduler.scheduler.add_scheduled(
                    "youtube", filename, title, dt.timestamp(), local_str,
                    {"video_id": video_id, "shorts_url": shorts_url, "publish_at": publish_at}
                )

                task['completed_clips'].append({
                    'filename': filename,
                    'title': title,
                    'shorts_url': shorts_url,
                    'publish_at': publish_at
                })

            except HttpError as clip_http_err:
                err_msg = str(clip_http_err)
                try:
                    err_details = json.loads(clip_http_err.content.decode('utf-8'))
                    err_msg = err_details.get('error', {}).get('message', str(clip_http_err))
                except Exception:
                    pass
                task['failed_clips'].append({'filename': filename, 'error': err_msg})
                # If daily quota exceeded, stop remaining clips
                if 'quotaExceeded' in err_msg or clip_http_err.resp.status == 403:
                    task['message'] = f"YouTube daily quota reached. Scheduled {len(task['completed_clips'])} Shorts."
                    break
            except Exception as clip_err:
                task['failed_clips'].append({'filename': filename, 'error': str(clip_err)})

        if len(task['completed_clips']) > 0:
            task['status'] = 'done'
            task['progress'] = 100
            if task['failed_clips']:
                task['message'] = f"Scheduled {len(task['completed_clips'])}/{total} Shorts on YouTube Cloud! ({len(task['failed_clips'])} failed)"
            else:
                task['message'] = f"Successfully scheduled all {len(task['completed_clips'])} Shorts on YouTube Cloud!"
        else:
            task['status'] = 'error'
            task['error'] = task['failed_clips'][0]['error'] if task['failed_clips'] else 'Batch upload failed.'

    except HttpError as e:
        task['status'] = 'error'
        try:
            err_details = json.loads(e.content.decode('utf-8'))
            task['error'] = err_details.get('error', {}).get('message', str(e))
        except Exception:
            task['error'] = str(e)
    except Exception as e:
        task['status'] = 'error'
        task['error'] = str(e)

def attach(app, ROOT_DIR: Path, SAVED_DIR: Path):
    class UploadReq(BaseModel):
        filename: str
        title: str = None
        description: str = None
        tags: list = None
        privacy: str = "public"
        publish_at: str = None

    @app.get('/youtube/status')
    def yt_status():
        secrets_file = ROOT_DIR / 'client_secrets.json'
        token_file = ROOT_DIR / 'token.json'
        has_secrets = secrets_file.exists()
        has_token = token_file.exists()

        channel = None
        is_connected = False
        if has_secrets and has_token:
            creds, _ = get_credentials(ROOT_DIR)
            if creds and creds.valid:
                is_connected = True
                channel = get_channel_info(creds)

        return {
            'has_secrets': has_secrets,
            'is_connected': is_connected,
            'channel': channel
        }

    @app.get('/youtube/auth-url')
    def yt_auth_url():
        secrets_file = ROOT_DIR / 'client_secrets.json'
        if not secrets_file.exists():
            raise HTTPException(404, "client_secrets.json not found")
        flow = Flow.from_client_secrets_file(
            str(secrets_file),
            scopes=SCOPES,
            redirect_uri='http://localhost:8770/'
        )
        auth_url, state = flow.authorization_url(prompt='consent', access_type='offline')
        active_flows[state] = flow
        return {'auth_url': auth_url, 'state': state}

    @app.post('/youtube/exchange-token')
    def yt_exchange(req: dict):
        code = req.get('code')
        state = req.get('state')
        if not code:
            raise HTTPException(400, "Missing authorization code")
        secrets_file = ROOT_DIR / 'client_secrets.json'
        token_file = ROOT_DIR / 'token.json'

        flow = None
        if state and state in active_flows:
            flow = active_flows[state]
        elif active_flows:
            flow = list(active_flows.values())[-1]
        else:
            flow = Flow.from_client_secrets_file(
                str(secrets_file),
                scopes=SCOPES,
                redirect_uri='http://localhost:8770/'
            )

        try:
            flow.fetch_token(code=code)
            creds = flow.credentials
            with open(token_file, 'w', encoding='utf-8') as f:
                f.write(creds.to_json())
            channel = get_channel_info(creds)
            return {'ok': True, 'channel': channel}
        except Exception as e:
            raise HTTPException(500, f"Token exchange failed: {str(e)}")

    @app.post('/youtube/upload')
    def yt_upload(req: UploadReq):
        target = (SAVED_DIR / req.filename).resolve()
        if not target.exists() or not target.is_file():
            raise HTTPException(404, "Saved clip file not found")

        creds, err = get_credentials(ROOT_DIR)
        if not creds or not creds.valid:
            raise HTTPException(401, "YouTube account not connected. Please connect first.")

        stem = target.stem
        raw_title = req.title or stem
        if ' - ' in raw_title:
            raw_title = raw_title.rsplit(' - ', 1)[0]

        title = f"{raw_title} #shorts"
        if len(title) > 100:
            title = title[:92] + " #shorts"

        description = req.description or f"{raw_title}\n\nClipped and edited with NeonClipping #shorts #gaming #gta6"
        tags = req.tags or ["shorts", "gaming", "neonclipping", "clips", "gta6"]
        privacy = req.privacy if req.privacy in ('public', 'unlisted', 'private') else 'public'

        import uuid
        task_id = str(uuid.uuid4())[:8]
        upload_tasks[task_id] = {
            'task_id': task_id,
            'filename': req.filename,
            'title': title,
            'status': 'starting',
            'progress': 0,
            'created_at': time.time(),
            'message': 'Preparing upload...'
        }

        th = threading.Thread(
            target=execute_upload,
            args=(task_id, ROOT_DIR, target, title, description, tags, privacy, req.publish_at),
            daemon=True
        )
        th.start()

        return {
            'ok': True,
            'task_id': task_id
        }

    @app.get('/youtube/upload-status/{task_id}')
    def yt_upload_status(task_id: str):
        task = upload_tasks.get(task_id)
        if not task:
            raise HTTPException(404, "Upload task not found")
        return task

    class BatchScheduleReq(BaseModel):
        clips: list

    @app.get('/youtube/auto-schedule-preview')
    def auto_schedule_preview(gap_hours: float = 2.0, count: int = 5):
        import publisher_scheduler
        quota = publisher_scheduler.scheduler.get_youtube_quota()
        remaining_slots = quota.get('remaining', 0)

        data = publisher_scheduler.scheduler._load_data()
        posted = set()
        for x in data.get('history', []):
            if x.get('platform') == 'youtube':
                posted.add(x.get('video_filename'))
        for x in data.get('scheduled', []):
            if x.get('platform') == 'youtube':
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
            target_iso = target_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            local_dt = target_dt.astimezone()
            local_str = local_dt.strftime('%b %d, %I:%M %p')
            size_mb = round(clip['size_bytes'] / (1024 * 1024), 1)

            planned.append({
                'order': i + 1,
                'filename': clip['filename'],
                'title': clip['title'],
                'size_mb': size_mb,
                'publish_at': target_iso,
                'publish_at_local': local_str
            })

        return {
            'quota': quota,
            'can_schedule': remaining_slots > 0 and len(unposted) > 0,
            'remaining_quota': remaining_slots,
            'unposted_total': len(unposted),
            'count': len(planned),
            'clips': planned
        }

    @app.post('/youtube/auto-schedule-batch')
    def auto_schedule_batch(req: BatchScheduleReq):
        if not req.clips:
            raise HTTPException(400, "No clips provided for batch schedule")

        import publisher_scheduler
        quota = publisher_scheduler.scheduler.get_youtube_quota()
        if quota.get('remaining', 0) < len(req.clips):
            raise HTTPException(400, f"Quota exceeded: only {quota.get('remaining', 0)} uploads remaining today")

        import uuid
        batch_id = str(uuid.uuid4())[:8]
        batch_tasks[batch_id] = {
            'batch_id': batch_id,
            'status': 'starting',
            'progress': 0,
            'current_index': 0,
            'total': len(req.clips),
            'message': 'Initializing batch YouTube cloud upload...',
            'created_at': time.time(),
            'completed_clips': []
        }

        th = threading.Thread(
            target=execute_batch_upload,
            args=(batch_id, ROOT_DIR, SAVED_DIR, req.clips),
            daemon=True
        )
        th.start()
        return {'ok': True, 'batch_id': batch_id}

    @app.get('/youtube/batch-status/{batch_id}')
    def batch_status(batch_id: str):
        task = batch_tasks.get(batch_id)
        if not task:
            raise HTTPException(404, "Batch task not found")
        return task
