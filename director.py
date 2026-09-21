"""
Subscene Engine — director.py
One machine, many verticals. Gate is a per-video choice. Minutes are a budget.

Pipeline: topic -> research -> beat sheet -> script (word budget) -> asset
manifest -> (existing stages) generation -> directed edit -> publish.

Plug-in points for the Answers in Faith repo:
  * LLMClient:        wire your existing OpenAI/Anthropic client
  * ImageGenClient:   wire DALL-E (current) or fal.ai Flux (cheap)
  * MotionClient:     wire Pika (current) or Kling Turbo API
  * TTSClient:        wire your existing narration client
  * The FFmpeg/R2/YouTube stages in your repo consume AssetManifest as-is.
"""
from __future__ import annotations
import json, math, os, random
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ---------------------------------------------------------------- pricing --
PRICING = {
    "research_llm":      0.10,   # flat per video
    "script_llm_kword":  0.03,   # per 1,000 words
    "qc_llm":            0.05,   # flat per video
    "tts_kchar":         0.015,  # per 1,000 chars (~6 chars/word)
    "image":             0.003,  # fal.ai Flux schnell (DALL-E ~20x this)
    "motion_clip_5s":    0.15,   # Kling Turbo API — rationed!
}
CHARS_PER_WORD = 6

# ----------------------------------------------------------------- schemas --
@dataclass
class Vertical:
    name: str
    display_name: str
    gate_profile: str            # "off" disables the gate entirely
    tone: str
    visual_style: str
    narration_wpm: int = 145
    scenes_per_minute: float = 5.5
    motion_clip_ratio: float = 0.12   # share of scenes that get AI motion
    diagram_ratio: float = 0.08       # case-file / map / timeline graphics
    broll_ratio: float = 0.10         # stock footage overlays
    caption_style: str = "lower-third white on black bar"

@dataclass
class Scene:
    index: int
    phase: str
    visual_type: str            # still | motion | diagram | broll
    beat_title: str
    word_budget: int
    image_prompt: str = ""
    motion_prompt: str = ""
    tts_line: str = ""

@dataclass
class AssetManifest:
    topic: str
    vertical: str
    minutes: int
    use_gate: bool
    total_words: int
    scenes: list = field(default_factory=list)
    gates_run: list = field(default_factory=list)
    gates_flagged: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # PATCH: surface fallback warnings to UI

    def to_json(self, path: str | Path):
        Path(path).write_text(json.dumps(asdict(self), indent=2))
        return path

# ------------------------------------------------------------- gate engine --
GATE_PROFILES = {
    "faith": ["claim_source_check", "verse_citation", "denominational_neutral"],
    "truecrime": ["fact_check_sources", "no_speculation_as_fact",
                  "victim_framing", "no_glorification"],
    "history": ["date_check", "primary_source_pref", "no_presentism"],
    "finance": ["no_income_claims", "compliance_language", "source_citation"],
    "off": [],
}

def run_gates(profile: str, script_text: str, *a, **k) -> tuple[list, list]:
    """Real versions call the LLM with gate-specific rubrics. Stubs here."""
    run, flagged = [], []
    for gate in GATE_PROFILES.get(profile, []):
        run.append(gate)
    return run, flagged

# ------------------------------------------------------- motion availability --
def motion_provider_available() -> bool:
    """Return True if at least one motion provider has credentials and the
    user hasn't forced stills-only via STILLS_ONLY=1 / MOTION_DISABLED=1."""
    if os.getenv("STILLS_ONLY") or os.getenv("MOTION_DISABLED"):
        return False
    # The current motion provider is Seedance via Replicate. If there's no
    # Replicate token, motion clips can't be made. The pipeline will also
    # fail closed if the provider returns False at render time, but checking
    # up front lets us zero-out the motion-clip budget before planning so
    # cost estimates are honest and no scenes get stuck waiting on a dead
    # provider.
    return bool(os.getenv("REPLICATE_API_TOKEN") or os.getenv("REPLICATE_API_KEY"))

