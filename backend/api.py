"""FastAPI service: /api/ask (SSE), /api/search, /api/provision/*, /api/browse, /api/samples, static UI."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from backend import config  # noqa: E402
from backend.generation.engine import Engine  # noqa: E402
from backend.generation.context import build_context  # noqa: E402

FRONTEND = config.ROOT / "frontend"
app = FastAPI(title="PIA 2021 Research Assistant", version="1.0")
engine: Engine | None = None

SAMPLES = [
    {"group": "Revenue & fiscal terms", "items": [
        "What royalty rates apply to onshore, shallow water and deep offshore crude oil production?",
        "What is royalty by price and who is the beneficiary?",
        "What percentage of operating expenditure must settlors contribute to host communities trusts?",
        "What is the rate of hydrocarbon tax and how is chargeable tax ascertained?",
        "What penalties apply to late payment of royalties, rents and hydrocarbon tax?",
        "Which fund must be set up for decommissioning and abandonment, and how is it funded?",
    ]},
    {"group": "Institutions & governance", "items": [
        "What are the powers of the Minister of Petroleum under the Act?",
        "How is the Governing Board of the Commission composed and how can members be removed?",
        "How are the assets of NNPC transferred to NNPC Limited and what does the Act say about dividends?",
        "What is the Frontier Exploration Fund and how is it funded?",
    ]},
    {"group": "Licensing & operations", "items": [
        "What are the grounds for revoking a petroleum prospecting licence or mining lease?",
        "What conditions apply to marginal field awards?",
        "What are the penalties for gas flaring and when is flaring permitted?",
        "How is the domestic crude oil supply obligation determined?",
    ]},
    {"group": "Cite a provision", "items": [
        "Explain section 306 and the provisions it relies on",
        "What does paragraph 10 of the Seventh Schedule provide?",
        "What is the definition of “host communities”?",
    ]},
]


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=1500)
    history: list[dict] = []
    rewrite: bool = True


@app.on_event("startup")
def _startup():
    global engine
    if not config.CHUNKS_JSONL.exists():
        from backend.ingest import parse_pdf, chunk
        parse_pdf.main()
        chunk.build_chunks()
    engine = Engine()
    print("[api] ready:", engine.retriever.status, "| LLM:", config.LLM_MODEL if engine.llm_ready else "not configured (retrieval-only mode)")


@app.get("/api/health")
def health():
    return {"ok": True, "llm": engine.llm_ready, "llm_model": config.LLM_MODEL if engine.llm_ready else None,
            "pipeline": engine.retriever.status, "sections": len(engine.corpus.sections),
            "definitions": len(engine.corpus.definitions), "chunks": len(engine.corpus.chunks),
            "instrument": engine.corpus.prov["instrument"], "citation": engine.corpus.prov["citation"]}


@app.get("/api/samples")
def samples():
    return SAMPLES


@app.post("/api/ask")
def ask(body: AskBody):
    def gen():
        try:
            for event, payload in engine.ask_stream(body.question.strip(), body.history, use_rewrite=body.rewrite):
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:  # never leave the UI hanging
            yield f"event: error\ndata: {json.dumps({'message': f'{type(e).__name__}: {e}'})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/search")
def search(body: AskBody):
    res, ctx, rw = engine.retrieve(body.question.strip(), use_rewrite=body.rewrite)
    return {"result": res, "sources": engine.sources_payload(res, ctx), "rewrites": rw}


def _section_payload(n: int):
    s = engine.corpus.sections.get(n)
    if not s:
        raise HTTPException(404, f"No section {n}")
    refs = sorted({r for c in engine.corpus.by_section[n] for r in c["refs_sections"]})
    sch = sorted({r for c in engine.corpus.by_section[n] for r in c["refs_schedules"]})
    titles = {k: engine.corpus.sections[k]["title"] for k in refs if k in engine.corpus.sections}
    return {
        "type": "section", "number": n, "title": s["title"], "chapter": s["chapter"], "chapter_title": s["chapter_title"],
        "part": s["part"], "part_title": s["part_title"], "page_start": s["page_start"], "page_end": s["page_end"],
        "subsections": s["subsections"] if n != 318 else [],
        "definitions": [{"term": d["term"], "text": d["text"]} for d in engine.corpus.prov["definitions"]] if n == 318 else [],
        "refers_to": [{"section": k, "title": titles.get(k, "")} for k in refs],
        "refers_to_schedules": sch,
        "cited_by": [{"section": k, "title": engine.corpus.sections[k]["title"]} for k in sorted(engine.cited_by.get(n, []))],
        "prev": n - 1 if n > 1 else None, "next": n + 1 if n < max(engine.corpus.sections) else None,
    }


@app.get("/api/provision/section/{n}")
def provision_section(n: int):
    return _section_payload(n)


@app.get("/api/provision/schedule/{ordinal}")
def provision_schedule(ordinal: int, para: int | None = None):
    for sc in engine.corpus.prov["schedules"]:
        if sc["ordinal"] == ordinal:
            paras = [p for p in sc["paragraphs"] if para is None or p["number"] == para]
            if not paras:
                raise HTTPException(404, "No such paragraph")
            return {"type": "schedule", "name": sc["name"], "title": sc["title"], "reference": sc["reference"],
                    "operative": sc["operative"], "page_start": sc["page_start"], "page_end": sc["page_end"],
                    "paragraphs": [{"number": p["number"], "heading": p["heading"], "part": p["part"], "part_title": p["part_title"],
                                    "page": p["page"], "subsections": p["subsections"]} for p in paras],
                    "all_paragraphs": [p["number"] for p in sc["paragraphs"]]}
    raise HTTPException(404, "No such schedule")


@app.get("/api/browse")
def browse():
    prov = engine.corpus.prov
    chapters = {}
    for s in prov["sections"]:
        ch = chapters.setdefault(s["chapter"], {"number": s["chapter"], "title": prov["chapters"].get(str(s["chapter"]), ""), "parts": {}})
        pk = s["part"] or "—"
        part = ch["parts"].setdefault(pk, {"part": s["part"], "title": s["part_title"], "sections": []})
        part["sections"].append({"n": s["number"], "title": s["title"].rstrip(".")})
    out = []
    for ch in chapters.values():
        ch["parts"] = list(ch["parts"].values())
        out.append(ch)
    scheds = [{"ordinal": sc["ordinal"], "name": sc["name"], "title": sc["title"], "operative": sc["operative"],
               "paragraphs": [{"n": p["number"], "heading": p["heading"]} for p in sc["paragraphs"]]} for sc in prov["schedules"]]
    return {"chapters": out, "schedules": scheds}


@app.get("/api/definitions")
def definitions(q: str = Query("", max_length=80)):
    ql = q.lower().strip()
    items = [{"term": d["term"], "text": d["text"]} for d in engine.corpus.prov["definitions"] if not ql or ql in d["term"].lower()]
    return items[:40]


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
