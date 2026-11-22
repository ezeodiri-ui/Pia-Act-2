"""
Structure-aware parser for the Petroleum Industry Act 2021 (Gazette Extraordinary No. 142).

Goals
-----
* Strip the running headers and the marginal side-notes (they sit in a left/right margin
  that flips from page to page) so they never pollute the statutory text.
* Recover the legal hierarchy:   Chapter > Part > Section > Subsection > Paragraph,
  plus the Schedules (with their Parts / paragraphs) and the Interpretation section (s.318).
* Use the "Arrangement of Sections" as the authoritative source of section titles and
  validate that sections 1..318 are found in strict sequence.
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pymupdf  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import config  # noqa: E402

TOC_ANOMALIES = []
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}
ORDINALS = ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH"]

HEADER_RE = re.compile(r"^(A \d+ )?2021 No\. 6( A \d+)? Petroleum Industry Act, 2021$")
HDR_LINE_1 = re.compile(r"^(A\s*\d+\s+)?2021\s*No\.\s*6(\s+A\s*\d+)?$")
HDR_LINE_2 = re.compile(r"^Petroleum Industry Act,?\s*2021$")
CHAPTER_RE = re.compile(r"^CHAPTER (\d+)\s*[—–-]\s*(.+)$")
PART_RE = re.compile(r"^PART ([IVX]+)\s*[—–-]\s*(.+)$")
SECTION_START_RE = r"^{n}\s*\.\s*[—–-]?\s*"
SUBSEC_RE = re.compile(r"^\((\d+)([A-Z]?)\)\s*[—–-]?\s*")
PARA_RE = re.compile(r"^\(([a-z]{1,2})\)\s*")
ROMAN_PARA_RE = re.compile(r"^\(((?:i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii))\)\s*")


def next_letter(l: str) -> str:
    """a..z, then the gazette convention aa, bb, cc ... zz"""
    if len(l) == 1:
        return chr(ord(l) + 1) if l < "z" else "aa"
    return chr(ord(l[0]) + 1) * 2 if l[0] < "z" else "zzz"


def clean(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s).strip()
    s = re.sub(r"\s+([;:,])", r"\1", s)          # "operations ;"  ->  "operations;"
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"\(\s*f\s*\)", "(f)", s)           # "(f )" artefact
    return s


# --------------------------------------------------------------------------
# 1. Page-level extraction (headers + margin notes removed)
# --------------------------------------------------------------------------
def extract_pages(doc):
    """Return a list of dicts: {idx, gazette, lines:[text], margin:[text]}.

    Works at *line* level (not block level) because pdf layout engines sometimes glue a
    marginal side-note onto the last line of a body block (e.g. 'Citation.' after s.319).
    """
    pages = []
    for idx, page in enumerate(doc):
        d = page.get_text("dict")
        blocks = [b for b in d["blocks"] if b.get("type") == 0]
        all_lines = []
        for b in blocks:
            btxt = " ".join(" ".join(sp["text"] for sp in ln["spans"]) for ln in b["lines"])
            for ln in b["lines"]:
                txt = "".join(sp["text"] for sp in ln["spans"])
                x0, y0, x1, y1 = ln["bbox"]
                all_lines.append((x0, y0, x1, y1, txt, " ".join(btxt.split())))
        # Page body column = extent of the long lines below the running header
        wide = [l for l in all_lines if (l[2] - l[0]) > 200 and l[1] > 150]
        bmin = min((l[0] for l in wide), default=90)
        bmax = max((l[2] for l in wide), default=500)
        gazette, body, margin = None, [], []
        for (x0, y0, x1, y1, txt, btxt) in all_lines:
            ctxt = " ".join(txt.split())
            if HEADER_RE.match(btxt) or (y0 < 175 and (HDR_LINE_1.match(ctxt) or HDR_LINE_2.match(ctxt))):
                g = re.search(r"A\s*(\d+)", ctxt)
                gazette = int(g.group(1)) if g else gazette
                continue
            if y0 > 150 and (x1 - x0) < 95 and (x1 <= bmin + 8 or x0 >= bmax - 8):
                margin.append(clean(txt))
                continue
            body.append((x0, y0, txt))
        body.sort(key=lambda l: (round(l[1] / 2.5), l[0]))
        lines = [clean(t) for (_, _, t) in body if clean(t)]
        pages.append({"idx": idx, "gazette": gazette, "lines": lines, "margin": [m for m in margin if m]})
    return pages


# --------------------------------------------------------------------------
# 2. Table of contents  (authoritative titles)
# --------------------------------------------------------------------------
def parse_toc(pages, body_start_idx):
    toc_sections, toc_parts, toc_chapters = {}, {}, {}
    cur_chapter, cur_part = None, None
    last = None  # ("sec", n) | ("part", key) | ("chap", n)
    for p in pages[:body_start_idx]:
        for ln in p["lines"]:
            if ln.startswith("ARRANGEMENT") or ln.startswith("Section :") or ln.startswith("SCHEDULES"):
                continue
            mc = CHAPTER_RE.match(ln)
            if mc:
                cur_chapter = int(mc.group(1))
                toc_chapters[cur_chapter] = clean(mc.group(2))
                last = ("chap", cur_chapter)
                continue
            mp = PART_RE.match(ln)
            if mp and cur_chapter:
                cur_part = mp.group(1)
                toc_parts[(cur_chapter, cur_part)] = clean(mp.group(2))
                last = ("part", (cur_chapter, cur_part))
                continue
            ms = re.match(r"^(\d{1,3})\s*\.?\s+([A-Z].*)$", ln)
            nxt = (max(toc_sections) + 1) if toc_sections else 1
            # The gazetted ToC contains a printing error ("235" appears twice, the first being s.234),
            # so we accept a printed number within +/-2 of the expected next one and renumber sequentially.
            if ms and abs(int(ms.group(1)) - nxt) <= 2:
                n = nxt
                if int(ms.group(1)) != nxt:
                    TOC_ANOMALIES.append(f"ToC prints '{ms.group(1)}.' where section {nxt} is expected: {ms.group(2)[:50]}")
                toc_sections[n] = {"title": clean(ms.group(2)), "chapter": cur_chapter, "part": cur_part}
                last = ("sec", n)
                continue
            # continuation line
            if last is None:
                continue
            if last[0] == "sec":
                toc_sections[last[1]]["title"] = clean(toc_sections[last[1]]["title"] + " " + ln)
            elif last[0] == "part" and ln.upper() == ln:
                toc_parts[last[1]] = clean(toc_parts[last[1]] + " " + ln)
            elif last[0] == "chap" and ln.upper() == ln:
                toc_chapters[last[1]] = clean(toc_chapters[last[1]] + " " + ln)
    return toc_sections, toc_parts, toc_chapters


# --------------------------------------------------------------------------
# 3. Body flow:  headings, sections, subsections, paragraphs
# --------------------------------------------------------------------------
def find_body_start(pages):
    for p in pages:
        t = " ".join(p["lines"])
        if "territorial waters" in t and "vested in" in t:
            return p["idx"]
    raise RuntimeError("Could not locate body start")


def flatten(pages, start_idx):
    """Flatten into (page_idx, gazette, line) triples."""
    out = []
    for p in pages[start_idx:]:
        for ln in p["lines"]:
            out.append((p["idx"], p["gazette"], ln))
    return out


def split_heading_from_body(ln):
    """A heading block may be glued to the first line of a section ('PART IV—… 29.—(1) There is…')."""
    m = re.match(r"^(PART [IVX]+\s*[—–-]\s*[A-Z ,’'&\-]+?)\s+(\d{1,3}\s*\.\s*[—–-]?.*)$", ln)
    if m:
        return m.group(1), m.group(2)
    return None, ln


def parse_body(flat, toc_sections, toc_parts, toc_chapters):
    sections = []
    cur = None
    expected = 1
    chapter, part = None, None
    schedules_started_at = None
    # locate where SCHEDULES begin
    for i, (_, _, ln) in enumerate(flat):
        if ln == "SCHEDULES":
            schedules_started_at = i
            break
    body_flat = flat[:schedules_started_at]
    sched_flat = flat[schedules_started_at + 1:]

    pending_heading_cont = None
    for (pidx, gaz, ln) in body_flat:
        head, rest = split_heading_from_body(ln)
        lines_to_process = [head, rest] if head else [ln]
        for l in lines_to_process:
            mc = CHAPTER_RE.match(l)
            if mc:
                chapter, part = int(mc.group(1)), None
                pending_heading_cont = "chapter"
                continue
            mp = PART_RE.match(l)
            if mp and chapter:
                part = mp.group(1)
                pending_heading_cont = "part"
                continue
            if pending_heading_cont and l.upper() == l and not re.match(r"^\d", l) and len(l) < 90 and not l.startswith("("):
                continue  # second line of a wrapped heading
            pending_heading_cont = None
            ms = re.match(SECTION_START_RE.format(n=expected), l)
            if ms and expected in toc_sections:
                if cur:
                    sections.append(cur)
                meta = toc_sections[expected]
                cur = {
                    "number": expected,
                    "title": meta["title"],
                    "chapter": chapter,
                    "chapter_title": toc_chapters.get(chapter, ""),
                    "part": part,
                    "part_title": toc_parts.get((chapter, part), ""),
                    "page_start": gaz, "page_end": gaz,
                    "pdf_page": pidx + 1,
                    "raw_lines": [l[ms.end():]],
                }
                expected += 1
                continue
            if cur is not None:
                cur["raw_lines"].append(l)
                if gaz:
                    cur["page_end"] = gaz
    if cur:
        sections.append(cur)
    return sections, sched_flat


# ---- inside a section: build subsection / paragraph tree -------------------
def build_tree(raw_lines):
    """
    Return list of subsections:
       {"label": "(1)", "text": "...", "paras":[{"label":"(a)","text":..,"subparas":[{"label":"(i)","text":..}]}]}
    A section with no numbered subsections gets a single subsection with label "".
    Sequence checks (expected next number/letter) prevent false splits on wrapped lines.
    """
    subs = []
    cur_sub = None
    cur_par = None
    cur_sp = None
    exp_sub = 1
    exp_par = "a"
    exp_roman = 1
    romans = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii", "xiv", "xv"]

    def new_sub(label, text):
        nonlocal cur_sub, cur_par, cur_sp, exp_par, exp_roman
        cur_sub = {"label": label, "text": text, "paras": []}
        subs.append(cur_sub)
        cur_par = cur_sp = None
        exp_par, exp_roman = "a", 1

    for ln in raw_lines:
        # numbered subsection
        m = SUBSEC_RE.match(ln)
        if m and (int(m.group(1)) == exp_sub and not m.group(2)):
            new_sub(f"({m.group(1)})", ln[m.end():])
            exp_sub += 1
            continue
        if m and m.group(2) and cur_sub is not None:       # e.g. (2A)
            new_sub(f"({m.group(1)}{m.group(2)})", ln[m.end():])
            continue
        mr = ROMAN_PARA_RE.match(ln)
        mp = PARA_RE.match(ln)
        # Prefer lettered paragraph when letter is the expected one (handles (i) after (h))
        if mp and cur_sub is not None and mp.group(1) == exp_par and not (mp.group(1) == "i" and exp_par != "i"):
            cur_par = {"label": f"({mp.group(1)})", "text": ln[mp.end():], "subparas": []}
            cur_sub["paras"].append(cur_par)
            exp_par = next_letter(exp_par)
            cur_sp, exp_roman = None, 1
            continue
        if mr and cur_par is not None and mr.group(1) == romans[min(exp_roman - 1, len(romans) - 1)]:
            cur_sp = {"label": f"({mr.group(1)})", "text": ln[mr.end():]}
            cur_par["subparas"].append(cur_sp)
            exp_roman += 1
            continue
        # continuation line
        if cur_sp is not None:
            cur_sp["text"] += " " + ln
        elif cur_par is not None:
            cur_par["text"] += " " + ln
        elif cur_sub is not None:
            cur_sub["text"] += " " + ln
        else:
            cur_sub = {"label": "", "text": ln, "paras": []}
            subs.append(cur_sub)
    for s in subs:
        s["text"] = clean(s["text"])
        for p in s["paras"]:
            p["text"] = clean(p["text"])
            for sp in p["subparas"]:
                sp["text"] = clean(sp["text"])
    # If the parser found a (1) that actually had no leading text but lead-in is on the section line, fine.
    return subs


def tree_to_text(subs, with_labels=True):
    out = []
    for s in subs:
        head = (s["label"] + " " if (with_labels and s["label"]) else "") + s["text"]
        out.append(head.strip())
        for p in s["paras"]:
            out.append("    " + p["label"] + " " + p["text"])
            for sp in p["subparas"]:
                out.append("        " + sp["label"] + " " + sp["text"])
    return "\n".join(out)


# --------------------------------------------------------------------------
# 4. Interpretation (s.318) -> definitions
# --------------------------------------------------------------------------
DEF_START = re.compile(r"^[“\"]([^”\"]{1,90})[”\"]\s*(.*)$")


def parse_definitions(section):
    defs, cur = [], None
    for ln in section["raw_lines"]:
        m = DEF_START.match(ln)
        if m and re.match(r"^(means|includes|has|refers|shall|in relation|is|are)\b", m.group(2) or "", re.I) or (
            m and m.group(2).startswith(("means", "includes"))):
            if cur:
                defs.append(cur)
            cur = {"term": clean(m.group(1)), "text": clean(ln)}
        elif m and cur is None:
            cur = {"term": clean(m.group(1)), "text": clean(ln)}
        elif cur is not None:
            cur["text"] += " " + ln
    if cur:
        defs.append(cur)
    for d in defs:
        d["text"] = clean(d["text"])
    return defs


# --------------------------------------------------------------------------
# 5. Schedules
# --------------------------------------------------------------------------
SCHED_HEAD = re.compile(r"^(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH) SCHEDULE\b\s*(.*)$")
PARA_START = r"^{n}\s*\.\s*[—–-]?\s*"


def parse_schedules(sched_flat, toc_parts_unused=None):
    """Group raw schedule lines then structure them into numbered paragraphs."""
    raw_scheds, cur = [], None
    for (pidx, gaz, ln) in sched_flat:
        m = SCHED_HEAD.match(ln)
        if m:
            cur = {"name": f"{m.group(1).title()} Schedule", "ordinal": ORDINALS.index(m.group(1)) + 1,
                   "first_line": clean(m.group(2)), "page_start": gaz, "pdf_page": pidx + 1, "lines": [], "operative": True}
            raw_scheds.append(cur)
            continue
        if ln.startswith("SCHEDULE TO PETROLEUM INDUSTRY BILL"):
            cur = {"name": "Schedule to the Petroleum Industry Bill, 2021 (Explanatory Memorandum)", "ordinal": 9,
                   "first_line": "", "page_start": gaz, "pdf_page": pidx + 1, "lines": [], "operative": False}
            raw_scheds.append(cur)
            continue
        if cur is not None:
            cur["lines"].append((gaz, ln))
    return [structure_schedule(sc) for sc in raw_scheds]


def structure_schedule(sc):
    lines = ([(sc["page_start"], sc["first_line"])] if sc["first_line"] else []) + sc["lines"]
    # lead-in = everything before paragraph 1  (applies-to reference + title)
    first_para = next((i for i, (_, l) in enumerate(lines) if re.match(PARA_START.format(n=1), l)), None)
    lead = [l for _, l in lines[: first_para if first_para is not None else 3]]
    reference = " ".join(l for l in lead if re.match(r"^(Sections?\b|\d)", l) or l.endswith(",") or re.match(r"^\d", l))
    title_lines = [l for l in lead if l not in reference.split("  ")]
    # crude but effective: reference lines start with 'Section(s)' or continue a numeric list
    ref_lines, ttl_lines = [], []
    for l in lead:
        if not ttl_lines and (re.match(r"^Sections?\b", l) or (ref_lines and re.match(r"^[\d(]", l)) or (ref_lines and ref_lines[-1].endswith(","))):
            ref_lines.append(l)
        else:
            ttl_lines.append(l)
    reference = clean(" ".join(ref_lines))
    if sc["ordinal"] == 9:
        title = "Explanatory Memorandum (Schedule to the Bill) – not an operative provision"
    elif first_para is not None:
        # title = the leading all-caps/title line(s) before any sub-heading
        title = clean(" ".join(ttl_lines[:1]))
    else:
        title = clean(ttl_lines[0]) if ttl_lines else sc["name"]
    body = lines[first_para:] if first_para is not None else lines[len(ref_lines) + 1:]

    paragraphs, part, part_title = [], None, ""
    expected = 1
    cur = None
    pending_heading = None
    i = 0
    while i < len(body):
        gaz, ln = body[i]
        mp = PART_RE.match(ln)
        if mp:
            part, part_title = mp.group(1), clean(mp.group(2))
            i += 1
            continue
        ms = re.match(PARA_START.format(n=expected), ln)
        if ms and first_para is not None:
            if cur:
                paragraphs.append(cur)
            cur = {"number": expected, "heading": pending_heading or "", "part": part, "part_title": part_title,
                   "page": gaz, "raw": [ln[ms.end():]]}
            pending_heading = None
            expected += 1
            i += 1
            continue
        # side-heading: short, un-punctuated line followed by the next numbered paragraph
        nxt = body[i + 1][1] if i + 1 < len(body) else ""
        is_heading = (len(ln) < 95 and not ln.endswith((".", ";", ",", ":", "—", "-", "and", "or")) and not ln.startswith("(")
                      and re.match(PARA_START.format(n=expected), nxt) is not None)
        if is_heading:
            pending_heading = ln
            i += 1
            continue
        if cur is not None:
            cur["raw"].append(ln)
        else:
            # unnumbered schedule (e.g. the Fourth Schedule's formula)
            cur = {"number": None, "heading": title, "part": part, "part_title": part_title, "page": gaz, "raw": [ln]}
        i += 1
    if cur:
        paragraphs.append(cur)
    for pg in paragraphs:
        pg["subsections"] = build_tree(pg["raw"])
        pg["text"] = tree_to_text(pg["subsections"])
        del pg["raw"]
    return {"name": sc["name"], "ordinal": sc["ordinal"], "reference": reference, "title": title,
            "operative": sc["operative"], "page_start": sc["page_start"],
            "page_end": paragraphs[-1]["page"] if paragraphs else sc["page_start"], "pdf_page": sc["pdf_page"],
            "paragraphs": paragraphs}


# --------------------------------------------------------------------------
def main():
    doc = pymupdf.open(str(config.PDF_PATH))
    pages = extract_pages(doc)
    body_start = find_body_start(pages)
    toc_sections, toc_parts, toc_chapters = parse_toc(pages, body_start)
    flat = flatten(pages, body_start)
    sections, sched_flat = parse_body(flat, toc_sections, toc_parts, toc_chapters)

    for a in TOC_ANOMALIES:
        print("NOTE:", a)
    missing = sorted(set(toc_sections) - {s["number"] for s in sections})
    print(f"ToC sections: {len(toc_sections)} | parsed sections: {len(sections)} | missing: {missing[:20]}")
    assert len(sections) == len(toc_sections), "Section count mismatch; inspect parser"

    definitions = []
    for s in sections:
        s["subsections"] = build_tree(s["raw_lines"])
        s["text"] = tree_to_text(s["subsections"])
        if s["number"] == 318:
            definitions = parse_definitions(s)
            for d in definitions:
                d["section"] = 318
                d["page"] = s["page_start"]
        del s["raw_lines"]

    schedules = parse_schedules(sched_flat)

    out = {
        "instrument": "Petroleum Industry Act, 2021",
        "citation": "Act No. 6 of 2021 (Federal Republic of Nigeria Official Gazette No. 142, Vol. 108, 27 August 2021)",
        "chapters": {str(k): v for k, v in toc_chapters.items()},
        "parts": {f"{k[0]}-{k[1]}": v for k, v in toc_parts.items()},
        "sections": sections,
        "definitions": definitions,
        "schedules": schedules,
    }
    config.DATA.mkdir(exist_ok=True)
    config.PROVISIONS_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"definitions: {len(definitions)}")
    for sc in schedules:
        print(f"  {sc['name']}: {len(sc['paragraphs'])} paragraphs | ref='{sc['reference']}' | title='{sc['title'][:60]}'")
    print(f"wrote {config.PROVISIONS_JSON}")


if __name__ == "__main__":
    main()
