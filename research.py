# research.py — AI-assisted research drafting
# Drafts the 6 research fields + theme suggestions + claim/scene drafts via OpenAI.
# David still reviews everything: this only PRE-FILLS, the 12-blocker gate is untouched.
import json
import requests
from config import settings

MODEL = "gpt-4o-mini"

VALID_CATEGORIES = [
    "general", "genesis_6", "sheol", "spiritual_warfare", "demons",
    "election", "end_times", "divorce", "women_ministry", "salvation",
    "character_of_god", "prophecy_dating",
]

# Claim-type guidance shared by both script prompts.
# BLOCKER_4 in the evidence gate HARD-FAILS any claim typed "speculation",
# so the model must never emit it — hedge and use "scholarly" instead.
CLAIM_TYPE_GUIDANCE = """claim_type rules (IMPORTANT):
- NEVER use "speculation" — the evidence gate automatically fails that type.
  If a claim is uncertain, type it "scholarly" and hedge the wording
  (e.g. "Some scholars believe ... may have ...", "Scripture does not say for certain").
- "scripture" = the text directly states it. "strong_inference" = clearly implied.
  Both REQUIRE at least 1 cross_reference or the gate fails.
- "traditional" = common church teaching. "scholarly" = academic/historical opinion."""

PROMPT_TEMPLATE = """You are a research assistant for "Answers in Faith", a YouTube channel that answers
Bible questions with theological rigor. The channel enforces 12 blockers:
- every claim needs scripture support and historical/literary context
- no "this word only means" without lexical evidence
- speculation must be labeled, never asserted as fact
- no date-setting, no Antichrist identification
- always present alternative interpretations honestly

Draft RESEARCH (not a script) for this video:

Topic: {topic}
Source question: {source_question}
Primary scripture: {primary_scripture}
Category: {category}
Gospel video: {gospel}

Fill exactly these 9 fields:
1. hook — gripping opening that raises the question honestly (2-5 sentences, plain text)
2. problem — why this confuses people / what's at stake (2-5 sentences)
3. explanation — what the text actually says, with context and original-language notes where relevant; mention the main alternative interpretation where one exists (2-5 sentences)
4. story — a relatable real-life illustration (2-5 sentences)
5. application — what the viewer should do with this (2-5 sentences)
6. cta — call to action inviting comments with the viewer's questions (1-2 sentences)
7. suggested_title — a compelling but honest video title/theme (10 words max, no clickbait)
8. suggested_scripture — the single best primary passage to anchor this video (e.g., "Genesis 6:1-4")
9. suggested_category — EXACTLY one of: general, genesis_6, sheol, spiritual_warfare, demons, election, end_times, divorce, women_ministry, salvation, character_of_god, prophecy_dating

Return ONLY a JSON object with keys: hook, problem, explanation, story, application, cta, suggested_title, suggested_scripture, suggested_category"""


def auto_research(topic, source_question, primary_scripture, category, gospel_video):
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured on the server.")
    prompt = PROMPT_TEMPLATE.format(
        topic=topic or "(not specified)",
        source_question=source_question or "(not specified)",
        primary_scripture=primary_scripture or "(not specified)",
        category=category or "general",
        gospel=gospel_video,
    )
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a careful biblical research assistant. Output only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
        },
        timeout=90,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
    content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    keys = ("hook", "problem", "explanation", "story", "application", "cta",
            "suggested_title", "suggested_scripture", "suggested_category")
    out = {k: str(data.get(k, "")).strip() for k in keys}
    if out["suggested_category"] not in VALID_CATEGORIES:
        out["suggested_category"] = "general"
    return out