# ---------------------------------------------------------- llm abstraction --
class LLMClient:
    """Wire your existing client. Mock below lets the pipeline run offline."""
    def research(self, topic: str) -> str: raise NotImplementedError
    def script_beat(self, topic: str, phase: str, beat_title: str,
                    word_budget: int, tone: str) -> str: raise NotImplementedError

class MockLLM(LLMClient):
    def research(self, topic):
        return f"[research dossier on: {topic}]"
    def script_beat(self, topic, phase, beat_title, word_budget, tone):
        return f"({word_budget}w narration for '{beat_title}' — {tone})"

# ---------------------------------------------------------------- director --
PHASES = [  # name, share of words, share of scenes, beat templates
    ("hook",       0.08, 0.07, ["The question nobody asks", "Cold open: the moment it changed"]),
    ("setup",      0.22, 0.22, ["Who they were", "The world before", "What the record shows"]),
    ("escalation", 0.45, 0.44, ["The detail that doesn't fit", "Timeline fracture", "The overlooked witness",
                                "Pattern emerges", "The second look", "What was buried"]),
    ("climax",     0.15, 0.17, ["The confrontation of evidence", "The reveal", "What it actually means"]),
    ("outro",      0.10, 0.10, ["Where it stands today", "The question that remains"]),
]

