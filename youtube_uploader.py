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

os.environ['HTTP_PROXY'] = 'http://127.0.0.1:18888'
os.environ['HTTPS_PROXY'] = 'http://127.0.0.1:18888'
os.environ['NO_PROXY'] = '127.0.0.1,localhost'

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly'
]

upload_tasks = {}
active_flows = {}

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

def execute_upload(task_id: str, root_dir: Path, video_path: Path, title: str, description: str, tags: list, privacy: str):
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
        body = {
            'snippet': {
                'title': title[:100],
                'description': description[:5000],
                'tags': tags,
                'categoryId': '20'  # Gaming
            },
            'status': {
                'privacyStatus': privacy,
                'selfDeclaredMadeForKids': False
            }
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
        task['message'] = "Uploaded successfully!"
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
            args=(task_id, ROOT_DIR, target, title, description, tags, privacy),
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
