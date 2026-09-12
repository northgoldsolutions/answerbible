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
#   GET  /sketch/voices                    -> list ElevenLabs account voices
#   GET  /sketch/episodes/{id}/voice-check -> resolve locked voices to names
#   GET  /sketch/tts-test?voice_id=..&text=..  -> one-line voice sample or exact TTS error
#
# v2: dialogue scenes render as per-line talking close-ups (Kling AI Avatar v2
# via the Pika model catalog, same key) so mouths sync to the ElevenLabs audio.
# Narrator-only scenes stay cinematic Pika loops. Spec knobs:
#   "orientation": "portrait"   -> 9:16 reels (720x1280, portrait stills)
#   scene "lip_sync": false     -> force the classic loop for that scene
#   scene "performance_prompt"  -> steer the avatar's gestures/expression
# render_info logs every avatar job's usage/charge + Pika balance before/after.
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
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

_STILL_LAST_ERROR = ""  # set by _openai_still on failure; surfaced in render report


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


@router.get("/pika-job/{job_id}")
def pika_job_status(job_id: str):
    """Fetch a Pika job's full status JSON (usage, charge, error code) —
    built for reading failed-job envelopes from a phone."""
    key = (settings.pika_api_key or "").strip()
    if not key:
        return {"ok": False, "reason": "PIKA_API_KEY not set on server"}
    try:
        r = requests.get(f"{_pika_base()}/v1/media/jobs/{job_id}",
                         headers={"X-API-Key": key}, timeout=30)
        return {"http_status": r.status_code, "job": r.json() if r.headers.get("content-type", "").startswith("application/json") else _redact(r.text[:500])}
    except Exception as e:
        return {"ok": False, "reason": _redact(e)}


@router.get("/voices")
def list_voices():
    """List every ElevenLabs voice on this account (name + id + labels) so
    voice IDs can be verified/picked from a phone."""
    if not settings.elevenlabs_api_key:
        return {"ok": False, "reason": "ELEVENLABS_API_KEY not set"}
    try:
        r = requests.get("https://api.elevenlabs.io/v1/voices",
                         headers={"xi-api-key": settings.elevenlabs_api_key}, timeout=30)
        if r.status_code != 200:
            return {"ok": False, "status": r.status_code, "body": _redact(r.text[:200])}
        voices = [{"name": v.get("name"), "voice_id": v.get("voice_id"),
                   "category": v.get("category"), "labels": v.get("labels")}
                  for v in r.json().get("voices", [])]
        return {"ok": True, "count": len(voices), "voices": voices}
    except Exception as e:
        return {"ok": False, "reason": _redact(e)}


@router.get("/episodes/{ep_id}/voice-check")
def voice_check(ep_id: str, db: Session = Depends(get_db)):
    """Resolve each locked voice in an episode's voice_map to its ElevenLabs
    voice name/labels — catches wrong-ID mixups (e.g. Jordan sounding female)."""
    ep = db.query(SketchEpisode).filter(SketchEpisode.episode_id == ep_id).first()
    if not ep:
        raise HTTPException(404, "Episode not found")
    out = {}
    for speaker, vid in (ep.voice_map or {}).items():
        entry = {"voice_id": vid}
        if settings.elevenlabs_api_key:
            try:
                r = requests.get(f"https://api.elevenlabs.io/v1/voices/{vid}",
                                 headers={"xi-api-key": settings.elevenlabs_api_key}, timeout=20)
                if r.status_code == 200:
                    v = r.json()
                    entry.update({"name": v.get("name"), "category": v.get("category"),
                                  "labels": v.get("labels")})
                else:
                    entry["error"] = f"HTTP {r.status_code}"
            except Exception as e:
                entry["error"] = _redact(e)
        out[speaker] = entry
    return {"episode": ep_id, "voices": out}


