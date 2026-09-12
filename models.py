# models.py
import os
from sqlalchemy import create_engine, Column, String, Text, DateTime, Boolean, ForeignKey, Integer, JSON, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
import enum
import uuid
from datetime import datetime

Base = declarative_base()

class Stage(enum.Enum):
    DISCOVERY = "discovery"
    RESEARCH = "research"
    SCRIPT = "script"
    EVIDENCE_GATE = "evidence_gate"
    HUMAN_REVIEW = "human_review"
    PRODUCTION = "production"
    ASSEMBLY = "assembly"
    QUALITY_GATE = "quality_gate"
    PACKAGING = "packaging"
    APPROVAL = "approval"
    PUBLISHED = "published"
    ANALYTICS = "analytics"

class Confidence(enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class ReviewStatus(enum.Enum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    REPAIR = "repair"

class ClaimType(enum.Enum):
    SCRIPTURE = "scripture"
    STRONG_INFERENCE = "strong_inference"
    TRADITIONAL = "traditional_interpretation"
    SCHOLARLY = "scholarly_opinion"
    SPECULATION = "speculation"

class DoctrinalCategory(enum.Enum):
    GENERAL = "general"
    GENESIS_6 = "genesis_6_nephilim"
    SHEOL_AFTERLIFE = "sheol_afterlife"
    SPIRITUAL_WARFARE = "spiritual_warfare"
    DEMONS = "demons"
    ELECTION = "election_predestination"
    END_TIMES = "end_times_prophecy"
    DIVORCE_REMARRIAGE = "divorce_remarriage"
    WOMEN_MINISTRY = "women_in_ministry"
    SALVATION = "salvation_gospel"
    CHARACTER_OF_GOD = "character_of_god"
    PROPHECY_DATING = "prophecy_date_setting"

class Production(Base):
    __tablename__ = "productions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    topic = Column(Text, nullable=False)
    source_question = Column(Text)
    stage = Column(Enum(Stage), default=Stage.DISCOVERY)
    status = Column(String, default="active")

    hook = Column(Text)
    problem = Column(Text)
    explanation = Column(Text)
    story = Column(Text)
    application = Column(Text)
    cta = Column(Text)

    title = Column(Text)
    thumbnail_prompt = Column(Text)
    description = Column(Text)
    keywords = Column(Text)
    youtube_video_id = Column(String)

    # Per-production format controls (long-form build)
    # video_format: "short" (single scene, ~30s) or "episode" (multi-scene, ~4-8 min)
    # orientation: "vertical" (9:16 TikTok/Shorts) or "landscape" (16:9 YouTube)
    #   None means "fall back to the server-wide VIDEO_ASPECT_RATIO env var"
    # scene_count: target number of scenes for episode auto-script (5-10)
    video_format = Column(String, default="short")
    orientation = Column(String)
    scene_count = Column(Integer)

    # Art-form controls
    # visual_style: "cinematic" (dark scholarly look) or "animated" (3D Pixar-style);
    #   None = cinematic default
    # burn_captions: burn big word-by-word captions into the video (Shorts style)
    visual_style = Column(String)
    burn_captions = Column(Boolean, default=False)

    # Theological metadata
    primary_scripture = Column(String)
    doctrinal_category = Column(Enum(DoctrinalCategory), default=DoctrinalCategory.GENERAL)
    alternative_views_reviewed = Column(Boolean, default=False)
    requires_manual_review = Column(Boolean, default=False)
    gospel_video = Column(Boolean, default=False)
    supporting_passages = Column(JSON, default=list)

    # Review chain
    evidence_gate_passed = Column(Boolean, default=False)
    human_review_passed = Column(Boolean, default=False)
    quality_gate_passed = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_by = Column(String)
    approved_at = Column(DateTime)
    video_url = Column(String)
    captions_url = Column(String)
    claims = relationship("Claim", back_populates="production", cascade="all, delete-orphan")
    scenes = relationship("Scene", back_populates="production", cascade="all, delete-orphan")
    reviews = relationship("ReviewDecision", back_populates="production", cascade="all, delete-orphan")

class Claim(Base):
    __tablename__ = "claims"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    production_id = Column(String, ForeignKey("productions.id"))

    claim_text = Column(Text, nullable=False)
    source_reference = Column(Text)
    source_text = Column(Text)
    original_language = Column(String)
    context = Column(Text)
    interpretation = Column(Text)
    confidence = Column(Enum(Confidence), default=Confidence.MEDIUM)
    alternative_interpretations = Column(Text)
    claim_type = Column(Enum(ClaimType), default=ClaimType.SPECULATION)
    cross_references = Column(JSON, default=list)
    character_of_god_relevant = Column(Boolean, default=False)
    gospel_relevant = Column(Boolean, default=False)

    evidence_status = Column(Enum(ReviewStatus), default=ReviewStatus.PENDING)
    evidence_notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    production = relationship("Production", back_populates="claims")

class Scene(Base):
    __tablename__ = "scenes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    production_id = Column(String, ForeignKey("productions.id"))

    order_index = Column(Integer, nullable=False)
    narration_text = Column(Text)
    narration_audio_path = Column(String)
    visual_prompt = Column(Text)
    visual_path = Column(String)
    duration_seconds = Column(Integer, default=5)
    claim_ids = Column(JSON, default=list)
    is_locked = Column(Boolean, default=False)
    generation_status = Column(String, default="pending")

    production = relationship("Production", back_populates="scenes")

class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    production_id = Column(String, ForeignKey("productions.id"))
    stage = Column(String, nullable=False)
    decision = Column(Enum(ReviewStatus), nullable=False)
    reviewer = Column(String, nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    production = relationship("Production", back_populates="reviews")

class SketchEpisode(Base):
    """Faith vs Views sketch pipeline (/sketch/*) — separate from the Answers in
    Faith productions table. spec holds the full episode JSON verbatim;
    voice_map persists the voices locked on first generation."""
    __tablename__ = "sketch_episodes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    episode_id = Column(String, unique=True, index=True)
    title = Column(Text)
    claim = Column(Text)
    status = Column(String, default="SCRIPT_READY")  # SCRIPT_READY | generating | done | failed
    progress = Column(String, default="")
    error = Column(Text)
    spec = Column(JSON, default=dict)
    voice_map = Column(JSON, default=dict)
    video_url = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

def get_engine(db_url=None):
    if db_url is None:
        db_url = os.getenv("DATABASE_URL", "sqlite:///./answers_in_faith.db")
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    return create_engine(db_url, connect_args=connect_args)

def init_db(engine):
    Base.metadata.create_all(bind=engine)
    # Migrations: add columns to the EXISTING productions table if missing.
    # create_all() never alters existing tables, so without this the new
    # long-form columns would 500 every request against the old Postgres table.
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    if 'productions' in inspector.get_table_names():
        columns = [c['name'] for c in inspector.get_columns('productions')]
        migrations = [
            ('video_url', 'VARCHAR'),
            ('captions_url', 'VARCHAR'),
            ('video_format', 'VARCHAR'),
            ('orientation', 'VARCHAR'),
            ('scene_count', 'INTEGER'),
            ('visual_style', 'VARCHAR'),
            ('burn_captions', 'BOOLEAN'),
        ]
        for col, col_type in migrations:
            if col not in columns:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE productions ADD COLUMN {col} {col_type}"))
                    conn.commit()
                print(f"[Migration] Added column productions.{col}")

SessionLocal = sessionmaker(autocommit=False, autoflush=False)
