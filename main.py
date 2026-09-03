# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os

from models import init_db, get_engine
from pipeline import router as pipeline_router
from config import settings

app = FastAPI(title="Answers in Faith Engine", version="1.0.0")

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

@app.get("/health")
def health():
    return {"status": "ok", "engine": "Answers in Faith v1.0", "theological_gates": 12}

@app.get("/api/download/{prod_id}")
def download_video(prod_id: str):
    file_path = f"./output/final/{prod_id}.mp4"
    if os.path.exists(file_path):
        return FileResponse(file_path, media_type="video/mp4", filename=f"{prod_id}.mp4")
    return {"error": "Video not found. It may have been lost due to container restart."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
