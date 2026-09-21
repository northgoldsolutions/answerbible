"""Generic Director API.

POST /api/direct         -> plan a video and estimate cost
POST /api/direct/produce -> turn that plan into a real rendered production

TTS for Director renders:
    Finance vertical uses "en-US-Chirp3-HD-Fenrir" (Google Cloud Chirp 3 HD),
    with a graceful fallback to Gemini multimodal TTS voice "Fenrir" when
    GOOGLE_APPLICATION_CREDENTIALS is unset or Cloud TTS is unreachable.
    Other verticals keep the existing ElevenLabs -> OpenAI -> silent chain
    in pipeline._produce_scenes().

    Both paths write MP3 to match the pipeline convention (ffmpeg consumes
    narration_audio_path with `-c:a aac`; MP3 decodes cleanly and we don't
    rely on per-codec path handling).
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import uuid
import wave
from pathlib import Path
from typing import Any

import requests

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from config import settings
from director import Vertical, Director, LiveLLM, estimate_cost, motion_provider_available
from models import (
    Production,
    ReviewDecision,
    ReviewStatus,
    Scene as DBScene,
    SessionLocal,
    Stage,
    get_engine,
)
from pipeline import _produce_scenes
from video_providers import _openai_image

router = APIRouter()
VERTICAL_DIR = Path(__file__).parent / "verticals"

# ---------------------------------------------------------------------------
# TTS config
# ---------------------------------------------------------------------------
# PATCH (c6172f5 hotfix): the Director voice default is the Google Cloud
# Chirp 3 HD Fenrir (matches the texttospeech.VoiceSelectionParams snippet
# the user pasted: en-US-Chirp3-HD-Fenrir). The short name "Fenrir" is kept
# as the Gemini fallback voice ID when Cloud TTS credentials are absent.
DIRECTOR_FINANCE_VOICE = "en-US-Chirp3-HD-Fenrir"   # Cloud TTS Chirp 3 HD
DIRECTOR_FINANCE_VOICE_GEMINI_FALLBACK = "Fenrir"   # Gemini multimodal TTS
DIRECTOR_FINANCE_VERTICAL = "finance"

GEMINI_TTS_MODELS = (
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-3.1-flash-tts-preview",
)

# Cloud TTS client is imported lazily so the module doesn't crash when the
# google-cloud-texttospeech package isn't installed (it's optional; Gemini
# TTS works off a plain API key).
_gctts_client = None
_gctts_import_error: str | None = None


def _get_gctts_client():
    """Lazy-load the Google Cloud Text-to-Speech client. Returns None if the
    package isn't installed or credentials aren't configured."""
    global _gctts_client, _gctts_import_error
    if _gctts_client is not None:
        return _gctts_client
    if _gctts_import_error is not None:
        return None
    try:
        from google.cloud import texttospeech  # type: ignore

        # GOOGLE_APPLICATION_CREDENTIALS must be set for ADC to work;
        # if not, fall through to Gemini.
        if not (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GOOGLE_API_KEY")):
            _gctts_import_error = "no GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_API_KEY"
            return None
        _gctts_client = texttospeech.TextToSpeechClient()
        return _gctts_client
    except Exception as e:  # ImportError or auth error at construction time
        _gctts_import_error = f"{type(e).__name__}: {e}"
        print(f"[TTS] Cloud TTS unavailable ({_gctts_import_error}); falling back to Gemini")
        return None


# ---------------------------------------------------------------------------

class DirectRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    vertical: str = "faith"
    minutes: int = Field(default=10, ge=1, le=60)
    use_gate: bool = True


class DirectProduceRequest(BaseModel):
    manifest: dict[str, Any]
    cost: dict[str, Any] | None = None
    llm_mode: str | None = None
    llm_note: str | None = None
    ignore_gate_flags: bool = False


def load_vertical(name: str) -> Vertical:
    p = VERTICAL_DIR / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(
            404,
            f"unknown vertical '{name}' — options: "
            + ", ".join(x.stem for x in VERTICAL_DIR.glob("*.yaml")),
        )
    return Vertical(**json.loads(p.read_text()))


@router.post("/api/direct")
def direct(req: DirectRequest):
    v = load_vertical(req.vertical)
    llm = LiveLLM()
    man = Director(v, llm=llm,
                   motion_available=motion_provider_available()).plan(
        req.topic, req.minutes, req.use_gate)
    # PATCH: surface LLM/motion fallback notes in the response so the UI can
    # show e.g. "Using stills-only mode — REPLICATE_API_TOKEN not set" and the
    # user doesn't think motion clips were forgotten.
    payload = json.loads(json.dumps(man, default=vars))
    return {
        "status": "planned",
        "llm_mode": "live" if llm.live else "mock",
        "llm_note": llm.reason or ("" if llm.live else "using placeholder narration"),
        "manifest": payload,
        "cost": estimate_cost(man, v),
    }


# ---------------------------------------------------------------------------
# Voice routing
# ---------------------------------------------------------------------------

def _director_voice_config(vertical_name: str) -> dict[str, str] | None:
    """Return the voice config for the given vertical, or None to let the
    pipeline use its default (ElevenLabs -> OpenAI -> silent) chain.

    Returns dict with 'name' (provider-specific) and 'provider' ('cloud'|'gemini').
    """
    if vertical_name != DIRECTOR_FINANCE_VERTICAL:
        return None
    return {"name": DIRECTOR_FINANCE_VOICE, "provider": "cloud",
            "fallback_name": DIRECTOR_FINANCE_VOICE_GEMINI_FALLBACK}


def _parse_lang_and_voice(full_name: str) -> tuple[str, str]:
    """Split a fully-qualified Cloud TTS name like 'en-US-Chirp3-HD-Fenrir'
    into (language_code, short_voice_name). Falls back to ('en-US', full_name)
    if it can't find a Chirp/Wavenet/Standard/Neural2 token."""
    for marker in ("-Chirp3-HD-", "-Chirp-HD-", "-Wavenet-", "-Standard-",
                   "-Neural2-", "-Studio-", "-Polyglot-", "-News-"):
        if marker in full_name:
            lang, short = full_name.split(marker, 1)
            return lang, short
    return "en-US", full_name


