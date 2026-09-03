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

from models import Production, Claim, Scene, ReviewDecision, Stage, ReviewStatus, Confidence, ClaimType, DoctrinalCategory, get_engine, SessionLocal
from config import settings
from theology_gate import run_theology_gate

router = APIRouter()

def get_db():
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    try:
        yield db
    finally:
        db.close()

# Schemas
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

# ─── STAGE 1: CREATE ───
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

# ─── STAGE 2: RESEARCH ───
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

# ─── STAGE 3: SCRIPT ───
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

# ─── STAGE 4: EVIDENCE GATE ───
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

    if not result.passed:
        decision = ReviewDecision(
            id=str(uuid.uuid4()), production_id=prod_id, stage="evidence_gate",
            decision=ReviewStatus.FAIL, reviewer="system",
            notes=f"{len(result.violations)} violations, {len(result.warnings)} warnings"
        )
        db.add(decision)
        db.commit()
        return {"id": prod.id, "stage": "evidence_gate", "decision": "FAIL",
                "violations": result.violations, "warnings": result.warnings,
                "message": "Theological gate FAILED. Repair and resubmit."}

    for claim in claims:
        claim.evidence_status = ReviewStatus.PASS
        claim.evidence_notes = "Passed all 12 theological blockers"

    prod.evidence_gate_passed = True
    prod.requires_manual_review = result.requires_manual

    decision = ReviewDecision(
        id=str(uuid.uuid4()), production_id=prod_id, stage="evidence_gate",
        decision=ReviewStatus.PASS, reviewer="system",
        notes=f"Passed. Manual review required: {result.requires_manual}"
    )
    db.add(decision)
    prod.stage = Stage.EVIDENCE_GATE
    db.commit()

    return {"id": prod.id, "stage": prod.stage.value, "decision": "PASS",
            "manual_review_required": result.requires_manual,
            "message": "Evidence gate passed. Awaiting human review (DAVID APPROVES)."}

# ─── STAGE 5: HUMAN REVIEW ───
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
        prod.stage = Stage.HUMAN_REVIEW
        prod.approved_by = data.reviewer
        prod.approved_at = datetime.utcnow()
        prod.human_review_passed = True
        db.commit()
        return {"id": prod.id, "stage": prod.stage.value, "message": "APPROVED. Ready for production."}
    else:
        db.commit()
        return {"id": prod.id, "stage": "evidence_gate", "message": f"Review: {data.decision.upper()}. Repair required."}