SCRIPT_PROMPT_TEMPLATE = """You are a research assistant for "Answers in Faith", a YouTube channel with 12 theological blockers:
- every claim needs scripture support and historical/literary context
- no "this word only means" without lexical evidence
- speculation must be labeled, never asserted as fact
- no date-setting, no Antichrist identification
- always present alternative interpretations honestly

Draft ONE claim and ONE scene for a video, based on the research below.
Quote the biblical text accurately from a standard translation (KJV or NIV). If unsure of exact wording, use KJV.

Topic: {topic}
Primary scripture: {primary_scripture}
Hook: {hook}
Problem: {problem}
Explanation: {explanation}
Story: {story}
Application: {application}
CTA: {cta}

Return ONLY a JSON object with these keys:
- claim_text: the single main theological claim (1-2 sentences)
- source_reference: e.g. "Genesis 6:2"
- source_text: the exact biblical verse text
- original_language: e.g. "Hebrew" or "Greek" (the language of the source text), with the key original word and meaning if known
- context: historical/literary/cultural context — MUST be 60+ characters
- interpretation: your exegesis (1-3 sentences)
- confidence: exactly one of: high, medium, low
- claim_type: exactly one of: scripture, strong_inference, traditional, scholarly
- cross_references: array of 1-4 related passages as strings (REQUIRED for scripture/strong_inference)
- alternative_interpretations: the main alternative view, honestly stated (1-2 sentences)
- narration_text: voiceover for scene 1, natural spoken style, 60-120 words, matching the hook + explanation
- visual_prompt: image/video generation prompt for scene 1, cinematic, dark scholarly atmosphere, no text in image

{claim_type_guidance}"""


def _normalize_cross_refs(data):
    cross = data.get("cross_references", [])
    if isinstance(cross, str):
        cross = [c.strip() for c in cross.split(",") if c.strip()]
    return [str(c) for c in cross][:4]


def _normalize_claim_type(raw):
    ct = str(raw or "scholarly").strip().lower().replace(" ", "_")
    # The gate hard-fails "speculation" — coerce to hedged scholarly instead.
    if ct == "speculation":
        ct = "scholarly"
    if ct not in ("scripture", "strong_inference", "traditional", "scholarly"):
        ct = "scholarly"
    return ct


def auto_script(topic, primary_scripture, hook, problem, explanation, story, application, cta):
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured on the server.")
    prompt = SCRIPT_PROMPT_TEMPLATE.format(
        topic=topic or "(not specified)",
        primary_scripture=primary_scripture or "(not specified)",
        hook=hook or "(none)", problem=problem or "(none)", explanation=explanation or "(none)",
        story=story or "(none)", application=application or "(none)", cta=cta or "(none)",
        claim_type_guidance=CLAIM_TYPE_GUIDANCE,
    )
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a careful biblical research assistant. Output only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.6,
        },
        timeout=90,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
    data = json.loads(resp.json()["choices"][0]["message"]["content"])
    return {
        "claim_text": str(data.get("claim_text", "")).strip(),
        "source_reference": str(data.get("source_reference", "")).strip(),
        "source_text": str(data.get("source_text", "")).strip(),
        "original_language": str(data.get("original_language", "")).strip(),
        "context": str(data.get("context", "")).strip(),
        "interpretation": str(data.get("interpretation", "")).strip(),
        "confidence": str(data.get("confidence", "medium")).strip().lower(),
        "claim_type": _normalize_claim_type(data.get("claim_type")),
        "cross_references": _normalize_cross_refs(data),
        "alternative_interpretations": str(data.get("alternative_interpretations", "")).strip(),
        "narration_text": str(data.get("narration_text", "")).strip(),
        "visual_prompt": str(data.get("visual_prompt", "")).strip(),
    }


# ============ EPISODE (long-form) SCRIPT GENERATOR ============

EPISODE_PROMPT_TEMPLATE = """You are a scriptwriter for "Answers in Faith", a YouTube channel with 12 theological blockers:
- every claim needs scripture support and historical/literary context
- no "this word only means" without lexical evidence
- speculation must be labeled, never asserted as fact
- no date-setting, no Antichrist identification
- always present alternative interpretations honestly

Write a LONG-FORM EPISODE script ({scene_count} scenes, roughly {minutes} minutes of spoken video)
based on the approved research below.

Topic: {topic}
Primary scripture: {primary_scripture}
Hook: {hook}
Problem: {problem}
Explanation: {explanation}
Story: {story}
Application: {application}
CTA: {cta}

EPISODE STRUCTURE ({scene_count} scenes, in this order):
- Scene 1: HOOK — open with the question/mystery from the hook, promise the payoff, 60-90 words.
- Scenes 2 to {second_last}: BODY — walk through the problem, what the text actually says
  (quote the primary scripture aloud in one scene), historical context, the story illustration,
  and the honest alternative interpretations. 80-130 words each.
- Scene {scene_count}: APPLICATION + CTA — land the application and end with the call to action, 60-90 words.

NARRATION RULES:
- Natural spoken style, second person, no headings or labels in the text.
- Each scene's narration must stand alone when heard back-to-back (no "as I said in the last scene").
- Quote scripture accurately from KJV or NIV; if unsure of exact wording, use KJV.

Return ONLY a JSON object:
{{
  "claims": [
    {{
      "claim_text": "one theological claim made in the episode (1-2 sentences)",
      "source_reference": "e.g. Genesis 6:2",
      "source_text": "exact biblical verse text",
      "original_language": "Hebrew or Greek, with the key word and meaning if known",
      "context": "historical/literary/cultural context — MUST be 60+ characters",
      "interpretation": "exegesis, 1-3 sentences",
      "confidence": "high|medium|low",
      "claim_type": "scripture|strong_inference|traditional|scholarly",
      "cross_references": ["1-4 related passages"],
      "alternative_interpretations": "main alternative view, honestly stated"
    }}
  ],
  "scenes": [
    {{
      "narration_text": "voiceover for this scene",
      "visual_prompt": "cinematic image prompt for this scene: dark scholarly atmosphere, ancient world, dramatic lighting, consistent visual style across all scenes, no text in image",
      "claim_index": 0
    }}
  ]
}}

REQUIREMENTS:
- Exactly {scene_count} scenes.
- 1 to 3 claims total — only the claims the episode actually makes on camera.
- claim_index points to the claim that scene relies on most (0-based; use 0 if none).
{claim_type_guidance}"""


