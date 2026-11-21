from pathlib import Path
import os
import shutil
from urllib.parse import unquote
from fastapi import HTTPException
from fastapi.responses import FileResponse

def attach(app, SAVED: Path, probe):
    SAVED.mkdir(parents=True, exist_ok=True)

    @app.get('/saved')
    def list_saved():
        clips = []
        total_bytes = 0
        for p in SAVED.glob('*.mp4'):
            try:
                st = p.stat()
                size_bytes = st.st_size
                total_bytes += size_bytes
                stem = p.stem
                if ' - ' in stem:
                    parts = stem.rsplit(' - ', 1)
                    title = parts[0]
                    short_id = parts[1]
                else:
                    title = stem
                    short_id = stem[:6]

                srt_path = SAVED / f"{stem}.srt"
                has_srt = srt_path.exists()

                size_mb = round(size_bytes / (1024 * 1024), 1)
                size_gb = round(size_bytes / (1024 * 1024 * 1024), 2)
                size_formatted = f"{size_gb} GB" if size_gb >= 1.0 else f"{size_mb} MB"

                clips.append({
                    'filename': p.name,
                    'stem': stem,
                    'title': title,
                    'short_id': short_id,
                    'path': str(p.resolve()),
                    'size_bytes': size_bytes,
                    'size_mb': size_mb,
                    'size_formatted': size_formatted,
                    'created': st.st_mtime,
                    'has_srt': has_srt,
                    'srt_filename': f"{stem}.srt" if has_srt else None
                })
            except OSError:
                continue

        clips.sort(key=lambda x: x['created'], reverse=True)

        total_gb = round(total_bytes / (1024 * 1024 * 1024), 2)
        total_mb = round(total_bytes / (1024 * 1024), 1)
        total_formatted = f"{total_gb} GB" if total_gb >= 1.0 else f"{total_mb} MB"

        return {
            'dir': str(SAVED.resolve()),
            'count': len(clips),
            'total_bytes': total_bytes,
            'total_size_formatted': total_formatted,
            'clips': clips
        }

    @app.api_route('/media-saved/{filename}', methods=['GET', 'HEAD'])
    def media_saved(filename: str):
        filename = unquote(filename)
        target = (SAVED / filename).resolve()
        if not target.is_relative_to(SAVED.resolve()) or not target.exists() or not target.is_file():
            raise HTTPException(404, 'Saved clip not found')
        if target.suffix.lower() == '.mp4':
            return FileResponse(target, media_type='video/mp4')
        elif target.suffix.lower() == '.srt':
            return FileResponse(target, filename=filename, media_type='text/plain')
        raise HTTPException(400, 'Unsupported file type')

    @app.delete('/saved/{filename}')
    def delete_saved(filename: str):
        filename = unquote(filename)
        target = (SAVED / filename).resolve()
        if not target.is_relative_to(SAVED.resolve()) or not target.exists() or not target.is_file():
            raise HTTPException(404, 'Saved clip not found')
        target.unlink(missing_ok=True)
        srt = target.with_suffix('.srt')
        if srt.exists():
            try:
                srt.unlink(missing_ok=True)
            except OSError:
                pass
        return {'ok': True, 'deleted': filename}

    @app.post('/saved/clear')
    @app.delete('/saved')
    def clear_saved():
        bytes_freed = 0
        deleted_count = 0
        for p in list(SAVED.glob('*.mp4')) + list(SAVED.glob('*.srt')):
            try:
                if p.is_file():
                    bytes_freed += p.stat().st_size
                    p.unlink(missing_ok=True)
                    deleted_count += 1
            except OSError:
                pass
        freed_mb = round(bytes_freed / (1024 * 1024), 1)
        freed_gb = round(bytes_freed / (1024 * 1024 * 1024), 2)
        return {
            'ok': True,
            'count': deleted_count,
            'bytes_freed': bytes_freed,
            'freed_mb': freed_mb,
            'freed_gb': freed_gb
        }