@router.get("/tts-test")
def tts_test(voice_id: str = "", text: str = "Jordan here. Testing, one two."):
    """Phone-friendly voice tester: generate one line with a given ElevenLabs
    voice ID and return the audio directly — or the exact error if this account
    can't use that voice (not saved to My Voices, plan restriction, bad ID)."""
    vid = (voice_id or "").strip() or settings.elevenlabs_voice_id
    if not settings.elevenlabs_api_key:
        return {"ok": False, "reason": "ELEVENLABS_API_KEY not set"}
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
            headers={"xi-api-key": settings.elevenlabs_api_key,
                     "Content-Type": "application/json"},
            json={"text": text[:300], "model_id": "eleven_turbo_v2_5",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}},
            timeout=45,
        )
        if r.status_code == 200 and len(r.content) > 1000:
            return Response(content=r.content, media_type="audio/mpeg")
        return {"ok": False, "voice_id": vid, "status": r.status_code,
                "detail": _redact(r.text[:300]),
                "hint": "Voice not accessible to this account. In ElevenLabs go to "
                        "Voices -> Explore, find the voice, tap '+' to add it to "
                        "My Voices, then retry. Some library voices need a paid plan."}
    except Exception as e:
        return {"ok": False, "voice_id": vid, "reason": _redact(e)}


@router.get("/still-test")
def still_test(orientation: str = "portrait",
               prompt: str = "tight close-up portrait of a man in a navy blazer, photorealistic, dramatic lighting"):
    """Diagnostics: try one OpenAI still in the given orientation and return the
    exact error if it fails (the pipeline otherwise only logs it server-side)."""
    orient = "vertical" if orientation.strip().lower() in ("portrait", "vertical", "9:16") else "landscape"
    path = f"{settings.output_dir}/sketch/_still_test.png"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ok_o = _openai_still(prompt, path, orient)
    err_o = _STILL_LAST_ERROR
    ok_p = False
    if not ok_o:
        ok_p = _pika_still(prompt, path, orient)
    err_p = None if ok_p else _STILL_LAST_ERROR
    out = {"ok": ok_o or ok_p, "orientation": orient,
           "provider": "openai" if ok_o else ("pika" if ok_p else None),
           "openai_error": None if ok_o else err_o,
           "pika_error": err_p if not ok_o else None}
    if out["ok"]:
        out["bytes"] = os.path.getsize(path)
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


def _openai_still(prompt: str, output_path: str, orientation: str = "landscape") -> bool:
    """DALL-E / gpt-image scene still (16:9 landscape or 9:16 vertical).
    Failure reason is captured in _STILL_LAST_ERROR for the render report."""
    global _STILL_LAST_ERROR
    _STILL_LAST_ERROR = ""
    key = settings.openai_api_key
    if not key:
        _STILL_LAST_ERROR = "OPENAI_API_KEY not set"
        return False
    try:
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1"),
                "prompt": (f"cinematic {'9:16 vertical' if orientation == 'vertical' else '16:9'} film still: {prompt}")[:3900],
                "size": "1024x1536" if orientation == "vertical" else "1536x1024",
                "n": 1,
            },
            timeout=180,
        )
        if r.status_code != 200:
            _STILL_LAST_ERROR = f"OpenAI HTTP {r.status_code}: {_redact(r.text[:200])}"
            print(f"[Sketch:Still] {_STILL_LAST_ERROR}")
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
            _STILL_LAST_ERROR = f"image download HTTP {img.status_code}"
            return False
        _STILL_LAST_ERROR = "empty image response"
        return False
    except Exception as e:
        _STILL_LAST_ERROR = f"exception: {_redact(e)}"
        print(f"[Sketch:Still] {_STILL_LAST_ERROR}")
        return False


def _pika_still(prompt: str, output_path: str, orientation: str = "landscape",
                job_log: Optional[list] = None, label: str = "") -> bool:
    """Fallback still from the Pika image catalog (default: Nano Banana 2) —
    runs on the Pika balance when OpenAI is out of credits."""
    global _STILL_LAST_ERROR
    model_path = os.getenv("PIKA_STILL_PATH") or "/v1/media/google/gemini-3.1-flash-image/text-to-image"
    res = _pika_job(model_path, {
        "prompt": (f"cinematic {'9:16 vertical' if orientation == 'vertical' else '16:9'} film still: {prompt}")[:3900],
        "num_images": 1,
        "aspect_ratio": "9:16" if orientation == "vertical" else "16:9",
        "output_format": "png",
    }, output_path, max_wait=300, job_log=job_log, label=label)
    if not res["ok"]:
        _STILL_LAST_ERROR = res["reason"]
    return res["ok"]


