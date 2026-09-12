# sketch.py
# Faith vs Views — sketch pipeline (NEW module; the /api/* Answers in Faith
# engine is untouched). Flow per episode spec:
#   OpenAI stills -> Pika motion (fallback: Ken Burns on the still at assembly)
#   -> per-character ElevenLabs dialogue -> FFmpeg assembly -> R2 upload.
#
# Pika = official API (dev.pika.art): submit job with X-API-Key, poll
# /v1/media/jobs/{id}, download from /content. Image inputs need a public URL,
# so scene stills are pushed to R2 first and that URL is handed to Pika.
#
# Endpoints (mounted at /sketch):
#   POST /sketch/episodes                  -> create episode from spec JSON
#   GET  /sketch/episodes                  -> list episodes
#   POST /sketch/episodes/{id}/generate    -> start pipeline (background)
#     ?relock_voices=true                  -> forget locked voices, pick fresh
#   GET  /sketch/episodes/{id}             -> status/progress/render report
#   GET  /sketch/episodes/{id}/download    -> get video URL (R2 or local file)
#   DELETE /sketch/episodes/{id}           -> remove episode
#   GET  /sketch/pika-check                -> Pika key/base-URL diagnostic
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
import os
import subprocess
import time
import uuid
from datetime import datetime
import requests

from models import SketchEpisode, get_engine, SessionLocal
from config import settings
from pipeline import (
    get_r2_client, _r2_public_base, _get_audio_duration, _silent_audio,
    _clip_from_video, _clip_from_image, _generate_placeholder_visual,
)

router = APIRouter()


def get_db():
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    try:
        yield db
    finally:
        db.close()


def _redact(msg) -> str:
    import re
    msg = re.sub(r'(X-API-Key|Bearer)\s+[A-Za-z0-9._~-]+', r'\1 [redacted]', str(msg))
    msg = re.sub(r'(api[_-]?key|token|secret)=([^\s&]+)', r'\1=[redacted]', msg, flags=re.I)
    return msg[:400]


def _pika_base() -> str:
    """Env values pasted from mobile often carry a trailing newline — strip it."""
    return (settings.pika_api_url or "https://api.dev.pika.art").strip().rstrip("/")


# ============ ENDPOINTS ============

@router.post("/episodes")
def create_episode(data: Dict[str, Any], db: Session = Depends(get_db)):
    """Create (or replace) an episode from its spec JSON — e.g. fvv-001.
    The spec is stored verbatim; nothing is generated until /generate is called."""
    ep_id = (data.get("episode_id") or "").strip() or f"ep-{uuid.uuid4().hex[:8]}"
    ep = db.query(SketchEpisode).filter(SketchEpisode.episode_id == ep_id).first()
    if ep:
        ep.spec = data
        ep.title = data.get("title") or ep.title
        ep.claim = data.get("claim") or ep.claim
        ep.status = data.get("status") or "SCRIPT_READY"
        ep.progress = "spec updated"
        ep.error = None
        ep.video_url = None
        ep.voice_map = {}
        ep.render_info = {}
        msg = "Episode spec replaced. Run generate."
    else:
        ep = SketchEpisode(
            id=str(uuid.uuid4()),
            episode_id=ep_id,
            title=data.get("title") or ep_id,
            claim=data.get("claim"),
            status=data.get("status") or "SCRIPT_READY",
            progress="spec stored",
            spec=data,
            voice_map={},
            render_info={},
        )
        db.add(ep)
        msg = "Episode created. Run generate."
    db.commit()
    return {"id": ep.episode_id, "status": ep.status,
            "scene_count": len((ep.spec or {}).get("scenes", [])),
            "message": msg}