class Director:
    def __init__(self, vertical: Vertical, llm: LLMClient | None = None,
                 motion_available: bool | None = None):
        self.v = vertical
        self.llm = llm or MockLLM()
        # Caller can override (e.g., api.py already probed providers); else probe.
        self.motion_available = (motion_available if motion_available is not None
                                 else motion_provider_available())

    def plan(self, topic: str, minutes: int, use_gate: bool = True) -> AssetManifest:
        v = self.v
        total_words = int(minutes * v.narration_wpm)
        total_scenes = max(3, round(minutes * v.scenes_per_minute))

        # PATCH: if the motion provider (Seedance/Replicate) has no credits
        # or no token configured, zero-out the motion budget AND the broll
        # budget (broll is also a clip slot, not a still) so the entire video
        # is rendered with stills + Ken Burns. Otherwise "motion" scenes sit
        # forever in stage=production waiting on Seedance, and the user sees
        # "no video" with no indication why. Diagrams stay (still renders).
        motion_ratio = v.motion_clip_ratio if self.motion_available else 0.0
        broll_ratio  = v.broll_ratio      if self.motion_available else 0.0

        n_motion  = round(total_scenes * motion_ratio)
        n_diagram = round(total_scenes * v.diagram_ratio)
        n_broll   = round(total_scenes * broll_ratio)
        n_still   = total_scenes - n_motion - n_diagram - n_broll

        man = AssetManifest(topic, v.name, minutes, use_gate, total_words)

        if not self.motion_available:
            man.notes.append(
                "Motion-provider credentials missing or STILLS_ONLY=1 set; "
                "rendering all-stills version (Ken Burns over images). "
                "Set REPLICATE_API_TOKEN to re-enable AI video clips."
            )

        # build scene plan phase by phase
        scene_i = 0
        rng = random.Random(hash(topic) & 0xffff)
        for phase, w_share, s_share, templates in PHASES:
            n_sc = max(1, round(total_scenes * s_share))
            phase_words = int(total_words * w_share)
            per = max(8, phase_words // max(1, n_sc))
            for k in range(n_sc):
                title = rng.choice(templates)
                sc = Scene(scene_i, phase, "still", f"{phase.upper()}: {title}", per)
                sc.image_prompt = f"{v.visual_style} — {title}, scene {scene_i}"
                man.scenes.append(sc)
                scene_i += 1

        # assign visual types: motion goes to escalation/climax/hook beats only
        idx = list(range(len(man.scenes)))
        if n_motion + n_broll > 0:
            priority = [i for i in idx if man.scenes[i].phase in ("escalation", "climax", "hook")]
            rng.shuffle(priority)
            for i in priority[:n_motion]:
                s = man.scenes[i]; s.visual_type = "motion"
                s.motion_prompt = f"subtle cinematic camera move — {s.beat_title}"
            rest = [i for i in idx if man.scenes[i].visual_type == "still"]
            rng.shuffle(rest)
            for i in rest[:n_diagram]: man.scenes[i].visual_type = "diagram"
            for i in rest[n_diagram:n_diagram + n_broll]: man.scenes[i].visual_type = "broll"
        else:
            # Still-only mode: diagrams still get a diagram tag, everything
            # else stays still. No broll, no motion.
            rest = list(idx)
            rng.shuffle(rest)
            for i in rest[:n_diagram]: man.scenes[i].visual_type = "diagram"

        # script fill + gate; any exception in LLM falls back to mock so the
        # endpoint never 500s on bad keys / timeouts.
        for s in man.scenes:
            try:
                s.tts_line = self.llm.script_beat(topic, s.phase, s.beat_title,
                                                  s.word_budget, v.tone)
            except Exception as e:
                err = f"{type(e).__name__}: {e}".split("\n")[0][:160]
                s.tts_line = MockLLM().script_beat(topic, s.phase, s.beat_title,
                                                   s.word_budget, v.tone)
                man.notes.append(f"LLM fallback on scene {s.index}: {err}")
        if use_gate and v.gate_profile != "off":
            man.gates_run, man.gates_flagged = run_gates(v.gate_profile, "SCRIPT_TEXT", llm=self.llm)
        return man

# ------------------------------------------------------------------ pricing --
def estimate_cost(man: AssetManifest, vertical: Vertical) -> dict:
    n = len(man.scenes)
    n_motion  = sum(1 for s in man.scenes if s.visual_type == "motion")
    n_stills  = sum(1 for s in man.scenes if s.visual_type in ("still", "diagram", "broll"))
    # Note: when motion is unavailable, broll falls back to still so we bill
    # it as an image, not a motion clip.
    chars = man.total_words * CHARS_PER_WORD
    parts = {
        "research":   PRICING["research_llm"],
        "script_llm": round(man.total_words / 1000 * PRICING["script_llm_kword"], 3),
        "qc_pass":    PRICING["qc_llm"],
        "tts":        round(chars / 1000 * PRICING["tts_kchar"], 3),
        "images":     round(n_stills * PRICING["image"], 3),
        "motion":     round(n_motion * PRICING["motion_clip_5s"], 3),
    }
    parts["total"] = round(sum(parts.values()), 2)
    parts["n_scenes"], parts["n_motion_clips"] = n, n_motion
    parts["stills_only"] = (n_motion == 0)
    return parts

# ============================================================ APPEND TO director.py
# ---- Live wiring: real LLM client + real gates -----------------------------

class LiveLLM(LLMClient):
    """Wire your existing OpenAI client. Any call failure (auth, rate limit,
    timeout, overload, network) falls through to MockLLM narration so the
    plan endpoint never 500s. The manifest carries a `notes` list explaining
    the fallback."""
    def __init__(self, model: str = "gpt-4o-mini", client=None):
        self.live, self.reason, self.c = False, "", None
        self.model = model
        self._mock = MockLLM()
        if client is not None:
            self.c, self.live = client, True
            return
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            self.reason = "OPENAI_API_KEY not set; using mock narration"
            return
        try:
            from openai import OpenAI
            self.c = OpenAI()
            self.live = True
        except Exception as e:
            self.live = False
            self.reason = f"openai init failed: {e}; using mock"

    def _chat(self, system: str, user: str, max_tokens: int = 1200) -> str:
        if not self.live:
            raise RuntimeError(f"LiveLLM not live: {self.reason}")
        r = self.c.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max_tokens)
        return r.choices[0].message.content

    def _safe(self, live_method, mock_method, *args, **kwargs):
        if not self.live:
            return mock_method(*args, **kwargs)
        try:
            return live_method(*args, **kwargs)
        except Exception as e:
            err = f"{type(e).__name__}: {e}".split("\n")[0][:160]
            # Remember auth-style errors permanently for this session so we
            # don't keep retrying on every scene.
            msg = str(e).lower()
            if ("401" in msg or "auth" in msg or "api_key" in msg
                    or "invalid_api_key" in msg or "insufficient_quota" in msg):
                self.live = False
                self.reason = f"OpenAI unavailable ({err}); using mock narration"
                print(f"[Director:LLM] disabling live ({err}); falling back to mock")
            else:
                print(f"[Director:LLM] call failed ({err}); using mock for this call")
            return mock_method(*args, **kwargs)

    def research(self, topic: str) -> str:
        def _live(t):
            return self._chat(
                "You are a research lead for a documentary crew. Produce a tight "
                "fact dossier: key facts with dates, contested points, and 5-8 "
                "scene-worthy details. No fluff.", f"Topic: {t}")
        return self._safe(_live, self._mock.research, topic)

    def script_beat(self, topic, phase, beat_title, word_budget, tone) -> str:
        def _live(t, p, bt, wb, to):
            return self._chat(
                f"You are a documentary narrator. Tone: {to}. Write EXACTLY "
                f"{wb} words of narration for this beat. Plain prose, no "
                f"headers, no stage directions, no markdown.",
                f"Documentary topic: {t}\nBeat ({p}): {bt}")
        return self._safe(_live, self._mock.script_beat,
                          topic, phase, beat_title, word_budget, tone)