def _cloud_tts(text: str, output_path: str, voice_full_name: str) -> bool:
    """Render with Google Cloud Text-to-Speech SDK (Chirp 3 HD etc.) and
    write an MP3 file. Returns True on success."""
    client = _get_gctts_client()
    if client is None:
        return False
    try:
        from google.cloud import texttospeech  # type: ignore

        lang_code, _ = _parse_lang_and_voice(voice_full_name)
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code=lang_code,
            name=voice_full_name,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.0,
        )
        resp = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as out:
            out.write(resp.audio_content)
        if os.path.getsize(output_path) < 200:
            print(f"[TTS] Cloud TTS returned suspiciously small file for {output_path}")
            return False
        print(f"[TTS] Cloud TTS voice {voice_full_name} -> {output_path}")
        return True
    except Exception as e:
        print(f"[TTS] Cloud TTS {voice_full_name} error: {type(e).__name__}: {e}")
        return False


def _pcm_to_wav(pcm: bytes, wav_path: str, sample_rate: int = 24000) -> None:
    """Write signed 16-bit little-endian PCM mono to a WAV file. Pads to an
    even byte count so the wave module doesn't complain on odd payloads."""
    if len(pcm) % 2 == 1:
        pcm = pcm + b"\x00"
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)


def _wav_to_mp3(wav_path: str, mp3_path: str) -> bool:
    """Transcode a WAV to MP3 via ffmpeg. The pipeline expects MP3 narration
    files; we always emit MP3 to keep the assembly path uniform."""
    ffmpeg = getattr(settings, "ffmpeg_path", "ffmpeg") or "ffmpeg"
    if shutil.which(ffmpeg) is None:
        # Fall back: just rename/copy the WAV to the target path. ffmpeg
        # in the pipeline reads via -i audio_path and aac-encodes on the
        # way into the video, so a WAV at an .mp3 extension will still
        # decode; the file extension mismatch is ugly but it renders.
        print(f"[TTS] ffmpeg not found at '{ffmpeg}'; leaving WAV at {wav_path}")
        shutil.copyfile(wav_path, mp3_path)
        return os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", wav_path, "-codec:a", "libmp3lame",
             "-b:a", "128k", "-ar", "24000", "-ac", "1", mp3_path],
            check=True, capture_output=True, timeout=120,
        )
        return os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0
    except Exception as e:
        print(f"[TTS] WAV->MP3 conversion failed: {type(e).__name__}: {e}")
        return False


