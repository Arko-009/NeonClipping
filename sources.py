from pathlib import Path
import json
import shutil
import math
from fastapi import HTTPException

def attach(app, DATA: Path, probe):
    def get_source_videos():
        # First gather all clip JSON files to know which videos are exported clips and count clips per source
        clip_stems = set()
        clips_per_source = {}
        for f in DATA.glob('*.json'):
            if f.name.endswith(('.state.json', '.request.json', '.words.json')):
                continue
            try:
                j = json.loads(f.read_text(encoding='utf-8'))
                if not isinstance(j, dict):
                    continue
                if j.get('result', {}).get('status') == 'done':
                    clip_stems.add(f.stem)
                    src_id = j.get('source') or j.get('settings', {}).get('id') or j.get('settings', {}).get('source_id')
                    if src_id:
                        clips_per_source[src_id] = clips_per_source.get(src_id, 0) + 1
            except (ValueError, OSError):
                pass

        sources = []
        total_bytes = 0

        for p in DATA.glob('*.mp4'):
            stem = p.stem
            # Ignore clips, temporary lowres action slices, or audio files
            if stem in clip_stems:
                continue
            if stem.startswith('lr_') or '.audio.' in p.name:
                continue

            # A full source video has an accompanying .request.json or .state.json
            state_file = DATA / f"{stem}.state.json"
            req_file = DATA / f"{stem}.request.json"
            if not (state_file.exists() or req_file.exists()):
                continue

            try:
                st = p.stat()
                size_bytes = st.st_size
                created = st.st_mtime
            except OSError:
                continue

            # Read metadata if available
            title = None
            url = None
            w = 1280
            h = 720
            duration = 0.0

            if state_file.exists():
                try:
                    sdata = json.loads(state_file.read_text(encoding='utf-8'))
                    url = sdata.get('url')
                    meta = sdata.get('meta') or sdata.get('preview_meta') or {}
                    w = meta.get('w') or sdata.get('w') or w
                    h = meta.get('h') or sdata.get('h') or h
                    duration = meta.get('duration') or sdata.get('duration') or duration
                    title = meta.get('title') or sdata.get('title')
                except (ValueError, OSError):
                    pass

            # 2. Check request.json
            req_file = DATA / f"{stem}.request.json"
            if req_file.exists():
                try:
                    rdata = json.loads(req_file.read_text(encoding='utf-8'))
                    if not url:
                        url = rdata.get('url')
                except (ValueError, OSError):
                    pass

            # If duration or resolution missing or 0, probe once safely
            if not duration or not w or not h:
                try:
                    m = probe(p)
                    w = m.get('w', w)
                    h = m.get('h', h)
                    duration = m.get('duration', duration)
                except Exception:
                    pass

            if not title:
                if url:
                    title = f"Source ({url.split('/')[-1]})"
                else:
                    title = f"Local Video ({stem[:8]})"

            size_mb = round(size_bytes / (1024 * 1024), 1)
            size_gb = round(size_bytes / (1024 * 1024 * 1024), 2)
            if size_gb >= 1.0:
                size_formatted = f"{size_gb} GB"
            else:
                size_formatted = f"{size_mb} MB"

            dur_sec = int(round(duration))
            if dur_sec >= 3600:
                dur_formatted = f"{dur_sec // 3600}h {(dur_sec % 3600) // 60}m"
            elif dur_sec >= 60:
                dur_formatted = f"{dur_sec // 60}m {dur_sec % 60}s"
            else:
                dur_formatted = f"{dur_sec}s"

            total_bytes += size_bytes

            sources.append({
                'id': stem,
                'title': title,
                'url': url,
                'filename': p.name,
                'path': str(p.resolve()),
                'size_bytes': size_bytes,
                'size_mb': size_mb,
                'size_gb': size_gb,
                'size_formatted': size_formatted,
                'duration': round(duration, 2),
                'dur_formatted': dur_formatted,
                'w': w,
                'h': h,
                'created': created,
                'clips_count': clips_per_source.get(stem, 0)
            })

        sources.sort(key=lambda s: s['created'], reverse=True)

        total_gb = round(total_bytes / (1024 * 1024 * 1024), 2)
        total_mb = round(total_bytes / (1024 * 1024), 1)
        if total_gb >= 1.0:
            total_formatted = f"{total_gb} GB"
        else:
            total_formatted = f"{total_mb} MB"

        return {
            'dir': str(DATA.resolve()),
            'count': len(sources),
            'total_bytes': total_bytes,
            'total_size_formatted': total_formatted,
            'sources': sources
        }

    @app.get('/sources')
    def list_sources():
        return get_source_videos()

    @app.delete('/sources/{id}')
    def delete_source(id: str):
        if not id or not id.isalnum():
            raise HTTPException(400, 'Invalid source ID.')
        
        mp4_path = DATA / f"{id}.mp4"
        if not mp4_path.exists():
            raise HTTPException(404, 'Source video not found.')

        deleted_files = []
        # Delete video
        try:
            mp4_path.unlink(missing_ok=True)
            deleted_files.append(mp4_path.name)
        except OSError as e:
            raise HTTPException(500, f"Could not delete video file: {e}")

        # Delete metadata files
        for ext in ('.request.json', '.state.json', '.words.json'):
            f = DATA / f"{id}{ext}"
            if f.exists():
                try:
                    f.unlink(missing_ok=True)
                    deleted_files.append(f.name)
                except OSError:
                    pass

        # Delete preview directory
        preview_dir = DATA / f"{id}-preview"
        if preview_dir.exists() and preview_dir.is_dir():
            try:
                shutil.rmtree(preview_dir, ignore_errors=True)
                deleted_files.append(preview_dir.name)
            except OSError:
                pass

        # Delete cache directory if any
        cache_dir = DATA / id
        if cache_dir.exists() and cache_dir.is_dir():
            try:
                shutil.rmtree(cache_dir, ignore_errors=True)
            except OSError:
                pass

        # Delete audio or wav files with this prefix
        for audio_f in DATA.glob(f"{id}.audio.*"):
            try:
                audio_f.unlink(missing_ok=True)
            except OSError:
                pass
        for wav_f in DATA.glob(f"{id}*.wav"):
            try:
                wav_f.unlink(missing_ok=True)
            except OSError:
                pass

        return {'ok': True, 'id': id, 'deleted': deleted_files}

    @app.post('/sources/clear')
    def clear_sources():
        data_info = get_source_videos()
        bytes_freed = 0
        deleted_count = 0
        for s in data_info['sources']:
            sid = s['id']
            mp4_path = DATA / f"{sid}.mp4"
            if mp4_path.exists():
                try:
                    bytes_freed += mp4_path.stat().st_size
                    mp4_path.unlink(missing_ok=True)
                except OSError:pass
            for ext in ('.request.json', '.state.json', '.words.json'):
                f = DATA / f"{sid}{ext}"
                if f.exists():
                    try:
                        bytes_freed += f.stat().st_size
                        f.unlink(missing_ok=True)
                    except OSError:pass
            pdir = DATA / f"{sid}-preview"
            if pdir.exists() and pdir.is_dir():
                try:shutil.rmtree(pdir, ignore_errors=True)
                except OSError:pass
            cdir = DATA / sid
            if cdir.exists() and cdir.is_dir():
                try:shutil.rmtree(cdir, ignore_errors=True)
                except OSError:pass
            deleted_count += 1

        for pattern in ('lr_*.mp4', '*.audio.*', '*_temp.*'):
            for tf in DATA.glob(pattern):
                try:
                    bytes_freed += tf.stat().st_size
                    tf.unlink(missing_ok=True)
                except OSError:pass

        total_mb = round(bytes_freed / (1024 * 1024), 1)
        total_gb = round(bytes_freed / (1024 * 1024 * 1024), 2)
        return {
            'ok': True,
            'count': deleted_count,
            'bytes_freed': bytes_freed,
            'freed_mb': total_mb,
            'freed_gb': f"{total_gb} GB" if total_gb >= 1.0 else f"{total_mb} MB"
        }