def _still(prompt: str, output_path: str, orientation: str = "landscape",
           job_log: Optional[list] = None, label: str = "") -> bool:
    """Still chain: OpenAI gpt-image first, Pika image catalog as fallback —
    an empty OpenAI balance no longer kills the render."""
    global _STILL_LAST_ERROR
    if _openai_still(prompt, output_path, orientation):
        return True
    openai_err = _STILL_LAST_ERROR
    if _pika_still(prompt, output_path, orientation, job_log, label):
        return True
    _STILL_LAST_ERROR = f"openai: {openai_err or 'n/a'} | pika: {_STILL_LAST_ERROR or 'n/a'}"
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


def _pika_balance_usd() -> Optional[float]:
    """Pika org balance in USD (balance_micro_usd / 1e6). None if unavailable."""
    key = (settings.pika_api_key or "").strip()
    if not key:
        return None
    base = _pika_base()
    for path in ("/billing/balance", "/v1/billing/balance"):
        try:
            r = requests.get(f"{base}{path}", headers={"X-API-Key": key}, timeout=20)
            if r.status_code == 200:
                return (r.json().get("balance_micro_usd") or 0) / 1_000_000
        except Exception as e:
            print(f"[Sketch:Pika] balance check failed: {_redact(e)}")
    return None


def _pika_job(path: str, payload: Dict[str, Any], out_path: str,
              max_wait: int = 600, job_log: Optional[list] = None,
              label: str = "") -> Dict[str, Any]:
    """Generic Pika-catalog job (any vendor/model on dev.pika.art):
    submit -> poll /v1/media/jobs/{id} -> download. Logs usage/charge."""
    key = (settings.pika_api_key or "").strip()
    if not key:
        return {"ok": False, "reason": "PIKA_API_KEY not set"}
    base = _pika_base()
    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    try:
        r = requests.post(f"{base}{path}", headers=headers, json=payload, timeout=60)
        if r.status_code not in (200, 201, 202):
            return {"ok": False, "reason": f"submit HTTP {r.status_code}: {_redact(r.text[:150])}"}
        job = r.json()
        job_id = job.get("id")
        if not job_id:
            return {"ok": False, "reason": f"no job id in response: {str(job)[:200]}"}
        deadline = time.time() + max_wait
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
            return {"ok": False, "reason": f"timed out after {max_wait}s"}
        if job_log is not None:
            job_log.append({"label": label, "path": path, "job_id": job_id,
                            "usage": status.get("usage"), "charge": status.get("charge")})
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
        if dl.status_code == 200 and len(dl.content) > 5000:
            with open(out_path, "wb") as f:
                f.write(dl.content)
            return {"ok": True, "reason": "ok"}
        return {"ok": False, "reason": f"download HTTP {dl.status_code}"}
    except Exception as e:
        return {"ok": False, "reason": f"exception: {_redact(e)}"}


def _pad_audio_min(path: str, min_sec: float = 2.2) -> str:
    """Kling avatar needs 2-300s of driving audio — pad short lines with silence."""
    try:
        if _get_audio_duration(path) >= min_sec:
            return path
        padded = path.replace(".mp3", "_pad.mp3")
        subprocess.run([settings.ffmpeg_path, "-y", "-i", path, "-af", "apad",
                        "-t", str(min_sec), "-c:a", "libmp3lame", "-b:a", "128k", padded],
                       capture_output=True, timeout=30)
        return padded if os.path.exists(padded) else path
    except Exception:
        return path


def _char_desc(chars: Dict[str, Any], speaker: str) -> str:
    """Best-effort character look description from the spec's character sheet."""
    c = chars.get(speaker)
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        for k in ("description", "appearance", "look", "visual", "prompt"):
            v = c.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return speaker


