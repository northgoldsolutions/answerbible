from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
import base64
import lzma
import os

from models import init_db, get_engine
from pipeline import router as pipeline_router
from sketch import router as sketch_router
from video_providers import provider_status
from config import settings
from api import router as direct_router
from dashboard_payload import DASHBOARD_HTML_XZ_B64

app = FastAPI(title="Answers in Faith Engine", version="1.2.1")

FALLBACK_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Answers in Faith — Dashboard</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#020617;color:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.wrap{max-width:1180px;margin:0 auto;padding:22px}h1{color:#f59e0b;margin:0 0 4px}small,.muted{color:#94a3b8}.card{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:18px;margin:14px 0;box-shadow:0 8px 24px #0006}.row{display:grid;grid-template-columns:2fr 1fr 110px auto;gap:10px;align-items:end}label{display:block;color:#94a3b8;font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;margin-bottom:5px}input,select{width:100%;background:#020617;border:1px solid #334155;color:#f8fafc;border-radius:7px;padding:10px}input[type=checkbox]{width:auto;transform:scale(1.25)}button,.btn{background:#f59e0b;color:#020617;border:0;border-radius:7px;padding:10px 14px;font-weight:800;cursor:pointer;text-decoration:none;display:inline-block}button:disabled{opacity:.55;cursor:wait}.secondary{background:#1e293b;color:#f8fafc;border:1px solid #334155}.danger{background:#dc2626;color:#fff}.ok{color:#22c55e}.err{color:#f87171}.loading{color:#f59e0b}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 8px;border-bottom:1px solid #1e293b;vertical-align:top}th{color:#94a3b8;font-size:.75rem;text-transform:uppercase}tr.clickable{cursor:pointer}tr.clickable:hover{background:#111c31}.badge{display:inline-block;border-radius:999px;background:#164e63;color:#a5f3fc;padding:3px 8px;font-size:.72rem;font-weight:700;white-space:nowrap}.scene{border-left:3px solid #334155;padding:8px 0 8px 12px;margin:8px 0}.scene .prompt{color:#94a3b8;font-size:.78rem;font-style:italic}.video{width:100%;max-height:70vh;background:#000;border-radius:8px;margin-top:12px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.cost{font-size:1.05rem;margin:10px 0}@media(max-width:760px){.wrap{padding:12px}.row{grid-template-columns:1fr}.actions button{width:100%}th:nth-child(3),td:nth-child(3){display:none}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Answers in Faith</h1>
  <small>Review Dashboard</small>

  <section class="card">
    <h2 style="margin-top:0">🎬 Director — any topic, any length</h2>
    <div class="row">
      <div><label>Topic</label><input id="dTopic" placeholder="e.g. Term vs. whole life"></div>
      <div><label>Vertical</label><select id="dVertical"><option>faith</option><option>truecrime</option><option>history</option><option>mystery</option><option>finance</option></select></div>
      <div><label>Minutes</label><input id="dMinutes" type="number" min="1" max="60" value="10"></div>
      <div><label>Gate</label><input id="dGate" type="checkbox" checked></div>
    </div>
    <div class="actions"><button id="dBtn" onclick="runDirect()">Generate Plan</button></div>
    <div id="dStatus" style="margin-top:10px"></div>
    <div id="dResult"></div>
  </section>

  <section id="app" class="card"><div class="loading">Loading productions...</div></section>
</div>
<script>
const API = location.origin + '/api';
let lastPlan = null;

