"""Generic Director API.

POST /api/direct         -> plan a video and estimate cost
POST /api/direct/produce -> turn that plan into a real rendered production
"""
from __future__ import annotations

import base64
import json
import os
import re
import uuid
import wave
from pathlib import Path
from typing import Any

import requests

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from config import settings
from director import Vertical, Director, LiveLLM, estimate_cost
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


class DirectRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    vertical: str = "faith"
    minutes: int = Field(default=10, ge=1, le=60)
    use_gate: bool = True


class DirectProduceRequest(BaseModel):
    # This is the complete object returned by POST /api/direct. The manifest is
    # reused as-is so the approved plan is what actually gets rendered.
    manifest: dict[str, Any]
    cost: dict[str, Any] | None = None
    llm_mode: str | None = None
    llm_note: str | None = None


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
    man = Director(v, llm=llm).plan(req.topic, req.minutes, req.use_gate)
    return {
        "status": "planned",
        "llm_mode": "live" if llm.live else "mock",
        "llm_note": llm.reason,
        "manifest": json.loads(json.dumps(man, default=vars)),
        "cost": estimate_cost(man, v),
    }


FENRIR_VOICE_NAME = "Fenrir"
GEMINI_TTS_MODELS = (
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",  # fallback for keys without 3.1 access
)


def _director_voice_name(prod: Production) -> str | None:
    """Finance Director renders use Fenrir; other verticals keep the existing chain."""
    source = (getattr(prod, "source_question", "") or "").lower()
    if source.startswith("director / finance /"):
        return FENRIR_VOICE_NAME
    return None


def _gemini_tts(text: str, output_path: str, voice_name: str = FENRIR_VOICE_NAME) -> bool:
    """Generate one scene of narration with Gemini TTS and write a WAV file."""
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key or not text.strip():
        return False

    preferred = (os.getenv("GEMINI_TTS_MODEL") or "").strip()
    models = [preferred] if preferred else list(GEMINI_TTS_MODELS)
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice_name}
                }
            },
        },
    }

    for model in models:
        try:
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=120,
            )
            if resp.status_code != 200:
                print(f"[TTS] Gemini {model} ERROR {resp.status_code}: {resp.text[:240]}")
                continue
            candidate = (resp.json().get("candidates") or [{}])[0]
            parts = ((candidate.get("content") or {}).get("parts") or [])
            inline = next((p.get("inlineData") or p.get("inline_data") or {} for p in parts), {})
            audio_b64 = inline.get("data")
            if not audio_b64:
                print(f"[TTS] Gemini {model} returned no audio data")
                continue
            audio = base64.b64decode(audio_b64)
            mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
            rate_match = re.search(r"rate=(\d+)", mime)
            sample_rate = int(rate_match.group(1)) if rate_match else 24000
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with wave.open(output_path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)  # Gemini L16 PCM is signed 16-bit little-endian
                wav.setframerate(sample_rate)
                wav.writeframes(audio)
            print(f"[TTS] Gemini voice {voice_name} generated {output_path} via {model}")
            return True
        except Exception as e:
            print(f"[TTS] Gemini {model} exception: {type(e).__name__}: {e}")
    return False


def _scene_visual_type(scene: dict[str, Any]) -> str:
    kind = str(scene.get("visual_type") or "still").strip().lower()
    return kind if kind in {"still", "motion", "diagram", "broll"} else "still"


def _scene_visual_prompt(scene: dict[str, Any], vertical: Vertical) -> str:
    kind = _scene_visual_type(scene)
    prompt = scene.get("motion_prompt") if kind == "motion" else scene.get("image_prompt")
    prompt = str(prompt or scene.get("beat_title") or scene.get("tts_line") or "documentary scene").strip()
    # The existing provider adds the cinematic/animated style prefix. Keep the
    # Director's visual style too, because verticals carry their own look.
    return f"{vertical.visual_style} — {prompt}"[:1800]


def _prepare_director_visuals_then_produce(prod_id: str):
    """Generate cheap stills first, then hand off to the existing renderer.

    pipeline._produce_scenes() skips any scene that already has a real visual.
    That lets the Director keep motion clips rationed: still/diagram/broll scenes
    use one image each; only scenes marked "motion" enter the video-provider chain.
    """
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    try:
        prod = db.query(Production).filter(Production.id == prod_id).first()
        if not prod:
            print(f"[Director] Production {prod_id} disappeared before render")
            return
        scenes = (
            db.query(DBScene)
            .filter(DBScene.production_id == prod_id)
            .order_by(DBScene.order_index)
            .all()
        )
        voice_name = _director_voice_name(prod)
        for scene in scenes:
            if voice_name and not scene.narration_audio_path:
                audio_path = f"{settings.output_dir}/audio/{scene.id}.wav"
                if _gemini_tts(scene.narration_text or prod.topic, audio_path, voice_name):
                    scene.narration_audio_path = audio_path
                    db.commit()
                else:
                    print(f"[TTS] Fenrir unavailable for scene {scene.order_index + 1}; existing TTS fallback will handle it")

            kind = (scene.generation_status or "").split(":", 1)[-1].lower()
            if kind == "motion":
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
                scene.generation_status = "done:openai_image"
                db.commit()
                print(f"[Director] Still visual ready for scene {scene.order_index + 1}")
            else:
                # Leave visual_path empty. The normal renderer will try its
                # configured fallback chain rather than silently shipping blank art.
                print(f"[Director] Still image failed for scene {scene.order_index + 1}; renderer fallback will handle it")
        db.commit()
    except Exception as e:
        print(f"[Director] Still pre-generation failed for {prod_id}: {e}")
    finally:
        db.close()

    # Existing engine owns TTS, motion-scene generation, assembly, captions, and R2.
    _produce_scenes(prod_id)


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
    if use_gate and gates_flagged:
        raise HTTPException(
            422,
            {
                "message": "Compliance gates flagged this plan. Revise or regenerate before rendering.",
                "gates_flagged": gates_flagged,
            },
        )

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
            orientation=None,  # use the service's VIDEO_ASPECT_RATIO default
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
        "voice": FENRIR_VOICE_NAME if vertical_name == "finance" else "default",
        "poll_url": f"/api/productions/{prod_id}",
        "message": "Video rendering started. Open the production to watch progress.",
    }