def _gemini_tts(text: str, output_mp3_path: str,
                voice_name: str = DIRECTOR_FINANCE_VOICE_GEMINI_FALLBACK) -> bool:
    """Generate one scene of narration using Gemini multimodal TTS and write
    an MP3 file (matches the pipeline convention). Gemini returns signed
    16-bit little-endian PCM mono at 24kHz; we wrap it as WAV then transcode
    to MP3 with ffmpeg."""
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key or not text.strip():
        return False

    preferred = (os.getenv("GEMINI_TTS_MODEL") or "").strip()
    models = [preferred] if preferred else list(GEMINI_TTS_MODELS)

    # Gemini TTS prebuilt voices are SHORT names only ("Fenrir", "Kore", ...).
    # If someone passed a fully-qualified name (en-US-Chirp3-HD-xxx) strip the
    # language/model prefix; Gemini's endpoint will 400 on the long form.
    _CHIRP_MARKERS = ("-Chirp3-HD-", "-Chirp-HD-", "-Chirp-", "-Wavenet-",
                      "-Standard-", "-Neural2-", "-Studio-", "-Polyglot-", "-News-")
    if "-" in voice_name and any(m in voice_name for m in _CHIRP_MARKERS):
        _, short = _parse_lang_and_voice(voice_name)
        if short and short != voice_name:
            print(f"[TTS] Gemini voice short-name coercion: {voice_name} -> {short}")
            voice_name = short

    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            # Be explicit: LINEAR16 PCM at 24kHz mono so we don't depend on
            # defaults that could change server-side.
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": 24000,
            },
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_name}
                }
            },
        },
    }

    wav_tmp = output_mp3_path.rsplit(".", 1)[0] + ".__gemini.wav"
    for model in models:
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
            if resp.status_code != 200:
                print(f"[TTS] Gemini {model} HTTP {resp.status_code}: {resp.text[:240]}")
                continue
            data = resp.json()
            candidate = (data.get("candidates") or [{}])[0]
            if candidate.get("finishReason") in {"RECITATION", "SAFETY", "BLOCKED"}:
                print(f"[TTS] Gemini {model} blocked: {candidate.get('finishReason')}")
                continue
            parts = ((candidate.get("content") or {}).get("parts") or [])
            inline = next(
                (p.get("inlineData") or p.get("inline_data") or {} for p in parts
                 if isinstance(p, dict) and (p.get("inlineData") or p.get("inline_data"))),
                {},
            )
            audio_b64 = inline.get("data")
            if not audio_b64:
                print(f"[TTS] Gemini {model} returned no audio (parts={len(parts)})")
                continue
            audio = base64.b64decode(audio_b64)
            mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
            rate_match = re.search(r"rate[=_-]?(\d+)", mime)
            try:
                sample_rate = int(rate_match.group(1)) if rate_match else 24000
            except (TypeError, ValueError):
                sample_rate = 24000
            _pcm_to_wav(audio, wav_tmp, sample_rate=sample_rate)
            ok = _wav_to_mp3(wav_tmp, output_mp3_path)
            # Clean up temp WAV regardless of success; MP3 is what we keep.
            try:
                os.remove(wav_tmp)
            except OSError:
                pass
            if ok:
                print(f"[TTS] Gemini voice '{voice_name}' -> {output_mp3_path} via {model}")
                return True
            print(f"[TTS] Gemini {model} produced PCM but MP3 conversion failed")
        except Exception as e:
            print(f"[TTS] Gemini {model} exception: {type(e).__name__}: {e}")
    return False


def _render_director_voice(text: str, mp3_path: str, voice_cfg: dict[str, str] | None) -> bool:
    """Try Cloud TTS first (for 'en-US-Chirp3-HD-Fenrir' and friends), fall
    back to Gemini multimodal TTS (short name). Returns True on success."""
    if not voice_cfg:
        return False
    name = voice_cfg.get("name", "")
    fb = voice_cfg.get("fallback_name", "")
    if _cloud_tts(text, mp3_path, name):
        return True
    if fb and _gemini_tts(text, mp3_path, voice_name=fb):
        print(f"[TTS] Cloud TTS failed; used Gemini fallback voice '{fb}'")
        return True
    if not fb and _gemini_tts(text, mp3_path, voice_name=name):
        return True
    return False


def _scene_visual_type(scene: dict[str, Any]) -> str:
    kind = str(scene.get("visual_type") or "still").strip().lower()
    return kind if kind in {"still", "motion", "diagram", "broll"} else "still"


def _scene_visual_prompt(scene: dict[str, Any], vertical: Vertical) -> str:
    kind = _scene_visual_type(scene)
    prompt = scene.get("motion_prompt") if kind == "motion" else scene.get("image_prompt")
    prompt = str(prompt or scene.get("beat_title") or scene.get("tts_line") or "documentary scene").strip()
    return f"{vertical.visual_style} — {prompt}"[:1800]


