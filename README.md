# Answers in Faith Engine

**Theological content pipeline with 12 non-negotiable blockers.**

FastAPI backend that enforces scripture integrity before any video gets narrated, assembled, or published. Built for Answers in Faith — not AgentTube.

## 12 Theological Blockers

| # | Blocker | Enforcement |
|---|---------|-------------|
| 1 | Scripture Support | Hard block if `source_reference` or `source_text` missing |
| 2 | Context Check | Block if historical/literary context < 50 chars |
| 3 | Language Overclaim | Block "this word only means" without lexical evidence |
| 4 | Interpretation vs Fact | `SPECULATION` type cannot pass as fact |
| 5 | "God Said" Filter | Block "God told me" unless biblical narrative |
| 6 | No Divination/Occult | Block numerology, astrology, tarot, manifestation |
| 7 | Prophecy Safety | Block date-setting, Antichrist ID; auto-flag manual review |
| 8 | Character of God | Block direct contradictions to Scripture's witness |
| 9 | Gospel Integrity | Extra scrutiny on salvation videos; block distortions |
| 10 | Cross-Reference | SCRIPTURE/INFERENCE claims need ≥1 supporting passage |
| 11 | Confidence Scoring | Low confidence must be qualified; cannot assert as fact |
| 12 | Human Review Gate | Sensitive topics (Genesis 6, end times, etc.) require explicit approval |

## Deploy to Railway

### 1. Create Repo & Push

```bash
cd answers-in-faith-engine
git init
git add .
git commit -m "Initial deploy — 12-blocker theological engine"
```

Create a private GitHub repo, push:
```bash
git remote add origin https://github.com/YOURNAME/answers-in-faith-engine.git
git branch -M main
git push -u origin main
```

### 2. Railway Deploy

```bash
npm install -g @railway/cli
railway login
railway init --name answers-in-faith-engine
railway up
```

### 3. Environment Variables

In Railway dashboard → Variables, add:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}   # Auto-added if you add PostgreSQL
OPENAI_API_KEY=sk-...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=josh
```

Optional: Add a **Railway PostgreSQL** plugin for production (better than SQLite).

### 4. Verify

```bash
curl https://your-app.up.railway.app/health
```

Response:
```json
{"status": "ok", "engine": "Answers in Faith v1.0", "theological_gates": 12}
```

## API Workflow

### Step 1: Create Production
```bash
curl -X POST https://your-app.up.railway.app/api/productions   -H "Content-Type: application/json"   -d '{"topic": "Who were the Nephilim?", "doctrinal_category": "genesis_6", "primary_scripture": "Genesis 6:1-4", "gospel_video": false}'
```

### Step 2: Submit Research
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/research   -H "Content-Type: application/json"   -d '{"hook": "Genesis 6 mentions beings that still confuse scholars...", "problem": "...", "explanation": "...", "story": "...", "application": "...", "cta": "..."}'
```

### Step 3: Submit Script + Claims
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/script   -H "Content-Type: application/json"   -d '{
    "claims": [
      {
        "claim_text": "The sons of God in Genesis 6:2 refers to divine beings, not human men",
        "source_reference": "Genesis 6:2",
        "source_text": "...",
        "original_language": "Hebrew: בְּנֵי הָאֱלֹהִים (bene ha elohim)",
        "context": "Ancient Near Eastern divine council imagery...",
        "interpretation": "The phrase bene ha elohim appears in Job 1:6, 38:7 as divine beings...",
        "confidence": "medium",
        "claim_type": "scholarly",
        "cross_references": ["Job 1:6", "Job 38:7", "Psalm 82:6"],
        "alternative_interpretations": "Some traditions interpret as Sethites or godly line of men"
      }
    ],
    "scenes": [
      {"order_index": 0, "narration_text": "Genesis 6 opens with one of the Bible's strangest passages...", "visual_prompt": "Ancient scroll, dark atmosphere", "claim_ids": [0]}
    ]
  }'
```

### Step 4: Run Evidence Gate
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/evidence
```

If Genesis 6 category → auto-blocks for manual review (Blocker 12).

### Step 5: Human Review (DAVID APPROVES)
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/review   -H "Content-Type: application/json"   -d '{"decision": "pass", "reviewer": "david", "notes": "Approved after reviewing alternative views"}'
```

### Step 6: Produce
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/produce
```

Background task: ElevenLabs TTS + FFmpeg visuals + assembly.

### Step 7: Quality Gate
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/quality   -H "Content-Type: application/json"   -d '{"decision": "pass", "reviewer": "david"}'
```

### Step 8: Packaging
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/packaging   -H "Content-Type: application/json"   -d '{"title": "Who Were the Nephilim? | Genesis 6 Explained", "description": "...", "keywords": "nephilim,genesis 6,sons of god,bible", "thumbnail_prompt": "Ancient scroll, mysterious figures, dark blue and gold"}'
```

### Step 9: Final Approval
```bash
curl -X POST https://your-app.up.railway.app/api/productions/{id}/approve   -H "Content-Type: application/json"   -d '{"decision": "pass", "reviewer": "david"}'
```

### Check Status Anytime
```bash
curl https://your-app.up.railway.app/api/productions/{id}
```

## File Structure

```
answers-in-faith-engine/
├── main.py                 # FastAPI app
├── models.py               # SQLAlchemy schema
├── config.py               # Settings
├── pipeline.py             # 12-stage API endpoints
├── theology_gate.py        # 12 non-negotiable blockers
├── youtube_publisher.py    # YouTube upload (OAuth)
├── requirements.txt
├── Dockerfile
├── railway.toml
├── .env.example
└── config/                 # YouTube OAuth credentials (gitignored)
```

## Architecture

```
Discovery → Research → Script → Evidence Gate (12 blockers) → Human Review → 
Production (TTS+Visuals) → Assembly (FFmpeg) → Quality Gate → Packaging → 
Approval → YouTube Upload
```

**Fail-closed at every gate.** A single violation stops the line.

## Next: React Dashboard

Want a review UI? Next build: React dashboard for David to:
- See all claims with scripture/context side-by-side
- Click Pass / Repair per claim
- Preview scenes before assembly
- Drag-and-drop scene reordering
- Burn captions via FFmpeg

## License

MIT — built for theological fidelity, not algorithmic growth.


## Dashboard (React + Vercel)

A review UI is included in `frontend/`:

```bash
cd frontend
npm install
cp .env.example .env.local
# Edit .env.local: VITE_API_URL=https://your-railway-app.up.railway.app/api
npm run dev
```

### Deploy Dashboard to Vercel

```bash
cd frontend
npm run build
vercel --prod
```

**Features:**
- Production list with stage filtering
- Claim-by-claim review with scripture, context, interpretation side-by-side
- Pass / Repair buttons per claim
- Visual pipeline tracker
- Scene manager with reorder controls
- Stage-appropriate action panels (Evidence Gate → Human Review → Quality Gate → Packaging → Final Approval)
- Auto-flags sensitive doctrinal categories with red dot indicator