@router.get("/episodes")
def list_episodes(db: Session = Depends(get_db)):
    eps = db.query(SketchEpisode).order_by(SketchEpisode.created_at.desc()).all()
    return [{"id": e.episode_id, "title": e.title, "status": e.status,
             "progress": e.progress, "video_url": e.video_url,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in eps]


@router.post("/episodes/{ep_id}/generate")
def generate_episode(ep_id: str, background_tasks: BackgroundTasks,
                     relock_voices: bool = False, db: Session = Depends(get_db)):
    ep = db.query(SketchEpisode).filter(SketchEpisode.episode_id == ep_id).first()
    if not ep:
        raise HTTPException(404, "Episode not found")
    if ep.status == "generating":
        raise HTTPException(400, "Already generating. Poll GET /sketch/episodes/{id} for progress.")
    if not (ep.spec or {}).get("scenes"):
        raise HTTPException(400, "Episode spec has no scenes.")
    if relock_voices:
        ep.voice_map = {}
    ep.status = "generating"
    ep.progress = "queued"
    ep.error = None
    ep.render_info = {}
    db.commit()
    background_tasks.add_task(_generate_episode, ep.id)
    return {"id": ep.episode_id, "status": "generating", "relock_voices": relock_voices,
            "message": "Sketch pipeline started in background. Poll GET /sketch/episodes/{id}."}


@router.get("/episodes/{ep_id}")
def get_episode(ep_id: str, db: Session = Depends(get_db)):
    ep = db.query(SketchEpisode).filter(SketchEpisode.episode_id == ep_id).first()
    if not ep:
        raise HTTPException(404, "Episode not found")
    return {"id": ep.episode_id, "title": ep.title, "claim": ep.claim,
            "status": ep.status, "progress": ep.progress, "error": ep.error,
            "video_url": ep.video_url, "voice_map": ep.voice_map or {},
            "render_info": ep.render_info or {},
            "scene_count": len((ep.spec or {}).get("scenes", [])),
            "updated_at": ep.updated_at.isoformat() if ep.updated_at else None}


@router.get("/episodes/{ep_id}/download")
def download_episode(ep_id: str, db: Session = Depends(get_db)):
    ep = db.query(SketchEpisode).filter(SketchEpisode.episode_id == ep_id).first()
    if not ep:
        raise HTTPException(404, "Episode not found")
    if ep.video_url:
        return {"id": ep.episode_id, "video_url": ep.video_url}
    local = f"{settings.output_dir}/sketch/{ep.episode_id}/{ep.episode_id}.mp4"
    if os.path.exists(local):
        return FileResponse(local, media_type="video/mp4", filename=f"{ep.episode_id}.mp4")
    raise HTTPException(404, "Video not ready yet. Check status first.")


@router.delete("/episodes/{ep_id}")
def delete_episode(ep_id: str, db: Session = Depends(get_db)):
    ep = db.query(SketchEpisode).filter(SketchEpisode.episode_id == ep_id).first()
    if not ep:
        raise HTTPException(404, "Episode not found")
    db.delete(ep)
    db.commit()
    return {"id": ep_id, "message": "Deleted"}


@router.get("/pika-check")
def pika_check():
    """Diagnostics: is the Pika key valid and is the base URL correct?
    Hits Pika's billing endpoint (free) and reports exactly what came back."""
    key = (settings.pika_api_key or "").strip()
    if not key:
        return {"ok": False, "reason": "PIKA_API_KEY not set on server"}
    base = _pika_base()
    headers = {"X-API-Key": key}
    out = {"base_url": base, "key_prefix": key[:7] + "...", "checks": {}}
    for path in ("/v1/billing/balance", "/billing/balance"):
        try:
            r = requests.get(f"{base}{path}", headers=headers, timeout=20)
            out["checks"][path] = {"status": r.status_code, "body": _redact(r.text[:200])}
        except Exception as e:
            out["checks"][path] = {"error": _redact(e)}
    ok = any(c.get("status") == 200 for c in out["checks"].values())
    out["ok"] = ok
    return out


# ============ PIPELINE ============

def _progress(db: Session, ep: SketchEpisode, msg: str):
    ep.progress = msg
    ep.updated_at = datetime.utcnow()
    db.commit()
    print(f"[Sketch:{ep.episode_id}] {msg}")


def _voice_for(speaker: str, chars: Dict[str, Any], voice_map: Dict[str, str]) -> str:
    """Resolve a speaker to an ElevenLabs voice. 'LOCK_ON_FIRST_GENERATION' in the
    spec means: pick the configured voice now and store it in voice_map so every
    later regeneration uses the same voice. Per-speaker override env var:
    SKETCH_VOICE_MAYA / SKETCH_VOICE_JORDAN / SKETCH_VOICE_NARRATOR etc."""
    if speaker in voice_map:
        return voice_map[speaker]
    cid = ((chars.get(speaker) or {}).get("voice_id") or "").strip()
    if cid and cid != "LOCK_ON_FIRST_GENERATION":
        voice_map[speaker] = cid
        return cid
    vid = (os.getenv(f"SKETCH_VOICE_{speaker.upper()}") or "").strip() or settings.elevenlabs_voice_id
    voice_map[speaker] = vid
    return vid


def _eleven_tts(text: str, voice_id: str, output_path: str) -> bool:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"}
    payload = {"text": text, "model_id": "eleven_turbo_v2_5",
               "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=45)
        if resp.status_code != 200:
            print(f"[Sketch:TTS] ElevenLabs ERROR {resp.status_code}: {_redact(resp.text[:200])}")
            return False
        with open(output_path, "wb") as f:
            f.write(resp.content)
        return os.path.getsize(output_path) > 0
    except Exception as e:
        print(f"[Sketch:TTS] ElevenLabs exception: {_redact(e)}")
        return False


def _openai_tts(text: str, output_path: str) -> bool:
    try:
        resp = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={"model": "tts-1", "voice": "alloy", "input": text},
            timeout=45,
        )
        if resp.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(resp.content)
            return os.path.getsize(output_path) > 0
        print(f"[Sketch:TTS] OpenAI ERROR {resp.status_code}: {_redact(resp.text[:200])}")
    except Exception as e:
        print(f"[Sketch:TTS] OpenAI exception: {_redact(e)}")
    return False


def _concat_audio(line_paths, output_path: str, pause: float = 0.35) -> bool:
    """Join dialogue lines with a short pause between speakers."""
    if not line_paths:
        return False
    if len(line_paths) == 1:
        subprocess.run(["cp", line_paths[0], output_path], capture_output=True, timeout=15)
        return os.path.exists(output_path)
    silence = output_path + ".pause.mp3"
    _silent_audio(silence, pause)
    list_file = output_path + ".concat.txt"
    with open(list_file, "w") as f:
        for i, p in enumerate(line_paths):
            f.write(f"file '{os.path.abspath(p)}'\n")
            if i < len(line_paths) - 1 and os.path.exists(silence):
                f.write(f"file '{os.path.abspath(silence)}'\n")
    cmd = [settings.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
           "-i", list_file, "-c:a", "libmp3lame", "-b:a", "128k", output_path]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    ok = result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0
    if not ok:
        print(f"[Sketch] Audio concat failed: {result.stderr.decode()[-200:]}")
    return ok


def _openai_still(prompt: str, output_path: str) -> bool:
    """DALL-E / gpt-image scene still (16:9 landscape)."""
    key = settings.openai_api_key
    if not key:
        return False
    try:
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
                "prompt": f"cinematic 16:9 film still: {prompt}"[:3900],
                "size": "1536x1024",
                "n": 1,
            },
            timeout=180,
        )
        if r.status_code != 200:
            print(f"[Sketch:Still] OpenAI ERROR {r.status_code}: {_redact(r.text[:200])}")
            return False
        data = r.json()["data"][0]
        import base64
        if data.get("b64_json"):
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(data["b64_json"]))
            return True
        if data.get("url"):
            img = requests.get(data["url"], timeout=90)
            if img.status_code == 200 and len(img.content) > 10000:
                with open(output_path, "wb") as f:
                    f.write(img.content)
                return True
        return False
    except Exception as e:
        print(f"[Sketch:Still] OpenAI exception: {_redact(e)}")
        return False


