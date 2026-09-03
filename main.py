# main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
