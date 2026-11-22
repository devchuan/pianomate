from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import library
from .music.converter import score_to_jianpu, score_to_musicxml
from .music.model import Score
from .music.pdf import score_to_pdf

WEB_DIR = Path(__file__).resolve().parent / "web"
STORE: Dict[str, Score] = {}
MAX_UPLOAD_BYTES = 120 * 1024 * 1024

app = FastAPI(title="琴伴 PianoMate API", version="2.0.0")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def _payload(score: Score, score_id: str | None = None) -> Dict[str, Any]:
    data: Dict[str, Any] = score.to_dict()
    if score_id:
        data["id"] = score_id
    data["jianpu"] = score_to_jianpu(score)
    data["musicxml"] = getattr(score, "source_xml", None) or score_to_musicxml(score)
    return data


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "learn.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/scores")
def api_scores(q: str = "", tag: str = "", source: str = "") -> Dict[str, Any]:
    return {"items": library.list_scores(q, tag, source)}


@app.get("/api/scores/{key}")
def api_score_by_key(key: str) -> Dict[str, Any]:
    try:
        score = library.load_score(key)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    score_id = uuid.uuid4().hex
    STORE[score_id] = score
    return _payload(score, score_id)


@app.post("/api/scores/import")
async def api_score_import(file: UploadFile = File(...)) -> Dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 120MB 限制")
    try:
        meta = library.import_score(file.filename or "score.musicxml", content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"乐谱导入失败：{exc}") from exc
    return meta


@app.get("/api/search")
def api_search(q: str = "", providers: str = ",".join(library.DEFAULT_PROVIDERS),
               limit: int = 12) -> Dict[str, Any]:
    selected = [item.strip() for item in providers.split(",") if item.strip()]
    return {"items": library.search_all(q, selected, limit)}


@app.get("/api/score/{score_id}/musicxml")
def api_musicxml(score_id: str) -> Response:
    score = STORE.get(score_id)
    if score is None:
        raise HTTPException(status_code=404, detail="乐谱不存在或已过期")
    filename = quote(f"{score.title}.musicxml")
    return Response(
        content=score_to_musicxml(score),
        media_type="application/vnd.recordare.musicxml+xml",
        headers={"Content-Disposition": f"attachment; filename=score.musicxml; filename*=UTF-8''{filename}"},
    )


@app.get("/api/score/{score_id}/pdf")
def api_score_pdf(score_id: str) -> Response:
    score = STORE.get(score_id)
    if score is None:
        raise HTTPException(status_code=404, detail="乐谱不存在或已过期")
    try:
        pdf = score_to_pdf(score)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF 生成失败：{exc}") from exc
    filename = quote(f"{score.title}.pdf")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=score.pdf; filename*=UTF-8''{filename}"},
    )