def _upload_file_to_r2(key: str, file_path: str, content_type: str) -> str:
    """Upload any file to R2 and return its public URL (used to hand stills to Pika)."""
    bucket = os.getenv("R2_BUCKET_NAME")
    if not bucket:
        raise ValueError("Missing R2_BUCKET_NAME")
    client = get_r2_client()
    client.upload_file(file_path, bucket, key, ExtraArgs={"ContentType": content_type})
    return f"{_r2_public_base()}/{key}"


def _pika_video(prompt: str, out_path: str, duration: float,
                image_url: Optional[str] = None) -> Dict[str, Any]:
    """Official Pika API (dev.pika.art). Submit job -> poll -> download.
    Returns {"ok": bool, "reason": str} so the render report shows exactly why
    a scene fell back to Ken Burns instead of failing silently."""
    key = (settings.pika_api_key or "").strip()
    if not key:
        return {"ok": False, "reason": "PIKA_API_KEY not set"}
    base = _pika_base()
    model_path = os.getenv("PIKA_GENERATE_PATH") or (
        "/v1/media/pika/pika-2.5/image-to-video" if image_url
        else "/v1/media/pika/pika-2.5/text-to-video")
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    try:
        payload = {
            "prompt": prompt[:900],
            "resolution": os.getenv("PIKA_RESOLUTION", "720p"),
            "duration_s": 5,  # Pika 2.5 clips are fixed at 5s; we loop to fit audio
        }
        if image_url:
            payload["image"] = image_url
        r = requests.post(f"{base}{model_path}", headers=headers, json=payload, timeout=60)
        if r.status_code not in (200, 201, 202):
            return {"ok": False, "reason": f"submit HTTP {r.status_code} at {base}{model_path}: {_redact(r.text[:150])}"}
        job = r.json()
        job_id = job.get("id")
        if not job_id:
            return {"ok": False, "reason": f"no job id in response: {str(job)[:200]}"}

        deadline = time.time() + 600
        status = {}
        while time.time() < deadline:
            time.sleep(5)
            s = requests.get(f"{base}/v1/media/jobs/{job_id}", headers=headers, timeout=30)
            if s.status_code != 200:
                return {"ok": False, "reason": f"poll HTTP {s.status_code}: {_redact(s.text[:200])}"}
            status = s.json()
            st = str(status.get("status", "")).lower()
            if st == "completed":
                break
            if st in ("failed", "error", "canceled"):
                return {"ok": False, "reason": f"job {st}: {_redact(status.get('error') or status)}"}
        else:
            return {"ok": False, "reason": "timed out after 10 min"}

        url = None
        out = status.get("output")
        if isinstance(out, str):
            url = out
        elif isinstance(out, dict):
            url = out.get("url")
        if not url:
            c = requests.get(f"{base}/v1/media/jobs/{job_id}/content", headers=headers, timeout=30)
            if c.status_code == 200:
                url = c.json().get("url")
        if not url:
            return {"ok": False, "reason": "completed but no output URL"}

        dl = requests.get(str(url), timeout=240)
        if dl.status_code == 200 and len(dl.content) > 10000:
            with open(out_path, "wb") as f:
                f.write(dl.content)
            return {"ok": True, "reason": "ok"}
        return {"ok": False, "reason": f"download HTTP {dl.status_code}"}
    except Exception as e:
        return {"ok": False, "reason": f"exception: {_redact(e)}"}


