"""
Legal-structure-aware chunking for the Petroleum Industry Act 2021.

Principles (why this preserves semantics)
-----------------------------------------
1. NEVER cut across a legal unit. Boundaries are only placed between subsections; a subsection is only
   split (between lettered paragraphs) when it alone exceeds MAX_TOKENS, and then the lead-in words
   ("The Commission shall—") are repeated in every piece so each piece still reads as a complete rule.
2. Small sections stay whole (one section = one chunk) so that definitions, exceptions and provisos
   that sit in later subsections are never separated from the rule they qualify.
3. Every chunk carries a breadcrumb ("Chapter 4 > Part II Hydrocarbon Tax > s.306 Royalty") that is
   prepended to the text that is EMBEDDED/INDEXED (contextual chunk headers) but NOT to the text shown
   to users, which stays verbatim.
4. Parent-child ("small-to-big"): child chunks are retrieved; the generator receives the full parent
   section so nothing is read out of context.
5. s.318 Interpretation is split per defined term (190 definitions) – each is a self-contained chunk.
6. Schedules are chunked per numbered paragraph with their Part and side-heading.
7. Cross-references ("section 306 of this Act", "paragraph 10 of the Seventh Schedule") are extracted
   and stored as edges so retrieval can pull in the provisions a rule depends on.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import config  # noqa: E402

WORD = re.compile(r"\S+")


def est_tokens(text: str) -> int:
    return int(len(WORD.findall(text)) * 1.35) + 1


# --------------------------------------------------------------------------
# Cross-reference extraction
# --------------------------------------------------------------------------
SEC_REF = re.compile(
    r"\bsections?\s+(\d{1,3})(?:\s*\(\s*\w+\s*\))*"
    r"((?:\s*(?:,|and|or|to|–|-)\s*(\d{1,3})(?:\s*\(\s*\w+\s*\))*)*)", re.I)
SCHED_REF = re.compile(r"\b(First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth)\s+Schedule\b", re.I)
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}


def extract_refs(text: str, own_section: int | None = None):
    secs = set()
    for m in SEC_REF.finditer(text):
        secs.add(int(m.group(1)))
        for n in re.findall(r"(\d{1,3})", m.group(2) or ""):
            # only treat as section numbers when plausible (<=319)
            if 1 <= int(n) <= 319:
                secs.add(int(n))
    secs = {n for n in secs if 1 <= n <= 319 and n != own_section}
    scheds = {ORD[m.group(1).lower()] for m in SCHED_REF.finditer(text)}
    return sorted(secs), sorted(scheds)


# --------------------------------------------------------------------------
def breadcrumb(sec, prov):
    ch = sec.get("chapter")
    parts = [f"Petroleum Industry Act 2021", f"Chapter {ch} – {sec.get('chapter_title','').title()}"]
    if sec.get("part"):
        parts.append(f"Part {sec['part']} – {sec.get('part_title','').title()}")
    parts.append(f"Section {sec['number']} – {sec['title'].rstrip('.')}")
    return " > ".join(parts)


def unit_text(sub, with_paras=True, paras=None):
    lines = [(sub["label"] + " " + sub["text"]).strip()]
    for p in (paras if paras is not None else sub["paras"]):
        lines.append("    " + p["label"] + " " + p["text"])
        for sp in p["subparas"]:
            lines.append("        " + sp["label"] + " " + sp["text"])
    return "\n".join(lines)


def split_big_subsection(sub, max_tokens):
    """Split one oversized subsection at lettered-paragraph boundaries, repeating the lead-in."""
    paras = sub["paras"]
    if not paras:
        return [(sub["label"], unit_text(sub))]
    lead = (sub["label"] + " " + sub["text"]).strip()
    pieces, cur, cur_tok = [], [], est_tokens(lead)
    for p in paras:
        ptxt = unit_text({"label": "", "text": "", "paras": []}, paras=[p])
        t = est_tokens(ptxt)
        if cur and cur_tok + t > max_tokens:
            pieces.append(cur)
            cur, cur_tok = [], est_tokens(lead)
        cur.append(p)
        cur_tok += t
    if cur:
        pieces.append(cur)
    out = []
    for k, group in enumerate(pieces):
        lab = f"{sub['label']}{group[0]['label']}–{group[-1]['label']}" if len(group) > 1 else f"{sub['label']}{group[0]['label']}"
        txt = lead + ("\n[… lead-in repeated for continuity …]" if k > 0 else "") + "\n" + \
            unit_text({"label": "", "text": "", "paras": []}, paras=group)
        out.append((lab, txt.replace("\n\n", "\n")))
    return out


def chunk_section(sec, prov, cid_start):
    crumb = breadcrumb(sec, prov)
    subs = sec["subsections"]
    full_tokens = est_tokens(sec["text"])
    chunks = []
    if full_tokens <= config.MAX_TOKENS:
        groups = [(f"{subs[0]['label']}–{subs[-1]['label']}" if len(subs) > 1 else subs[0]["label"] or "whole section", sec["text"])]
    else:
        # greedily pack whole subsections up to TARGET tokens; split oversized ones
        units = []
        for s in subs:
            if est_tokens(unit_text(s)) > config.MAX_TOKENS:
                units.extend(split_big_subsection(s, config.TARGET_TOKENS))
            else:
                units.append((s["label"], unit_text(s)))
        groups, cur, cur_tok = [], [], 0
        for lab, txt in units:
            t = est_tokens(txt)
            if cur and cur_tok + t > config.TARGET_TOKENS:
                groups.append(cur)
                cur, cur_tok = [], 0
            cur.append((lab, txt))
            cur_tok += t
        if cur:
            groups.append(cur)
        # merge a tiny trailing group backwards (a lone proviso must not be orphaned)
        if len(groups) > 1 and sum(est_tokens(t) for _, t in groups[-1]) < config.MIN_TOKENS:
            groups[-2].extend(groups.pop())
        merged = []
        for g in groups:
            lab = g[0][0] if len(g) == 1 else f"{g[0][0]}–{g[-1][0]}"
            merged.append((lab, "\n".join(t for _, t in g)))
        groups = merged
    for k, (lab, txt) in enumerate(groups):
        refs, scheds = extract_refs(txt, sec["number"])
        chunks.append({
            "id": f"s{sec['number']}-{k+1}",
            "kind": "section",
            "section": sec["number"],
            "part_index": k + 1, "part_count": len(groups),
            "parent_id": f"s{sec['number']}",
            "title": sec["title"].rstrip("."),
            "label": lab,
            "chapter": sec["chapter"], "part": sec.get("part"),
            "breadcrumb": crumb,
            "page_start": sec["page_start"], "page_end": sec["page_end"],
            "text": txt,
            "embed_text": f"{crumb}\nProvision: {lab}\n{txt}",
            "refs_sections": refs, "refs_schedules": scheds,
            "tokens": est_tokens(txt),
        })
    return chunks


def chunk_definitions(prov):
    out = []
    for i, d in enumerate(prov["definitions"]):
        crumb = f"Petroleum Industry Act 2021 > Chapter 5 – Miscellaneous Provisions > Section 318 – Interpretation > Definition of “{d['term']}”"
        refs, scheds = extract_refs(d["text"], 318)
        out.append({
            "id": f"s318-d{i+1}", "kind": "definition", "section": 318, "parent_id": "s318",
            "title": f"Interpretation – “{d['term']}”", "label": f"definition of “{d['term']}”",
            "term": d["term"], "chapter": 5, "part": None, "breadcrumb": crumb,
            "page_start": d.get("page"), "page_end": d.get("page"),
            "text": d["text"], "embed_text": f"{crumb}\n{d['text']}",
            "refs_sections": refs, "refs_schedules": scheds, "tokens": est_tokens(d["text"]),
        })
    return out


def chunk_schedules(prov):
    out = []
    for sc in prov["schedules"]:
        for pg in sc["paragraphs"]:
            num = pg["number"]
            label = f"paragraph {num}" if num else "whole schedule"
            head = pg["heading"] or ""
            part = f" > Part {pg['part']} – {pg['part_title'].title()}" if pg.get("part") else ""
            crumb = (f"Petroleum Industry Act 2021 > {sc['name']} – {sc['title'].title() if sc['title'] else ''}{part}"
                     f"{' > ' + head if head else ''} > {label}")
            refs, scheds = extract_refs(pg["text"] + " " + sc["reference"], None)
            body = pg["text"]
            toks = est_tokens(body)
            segments = [(label, body)]
            if toks > config.MAX_TOKENS and pg["subsections"]:
                segments = []
                for s in pg["subsections"]:
                    if est_tokens(unit_text(s)) > config.MAX_TOKENS:
                        segments.extend(split_big_subsection(s, config.TARGET_TOKENS))
                    else:
                        segments.append((s["label"], unit_text(s)))
                packed, cur, ct = [], [], 0
                for lab, txt in segments:
                    t = est_tokens(txt)
                    if cur and ct + t > config.TARGET_TOKENS:
                        packed.append(cur); cur, ct = [], 0
                    cur.append((lab, txt)); ct += t
                if cur:
                    packed.append(cur)
                segments = [(f"{label} {g[0][0]}–{g[-1][0]}".strip() if len(g) > 1 else f"{label} {g[0][0]}".strip(),
                             "\n".join(t for _, t in g)) for g in packed]
            for k, (lab, txt) in enumerate(segments):
                cid = f"sch{sc['ordinal']}-p{num if num else 0}-{k+1}"
                out.append({
                    "id": cid, "kind": "schedule", "section": None, "schedule": sc["ordinal"],
                    "schedule_name": sc["name"], "operative": sc["operative"],
                    "parent_id": f"sch{sc['ordinal']}-p{num if num else 0}",
                    "title": f"{sc['name']}{(' – ' + (head or (sc['title'].title() if sc['title'] else ''))).rstrip(' –')}",
                    "heading": head, "schedule_title": sc["title"].title() if sc["title"] else "",
                    "label": lab, "chapter": None, "part": pg.get("part"), "breadcrumb": crumb,
                    "page_start": pg["page"], "page_end": pg["page"],
                    "applies_to": sc["reference"],
                    "text": txt, "embed_text": f"{crumb}\nApplies to: {sc['reference']}\n{txt}",
                    "refs_sections": refs, "refs_schedules": scheds, "tokens": est_tokens(txt),
                })
    return out


def build_chunks():
    prov = json.loads(config.PROVISIONS_JSON.read_text())
    chunks = []
    for sec in prov["sections"]:
        if sec["number"] == 318:
            chunks.extend(chunk_definitions(prov))
            continue
        chunks.extend(chunk_section(sec, prov, 0))
    chunks.extend(chunk_schedules(prov))
    with config.CHUNKS_JSONL.open("w") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return chunks, prov


if __name__ == "__main__":
    chunks, prov = build_chunks()
    import statistics as st
    toks = [c["tokens"] for c in chunks]
    kinds = {}
    for c in chunks:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"chunks: {len(chunks)} {kinds}")
    print(f"tokens  min={min(toks)}  median={int(st.median(toks))}  mean={int(st.mean(toks))}  p95={sorted(toks)[int(.95*len(toks))]}  max={max(toks)}")
    multi = [c for c in chunks if c["kind"] == "section" and c["part_count"] > 1]
    print(f"sections split into >1 chunk: {len({c['section'] for c in multi})}")
    over = [c["id"] for c in chunks if c["tokens"] > config.MAX_TOKENS * 1.25]
    print("oversized chunks:", over[:15])
