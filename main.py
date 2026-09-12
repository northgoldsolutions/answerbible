# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os

from models import init_db, get_engine
from pipeline import router as pipeline_router
from sketch import router as sketch_router
from video_providers import provider_status
from config import settings

app = FastAPI(title="Answers in Faith Engine", version="1.2.0")

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

@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": "Answers in Faith v1.2",
        "theological_gates": 12,
        "video_providers": provider_status(),
        "sketch": {
            "router": True,
            "pika_configured": bool((settings.pika_api_key or "").strip()),
            "openai_stills": bool(settings.openai_api_key),
            "elevenlabs_dialogue": bool(settings.elevenlabs_api_key),
        },
        "r2_configured": bool(os.getenv("R2_ACCOUNT_ID") and os.getenv("R2_BUCKET_NAME")),
        "r2_public_url_set": bool(os.getenv("R2_PUBLIC_URL")),
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
