"""Text utilities: legal-aware tokenizer, stemming, and the domain query-expansion lexicon."""
from __future__ import annotations

import re

import snowballstemmer

_stem = snowballstemmer.stemmer("english")
_STOP = set("""a an and are as at be been being by for from has have had in into is it its of on or that the their there
this to was were which will with within who whom whose where when while than then these those such any all each other
also not no nor so if but""".split())
_TOKEN = re.compile(r"[a-z0-9]+(?:[.%][0-9]+)?%?|%")
_cache: dict[str, str] = {}


def stem(w: str) -> str:
    s = _cache.get(w)
    if s is None:
        s = _stem.stemWord(w)
        _cache[w] = s
    return s


def tokenize(text: str, keep_stop: bool = False) -> list[str]:
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    out = []
    for t in _TOKEN.findall(text):
        if not keep_stop and t in _STOP:
            continue
        out.append(stem(t) if t.isalpha() else t)
    return out


# ----------------------------------------------------------------------------
# Domain lexicon: acronyms and lay phrases -> statutory vocabulary.
# Used for SPARSE query expansion only (appended terms), never shown to the user.
# ----------------------------------------------------------------------------
LEXICON = {
    r"\bnuprc\b": "Nigerian Upstream Petroleum Regulatory Commission the Commission upstream",
    r"\bnmdpra\b": "Nigerian Midstream and Downstream Petroleum Regulatory Authority the Authority midstream downstream",
    r"\bnnpc( ltd| limited)?\b": "Nigerian National Petroleum Company Limited NNPC Limited",
    r"\bdpr\b": "Department of Petroleum Resources Commission Authority",
    r"\bhcdt\b|\bhost communit(y|ies) (development )?trust": "host communities development trust settlor",
    r"\bhydrocarbon tax\b|\bpetroleum profit tax\b|\bppt\b": "hydrocarbon tax chargeable tax petroleum mining lease",
    r"\bcit\b|\bcompanies income tax\b": "companies income tax Act",
    r"\bfef\b": "Frontier Exploration Fund frontier basins",
    r"\bnsia\b": "Nigerian Sovereign Investment Authority",
    r"\bfaac\b|\bfederation account\b": "Federation Account",
    r"\bijv(c)?\b": "incorporated joint venture company",
    r"\bpsc\b|\bproduction sharing\b": "production sharing contract profit oil",
    r"\bfdp\b": "field development plan",
    r"\bbopd\b": "barrels of oil per day",
    r"\bmarginal fields?\b": "marginal field farm-out",
    r"\bflar(e|ing)\b": "gas flaring venting natural gas flare penalties",
    r"\bdecommission(ing)?\b": "abandonment decommissioning fund",
    r"\bsubsidy\b": "price regulation petroleum products Authority",
    r"\bpms\b|\bpetrol\b|\bgasoline\b": "petroleum products premium motor spirit downstream",
    r"\bdangote\b|\brefiner(y|ies)\b": "crude oil refining licence domestic crude supply obligation",
    r"\bdsdp\b|\bdomestic (crude|gas) (supply|delivery)": "domestic crude oil supply obligation domestic gas delivery obligation",
    r"\btax appeal\b": "Tax Appeal Tribunal appeal assessment",
    r"\bfirs\b|\bnrs\b|\brevenue service\b": "Service Nigeria Revenue Service tax administration",
    r"\bcapital allowance\b": "capital allowances qualifying expenditure annual allowance",
    r"\bsignature bonus\b|\bbonus\b": "bonus renewal bonus rents fees royalties",
    r"\bterrain\b": "onshore shallow water deep offshore frontier basins",
    r"\bsettlor\b": "settlor holder of a petroleum mining lease host communities",
    r"\bwithin what (period|time)\b|\bhow long\b|\btime ?limit\b|\bdeadline\b|\bwhen must\b": "timeframe period within months days",
    r"\bwho owns?\b|\bownership\b|\bowner of\b": "vested vesting property and ownership of petroleum Government of the Federation",
    r"\bnot (be )?deductible\b|\bnon-?deductible\b|\bdisallowed\b": "deductions not allowed",
    r"\brepeal(ed|s)?\b|\breplace[sd]?\b|\bsupersede[sd]?\b": "repeals saving provisions transitional enactments repealed",
    r"\bchairman\b|\bboard\b": "Governing Board Chairman non-executive members",
}
_LEX = [(re.compile(k, re.I), v) for k, v in LEXICON.items()]


def expand_query(q: str) -> str:
    extra = []
    for rx, add in _LEX:
        if rx.search(q):
            extra.append(add)
    return (q + " " + " ".join(extra)).strip() if extra else q


# ----------------------------------------------------------------------------
# Explicit citation detector:  "section 306", "s. 104(2)", "sections 260 to 262", "Seventh Schedule para 10"
# ----------------------------------------------------------------------------
_SEC = re.compile(r"\b(?:sections?|secs?\.?|s\.)\s*(\d{1,3})(?:\s*\(\s*(\w+)\s*\))*(?:\s*(?:,|and|to|-|–)\s*(\d{1,3}))?", re.I)
_SCH = re.compile(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth)\s+schedule\b(?:[^.?\n]{0,40}?\bpara(?:graph)?s?\.?\s*(\d{1,2}))?", re.I)
_PARA_OF = re.compile(r"\bpara(?:graph)?s?\.?\s*(\d{1,2})\s+of\s+the\s+(first|second|third|fourth|fifth|sixth|seventh|eighth)\s+schedule\b", re.I)
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8}


def detect_citations(q: str):
    secs, scheds = [], []
    for m in _SEC.finditer(q):
        a = int(m.group(1))
        if 1 <= a <= 319:
            secs.append(a)
        if m.group(3) and 1 <= int(m.group(3)) <= 319:
            b = int(m.group(3))
            if re.search(r"\bto\b|-|–", q[m.start():m.end()]) and 0 < b - a <= 12:
                secs.extend(range(a + 1, b + 1))
            else:
                secs.append(b)
    for m in _SCH.finditer(q):
        scheds.append((_ORD[m.group(1).lower()], int(m.group(2)) if m.group(2) else None))
    for m in _PARA_OF.finditer(q):
        scheds.append((_ORD[m.group(2).lower()], int(m.group(1))))
    return sorted(set(secs)), sorted(set(scheds), key=lambda x: (x[0], x[1] or 0))


_DEF_INTENT = re.compile(r"\b(defin(e|ed|ition)|what (is|are) (a|an|the)?\s*\w+(\s\w+)?\??$|meaning of|means?\b|interpret(ation|ed)?|what does .{1,40} mean)", re.I)


def is_definitional(q: str) -> bool:
    """True when the user is asking what a term MEANS (so s.318 definition chunks should rank high)."""
    return bool(_DEF_INTENT.search(q.strip()))