def _cleanup_stale_audio(scene_id: str) -> None:
    """Remove any stale audio artifacts from previous runs (.mp3, .wav,
    .silent, .pause.mp3) so the pipeline can't pick up a silent fallback
    over a fresh Director voice render."""
    audio_dir = f"{settings.output_dir}/audio"
    for suffix in (".mp3", ".wav", ".mp3.silent", ".wav.silent",
                   ".mp3.pause.mp3", ".__gemini.wav"):
        p = os.path.join(audio_dir, scene_id + suffix)
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _prepare_director_visuals_then_produce(prod_id: str):
    """Generate cheap stills first, render Director voice, then hand off.

    pipeline._produce_scenes() skips any scene that already has a real
    (non-.silent) audio file and a real visual, so we pre-fill narration
    with the Director voice and stills with OpenAI images before calling it.
    """
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    voice_cfg: dict[str, str] | None = None
    vertical_name = ""
    try:
        prod = db.query(Production).filter(Production.id == prod_id).first()
        if not prod:
            print(f"[Director] Production {prod_id} disappeared before render")
            return
        # Derive vertical name from source_question ("Director / <display_name> / N min")
        # OR fall back to parsing topic/keywords. This is more robust than
        # checking a hardcoded string.
        vertical_name = _infer_vertical(prod)
        voice_cfg = _director_voice_config(vertical_name)

        scenes = (
            db.query(DBScene)
            .filter(DBScene.production_id == prod_id)
            .order_by(DBScene.order_index)
            .all()
        )
        for scene in scenes:
            # Director voice overrides any existing audio for this production
            # run (stale artifacts from a previous failed run would otherwise
            # block the new voice from being written).
            has_real_audio = bool(
                scene.narration_audio_path
                and os.path.exists(scene.narration_audio_path)
                and not os.path.exists(scene.narration_audio_path + ".silent")
            )
            if voice_cfg:
                _cleanup_stale_audio(scene.id)
                audio_path = f"{settings.output_dir}/audio/{scene.id}.mp3"
                text = scene.narration_text or prod.topic or ""
                if text and _render_director_voice(text, audio_path, voice_cfg):
                    scene.narration_audio_path = audio_path
                    # Clear any lingering silent marker we may have missed.
                    for marker in (audio_path + ".silent", audio_path + ".pause.mp3"):
                        try:
                            if os.path.exists(marker):
                                os.remove(marker)
                        except OSError:
                            pass
                    has_real_audio = True
                    db.commit()
                else:
                    print(f"[TTS] Director voice unavailable for scene "
                          f"{scene.order_index + 1}; pipeline fallback will handle it")
            elif not has_real_audio:
                _cleanup_stale_audio(scene.id)

            kind = (scene.generation_status or "").split(":", 1)[-1].lower()
            # Only pre-generate a still image for scenes the pipeline will
            # actually assemble as stills. "motion" is a video-clip slot:
            # let pipeline.generate_scene_visual() handle it (Seedance/etc.).
            # "broll" and "diagram" are ASSEMBLED AS STILLS (Ken Burns /
            # cross-dissolve in the compositor) — generate a still for them
            # here so the assembler never gets stuck waiting on a video clip.
            if kind == "motion":
                # Make sure no leftover stale PNG from a prior run blocks
                # these scenes from going through the video provider.
                for ext in (".png", ".mp4", ".jpg", ".webp"):
                    stale = f"{settings.output_dir}/visuals/{scene.id}{ext}"
                    try:
                        if os.path.exists(stale) and scene.visual_path != stale:
                            os.remove(stale)
                    except OSError:
                        pass
                continue
            out_path = f"{settings.output_dir}/visuals/{scene.id}.png"
            prompt = scene.visual_prompt or scene.narration_text or prod.topic
            if _openai_image(
                prompt,
                out_path,
                orientation=getattr(prod, "orientation", None),
                style=getattr(prod, "visual_style", None),
            ):
                scene.visual_path = out_path
                scene.generation_status = f"done:openai_image_broll" if kind == "broll" else "done:openai_image"
                db.commit()
                print(f"[Director] Still visual ready for scene {scene.order_index + 1}")
            else:
                print(f"[Director] Still image failed for scene {scene.order_index + 1}; "
                      f"renderer fallback will handle it")
        db.commit()
    except Exception as e:
        print(f"[Director] Still pre-generation failed for {prod_id}: {e}")
    finally:
        db.close()

    _produce_scenes(prod_id)