def _generate_episode(ep_pk: str):
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    render_report = {"scenes": {}, "pika_ok": 0, "ken_burns": 0, "placeholder": 0}
    try:
        ep = db.query(SketchEpisode).filter(SketchEpisode.id == ep_pk).first()
        if not ep:
            return
        spec = ep.spec or {}
        chars = spec.get("characters", {})
        scenes = spec.get("scenes", [])
        voice_map = dict(ep.voice_map or {})

        root = f"{settings.output_dir}/sketch/{ep.episode_id}"
        audio_dir = f"{root}/audio"
        still_dir = f"{root}/stills"
        clip_dir = f"{root}/clips"
        for d in (audio_dir, still_dir, clip_dir):
            os.makedirs(d, exist_ok=True)

        # --- Step 1: dialogue TTS per scene (per-character voices, locked) ---
        scene_audio = {}
        for scene in scenes:
            n = scene.get("scene")
            _progress(db, ep, f"scene {n}: dialogue TTS")
            line_paths = []
            for li, line in enumerate(scene.get("dialogue", [])):
                speaker = (line.get("speaker") or "narrator").strip()
                text = (line.get("line") or "").strip()
                if not text:
                    continue
                voice_id = _voice_for(speaker, chars, voice_map)
                path = f"{audio_dir}/s{n}_l{li}.mp3"
                ok = False
                if settings.elevenlabs_api_key:
                    ok = _eleven_tts(text, voice_id, path)
                if not ok and settings.openai_api_key:
                    ok = _openai_tts(text, path)
                if not ok:
                    est = max(2.0, len(text) * 0.06)
                    _silent_audio(path, est)
                    print(f"[Sketch:TTS] silent fallback for scene {n} line {li}")
                line_paths.append(path)
            joined = f"{audio_dir}/s{n}.mp3"
            if not _concat_audio(line_paths, joined):
                raise RuntimeError(f"scene {n}: dialogue audio concat failed")
            scene_audio[n] = joined
        ep.voice_map = voice_map  # LOCK_ON_FIRST_GENERATION: persist chosen voices
        db.commit()

        # --- Step 2: visuals per scene (OpenAI still -> R2 URL -> Pika motion) ---
        clips = []
        for scene in scenes:
            n = scene.get("scene")
            audio = scene_audio[n]
            dur = _get_audio_duration(audio)
            if dur <= 0:
                dur = float(scene.get("duration_sec") or 6)
            dur = max(3.0, min(dur, 60.0))

            _progress(db, ep, f"scene {n}: still")
            still = f"{still_dir}/s{n}.png"
            _openai_still(scene.get("still_prompt") or ep.title or "Faith vs Views", still)

            motion = f"{still_dir}/s{n}.mp4"
            has_motion = False
            if (settings.pika_api_key or "").strip():
                _progress(db, ep, f"scene {n}: pika motion")
                still_url = None
                if os.path.exists(still):
                    try:
                        still_url = _upload_file_to_r2(
                            f"sketch/stills/{ep.episode_id}-s{n}.png", still, "image/png")
                    except Exception as e:
                        print(f"[Sketch:Pika] still upload failed, text-to-video instead: {_redact(e)}")
                raw_motion = (scene.get("motion_prompt") or scene.get("still_prompt")
                              or "subtle cinematic motion")
                # Tighten for 5s fixed-length clips that loop under dialogue:
                # one continuous action, no cuts, loops cleanly.
                tight_motion = (raw_motion + ", slow continuous motion only, single action, "
                                "no scene changes, seamless loop")[:880]
                res = _pika_video(tight_motion, motion, dur, image_url=still_url)
                has_motion = res["ok"]
                render_report["scenes"][str(n)] = (
                    "pika" if res["ok"] else f"ken_burns_fallback ({res['reason'][:120]}")
                if res["ok"]:
                    render_report["pika_ok"] += 1
                else:
                    render_report["ken_burns"] += 1
                    print(f"[Sketch:Pika] scene {n} fell back: {res['reason'][:200]}")
                ep.render_info = dict(render_report)
                db.commit()

            clip = f"{clip_dir}/s{n}.mp4"
            _progress(db, ep, f"scene {n}: render clip")
            if has_motion:
                result = _clip_from_video(motion, audio, dur, clip, "landscape")
            elif os.path.exists(still):
                result = _clip_from_image(still, audio, dur, clip, "landscape")
            else:
                _generate_placeholder_visual(scene.get("still_prompt") or "Faith vs Views",
                                             f"{still_dir}/s{n}_ph.png", "landscape")
                result = _clip_from_image(f"{still_dir}/s{n}_ph.png", audio, dur, clip, "landscape")
                render_report["scenes"][str(n)] = "placeholder"
                render_report["placeholder"] += 1
            if result.returncode != 0 or not os.path.exists(clip):
                raise RuntimeError(f"scene {n}: clip render failed: {result.stderr.decode()[-250:]}")
            clips.append(clip)

        # --- Step 3: concat scenes ---
        _progress(db, ep, "assembling final video")
        concat_file = f"{root}/concat.txt"
        with open(concat_file, "w") as f:
            for c in clips:
                f.write(f"file '{os.path.abspath(c)}'\n")
        final = f"{root}/{ep.episode_id}.mp4"
        cmd = [settings.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
               "-i", concat_file, "-c", "copy", final]
        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode != 0 or not os.path.exists(final):
            cmd = [settings.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
                   "-i", concat_file, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "128k", "-ar", "44100", final]
            result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0 or not os.path.exists(final):
            raise RuntimeError(f"final concat failed: {result.stderr.decode()[:300]}")

        # --- Step 4: R2 upload ---
        _progress(db, ep, "uploading to R2")
        try:
            ep.video_url = _upload_file_to_r2(f"sketch/{ep.episode_id}.mp4", final, "video/mp4")
            print(f"[Sketch:{ep.episode_id}] R2: {ep.video_url}")
        except Exception as e:
            print(f"[Sketch:{ep.episode_id}] R2 upload failed (local kept): {_redact(e)}")

        ep.render_info = dict(render_report)
        ep.status = "done"
        _progress(db, ep, f"done (pika: {render_report['pika_ok']}, "
                         f"ken_burns: {render_report['ken_burns']}, "
                         f"placeholder: {render_report['placeholder']})")
    except Exception as e:
        print(f"[Sketch] generation error: {_redact(e)}")
        try:
            ep = db.query(SketchEpisode).filter(SketchEpisode.id == ep_pk).first()
            if ep:
                ep.status = "failed"
                ep.error = _redact(e)
                ep.progress = "failed"
                ep.render_info = dict(render_report)
                db.commit()
        except Exception as e2:
            print(f"[Sketch] failed to record error: {e2}")
    finally:
        db.close()


# ============ STARTUP RECOVERY ============
def _recover_stuck_sketches():
    """Redeploys kill in-flight background tasks — reset stuck sketches on boot."""
    try:
        engine = get_engine(settings.database_url)
        db = SessionLocal(bind=engine)
        stuck = db.query(SketchEpisode).filter(SketchEpisode.status == "generating").all()
        for ep in stuck:
            ep.status = "failed"
            ep.error = "Reset on server restart — background task was interrupted. Re-run generate."
            ep.progress = "failed"
        if stuck:
            db.commit()
            print(f"[Sketch:Recovery] Reset {len(stuck)} stuck episode(s)")
        db.close()
    except Exception as e:
        print(f"[Sketch:Recovery] error: {e}")


_recover_stuck_sketches()
