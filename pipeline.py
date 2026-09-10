# pipeline.py
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime
import os
import subprocess
import requests

# R2 Storage inline
import boto3
from botocore.config import Config

def get_r2_client():
    account_id = os.getenv('R2_ACCOUNT_ID')
    access_key = os.getenv('R2_ACCESS_KEY_ID')
    secret_key = os.getenv('R2_SECRET_ACCESS_KEY')
    if not all([account_id, access_key, secret_key]):
        raise ValueError("Missing R2 credentials")
    return boto3.client(
        's3',
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version='s3v4')
    )

def _r2_public_base() -> str:
    return os.getenv('R2_PUBLIC_URL', f"https://pub-{os.getenv('R2_ACCOUNT_ID')}.r2.dev").rstrip('/')

def upload_video(prod_id: str, file_path: str) -> str:
    bucket = os.getenv('R2_BUCKET_NAME')
    if not bucket:
        raise ValueError("Missing R2_BUCKET_NAME")
    key = f"videos/{prod_id}.mp4"
    client = get_r2_client()
    client.upload_file(file_path, bucket, key, ExtraArgs={'ContentType': 'video/mp4'})
    return f"{_r2_public_base()}/{key}"

def upload_captions(prod_id: str, file_path: str) -> str:
    bucket = os.getenv('R2_BUCKET_NAME')
    if not bucket:
        raise ValueError("Missing R2_BUCKET_NAME")
    key = f"captions/{prod_id}.srt"
    client = get_r2_client()
    client.upload_file(file_path, bucket, key, ExtraArgs={'ContentType': 'text/plain'})
    return f"{_r2_public_base()}/{key}"

from models import Production, Claim, Scene, ReviewDecision, Stage, ReviewStatus, Confidence, ClaimType, DoctrinalCategory, get_engine, SessionLocal
from config import settings
from theology_gate import run_theology_gate
from video_providers import generate_scene_visual, provider_status
from research import auto_research, auto_script

router = APIRouter()

def get_db():
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    try:
        yield db
    finally:
        db.close()

class ProductionCreate(BaseModel):
    topic: str
    source_question: Optional[str] = None
    doctrinal_category: Optional[str] = "general"
    primary_scripture: Optional[str] = None
    gospel_video: Optional[bool] = False
    supporting_passages: Optional[List[str]] = []

class ResearchSubmit(BaseModel):
    hook: str
    problem: str
    explanation: str
    story: str
    application: str
    cta: str

class ClaimSubmit(BaseModel):
    claim_text: str
    source_reference: str
    source_text: str
    original_language: Optional[str] = "Hebrew"
    context: str
    interpretation: str
    confidence: str = "medium"
    alternative_interpretations: Optional[str] = ""
    claim_type: str = "speculation"
    cross_references: Optional[List[str]] = []
    character_of_god_relevant: Optional[bool] = False
    gospel_relevant: Optional[bool] = False

class ScriptSubmit(BaseModel):
    claims: List[ClaimSubmit]
    scenes: List[dict]

class ReviewSubmit(BaseModel):
    decision: str
    reviewer: str
    notes: Optional[str] = ""

class PackagingSubmit(BaseModel):
    title: str
    description: str
    keywords: str
    thumbnail_prompt: str

@router.post("/productions")
def create_production(data: ProductionCreate, db: Session = Depends(get_db)):
    cat_map = {
        "general": DoctrinalCategory.GENERAL,
        "genesis_6": DoctrinalCategory.GENESIS_6,
        "sheol": DoctrinalCategory.SHEOL_AFTERLIFE,
        "spiritual_warfare": DoctrinalCategory.SPIRITUAL_WARFARE,
        "demons": DoctrinalCategory.DEMONS,
        "election": DoctrinalCategory.ELECTION,
        "end_times": DoctrinalCategory.END_TIMES,
        "divorce": DoctrinalCategory.DIVORCE_REMARRIAGE,
        "women_ministry": DoctrinalCategory.WOMEN_MINISTRY,
        "salvation": DoctrinalCategory.SALVATION,
        "character_of_god": DoctrinalCategory.CHARACTER_OF_GOD,
        "prophecy_dating": DoctrinalCategory.PROPHECY_DATING,
    }
    prod = Production(
        id=str(uuid.uuid4()),
        topic=data.topic,
        source_question=data.source_question,
        stage=Stage.DISCOVERY,
        doctrinal_category=cat_map.get(data.doctrinal_category, DoctrinalCategory.GENERAL),
        primary_scripture=data.primary_scripture,
        gospel_video=data.gospel_video,
        supporting_passages=data.supporting_passages or []
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)
    return {"id": prod.id, "stage": prod.stage.value, "message": "Production created. Submit research."}

