"""
Post-hoc verification of a generated answer against the supplied provisions:
  * every bracketed citation must resolve to a provision that was actually supplied (and the subsection/paragraph must exist);
  * every “quotation” must appear verbatim (after whitespace/punctuation normalisation) in the supplied text.
"""
from __future__ import annotations

import re

CITE = re.compile(r"\[(s\.\s*\d{1,3}(?:\s*\([^)\]]{1,6}\))*(?:\s*[“\"][^”\"\]]{1,80}[”\"])?|"
                  r"(?:First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth) Schedule[^\]]{0,60})\]")
QUOTE = re.compile(r"[“\"]([^”\"]{12,600})[”\"]")
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("—", "-").replace("–", "-")
    s = re.sub(r"\[…\]|\.\.\.|…", " ", s)
    s = re.sub(r"[^a-z0-9%$£#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def verify(answer: str, ctx: dict, corpus) -> dict:
    plain = norm(ctx["plain"])
    cites, bad = [], []
    for m in CITE.finditer(answer):
        raw = m.group(1).strip()
        ok, why = True, ""
        if raw.lower().startswith("s."):
            num = int(re.match(r"s\.\s*(\d+)", raw, re.I).group(1))
            subs = re.findall(r"\(([^)]{1,6})\)", raw.split("“")[0])
            if num not in ctx["sections"]:
                ok, why = False, f"section {num} was not among the supplied provisions"
            elif num in corpus.sections and subs and num != 318:
                labels = {s["label"].strip("()") for s in corpus.sections[num]["subsections"]}
                if subs[0] not in labels and labels != {""}:
                    ok, why = False, f"section {num} has no subsection ({subs[0]})"
        else:
            so = ORD[raw.split()[0].lower()]
            pm = re.search(r"para(?:graph)?s?\.?\s*(\d+)", raw, re.I)
            if so not in ctx["schedules"]:
                ok, why = False, f"{raw.split(',')[0]} was not among the supplied provisions"
            elif pm and int(pm.group(1)) not in ctx["schedules"][so] and None not in ctx["schedules"][so]:
                ok, why = False, f"paragraph {pm.group(1)} of that Schedule was not supplied"
        cites.append({"text": raw, "ok": ok, "why": why})
        if not ok:
            bad.append(raw)
    quotes = []
    for m in QUOTE.finditer(answer):
        q = m.group(1)
        # skip bracketed citation strings like [s.318 “petroleum”]
        if answer[max(0, m.start() - 3):m.start()].strip().endswith(("318", "318 ")):
            continue
        nq = norm(q)
        found = bool(nq) and nq in plain
        quotes.append({"text": q[:140], "ok": found})
    uniq = {c["text"]: c for c in cites}
    return {
        "citations": list(uniq.values()),
        "citations_ok": sum(c["ok"] for c in uniq.values()), "citations_total": len(uniq),
        "quotes": quotes,
        "quotes_ok": sum(q["ok"] for q in quotes), "quotes_total": len(quotes),
        "problems": sorted(set(bad)) + [f"quotation not found verbatim: “{q['text'][:70]}…”" for q in quotes if not q["ok"]],
        "uncited_answer": len(uniq) == 0,
    }