function esc(s){return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
async function api(path, opts={}){
  const r = await fetch(API + path, opts);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch(e) { data = text; }
  if (!r.ok) throw new Error((data && (data.detail || data.message)) || text || ('HTTP ' + r.status));
  return data;
}
function stageLabel(s){return String(s || '').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());}
function videoSrc(p){
  if (p.video_url) return p.video_url.startsWith('http') ? p.video_url : location.origin + p.video_url;
  return p.has_video ? API + '/download/' + p.id : '';
}
async function loadList(){
  const app = document.getElementById('app');
  app.innerHTML = '<div class="loading">Loading productions...</div>';
  try {
    const prods = await api('/productions');
    if (!prods.length) { app.innerHTML = '<div class="muted">No productions yet.</div>'; return; }
    app.innerHTML = '<h2 style="margin-top:0">Productions</h2><table><thead><tr><th>Topic</th><th>Format</th><th>Stage</th><th></th></tr></thead><tbody>' +
      prods.map(p => '<tr class="clickable" onclick="loadDetail(\'' + p.id + '\')"><td><b>' + esc(p.topic) + '</b><br><small class="muted">' + esc(p.id) + '</small></td><td><span class="badge">' + esc(p.video_format || 'short') + '</span></td><td>' + stageLabel(p.stage) + '</td><td><button class="danger" onclick="event.stopPropagation();removeProduction(\'' + p.id + '\')">Delete</button></td></tr>').join('') +
      '</tbody></table>';
  } catch(e) { app.innerHTML = '<div class="err">' + esc(e.message) + '</div>'; }
}
async function removeProduction(id){
  if (!confirm('Delete this production permanently?')) return;
  await api('/productions/' + id, {method:'DELETE'});
  loadList();
}
async function loadDetail(id){
  const app = document.getElementById('app');
  app.innerHTML = '<div class="loading">Loading production...</div>';
  try {
    const p = await api('/productions/' + id);
    const src = videoSrc(p);
    app.innerHTML = '<button class="secondary" onclick="loadList()">← Back</button>' +
      '<h2>' + esc(p.topic) + '</h2><div class="muted">' + esc(p.id) + ' · ' + stageLabel(p.stage) + ' · ' + esc(p.status || '') + '</div>' +
      (src ? '<video class="video" controls preload="metadata" src="' + esc(src) + '"></video><div class="actions"><a class="btn" href="' + esc(src) + '" download>Download Video</a>' + (p.captions_url ? '<a class="btn secondary" href="' + esc(p.captions_url) + '" download>Captions</a>' : '') + '</div>' : '<div class="card muted">No video file yet. Rendering can take several minutes; refresh this page.</div>') +
      '<h3>Scenes (' + p.scenes.length + ')</h3>' + p.scenes.map(s => '<div class="scene"><b>Scene ' + (s.order + 1) + '</b> <span class="badge">' + esc(s.status) + '</span><div>' + esc(s.narration_text || '') + '</div><div class="prompt">' + esc(s.visual_prompt || '') + '</div></div>').join('') +
      '<div class="actions"><button onclick="loadDetail(\'' + p.id + '\')">Refresh</button></div>';
  } catch(e) { app.innerHTML = '<div class="err">' + esc(e.message) + '</div>'; }
}
async function runDirect(){
  const topic = document.getElementById('dTopic').value.trim();
  if (!topic) return alert('Enter a topic first.');
  const btn = document.getElementById('dBtn');
  btn.disabled = true; btn.textContent = 'Planning...';
  document.getElementById('dStatus').innerHTML = '<span class="loading">Planning video...</span>';
  try {
    const res = await api('/direct', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:topic,vertical:document.getElementById('dVertical').value,minutes:parseInt(document.getElementById('dMinutes').value)||10,use_gate:document.getElementById('dGate').checked})});
    lastPlan = res;
    renderPlan(res);
    document.getElementById('dStatus').innerHTML = '<span class="ok">Plan ready. Review it, then generate the video.</span>';
  } catch(e) { document.getElementById('dStatus').innerHTML = '<span class="err">' + esc(e.message) + '</span>'; }
  finally { btn.disabled = false; btn.textContent = 'Generate Plan'; }
}
function renderPlan(res){
  const m = res.manifest, c = res.cost;
  const clipsLabel = c.stills_only
    ? '<span class="badge" style="background:#7c2d12;color:#fed7aa;margin-left:6px">stills only (no motion credits)</span>'
    : ' (' + c.n_motion_clips + ' motion clip' + (c.n_motion_clips===1?'':'s') + ')';
  const llmNote = (res.llm_mode !== 'live' && res.llm_note)
    ? '<div class="err" style="font-size:.82rem;margin-top:6px">⚠ ' + esc(res.llm_note) + '</div>' : '';
  const planNotes = (m.notes && m.notes.length)
    ? '<div style="margin-top:8px;font-size:.82rem;color:#fbbf24">' +
        m.notes.map(function(n){return '• ' + esc(n);}).join('<br>') + '</div>' : '';
  document.getElementById('dResult').innerHTML = '<div class="card"><b>' + esc(m.topic) + '</b>' +
    '<div class="muted">' + m.minutes + ' min · ' + esc(m.vertical) + ' · ' + m.total_words +
    ' words · ' + c.n_scenes + ' scenes · LLM ' + esc(res.llm_mode) + '</div>' +
    '<div class="cost">Estimated cost: <b>$' + c.total + '</b>' + clipsLabel + '</div>' +
    llmNote + planNotes +
    m.scenes.map(function(s){
       return '<div class="scene"><b>' + (s.index + 1) + '. ' + esc(s.beat_title) + '</b> ' +
         '<span class="badge">' + esc(s.visual_type) + '</span><div>' + esc(s.tts_line) + '</div>' +
         '<div class="prompt">' + esc(s.visual_type === 'motion' ? s.motion_prompt : s.image_prompt) +
         '</div></div>';
    }).join('') +
    '<div class="actions"><button onclick="produceDirectVideo()">🎥 Generate Video</button>' +
    '<button class="secondary" onclick="downloadPlan()">Download Manifest</button></div></div>';
}
async function produceDirectVideo(){
  if (!lastPlan) return alert('Generate a plan first.');
  const statusEl = document.getElementById('dStatus');
  statusEl.innerHTML = '<span class="loading">Creating scenes and starting the render… (this can take 5–10 minutes for motion clips)</span>';
  let res;
  try {
    res = await api('/direct/produce', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(lastPlan)});
  } catch(e) {
    statusEl.innerHTML = '<span class="err">Failed to start: ' + esc(e.message) + '</span>';
    return;
  }
  statusEl.innerHTML = '<span class="ok">Render started — polling progress. Keep this tab open; motion clips take several minutes each.</span>';
  // Poll the production until it leaves the PRODUCTION stage, then open detail.
  const pid = res.production_id;
  let lastMsg = '';
  const tick = async () => {
    try {
      const p = await api('/productions/' + pid);
      const done = p.scenes.filter(s => (s.status||'').startsWith('done:')).length;
      const total = p.scenes.length;
      const msg = `${done}/${total} scenes ready · stage ${stageLabel(p.stage)}${p.has_video ? ' · video ready' : ''}`;
      if (msg !== lastMsg) {
        lastMsg = msg;
        statusEl.innerHTML = '<span class="loading">' + esc(msg) + '</span>';
      }
      if (p.has_video) {
        statusEl.innerHTML = '<span class="ok">✅ Video ready. Opening…</span>';
        setTimeout(() => loadDetail(pid), 300);
        return;
      }
      if (p.stage === 'quality_gate' || p.stage === 'published' || p.stage === 'packaging' || p.stage === 'approval') {
        statusEl.innerHTML = '<span class="ok">Render complete (stage: ' + stageLabel(p.stage) + '). Opening…</span>';
        setTimeout(() => loadDetail(pid), 300);
        return;
      }
      // Stop polling after 20 min to avoid runaway tabs.
      setTimeout(tick, 5000);
    } catch(e) {
      statusEl.innerHTML = '<span class="err">Poll error: ' + esc(e.message) + ' — will retry.</span>';
      setTimeout(tick, 8000);
    }
  };
  setTimeout(tick, 1500);
}
function downloadPlan(){
  if (!lastPlan) return;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(lastPlan,null,2)],{type:'application/json'}));
  a.download = 'director-manifest.json';
  a.click();
}
loadList();
</script>
</body>
</html>"""

def _load_dashboard() -> str:
    try:
        return lzma.decompress(base64.b64decode(DASHBOARD_HTML_XZ_B64)).decode("utf-8")
    except Exception as e:
        print(f"[Dashboard] Compressed payload unavailable ({e}); serving built-in dashboard")
        return FALLBACK_DASHBOARD_HTML

DASHBOARD_HTML = _load_dashboard()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = get_engine(settings.database_url)
init_db(engine)

app.include_router(pipeline_router, prefix="/api")
app.include_router(sketch_router, prefix="/sketch")
app.include_router(direct_router)

@app.get("/", include_in_schema=False)
def dashboard():
    return HTMLResponse(DASHBOARD_HTML)

@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "Answers in Faith v1.2.1",
        "theological_gates": 12,
        "video_providers": provider_status(),
        "sketch": {
            "router": True,
            "pika_configured": bool((settings.pika_api_key or "").strip()),
            "openai_stills": bool(settings.openai_api_key),
            "elevenlabs_dialogue": bool(settings.elevenlabs_api_key),
        },
        "r2_configured": bool(os.getenv("R2_ACCOUNT_ID") and os.getenv("R2_BUCKET_NAME")),
        "r2_account_id": os.getenv("R2_ACCOUNT_ID", ""),
        "r2_bucket": os.getenv("R2_BUCKET_NAME", ""),
        "r2_public_url": os.getenv("R2_PUBLIC_URL", ""),
    }

@app.get("/api/download/{prod_id}")
def download_video(prod_id: str):
    file_path = f"./output/final/{prod_id}.mp4"
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4", filename=f"{prod_id}.mp4")
    return {"error": "Video not found. It may have been lost due to container restart."}

@app.get("/api/download-captions/{prod_id}")
def download_captions(prod_id: str):
    file_path = f"./output/captions/{prod_id}.srt"
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="text/plain", filename=f"{prod_id}.srt")
    return {"error": "Captions not found."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
