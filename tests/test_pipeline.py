"""Run: python -m pytest -q   (works offline; no LLM or neural models needed)"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config
from backend.generation.context import build_context
from backend.generation.verify import verify
from backend.retrieval.retriever import Retriever
from backend.retrieval.text import detect_citations

PROV = json.loads(config.PROVISIONS_JSON.read_text())
R = Retriever(use_dense=False, use_rerank=False, verbose=False)

def test_structure_complete():
    nums = [s["number"] for s in PROV["sections"]]
    assert nums == list(range(1, 320))
    assert len(PROV["definitions"]) == 190 and len(PROV["schedules"]) == 9

def test_no_header_or_margin_leakage():
    for s in PROV["sections"]:
        assert "2021 No. 6" not in s["text"]

def test_known_provisions_verbatim():
    s = {x["number"]: x for x in PROV["sections"]}
    assert "vested in the Government of the Federation" in s[1]["text"]
    sch7 = [x for x in PROV["schedules"] if x["ordinal"] == 7][0]
    p10 = [p for p in sch7["paragraphs"] if p["number"] == 10][0]["text"]
    assert "onshore areas 15%" in p10 and "deep offshore (greater than 200m water depth) 7.5%" in p10

def test_chunks_respect_limits():
    ch = [json.loads(l) for l in config.CHUNKS_JSONL.open()]
    assert max(c["tokens"] for c in ch) <= config.MAX_TOKENS * 1.25

def test_citation_detection():
    assert detect_citations("section 104 and sections 260 to 262")[0] == [104, 260, 261, 262]
    assert (7, 10) in detect_citations("paragraph 10 of the Seventh Schedule")[1]

def test_retrieval_examples():
    keys = lambda q: [p["key"] for p in R.search(q)["parents"]]
    assert "sch7-p10" in keys("What royalty applies to onshore crude oil?")[:3]
    assert "s240" in keys("How much must settlors contribute to host communities?")[:3]
    assert keys("Explain section 306")[0] == "s306"

def test_verifier_catches_fabrication():
    res = R.search("What are the penalties for gas flaring?"); ctx = build_context(res, R.corpus)
    good = "Flaring is penalised [s.104]. The Act says “A licensee, lessee or marginal field operator that flares or vents natural gas” [s.104(1)]."
    v = verify(good, ctx, R.corpus); assert v["citations_ok"] == v["citations_total"] and v["quotes_ok"] == v["quotes_total"]
    bad = "The Act provides “a flat penalty of one billion naira for every flare” [s.999] and [s.104(9)]."
    v = verify(bad, ctx, R.corpus); assert v["problems"] and v["quotes_ok"] == 0
