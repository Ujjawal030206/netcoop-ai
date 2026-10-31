"""Dashboard + REST API.

    python server.py            -> http://127.0.0.1:8000

POST /api/diagnose   body: {"scenario": "weak_wifi.json"} or {"telemetry": <any supported input>}
                     optional: "crash_agents": ["infrastructure_agent"], "llm": true
GET  /api/scenarios  bundled scenarios with their expected outcome
GET  /api/schema     the netcoop-msg/1.0 JSON Schema
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from netcoop import knowledge
from netcoop.orchestrator import diagnose
from netcoop.protocol import SCHEMA_PATH

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "data" / "scenarios"
MAX_BYTES = 5_000_000

app = FastAPI(title="NetCoop AI", version="1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    from netcoop import llm
    return {"ok": True, "llm_configured": llm.is_configured(), "faults": list(knowledge.signatures())}


@app.get("/api/scenarios")
def scenarios():
    manifest = json.loads((SCENARIOS / "manifest.json").read_text(encoding="utf-8"))
    return [{"file": k, **v} for k, v in manifest.items()]


@app.get("/api/scenarios/{name}")
def scenario(name: str):
    p = (SCENARIOS / name).resolve()
    if p.parent != SCENARIOS.resolve() or not p.exists():
        raise HTTPException(404, "unknown scenario")
    return JSONResponse({"name": name, "content": p.read_text(encoding="utf-8")})


@app.get("/api/schema")
def schema():
    return JSONResponse(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@app.post("/api/diagnose")
async def api_diagnose(request: Request):
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(413, "input larger than 5 MB")
    try:
        req = json.loads(body or b"{}")
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"request body is not JSON: {e.msg}")
    if not isinstance(req, dict):
        raise HTTPException(400, "request body must be an object")
    if req.get("scenario"):
        p = (SCENARIOS / str(req["scenario"])).resolve()
        if p.parent != SCENARIOS.resolve() or not p.exists():
            raise HTTPException(404, "unknown scenario")
        raw = p.read_text(encoding="utf-8")
    elif "telemetry" in req:
        raw = req["telemetry"]
    else:
        raise HTTPException(400, "send {'scenario': name} or {'telemetry': data}")
    narrator = None
    if req.get("llm"):
        from netcoop.llm import narrator as make
        narrator = make()
    crash = [str(a) for a in (req.get("crash_agents") or [])][:5]
    res = diagnose(raw, crash_agents=crash, narrator=narrator)
    tel = res["telemetry"] or {}
    return JSONResponse(json.loads(json.dumps({
        "report": res["report"],
        "markdown": res["markdown"],
        "messages": res["messages"],
        "rejected_messages": res["rejected_messages"],
        "elapsed_ms": res["elapsed_ms"],
        "topology": tel.get("topology"),
        "devices": {d: {"type": v["type"], "status": v["status"], "segment": v["segment"],
                        "metrics": {m: {"value": i["value"], "unit": i["unit"], "series": [s[1] for s in i["series"]]}
                                    for m, i in v["metrics"].items()}}
                    for d, v in (tel.get("devices") or {}).items()},
        "quality": tel.get("quality"),
        "thresholds": knowledge.thresholds(),
    }, default=str)))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")))