def _infer_vertical(prod: Production) -> str:
    """Figure out which vertical this production is running under.

    source_question looks like 'Director / Money & Business / 10 min'
    (display_name), NOT the YAML slug 'finance'. We compare case-insensitively
    against every YAML in verticals/ by name and display_name.
    """
    src = (getattr(prod, "source_question", "") or "").lower()
    # Try to match against known vertical slugs and display names.
    for yml in VERTICAL_DIR.glob("*.yaml"):
        try:
            v = Vertical(**json.loads(yml.read_text()))
            slug = yml.stem.lower()
            display = (getattr(v, "display_name", "") or "").lower()
            if slug and slug in src:
                return slug
            if display and display in src:
                return slug
        except Exception:
            continue
    # Keyword fallback: the /api/direct/produce caller stores vertical_name
    # as part of the keywords string ("finance, term, whole, life, ...").
    keywords = (getattr(prod, "keywords", "") or "").lower()
    for yml in VERTICAL_DIR.glob("*.yaml"):
        if yml.stem.lower() in keywords:
            return yml.stem.lower()
    return ""


@router.post("/api/direct/produce")
def direct_produce(req: DirectProduceRequest, background_tasks: BackgroundTasks):
    manifest = req.manifest or {}
    scenes = manifest.get("scenes") or []
    if not isinstance(scenes, list) or not scenes:
        raise HTTPException(400, "manifest.scenes is required — generate a plan first")

    topic = str(manifest.get("topic") or "").strip()
    if len(topic) < 3:
        raise HTTPException(400, "manifest.topic is required")

    vertical_name = str(manifest.get("vertical") or "faith").strip().lower()
    vertical = load_vertical(vertical_name)
    minutes = int(manifest.get("minutes") or 10)
    use_gate = bool(manifest.get("use_gate", True))
    gates_flagged = manifest.get("gates_flagged") or []
    if use_gate and gates_flagged and not req.ignore_gate_flags:
        raise HTTPException(
            422,
            {
                "message": "Compliance gates flagged this plan.",
                "gates_flagged": gates_flagged,
                "can_override": True,
            },
        )

    voice_cfg = _director_voice_config(vertical_name)
    voice_label = voice_cfg["name"] if voice_cfg else "default"

    prod_id = str(uuid.uuid4())
    narration_lines = [str(s.get("tts_line") or "").strip() for s in scenes]
    description = " ".join(line for line in narration_lines if line)[:900]
    keyword_parts = [vertical_name] + [w.strip(".,:;!?\"'()").lower() for w in topic.split() if len(w) > 3]
    keywords = ", ".join(dict.fromkeys(keyword_parts))[:500]

    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    try:
        prod = Production(
            id=prod_id,
            topic=topic,
            source_question=f"Director / {vertical.display_name} / {minutes} min",
            stage=Stage.PRODUCTION,
            status="active",
            title=f"{topic} | {vertical.display_name}",
            description=description,
            keywords=keywords,
            video_format="episode",
            orientation=None,
            scene_count=len(scenes),
            visual_style="cinematic",
            burn_captions=False,
            evidence_gate_passed=True,
            human_review_passed=True,
            approved_by="director",
        )
        db.add(prod)
        db.flush()

        for i, scene_data in enumerate(scenes):
            kind = _scene_visual_type(scene_data)
            narration = str(scene_data.get("tts_line") or "").strip()
            if not narration:
                narration = str(scene_data.get("beat_title") or f"{topic}, scene {i + 1}")
            word_count = len(re.findall(r"\S+", narration))
            estimated_seconds = max(3, min(settings.max_scene_duration, round(word_count / 2.5)))
            db.add(
                DBScene(
                    id=str(uuid.uuid4()),
                    production_id=prod_id,
                    order_index=int(scene_data.get("index", i)),
                    narration_text=narration,
                    visual_prompt=_scene_visual_prompt(scene_data, vertical),
                    duration_seconds=estimated_seconds,
                    claim_ids=[],
                    generation_status=f"director:{kind}",
                )
            )

        db.add(
            ReviewDecision(
                id=str(uuid.uuid4()),
                production_id=prod_id,
                stage="director",
                decision=ReviewStatus.PASS,
                reviewer="director",
                notes=(
                    f"Generic Director render started. Vertical={vertical_name}; "
                    f"minutes={minutes}; gate={'on' if use_gate else 'off'}; "
                    f"voice={voice_label}; "
                    f"gates_run={manifest.get('gates_run') or []}"
                )[:500],
            )
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Could not create Director production: {e}")
    finally:
        db.close()

    background_tasks.add_task(_prepare_director_visuals_then_produce, prod_id)
    return {
        "status": "production_started",
        "production_id": prod_id,
        "scene_count": len(scenes),
        "voice": voice_label,
        "voice_provider": (voice_cfg or {}).get("provider", "pipeline_default"),
        "poll_url": f"/api/productions/{prod_id}",
        "message": "Video rendering started. Open the production to watch progress.",
    }