@router.post("/productions/{prod_id}/research")
def submit_research(prod_id: str, data: ResearchSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.DISCOVERY:
        raise HTTPException(400, f"Expected DISCOVERY, got {prod.stage.value}")
    prod.hook = data.hook
    prod.problem = data.problem
    prod.explanation = data.explanation
    prod.story = data.story
    prod.application = data.application
    prod.cta = data.cta
    prod.stage = Stage.RESEARCH
    db.commit()
    return {"id": prod.id, "stage": prod.stage.value, "message": "Research submitted. Submit script + claims."}

@router.post("/productions/{prod_id}/auto-research")
def auto_research_endpoint(prod_id: str, db: Session = Depends(get_db)):
    """AI drafts the 6 research fields. Only available at DISCOVERY stage.
    David reviews the draft on the Overview tab — gates are untouched."""
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.DISCOVERY:
        raise HTTPException(400, f"Auto-research only works at DISCOVERY stage, got {prod.stage.value}")
    try:
        draft = auto_research(
            prod.topic, prod.source_question, prod.primary_scripture,
            prod.doctrinal_category.value, prod.gospel_video
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Research generation failed: {e}")
    prod.hook = draft["hook"]
    prod.problem = draft["problem"]
    prod.explanation = draft["explanation"]
    prod.story = draft["story"]
    prod.application = draft["application"]
    prod.cta = draft["cta"]
    prod.stage = Stage.RESEARCH
    db.commit()
    return {"id": prod.id, "stage": prod.stage.value, "draft": draft,
            "message": "AI research draft saved. Review it on the Overview tab, then submit script + claims."}

@router.post("/productions/{prod_id}/auto-script")
def auto_script_endpoint(prod_id: str, db: Session = Depends(get_db)):
    """AI drafts ONE claim + ONE scene from the approved research.
    Draft is returned for review only — nothing is saved until you submit."""
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.RESEARCH:
        raise HTTPException(400, f"Auto-script only works at RESEARCH stage, got {prod.stage.value}")
    try:
        draft = auto_script(
            prod.topic, prod.primary_scripture,
            prod.hook, prod.problem, prod.explanation,
            prod.story, prod.application, prod.cta
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Script drafting failed: {e}")
    return {"id": prod.id, "draft": draft,
            "message": "Claims & scene drafted. Review every field, edit, then submit."}

@router.post("/productions/{prod_id}/script")
def submit_script(prod_id: str, data: ScriptSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.RESEARCH:
        raise HTTPException(400, f"Expected RESEARCH, got {prod.stage.value}")

    claim_map = {}
    for i, c in enumerate(data.claims):
        type_map = {
            "scripture": ClaimType.SCRIPTURE,
            "strong_inference": ClaimType.STRONG_INFERENCE,
            "traditional": ClaimType.TRADITIONAL,
            "scholarly": ClaimType.SCHOLARLY,
            "speculation": ClaimType.SPECULATION,
        }
        conf_map = {"high": Confidence.HIGH, "medium": Confidence.MEDIUM, "low": Confidence.LOW}
        claim = Claim(
            id=str(uuid.uuid4()),
            production_id=prod_id,
            claim_text=c.claim_text,
            source_reference=c.source_reference,
            source_text=c.source_text,
            original_language=c.original_language,
            context=c.context,
            interpretation=c.interpretation,
            confidence=conf_map.get(c.confidence.lower(), Confidence.MEDIUM),
            alternative_interpretations=c.alternative_interpretations or "",
            claim_type=type_map.get(c.claim_type.lower(), ClaimType.SPECULATION),
            cross_references=c.cross_references or [],
            character_of_god_relevant=c.character_of_god_relevant or False,
            gospel_relevant=c.gospel_relevant or False,
        )
        db.add(claim)
        claim_map[i] = claim.id

    db.flush()
    for s in data.scenes:
        scene = Scene(
            id=str(uuid.uuid4()),
            production_id=prod_id,
            order_index=s.get("order_index", 0),
            narration_text=s.get("narration_text", ""),
            visual_prompt=s.get("visual_prompt", ""),
            claim_ids=[claim_map.get(idx, idx) for idx in s.get("claim_ids", [])]
        )
        db.add(scene)

    prod.stage = Stage.SCRIPT
    db.commit()
    return {"id": prod.id, "stage": prod.stage.value, "claim_count": len(data.claims), "message": "Script submitted. Run evidence gate."}

@router.post("/productions/{prod_id}/evidence")
def run_evidence_gate_endpoint(prod_id: str, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.SCRIPT:
        raise HTTPException(400, f"Expected SCRIPT, got {prod.stage.value}")

    claims = db.query(Claim).filter(Claim.production_id == prod_id).all()
    result = run_theology_gate(prod, claims)

    for v in result.violations:
        cid = v.get("claim_id")
        if cid and cid != "production":
            claim = db.query(Claim).filter(Claim.id == cid).first()
            if claim:
                claim.evidence_status = ReviewStatus.FAIL
                claim.evidence_notes = f"[{v['rule']}] {v['detail']}"

    for w in result.warnings:
        cid = w.get("claim_id")
        if cid and cid != "production":
            claim = db.query(Claim).filter(Claim.id == cid).first()
            if claim and claim.evidence_status != ReviewStatus.FAIL:
                claim.evidence_notes = (claim.evidence_notes or "") + f"\n[WARNING:{w['rule']}] {w['detail']}"

    prod.stage = Stage.EVIDENCE_GATE

    if len(result.violations) == 0:
        for claim in claims:
            claim.evidence_status = ReviewStatus.PASS
            claim.evidence_notes = "Passed all 12 theological blockers"
        prod.evidence_gate_passed = True

    if not result.passed:
        decision = ReviewDecision(
            id=str(uuid.uuid4()), production_id=prod_id, stage="evidence_gate",
            decision=ReviewStatus.FAIL, reviewer="system",
            notes=f"{len(result.violations)} violations, {len(result.warnings)} warnings"
        )
        db.add(decision)
        db.commit()
        return {"id": prod.id, "stage": prod.stage.value, "decision": "FAIL",
                "violations": result.violations, "warnings": result.warnings,
                "message": "Theological gate FAILED. Repair and resubmit."}

    prod.requires_manual_review = result.requires_manual

    decision = ReviewDecision(
        id=str(uuid.uuid4()), production_id=prod_id, stage="evidence_gate",
        decision=ReviewStatus.PASS, reviewer="system",
        notes=f"Passed. Manual review required: {result.requires_manual}"
    )
    db.add(decision)
    db.commit()

    return {"id": prod.id, "stage": prod.stage.value, "decision": "PASS",
            "manual_review_required": result.requires_manual,
            "message": "Evidence gate passed. Awaiting human review (DAVID APPROVES)."}

@router.post("/productions/{prod_id}/review")
def human_review(prod_id: str, data: ReviewSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.EVIDENCE_GATE:
        raise HTTPException(400, f"Expected EVIDENCE_GATE, got {prod.stage.value}")

    decision = ReviewDecision(
        id=str(uuid.uuid4()), production_id=prod_id, stage="human_review",
        decision=ReviewStatus(data.decision.lower()), reviewer=data.reviewer, notes=data.notes or ""
    )
    db.add(decision)

    if data.decision.lower() == "pass":
        # DAVID APPROVES — human override of the automated gate.
        # Mark all claims passed so production can proceed; keep an audit note.
        claims = db.query(Claim).filter(Claim.production_id == prod_id).all()
        for claim in claims:
            if claim.evidence_status != ReviewStatus.PASS:
                old = (claim.evidence_notes or "")[:200]
                claim.evidence_status = ReviewStatus.PASS
                claim.evidence_notes = f"OVERRIDDEN by human review ({data.reviewer})" + (f" — gate had flagged: {old}" if old else "")
        prod.evidence_gate_passed = True
        prod.stage = Stage.HUMAN_REVIEW
        prod.approved_by = data.reviewer
        prod.approved_at = datetime.utcnow()
        prod.human_review_passed = True
        db.commit()
        return {"id": prod.id, "stage": prod.stage.value, "message": "APPROVED (human override). Ready for production."}
    else:
        db.commit()
        return {"id": prod.id, "stage": "evidence_gate", "message": f"Review: {data.decision.upper()}. Repair required."}

@router.post("/productions/{prod_id}/produce")
def produce(prod_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage not in (Stage.HUMAN_REVIEW, Stage.PRODUCTION, Stage.ASSEMBLY):
        raise HTTPException(400, f"Expected HUMAN_REVIEW, got {prod.stage.value}")

    failed = db.query(Claim).filter(Claim.production_id == prod_id, Claim.evidence_status != ReviewStatus.PASS).count()
    if failed > 0:
        raise HTTPException(400, f"{failed} claims have not passed evidence gate")

    prod.stage = Stage.PRODUCTION
    prod.status = "active"
    db.commit()
    background_tasks.add_task(_produce_scenes, prod_id)
    return {"id": prod.id, "stage": prod.stage.value,
            "providers": provider_status(),
            "message": "Production started in background."}

# ============ PRODUCTION ENGINE (Lumen-style provider chain) ============

def _produce_scenes(prod_id: str):
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    try:
        scenes = db.query(Scene).filter(Scene.production_id == prod_id).order_by(Scene.order_index).all()
        for scene in scenes:
            audio_ok = scene.narration_audio_path and os.path.exists(scene.narration_audio_path)
            visual_ok = scene.visual_path and os.path.exists(scene.visual_path)
            if scene.is_locked and audio_ok and visual_ok:
                continue
            if not audio_ok:
                audio_path = f"{settings.output_dir}/audio/{scene.id}.mp3"
                ok = False
                if scene.narration_text and settings.elevenlabs_api_key:
                    ok = _elevenlabs_tts(scene.narration_text, audio_path)
                if not ok and scene.narration_text and settings.openai_api_key:
                    _openai_tts(scene.narration_text, audio_path)
                    ok = os.path.exists(audio_path) and os.path.getsize(audio_path) > 0
                if not ok:
                    est = max(3.0, min(float(settings.max_scene_duration), len(scene.narration_text or "") * 0.06))
                    _silent_audio(audio_path, est)
                    print(f"[TTS] No voice generated for scene {scene.id}, using silent track")
                scene.narration_audio_path = audio_path
            if not visual_ok:
                est_dur = max(3.0, min(float(settings.max_scene_duration),
                                       len(scene.narration_text or "") * 0.06))
                out_base = f"{settings.output_dir}/visuals/{scene.id}"
                path, provider, simulated = generate_scene_visual(
                    scene.visual_prompt or scene.narration_text or "Answers in Faith",
                    out_base, est_dur)
                if simulated:
                    path = out_base + ".png"
                    _generate_placeholder_visual(
                        scene.visual_prompt or scene.narration_text or "Answers in Faith", path)
                    scene.generation_status = "simulated"
                    print(f"[Video] Scene {scene.id} using PLACEHOLDER visual (simulated)")
                else:
                    scene.generation_status = f"done:{provider}"
                    print(f"[Video] Scene {scene.id} visual via {provider}: {path}")
                scene.visual_path = path
            if scene.generation_status != "simulated" and not scene.generation_status.startswith("done:"):
                scene.generation_status = "done"
            db.commit()
        prod = db.query(Production).filter(Production.id == prod_id).first()
        prod.stage = Stage.ASSEMBLY
        db.commit()
        _assemble_video(prod_id, db)
    except Exception as e:
        print(f"Production error: {e}")
        _fail_production(db, prod_id, f"Production error: {e}")
    finally:
        db.close()

def _fail_production(db, prod_id: str, note: str):
    """Never leave a production stuck in production/assembly — send it back for retry."""
    try:
        prod = db.query(Production).filter(Production.id == prod_id).first()
        if prod and prod.stage in (Stage.PRODUCTION, Stage.ASSEMBLY):
            prod.stage = Stage.HUMAN_REVIEW
            db.add(ReviewDecision(
                id=str(uuid.uuid4()), production_id=prod_id, stage="production",
                decision=ReviewStatus.FAIL, reviewer="system", notes=note[:500]))
            db.commit()
            print(f"[Recovery] {prod_id} reset to HUMAN_REVIEW: {note[:200]}")
    except Exception as e2:
        print(f"Recovery failed: {e2}")

def _silent_audio(output_path: str, seconds: float):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i",
           "anullsrc=r=44100:cl=stereo", "-t", str(seconds), output_path]
    subprocess.run(cmd, capture_output=True, timeout=30)

def _elevenlabs_tts(text: str, output_path: str):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
    headers = {"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"}
    payload = {"text": text, "model_id": "eleven_turbo_v2_5",
               "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"[TTS] ElevenLabs ERROR {resp.status_code}: {resp.text[:200]}")
            return False
        with open(output_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        print(f"[TTS] ElevenLabs exception: {e}")
        return False

def _openai_tts(text: str, output_path: str):
    try:
        resp = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={"model": "tts-1", "voice": "alloy", "input": text},
            timeout=30
        )
        if resp.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(resp.content)
        else:
            print(f"[TTS] OpenAI ERROR {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"[TTS] OpenAI exception: {e}")

def _generate_placeholder_visual(prompt: str, output_path: str):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    txt_file = output_path.replace(".png", ".txt")
    with open(txt_file, "w") as f:
        f.write(prompt[:120])
    cmd = [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i",
           "color=c=0x0f172a:s=1280x720:d=1", "-vf",
           f"drawtext=textfile='{txt_file}':fontcolor=white:fontsize=28:x=(w-text_w)/2:y=(h-text_h)/2",
           "-frames:v", "1", output_path]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0 or not os.path.exists(output_path):
        cmd = [settings.ffmpeg_path, "-y", "-f", "lavfi", "-i",
               "color=c=0x0f172a:s=1280x720:d=1", "-frames:v", "1", output_path]
        subprocess.run(cmd, capture_output=True, timeout=30)

def _clip_from_image(image_path: str, audio_path: str, dur: float, clip: str):
    """Ken Burns pan/zoom on a still image — output normalized for concat.
    Memory-safe: zoompan generates ALL frames from a single input frame (d=frames),
    no '-loop 1' — a looped source outruns the encoder, buffers frames unboundedly,
    and gets ffmpeg OOM-killed on small containers (observed: rc=-9 / SIGKILL)."""
    frames = max(1, int(dur * 30))
    vf = (f"scale=1600:900:force_original_aspect_ratio=increase,crop=1600:900,"
          f"zoompan=z='1+0.15*on/{frames}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
          f"s=1280x720:fps=30,format=yuv420p")
    cmd = [
        settings.ffmpeg_path, "-y", "-i", image_path,
        "-i", audio_path, "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-t", str(dur), clip
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=180)
    if result.returncode != 0 or not os.path.exists(clip):
        print(f"[Assembly] Ken Burns render failed, retrying static. Tail: {result.stderr.decode()[-200:]}")
        vf_simple = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=30,format=yuv420p"
        cmd_simple = [
            settings.ffmpeg_path, "-y", "-re", "-loop", "1", "-framerate", "30", "-i", image_path,
            "-i", audio_path, "-vf", vf_simple,
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-t", str(dur), "-shortest", clip
        ]
        result = subprocess.run(cmd_simple, capture_output=True, timeout=180)
    return result

def _clip_from_video(video_path: str, audio_path: str, dur: float, clip: str):
    """Loop/trim an AI-generated clip to narration length — normalized for concat.
    '-re' paces the looped input at realtime so frames can't buffer unboundedly (OOM guard)."""
    vf = "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,fps=30,format=yuv420p"
    cmd = [
        settings.ffmpeg_path, "-y", "-re", "-stream_loop", "-1", "-i", video_path,
        "-i", audio_path, "-map", "0:v", "-map", "1:a", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-t", str(dur), clip
    ]
    return subprocess.run(cmd, capture_output=True, timeout=180)

def _assemble_video(prod_id: str, db: Session):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    scenes = db.query(Scene).filter(Scene.production_id == prod_id).order_by(Scene.order_index).all()
    if not scenes:
        _fail_production(db, prod_id, "No scenes to assemble")
        return

    scene_list = []
    last_clip_error = ""
    for scene in scenes:
        if not scene.narration_audio_path or not os.path.exists(scene.narration_audio_path):
            last_clip_error = f"missing audio file: {scene.narration_audio_path}"
            print(f"[Assembly] Missing audio for scene {scene.id}")
            continue
        if not scene.visual_path or not os.path.exists(scene.visual_path):
            last_clip_error = f"missing visual file: {scene.visual_path}"
            print(f"[Assembly] Missing visual for scene {scene.id}")
            continue
        dur = _get_audio_duration(scene.narration_audio_path)
        if dur <= 0:
            dur = 5.0
        dur = min(dur, float(settings.max_scene_duration))
        clip = f"{settings.output_dir}/final/{scene.id}_clip.mp4"
        try:
            if scene.visual_path.lower().endswith(".mp4"):
                result = _clip_from_video(scene.visual_path, scene.narration_audio_path, dur, clip)
            else:
                result = _clip_from_image(scene.visual_path, scene.narration_audio_path, dur, clip)
            if result.returncode == 0 and os.path.exists(clip):
                scene_list.append(clip)
            else:
                last_clip_error = f"rc={result.returncode} | stderr tail: {result.stderr.decode()[-260:]}"
                print(f"[Assembly] Scene clip failed: {last_clip_error[:200]}")
        except Exception as e:
            last_clip_error = f"{type(e).__name__}: {e}"
            print(f"[Assembly] Scene exception: {e}")

    if not scene_list:
        _fail_production(db, prod_id, f"All scene clips failed to render. Last error: {last_clip_error[:350]}")
        return

    concat_file = f"{settings.output_dir}/final/{prod_id}_concat.txt"
    with open(concat_file, "w") as f:
        for clip in scene_list:
            f.write(f"file '{os.path.abspath(clip)}'\n")
    final_output = f"{settings.output_dir}/final/{prod_id}.mp4"
    cmd = [settings.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
           "-i", concat_file, "-c", "copy", final_output]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0 or not os.path.exists(final_output):
        cmd = [settings.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
               "-i", concat_file, "-c:v", "libx264", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "128k", "-ar", "44100", final_output]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode != 0 or not os.path.exists(final_output):
        _fail_production(db, prod_id, f"Concat failed: {result.stderr.decode()[:300]}")
        return

    print(f"[Assembly] SUCCESS: {final_output}")
    try:
        public_url = upload_video(prod_id, final_output)
        prod.video_url = public_url
        print(f"[R2] Uploaded: {public_url}")
    except Exception as e:
        print(f"[R2] Upload failed (local file kept): {e}")

    try:
        srt_path = _write_captions(prod_id, scenes)
        if srt_path:
            try:
                prod.captions_url = upload_captions(prod_id, srt_path)
                print(f"[R2] Captions uploaded: {prod.captions_url}")
            except Exception as e:
                print(f"[R2] Captions upload failed (local kept): {e}")
    except Exception as e:
        print(f"[Captions] Failed: {e}")

    simulated = any(s.generation_status == "simulated" for s in scenes)
    if simulated:
        prod.status = "simulated"
        db.add(ReviewDecision(
            id=str(uuid.uuid4()), production_id=prod_id, stage="production",
            decision=ReviewStatus.FAIL, reviewer="system",
            notes="One or more scenes used PLACEHOLDER visuals. Quality gate will block PASS until scenes are regenerated with a real provider."))
        print(f"[Assembly] {prod_id} marked SIMULATED — placeholder visuals present")
    else:
        prod.status = "ready"

    prod.stage = Stage.QUALITY_GATE
    db.commit()

def _write_captions(prod_id: str, scenes) -> Optional[str]:
    """Generate SRT captions from scene narration timed to audio durations."""
    def ts(seconds: float) -> str:
        h = int(seconds // 3600); m = int((seconds % 3600) // 60)
        s = int(seconds % 60); ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = []
    idx = 1
    cursor = 0.0
    for scene in scenes:
        text = (scene.narration_text or "").strip()
        if not text:
            continue
        dur = 5.0
        if scene.narration_audio_path and os.path.exists(scene.narration_audio_path):
            d = _get_audio_duration(scene.narration_audio_path)
            if d > 0:
                dur = min(d, float(settings.max_scene_duration))
        words = text.split()
        per = 8
        chunks = [words[i:i + per] for i in range(0, len(words), per)]
        chunk_dur = dur / max(1, len(chunks))
        t = cursor
        for chunk in chunks:
            blocks.append(f"{idx}\n{ts(t)} --> {ts(t + chunk_dur)}\n{' '.join(chunk)}\n")
            idx += 1
            t += chunk_dur
        cursor += dur

    if not blocks:
        return None
    out_dir = f"{settings.output_dir}/captions"
    os.makedirs(out_dir, exist_ok=True)
    srt_path = f"{out_dir}/{prod_id}.srt"
    with open(srt_path, "w") as f:
        f.write("\n".join(blocks))
    print(f"[Captions] Wrote {srt_path} ({idx - 1} blocks)")
    return srt_path

def _get_audio_duration(path: str) -> float:
    import re
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    try:
        result = subprocess.run([settings.ffmpeg_path, "-i", path],
                                capture_output=True, text=True, timeout=10)
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", result.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return 5.0

# ============ END ENGINE ============

@router.post("/productions/{prod_id}/quality")
def quality_gate(prod_id: str, data: ReviewSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.QUALITY_GATE:
        raise HTTPException(400, f"Expected QUALITY_GATE, got {prod.stage.value}")

    if data.decision.lower() == "pass" and prod.status == "simulated":
        db.add(ReviewDecision(
            id=str(uuid.uuid4()), production_id=prod_id, stage="quality_gate",
            decision=ReviewStatus.FAIL, reviewer="system",
            notes="BLOCKED: production contains placeholder (simulated) visuals. Regenerate with a real provider before approval."))
        db.commit()
        raise HTTPException(400, "BLOCKED: this video contains placeholder visuals (status=simulated). "
                                 "Configure REPLICATE_API_TOKEN or OPENAI_API_KEY and re-run produce.")

    decision = ReviewDecision(
        id=str(uuid.uuid4()), production_id=prod_id, stage="quality_gate",
        decision=ReviewStatus(data.decision.lower()), reviewer=data.reviewer, notes=data.notes or ""
    )
    db.add(decision)
    if data.decision.lower() == "pass":
        prod.quality_gate_passed = True
        prod.stage = Stage.PACKAGING
        db.commit()
        return {"id": prod.id, "stage": prod.stage.value, "message": "Quality passed. Submit packaging."}
    else:
        db.commit()
        return {"id": prod.id, "message": "Quality check failed. Repair scenes."}

@router.post("/productions/{prod_id}/packaging")
def submit_packaging(prod_id: str, data: PackagingSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.PACKAGING:
        raise HTTPException(400, f"Expected PACKAGING, got {prod.stage.value}")
    prod.title = data.title
    prod.description = data.description
    prod.keywords = data.keywords
    prod.thumbnail_prompt = data.thumbnail_prompt
    prod.stage = Stage.APPROVAL
    db.commit()
    return {"id": prod.id, "stage": prod.stage.value, "message": "Packaging set. Final approval needed."}

@router.post("/productions/{prod_id}/approve")
def final_approval(prod_id: str, data: ReviewSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.APPROVAL:
        raise HTTPException(400, f"Expected APPROVAL, got {prod.stage.value}")
    if data.decision.lower() != "pass":
        return {"id": prod.id, "message": "Approval denied."}
    if prod.status == "simulated":
        raise HTTPException(400, "BLOCKED: simulated (placeholder) productions cannot be published.")
    prod.stage = Stage.PUBLISHED
    db.commit()
    return {"id": prod.id, "stage": prod.stage.value, "message": "APPROVED. Ready for YouTube upload."}

@router.get("/productions/{prod_id}")
def get_production(prod_id: str, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    claims = db.query(Claim).filter(Claim.production_id == prod_id).all()
    scenes = db.query(Scene).filter(Scene.production_id == prod_id).order_by(Scene.order_index).all()

    video_path = f"./output/final/{prod_id}.mp4"
    has_video = bool(prod.video_url) or os.path.exists(video_path)
    video_url = prod.video_url
    if not video_url and os.path.exists(video_path):
        video_url = f"/api/download/{prod_id}"
    captions_url = getattr(prod, "captions_url", None)
    if not captions_url and os.path.exists(f"./output/captions/{prod_id}.srt"):
        captions_url = f"/api/download-captions/{prod_id}"

    return {
        "id": prod.id, "topic": prod.topic, "stage": prod.stage.value,
        "status": prod.status,
        "simulated": prod.status == "simulated",
        "doctrinal_category": prod.doctrinal_category.value,
        "primary_scripture": prod.primary_scripture,
        "gospel_video": prod.gospel_video,
        "evidence_gate_passed": prod.evidence_gate_passed,
        "human_review_passed": prod.human_review_passed,
        "quality_gate_passed": prod.quality_gate_passed,
        "approved_by": prod.approved_by,
        "requires_manual_review": prod.requires_manual_review,
        "hook": prod.hook,
        "problem": prod.problem,
        "explanation": prod.explanation,
        "story": prod.story,
        "application": prod.application,
        "cta": prod.cta,
        "title": prod.title,
        "description": prod.description,
        "keywords": prod.keywords,
        "thumbnail_prompt": prod.thumbnail_prompt,
        "has_video": has_video,
        "video_url": video_url,
        "captions_url": captions_url,
        "claims": [{"id": c.id, "text": c.claim_text, "status": c.evidence_status.value,
                    "confidence": c.confidence.value, "type": c.claim_type.value,
                    "source_reference": c.source_reference,
                    "source_text": c.source_text,
                    "context": c.context,
                    "interpretation": c.interpretation,
                    "cross_references": c.cross_references,
                    "alternative_interpretations": c.alternative_interpretations,
                    "evidence_notes": c.evidence_notes} for c in claims],
        "scenes": [{"id": s.id, "order": s.order_index, "status": s.generation_status, "locked": s.is_locked,
                    "simulated": s.generation_status == "simulated",
                    "narration_text": s.narration_text, "visual_prompt": s.visual_prompt} for s in scenes]
    }

@router.get("/productions/{prod_id}/decisions")
def list_decisions(prod_id: str, db: Session = Depends(get_db)):
    """Read the audit trail — including system failure notes from production/assembly."""
    ds = db.query(ReviewDecision).filter(ReviewDecision.production_id == prod_id).order_by(ReviewDecision.created_at).all()
    return [{"stage": d.stage, "decision": d.decision.value, "reviewer": d.reviewer,
             "notes": d.notes,
             "created_at": d.created_at.isoformat() if d.created_at else None} for d in ds]

@router.get("/productions")
def list_productions(stage: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Production)
    if stage:
        q = q.filter(Production.stage == stage)
    prods = q.order_by(Production.created_at.desc()).all()
    return [{"id": p.id, "topic": p.topic, "stage": p.stage.value,
             "status": p.status,
             "doctrinal_category": p.doctrinal_category.value,
             "primary_scripture": p.primary_scripture,
             "created_at": p.created_at} for p in prods]

@router.delete("/productions/{prod_id}")
def delete_production(prod_id: str, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    db.query(Claim).filter(Claim.production_id == prod_id).delete()
    db.query(Scene).filter(Scene.production_id == prod_id).delete()
    db.query(ReviewDecision).filter(ReviewDecision.production_id == prod_id).delete()
    db.delete(prod)
    db.commit()
    return {"id": prod_id, "message": "Deleted"}

# ============ STARTUP RECOVERY ============
def _recover_stuck_on_boot():
    """Redeploys kill in-flight background tasks — reset stuck productions on boot."""
    try:
        engine = get_engine(settings.database_url)
        db = SessionLocal(bind=engine)
        stuck = db.query(Production).filter(Production.stage.in_([Stage.PRODUCTION, Stage.ASSEMBLY])).all()
        for prod in stuck:
            prod.stage = Stage.HUMAN_REVIEW
            db.add(ReviewDecision(
                id=str(uuid.uuid4()), production_id=prod.id, stage="recovery",
                decision=ReviewStatus.FAIL, reviewer="system",
                notes="Reset on server restart — background task was interrupted. Re-run Start Production."))
        if stuck:
            db.commit()
            print(f"[Recovery] Reset {len(stuck)} stuck production(s) to HUMAN_REVIEW")
        db.close()
    except Exception as e:
        print(f"[Recovery] Startup recovery error: {e}")

_recover_stuck_on_boot()