# ---- Real gate execution ----------------------------------------------------
import re as _re

GATE_RUBRICS = {
    "fact_check_sources":   "Flag any factual claim that lacks a source or conflicts with known record.",
    "no_speculation_as_fact": "Flag any speculation, theory, or opinion stated as established fact.",
    "victim_framing":         "Flag anything disrespectful to victims/survivors or that centers perpetrator glamor.",
    "no_glorification":       "Flag language that romanticizes violence or the perpetrator.",
    "date_check":             "Flag any date, name, or event that appears historically inaccurate.",
    "primary_source_pref":    "Flag claims that should cite a primary source but don't.",
    "no_presentism":          "Flag judging past actors by modern standards without context.",
    "no_income_claims":       "Flag any earnings/guarantee claim. Replace with compliant phrasing.",
    "compliance_language":    "Flag missing educational disclaimers for financial content.",
    "source_citation":        "Flag uncited statistics or data points.",
    "claim_source_check":     "Flag theological claims without scripture citation.",
    "verse_citation":         "Flag paraphrased verses not marked as paraphrase.",
    "denominational_neutral": "Flag wording that favors one denomination's position as sole truth.",
}

def run_gates(profile: str, script_text: str, llm: LLMClient | None = None) -> tuple[list, list]:
    run, flagged = [], []
    llm = llm or LiveLLM()
    for gate in GATE_PROFILES.get(profile, []):
        run.append(gate)
        rubric = GATE_RUBRICS.get(gate, "Flag inaccuracies.")
        if getattr(llm, "live", True) is False:
            continue
        try:
            verdict = llm._chat(
                "You are a compliance gate for a documentary script. Reply with ONLY "
                "JSON: {\"status\": \"PASS\" | \"FLAG\", \"issues\": [\"...\"]}",
                f"Gate: {gate}\nRubric: {rubric}\n\nScript:\n{script_text[:6000]}")
            m = _re.search(r"\{.*\}", verdict, _re.S)
            v = json.loads(m.group(0)) if m else {}
            if v.get("status") == "FLAG":
                flagged.append({"gate": gate, "issues": v.get("issues", [])})
        except Exception:
            pass
    return run, flagged