def auto_script_episode(topic, primary_scripture, hook, problem, explanation,
                        story, application, cta, scene_count=6):
    """Multi-scene long-form draft: returns {"claims": [...], "scenes": [...]}.
    Nothing is saved by this function — the caller returns the draft for review."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured on the server.")
    scene_count = max(4, min(10, int(scene_count or 6)))
    minutes = round(scene_count * 0.75, 1)  # ~110 words/scene at ~150wpm ≈ 45s per scene
    prompt = EPISODE_PROMPT_TEMPLATE.format(
        topic=topic or "(not specified)",
        primary_scripture=primary_scripture or "(not specified)",
        hook=hook or "(none)", problem=problem or "(none)", explanation=explanation or "(none)",
        story=story or "(none)", application=application or "(none)", cta=cta or "(none)",
        scene_count=scene_count, second_last=scene_count - 1, minutes=minutes,
        claim_type_guidance=CLAIM_TYPE_GUIDANCE,
    )
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You are a careful biblical scriptwriter. Output only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.6,
            "max_tokens": 4000,
        },
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
    data = json.loads(resp.json()["choices"][0]["message"]["content"])

    claims = []
    for c in (data.get("claims") or [])[:3]:
        claims.append({
            "claim_text": str(c.get("claim_text", "")).strip(),
            "source_reference": str(c.get("source_reference", "")).strip(),
            "source_text": str(c.get("source_text", "")).strip(),
            "original_language": str(c.get("original_language", "")).strip(),
            "context": str(c.get("context", "")).strip(),
            "interpretation": str(c.get("interpretation", "")).strip(),
            "confidence": str(c.get("confidence", "medium")).strip().lower()
                          if str(c.get("confidence", "")).strip().lower() in ("high", "medium", "low") else "medium",
            "claim_type": _normalize_claim_type(c.get("claim_type")),
            "cross_references": _normalize_cross_refs(c),
            "alternative_interpretations": str(c.get("alternative_interpretations", "")).strip(),
        })

    scenes = []
    for i, s in enumerate((data.get("scenes") or [])[:10]):
        try:
            ci = int(s.get("claim_index", 0))
        except (TypeError, ValueError):
            ci = 0
        scenes.append({
            "order_index": i,
            "narration_text": str(s.get("narration_text", "")).strip(),
            "visual_prompt": str(s.get("visual_prompt", "")).strip(),
            "claim_index": max(0, min(ci, max(0, len(claims) - 1))),
        })

    if not scenes:
        raise RuntimeError("AI returned no scenes — try again.")
    if not claims:
        # The gate requires at least one claim; build a minimal scholarly one from the topic.
        claims.append({
            "claim_text": f"This video explores {topic} based on {primary_scripture or 'Scripture'}.",
            "source_reference": primary_scripture or "",
            "source_text": "", "original_language": "",
            "context": "", "interpretation": "",
            "confidence": "medium", "claim_type": "scholarly",
            "cross_references": [], "alternative_interpretations": "",
        })

    return {"claims": claims, "scenes": scenes, "multi": True,
            "scene_count": len(scenes), "estimated_minutes": minutes}
