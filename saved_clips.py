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
        try:
            import publisher_scheduler
            publisher_scheduler.scheduler.prune_dual_scheduled(SAVED)
        except Exception:
            pass

        clips = []
        total_bytes = 0
        for p in SAVED.glob('*.mp4'):
            if p.name.startswith('_'):
                continue
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
        ig_cache = SAVED / f"_ig_norm_{target.stem}.mp4"
        if ig_cache.exists():
            try:
                ig_cache.unlink(missing_ok=True)
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

    @app.post('/saved/ai-retitle')
    def ai_retitle_saved():
        import re, json
        import smart
        from instagram_publisher import extract_clip_transcript
        data_dir = SAVED.parent / 'data'
        
        banned_prefixes = ('live clip', 'dialogue candidate', 'activity highlight', 'clip', 'untitled')
        renamed = []
        for p in list(SAVED.glob('*.mp4')):
            if p.name.startswith('_'):
                continue
            stem = p.stem
            if ' - ' in stem:
                parts = stem.rsplit(' - ', 1)
                title = parts[0]
                short_id = parts[1]
            else:
                title = stem
                short_id = stem[:6]
            
            # Check if title is generic
            is_generic = any(title.strip().lower().startswith(b) for b in banned_prefixes) or len(title.strip()) < 4
            if not is_generic:
                continue
                
            t = extract_clip_transcript(p.name, SAVED, data_dir)
            new_title, new_hook = smart.generate_title_and_hook(t, stream_title="")
            clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', str(new_title or '')).strip().strip('. ')
            if not clean or any(clean.lower().startswith(b) for b in banned_prefixes):
                clean = smart.fallback_title(t)
            
            new_filename = f"{clean} - {short_id}.mp4"
            if new_filename == p.name:
                continue
                
            new_path = SAVED / new_filename
            try:
                p.rename(new_path)
            except OSError:
                continue
            
            old_srt = SAVED / f"{stem}.srt"
            if old_srt.exists():
                try: old_srt.rename(SAVED / f"{clean} - {short_id}.srt")
                except OSError: pass
                
            old_ig = SAVED / f"_ig_norm_{stem}.mp4"
            if old_ig.exists():
                try: old_ig.rename(SAVED / f"_ig_norm_{clean} - {short_id}.mp4")
                except OSError: pass
                
            for jf in data_dir.glob(f"{short_id}*.json"):
                if jf.name.endswith(('.state.json', '.request.json', '.words.json')):
                    continue
                try:
                    jdata = json.loads(jf.read_text(encoding='utf-8'))
                    if isinstance(jdata, dict) and 'settings' in jdata:
                        jdata['settings']['title'] = clean
                        if new_hook: jdata['settings']['hook'] = new_hook
                        jf.write_text(json.dumps(jdata, indent=2), encoding='utf-8')
                except Exception:
                    pass
                    
            renamed.append({'old': p.name, 'new': new_filename, 'title': clean, 'hook': new_hook})
            
        return {'ok': True, 'count': len(renamed), 'renamed': renamed}