def _generate_episode(ep_pk: str):
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    render_report = {"scenes": {}, "pika_ok": 0, "ken_burns": 0, "placeholder": 0, "talking": 0}
    try:
        ep = db.query(SketchEpisode).filter(SketchEpisode.id == ep_pk).first()
        if not ep:
            return
        spec = ep.spec or {}
        chars = spec.get("characters", {})
        scenes = spec.get("scenes", [])
        voice_map = dict(ep.voice_map or {})
        orient = "vertical" if str(spec.get("orientation") or "").strip().lower() in (
            "portrait", "vertical", "9:16", "9x16") else "landscape"
        lip_sync_on = (str(spec.get("lip_sync", "on")).strip().lower() not in ("off", "false", "0")
                       and (os.getenv("SKETCH_LIPSYNC") or "on").strip().lower() not in ("off", "false", "0"))
        max_avatar = int(os.getenv("SKETCH_MAX_AVATAR_JOBS") or "40")
        # Talking-shot engine: "lipsync" (default, cheap) animates the close-up
        # with Pika 2.5 then Kling Lipsync locks the mouth to the line audio;
        # "avatar" uses full Kling AI Avatar v2 (needs balance > ~$13.50 because
        # Pika pre-authorizes the 300s worst case at submit).
        talk_mode = (os.getenv("SKETCH_TALK_MODE") or "lipsync").strip().lower()
        jobs: list = []
        render_report["balance_start_usd"] = _pika_balance_usd()

        root = f"{settings.output_dir}/sketch/{ep.episode_id}"
        audio_dir = f"{root}/audio"
        still_dir = f"{root}/stills"
        clip_dir = f"{root}/clips"
        for d in (audio_dir, still_dir, clip_dir):
            os.makedirs(d, exist_ok=True)

        # --- Step 1: dialogue TTS per scene (per-character voices, locked) ---
        scene_audio = {}
        tts_report = {}
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
                    if ok:
                        tts_report[f"s{n}_l{li}"] = f"elevenlabs:{voice_id} ({speaker})"
                if not ok and settings.openai_api_key:
                    ok = _openai_tts(text, path)
                    if ok:
                        tts_report[f"s{n}_l{li}"] = f"OPENAI_ALLOY_FALLBACK ({speaker}, wanted elevenlabs:{voice_id})"
                if not ok:
                    est = max(2.0, len(text) * 0.06)
                    _silent_audio(path, est)
                    tts_report[f"s{n}_l{li}"] = f"SILENT_FALLBACK ({speaker}, wanted elevenlabs:{voice_id})"
                    print(f"[Sketch:TTS] silent fallback for scene {n} line {li}")
                line_paths.append(path)
            joined = f"{audio_dir}/s{n}.mp3"
            if not _concat_audio(line_paths, joined):
                raise RuntimeError(f"scene {n}: dialogue audio concat failed")
            scene_audio[n] = joined
        render_report["tts"] = tts_report  # which engine/voice actually spoke each line
        ep.voice_map = voice_map  # LOCK_ON_FIRST_GENERATION: persist chosen voices
        db.commit()

        # --- Step 2: visuals per scene ---
        # Dialogue scenes (v2): per-line talking close-ups via Kling AI Avatar v2
        # on the Pika catalog (close-up still + that line's audio -> lip-synced
        # performance shot). Narrator-only / opted-out scenes keep the classic
        # path: OpenAI still -> R2 URL -> Pika motion looped under joined audio.
        clips = []
        avatar_jobs = 0
        for scene in scenes:
            n = scene.get("scene")
            audio = scene_audio[n]
            dur = _get_audio_duration(audio)
            if dur <= 0:
                dur = float(scene.get("duration_sec") or 6)
            dur = max(3.0, min(dur, 60.0))
            dialogue = scene.get("dialogue", [])
            talking = (lip_sync_on
                       and str(scene.get("lip_sync", "on")).strip().lower() not in ("off", "false", "0")
                       and any((l.get("speaker") or "narrator").strip() != "narrator" for l in dialogue)
                       and bool((settings.pika_api_key or "").strip()))
            clip = f"{clip_dir}/s{n}.mp4"

            if talking:
                _progress(db, ep, f"scene {n}: talking shots (lip-sync)")
                line_clips = []
                fail_reason = ""
                for li, line in enumerate(dialogue):
                    speaker = (line.get("speaker") or "narrator").strip()
                    if speaker == "narrator" or not (line.get("line") or "").strip():
                        continue
                    if avatar_jobs >= max_avatar:
                        fail_reason = f"avatar job cap ({max_avatar}) reached"
                        break
                    line_audio = f"{audio_dir}/s{n}_l{li}.mp3"
                    if not os.path.exists(line_audio):
                        fail_reason = f"missing audio for line {li}"
                        break
                    close = f"{still_dir}/s{n}_{speaker}_close.png"
                    if not os.path.exists(close):
                        desc = _char_desc(chars, speaker)
                        close_prompt = (
                            f"tight close-up portrait of {speaker}"
                            + (f", {desc}" if desc and desc != speaker else "")
                            + ", facing camera, photorealistic, dramatic cinematic lighting"
                            + (f", scene context: {scene.get('still_prompt')}" if scene.get("still_prompt") else ""))
                        _still(close_prompt, close, orient, jobs, f"s{n} close:{speaker}")
                    if not os.path.exists(close):
                        fail_reason = f"close-up still failed for {speaker}: {_STILL_LAST_ERROR or 'unknown'}"
                        break
                    try:
                        close_url = _upload_file_to_r2(
                            f"sketch/stills/{ep.episode_id}-s{n}_{speaker}.png", close, "image/png")
                        line_audio_p = _pad_audio_min(line_audio)
                        audio_url = _upload_file_to_r2(
                            f"sketch/audio/{ep.episode_id}-s{n}l{li}.mp3", line_audio_p, "audio/mpeg")
                    except Exception as e:
                        fail_reason = f"R2 upload failed: {_redact(e)[:100]}"
                        break
                    perf = (scene.get("performance_prompt")
                            or "natural conversational performance, expressive face, subtle hand gestures")
                    raw_line = f"{still_dir}/s{n}_l{li}_avatar.mp4"
                    if talk_mode == "avatar":
                        _progress(db, ep, f"scene {n} line {li}: avatar ({speaker})")
                        res = _pika_job(
                            "/v1/media/kling/kling-ai-avatar-v2/avatar",
                            {"image_url": close_url, "sound_file": audio_url,
                             "prompt": perf[:900], "mode": os.getenv("PIKA_AVATAR_MODE", "std")},
                            raw_line, max_wait=600, job_log=jobs, label=f"s{n}l{li}:{speaker}")
                    else:
                        ldur0 = _get_audio_duration(line_audio_p)
                        if ldur0 <= 0:
                            ldur0 = 3.0
                        base_raw = f"{still_dir}/s{n}_l{li}_base.mp4"
                        _progress(db, ep, f"scene {n} line {li}: base motion ({speaker})")
                        res_b = _pika_job(
                            os.getenv("PIKA_GENERATE_PATH") or "/v1/media/pika/pika-2.5/image-to-video",
                            {"prompt": (perf + ", speaking to camera, slow continuous motion, "
                                        "single action, no scene changes")[:880],
                             "resolution": os.getenv("PIKA_RESOLUTION", "720p"),
                             "duration_s": 5, "image": close_url},
                            base_raw, max_wait=600, job_log=jobs, label=f"s{n}l{li} base:{speaker}")
                        if not res_b["ok"]:
                            # free base: static close-up video; lipsync still animates the mouth
                            subprocess.run([settings.ffmpeg_path, "-y", "-loop", "1", "-i", close,
                                            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                                            "-t", str(ldur0), "-c:v", "libx264", "-preset", "veryfast",
                                            "-pix_fmt", "yuv420p", "-shortest", base_raw],
                                           capture_output=True, timeout=120)
                        if not os.path.exists(base_raw):
                            fail_reason = f"base clip failed: {res_b['reason'][:200]}"
                            break
                        if ldur0 > 5.2 and res_b["ok"]:
                            looped = f"{still_dir}/s{n}_l{li}_base_loop.mp4"
                            subprocess.run([settings.ffmpeg_path, "-y", "-stream_loop", "-1",
                                            "-i", base_raw, "-t", str(ldur0), "-an",
                                            "-c:v", "libx264", "-preset", "veryfast",
                                            "-pix_fmt", "yuv420p", looped],
                                           capture_output=True, timeout=180)
                            if os.path.exists(looped):
                                base_raw = looped
                        try:
                            base_url = _upload_file_to_r2(
                                f"sketch/clips/{ep.episode_id}-s{n}l{li}_base.mp4", base_raw, "video/mp4")
                        except Exception as e:
                            fail_reason = f"R2 upload failed: {_redact(e)[:100]}"
                            break
                        _progress(db, ep, f"scene {n} line {li}: lipsync ({speaker})")
                        res = _pika_job(
                            "/v1/media/kling/kling-lipsync/avatar",
                            {"video_url": base_url, "audio_url": audio_url,
                             "sound_insert_time": 0, "sound_start_time": 0,
                             "sound_end_time": int(ldur0 * 1000),
                             "sound_volume": 1, "original_audio_volume": 0},
                            raw_line, max_wait=600, job_log=jobs, label=f"s{n}l{li}:{speaker}")
                    avatar_jobs += 1
                    if not res["ok"]:
                        kind = "avatar" if talk_mode == "avatar" else "lipsync"
                        fail_reason = f"{kind} s{n}l{li}: {res['reason'][:300]}"
                        break
                    ldur = _get_audio_duration(line_audio_p)
                    if ldur <= 0:
                        ldur = 3.0
                    line_clip = f"{clip_dir}/s{n}_l{li}.mp4"
                    result = _clip_from_video(raw_line, line_audio_p, ldur, line_clip, orient)
                    if result.returncode != 0 or not os.path.exists(line_clip):
                        fail_reason = f"line clip render failed: {result.stderr.decode()[-120:]}"
                        break
                    line_clips.append(line_clip)

                if line_clips and not fail_reason:
                    lc = f"{clip_dir}/s{n}_lines.txt"
                    with open(lc, "w") as f:
                        for c in line_clips:
                            f.write(f"file '{os.path.abspath(c)}'\n")
                    result = subprocess.run(
                        [settings.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
                         "-i", lc, "-c:v", "libx264", "-preset", "veryfast", "-threads", "2",
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", clip],
                        capture_output=True, timeout=300)
                    if result.returncode == 0 and os.path.exists(clip):
                        render_report["scenes"][str(n)] = f"talking_{talk_mode} x{len(line_clips)}"
                        render_report["talking"] += 1
                        ep.render_info = dict(render_report)
                        db.commit()
                        clips.append(clip)
                        continue
                    fail_reason = f"line concat failed: {result.stderr.decode()[-120:]}"
                render_report.setdefault("avatar_notes", {})[str(n)] = fail_reason or "no usable lines"
                print(f"[Sketch:Avatar] scene {n} fell back to classic: {fail_reason[:200]}")

            _progress(db, ep, f"scene {n}: still")
            still = f"{still_dir}/s{n}.png"
            _still(scene.get("still_prompt") or ep.title or "Faith vs Views", still, orient, jobs, f"s{n} still")

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

            _progress(db, ep, f"scene {n}: render clip")
            if has_motion:
                result = _clip_from_video(motion, audio, dur, clip, orient)
            elif os.path.exists(still):
                result = _clip_from_image(still, audio, dur, clip, orient)
            else:
                _generate_placeholder_visual(scene.get("still_prompt") or "Faith vs Views",
                                             f"{still_dir}/s{n}_ph.png", orient)
                result = _clip_from_image(f"{still_dir}/s{n}_ph.png", audio, dur, clip, orient)
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

        render_report["jobs"] = jobs
        render_report["balance_end_usd"] = _pika_balance_usd()
        ep.render_info = dict(render_report)
        ep.status = "done"
        _progress(db, ep, f"done (pika: {render_report['pika_ok']}, "
                         f"talking: {render_report['talking']}, "
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
