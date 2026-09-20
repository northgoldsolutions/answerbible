"""api.py — mount inside the existing FastAPI app (main.py) in the engine service.

    from api import router as direct_router
    app.include_router(direct_router)

Route:  POST /api/direct
Body:   {"topic": str, "vertical": "faith|truecrime|history|mystery|finance",
         "minutes": int (1-60), "use_gate": bool}
Resp:   {"manifest": {...}, "cost": {...}, "status": "planned"}
"""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from director import Vertical, Director, LiveLLM, estimate_cost

router = APIRouter()
VERTICAL_DIR = Path(__file__).parent / "verticals"

class DirectRequest(BaseModel):
    topic: str = Field(min_length=3, max_length=300)
    vertical: str = "faith"
    minutes: int = Field(default=10, ge=1, le=60)
    use_gate: bool = True

def load_vertical(name: str) -> Vertical:
    p = VERTICAL_DIR / f"{name}.yaml"
    if not p.exists():
        raise HTTPException(404, f"unknown vertical '{name}' — options: "
            + ", ".join(x.stem for x in VERTICAL_DIR.glob('*.yaml')))
    return Vertical(**json.loads(p.read_text()))

@router.post("/api/direct")
def direct(req: DirectRequest):
    v = load_vertical(req.vertical)
    llm = LiveLLM()
    man = Director(v, llm=llm).plan(req.topic, req.minutes, req.use_gate)
    return {"status": "planned", "llm_mode": "live" if llm.live else "mock",
            "llm_note": llm.reason,
            "manifest": json.loads(json.dumps(man, default=vars)),
            "cost": estimate_cost(man, v)}
