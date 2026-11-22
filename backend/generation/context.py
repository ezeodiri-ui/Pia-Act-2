"""Turn retrieval results into the numbered, verbatim provision blocks that the LLM may rely on."""
from __future__ import annotations

from backend import config
from backend.ingest.chunk import est_tokens


def ref_label(c) -> str:
    if c["kind"] == "schedule":
        return f"{c['schedule_name']}, {c['label']}"
    if c["kind"] == "definition":
        return f"s.318 (Interpretation), {c['label']}"
    return f"s.{c['section']}"


def build_context(result: dict, corpus, max_tokens: int = config.MAX_CONTEXT_TOKENS) -> dict:
    """
    Returns {"blocks": [...], "text": str, "sections": set, "schedules": {ord: set(paras)}, "plain": str (for quote check)}
    Whole sections are supplied (small-to-big). Very long sections (> 1,400 tokens) are supplied as the matched chunks
    plus their immediate neighbours so the LLM never sees a fragment without its lead-in.
    """
    blocks, used = [], 0

    def add(kind, header, body, meta):
        nonlocal used
        t = est_tokens(body) + 30
        if used + t > max_tokens and blocks:
            return False
        blocks.append({"n": len(blocks) + 1, "header": header, "body": body, **meta})
        used += t
        return True

    seen_keys = set()
    for p in result["parents"]:
        key = p["key"]
        if key in seen_keys:
            continue
        seen_keys.add(key)
        chunk_ids = [c["id"] for c in p["chunks"]]
        head = corpus.by_id[chunk_ids[0]]
        if p["kind"] == "section":
            sec = corpus.sections[p["section"]]
            full = corpus.by_section[p["section"]]
            total = sum(c["tokens"] for c in full)
            if total <= 1400:
                body = sec["text"]
                span = "complete section"
            else:
                idxs = sorted({full.index(corpus.by_id[cid]) for cid in chunk_ids})
                keep = sorted({j for i in idxs for j in (i - 1, i, i + 1) if 0 <= j < len(full)})
                body = "\n[…]\n".join(full[j]["text"] for j in keep)
                span = "extract (long section; matched provisions with neighbours)"
            hdr = (f"Section {sec['number']} – {sec['title'].rstrip('.')} "
                   f"[Chapter {sec['chapter']}{', Part ' + sec['part'] if sec.get('part') else ''}; Gazette p. A{sec['page_start']}; {span}]")
            ok = add("section", hdr, body, {"kind": "section", "section": sec["number"], "key": key,
                                            "chunk_ids": chunk_ids, "page": sec["page_start"]})
        elif p["kind"] == "schedule":
            parts = corpus.by_parent[key]
            body = "\n".join(c["text"] for c in parts)
            lab = parts[0]["label"]
            pnum = key.split("-p")[1] if "-p" in key else ""
            para_txt = f", paragraph {pnum}" if pnum.isdigit() and pnum != "0" else ""
            ttl = head.get("heading") or head.get("schedule_title") or ""
            hdr = (f"{head['schedule_name']}{' – ' + ttl if ttl else ''}{para_txt} "
                   f"[Applies to: {head.get('applies_to') or 'n/a'}; Gazette p. A{head['page_start']}]")
            if not head.get("operative", True):
                hdr += " [NOT AN OPERATIVE PROVISION – explanatory memorandum]"
            ok = add("schedule", hdr, body, {"kind": "schedule", "schedule": head["schedule"], "key": key,
                                             "chunk_ids": [c["id"] for c in parts], "page": head["page_start"],
                                             "para": int(key.split("-p")[1]) if "-p" in key and key.split("-p")[1].isdigit() else None})
        else:  # definition
            ok = add("definition", f"Definition (s.318 Interpretation) – “{head['term']}”", head["text"],
                     {"kind": "definition", "section": 318, "key": key, "chunk_ids": [head["id"]], "page": head["page_start"]})
        if not ok:
            break

    # definitions attached by term detection
    for did in result.get("definitions", []):
        d = corpus.by_id[did]
        if any(b["key"] == did for b in blocks):
            continue
        add("definition", f"Definition (s.318 Interpretation) – “{d['term']}”", d["text"],
            {"kind": "definition", "section": 318, "key": did, "chunk_ids": [did], "page": d["page_start"], "attached": True})

    # cross-referenced provisions (flagged as secondary)
    for x in result.get("xrefs", []):
        cid = x["chunks"][0]
        c = corpus.by_id[cid]
        if x["type"] == "section":
            sec = corpus.sections[x["section"]]
            if est_tokens(sec["text"]) > 900:
                continue
            add("section", f"Section {sec['number']} – {sec['title'].rstrip('.')} [cross-referenced by a provision above; Gazette p. A{sec['page_start']}]",
                sec["text"], {"kind": "section", "section": sec["number"], "key": f"s{sec['number']}", "chunk_ids": x["chunks"],
                              "page": sec["page_start"], "xref": True})
        else:
            body = "\n".join(k["text"] for k in corpus.by_parent[c["parent_id"]])
            add("schedule", f"{c['schedule_name']} – {c.get('heading') or c.get('schedule_title') or ''}, {c['label']} [cross-referenced; Gazette p. A{c['page_start']}]",
                body, {"kind": "schedule", "schedule": c["schedule"], "key": c["parent_id"], "chunk_ids": [c["id"]],
                       "page": c["page_start"], "xref": True,
                       "para": int(c["parent_id"].split("-p")[1]) if "-p" in c["parent_id"] and c["parent_id"].split("-p")[1].isdigit() else None})

    text = "\n\n".join(f"=== PROVISION {b['n']}: {b['header']} ===\n{b['body']}" for b in blocks)
    sections = {b["section"] for b in blocks if b["kind"] in ("section",) or b["kind"] == "definition"}
    schedules = {}
    for b in blocks:
        if b["kind"] == "schedule":
            schedules.setdefault(b["schedule"], set()).add(b.get("para"))
    return {"blocks": blocks, "text": text, "sections": sections, "schedules": schedules,
            "plain": " ".join(b["body"] for b in blocks)}