# ─── STAGE 6: PRODUCE (TTS + Visuals) ───
@router.post("/productions/{prod_id}/produce")
def produce(prod_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.HUMAN_REVIEW:
        raise HTTPException(400, f"Expected HUMAN_REVIEW, got {prod.stage.value}")

    failed = db.query(Claim).filter(Claim.production_id == prod_id, Claim.evidence_status != ReviewStatus.PASS).count()
    if failed > 0:
        raise HTTPException(400, f"{failed} claims have not passed evidence gate")

    prod.stage = Stage.PRODUCTION
    db.commit()
    background_tasks.add_task(_produce_scenes, prod_id)
    return {"id": prod.id, "stage": prod.stage.value, "message": "Production started in background."}

def _produce_scenes(prod_id: str):
    engine = get_engine(settings.database_url)
    db = SessionLocal(bind=engine)
    try:
        scenes = db.query(Scene).filter(Scene.production_id == prod_id).order_by(Scene.order_index).all()
        for scene in scenes:
            if scene.is_locked and scene.narration_audio_path and scene.visual_path:
                continue
            if settings.elevenlabs_api_key and scene.narration_text:
                audio_path = f"{settings.output_dir}/audio/{scene.id}.mp3"
                _elevenlabs_tts(scene.narration_text, audio_path)
                scene.narration_audio_path = audio_path
            elif settings.openai_api_key and scene.narration_text:
                audio_path = f"{settings.output_dir}/audio/{scene.id}.mp3"
                _openai_tts(scene.narration_text, audio_path)
                scene.narration_audio_path = audio_path
            if scene.visual_prompt:
                visual_path = f"{settings.output_dir}/visuals/{scene.id}.png"
                _generate_placeholder_visual(scene.visual_prompt, visual_path)
                scene.visual_path = visual_path
            scene.generation_status = "done"
            db.commit()
        prod = db.query(Production).filter(Production.id == prod_id).first()
        prod.stage = Stage.ASSEMBLY
        db.commit()
        _assemble_video(prod_id, db)
    except Exception as e:
        print(f"Production error: {e}")
    finally:
        db.close()

def _elevenlabs_tts(text: str, output_path: str):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
    headers = {"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"}
    payload = {"text": text, "model_id": "eleven_monolingual_v1",
               "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}}
    resp = requests.post(url, json=payload, headers=headers)
    with open(output_path, "wb") as f:
        f.write(resp.content)

def _openai_tts(text: str, output_path: str):
    import openai
    client = openai.OpenAI(api_key=settings.openai_api_key)
    response = client.audio.speech.create(model="tts-1", voice="alloy", input=text)
    response.stream_to_file(output_path)

def _generate_placeholder_visual(prompt: str, output_path: str):
    safe = prompt.replace("'", "").replace('"', '')[:100]
    cmd = [settings.ffmpeg_path, "-f", "lavfi", "-i", "color=c=darkblue:s=1280x720:d=5",
           "-vf", f"drawtext=text='{safe}':fontcolor=white:fontsize=24:x=(w-text_w)/2:y=(h-text_h)/2",
           "-frames:v", "1", output_path, "-y"]
    subprocess.run(cmd, capture_output=True)

# ─── STAGE 7: ASSEMBLY ───
def _assemble_video(prod_id: str, db: Session):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    scenes = db.query(Scene).filter(Scene.production_id == prod_id).order_by(Scene.order_index).all()
    if not scenes:
        return
    concat_file = f"{settings.output_dir}/final/{prod_id}_concat.txt"
    scene_list = []
    for scene in scenes:
        if not scene.narration_audio_path or not scene.visual_path:
            continue
        dur = _get_audio_duration(scene.narration_audio_path)
        clip = f"{settings.output_dir}/final/{scene.id}_clip.mp4"
        cmd = [settings.ffmpeg_path, "-loop", "1", "-i", scene.visual_path,
               "-i", scene.narration_audio_path, "-c:v", "libx264", "-tune", "stillimage",
               "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-t", str(dur), "-y", clip]
        subprocess.run(cmd, capture_output=True)
        scene_list.append(clip)
    if not scene_list:
        return
    with open(concat_file, "w") as f:
        for clip in scene_list:
            f.write(f"file '{os.path.abspath(clip)}'\n")
    final_output = f"{settings.output_dir}/final/{prod_id}.mp4"
    cmd = [settings.ffmpeg_path, "-f", "concat", "-safe", "0", "-i", concat_file, "-c", "copy", "-y", final_output]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        prod.stage = Stage.QUALITY_GATE
        db.commit()

def _get_audio_duration(path: str) -> float:
    cmd = [settings.ffmpeg_path, "-i", path, "-show_entries", "format=duration",
           "-v", "quiet", "-of", "csv=p=0"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except:
        return 5.0

# ─── STAGE 8: QUALITY GATE ───
@router.post("/productions/{prod_id}/quality")
def quality_gate(prod_id: str, data: ReviewSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.QUALITY_GATE:
        raise HTTPException(400, f"Expected QUALITY_GATE, got {prod.stage.value}")
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

# ─── STAGE 9: PACKAGING ───
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

# ─── STAGE 10: FINAL APPROVAL ───
@router.post("/productions/{prod_id}/approve")
def final_approval(prod_id: str, data: ReviewSubmit, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    if prod.stage != Stage.APPROVAL:
        raise HTTPException(400, f"Expected APPROVAL, got {prod.stage.value}")
    if data.decision.lower() != "pass":
        return {"id": prod.id, "message": "Approval denied."}
    prod.stage = Stage.PUBLISHED
    db.commit()
    return {"id": prod.id, "stage": prod.stage.value, "message": "APPROVED. Ready for YouTube upload."}

# ─── GET STATUS ───
@router.get("/productions/{prod_id}")
def get_production(prod_id: str, db: Session = Depends(get_db)):
    prod = db.query(Production).filter(Production.id == prod_id).first()
    if not prod:
        raise HTTPException(404, "Production not found")
    claims = db.query(Claim).filter(Claim.production_id == prod_id).all()
    scenes = db.query(Scene).filter(Scene.production_id == prod_id).order_by(Scene.order_index).all()
    return {
        "id": prod.id, "topic": prod.topic, "stage": prod.stage.value,
        "doctrinal_category": prod.doctrinal_category.value,
        "primary_scripture": prod.primary_scripture,
        "gospel_video": prod.gospel_video,
        "evidence_gate_passed": prod.evidence_gate_passed,
        "human_review_passed": prod.human_review_passed,
        "quality_gate_passed": prod.quality_gate_passed,
        "approved_by": prod.approved_by,
        "claims": [{"id": c.id, "text": c.claim_text, "status": c.evidence_status.value,
                    "confidence": c.confidence.value, "type": c.claim_type.value} for c in claims],
        "scenes": [{"id": s.id, "order": s.order_index, "status": s.generation_status, "locked": s.is_locked} for s in scenes]
    }

@router.get("/productions")
def list_productions(stage: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(Production)
    if stage:
        q = q.filter(Production.stage == stage)
    prods = q.order_by(Production.created_at.desc()).all()
    return [{"id": p.id, "topic": p.topic, "stage": p.stage.value,
             "doctrinal_category": p.doctrinal_category.value,
             "primary_scripture": p.primary_scripture,
             "created_at": p.created_at} for p in prods]
