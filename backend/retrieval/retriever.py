"""
Hybrid retrieval pipeline
-------------------------
  query ──► citation detector (pin exact sections/paragraphs)
        ──► lexicon expansion ──► BM25 (title-boosted, bigrams)  ┐
        ──► dense embeddings (BGE-M3) [or LSA offline fallback]  ├─► Reciprocal Rank Fusion
        ──► LLM rewrites / decomposition (optional, multi-query) ┘
        ──► cross-encoder rerank of the fused top-N
        ──► aggregate to parent provisions (section / schedule paragraph / definition)
        ──► attach definitions (s.318) + follow cross-references (sections, schedules)
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import config  # noqa: E402
from backend.retrieval.index import BM25, Corpus, LSA, doc_tokens, query_tokens  # noqa: E402
from backend.retrieval.text import detect_citations, expand_query, is_definitional  # noqa: E402


def _ranks(scores: np.ndarray) -> np.ndarray:
    order = np.argsort(-scores, kind="stable")
    r = np.empty(len(scores), dtype=np.int32)
    r[order] = np.arange(1, len(scores) + 1)
    return r


def _minmax(x: np.ndarray) -> np.ndarray:
    lo, hi = float(x.min()), float(x.max())
    return (x - lo) / (hi - lo + 1e-9)


class Retriever:
    def __init__(self, use_dense: bool = True, use_rerank: bool = True, verbose: bool = True):
        t0 = time.time()
        self.corpus = Corpus()
        self.chunks = self.corpus.chunks
        self.bm25 = BM25([doc_tokens(c) for c in self.chunks])
        self.dense = self.lsa = self.reranker = None
        self.status = {"chunks": len(self.chunks), "sparse": "BM25 (title-boosted, bigrams)"}
        texts = [c["embed_text"] for c in self.chunks]
        if use_dense:
            try:
                from backend.retrieval.index import Dense
                self.dense = Dense(texts, config.INDEX_DIR)
                self.status["dense"] = config.EMBED_MODEL
            except Exception as e:  # models not downloaded / no internet / library missing
                if verbose:
                    print(f"[retriever] dense model unavailable ({type(e).__name__}); using offline LSA fallback")
        if self.dense is None:
            self.lsa = LSA(texts)
            self.status["dense"] = "LSA (offline fallback)"
        if use_rerank:
            try:
                from backend.retrieval.index import Reranker
                self.reranker = Reranker()
                self.status["rerank"] = config.RERANK_MODEL
            except Exception as e:
                if verbose:
                    print(f"[retriever] reranker unavailable ({type(e).__name__}); using fused scores only")
        self.status.setdefault("rerank", "none (fusion only)")
        self.status["load_seconds"] = round(time.time() - t0, 1)
        self._def_rx = [(d, re.compile(r"\b" + re.escape(d["term"].lower()) + r"s?\b")) for d in self.corpus.definitions
                        if len(d["term"]) >= 4]

    # ------------------------------------------------------------------
    def _signals(self, query: str):
        sp = self.bm25.scores(query_tokens(expand_query(query)))
        if self.dense is not None:
            de = self.dense.scores(query)
        else:
            de = self.lsa.scores(expand_query(query))
        return sp, de

    def search(self, query: str, rewrites: list[str] | None = None, top_parents: int | None = None,
               rerank: bool = True) -> dict:
        t0 = time.time()
        top_parents = top_parents or config.FINAL_SECTIONS
        n = len(self.chunks)
        timings = {}

        # 1. explicit citations -> pinned chunks
        secs, scheds = detect_citations(query)
        pinned: dict[str, str] = {}
        for s in secs:
            for c in self.corpus.by_section.get(s, []):
                pinned[c["id"]] = f"cited: section {s}"
        soft_sched = set()
        specific = {so for (so, para) in scheds if para}
        for (so, para) in scheds:
            if para:
                for c in self.corpus.by_schedule.get(so, []):
                    if c["parent_id"] == f"sch{so}-p{para}":
                        pinned[c["id"]] = f"cited: schedule {so} para {para}"
            elif so not in specific:
                soft_sched.add(so)               # "the Seventh Schedule" alone: gentle boost only

        # 2. multi-signal retrieval over the query + rewrites; weighted RRF over *all* chunks
        queries = [(query, 1.0)] + [(r, 0.7) for r in (rewrites or [])[:3]]
        fused = np.zeros(n, dtype=np.float32)
        sp_main = de_main = None
        for qi, (q, w) in enumerate(queries):
            sp, de = self._signals(q)
            rs, rd = _ranks(sp), _ranks(de)
            fused += w * (1.0 / (config.RRF_K + rs) + 1.0 / (config.RRF_K + rd))
            if qi == 0:
                sp_main, de_main = sp, de
        rs_main, rd_main = _ranks(sp_main), _ranks(de_main)
        timings["retrieve_ms"] = int((time.time() - t0) * 1000)

        # 3. cross-encoder rerank of top-N
        order = np.argsort(-fused)
        cand = list(order[: config.RERANK_CANDIDATES])
        for cid in pinned:                       # pinned chunks always enter the candidate set
            i = self.corpus.idx[cid]
            if i not in cand:
                cand.append(i)
        rr = {}
        if self.reranker is not None and rerank:
            t1 = time.time()
            docs = [self.chunks[i]["breadcrumb"].split(" > ", 1)[-1] + "\n" + self.chunks[i]["text"] for i in cand]
            sc = self.reranker.score(query, docs)
            rr = {i: float(s) for i, s in zip(cand, sc)}
            timings["rerank_ms"] = int((time.time() - t1) * 1000)
        def_intent = is_definitional(query)
        fn = _minmax(fused[cand])
        final = {}
        for k, i in enumerate(cand):
            base = float(fn[k])
            score = (0.7 * rr[i] + 0.3 * base) if rr else base
            ck = self.chunks[i]
            if ck["id"] in pinned:
                score += 1.0
            elif ck["kind"] == "schedule" and ck["schedule"] in soft_sched:
                score += 0.25
            if ck["kind"] == "definition" and not def_intent:
                score *= 0.65                    # definition chunks are short and over-score on BM25 for non-definitional questions
            final[i] = score
        ranked = sorted(final, key=final.get, reverse=True)

        # 4. aggregate to parent provisions
        groups: dict[str, list[int]] = defaultdict(list)
        for i in ranked:
            c = self.chunks[i]
            key = c["id"] if c["kind"] == "definition" else c["parent_id"]
            groups[key].append(i)
        parents = []
        for key, idxs in groups.items():
            sc = [final[i] for i in idxs]
            score = max(sc) + 0.1 * sum(sorted(sc, reverse=True)[1:3])
            parents.append((score, key, idxs))
        parents.sort(reverse=True)
        parents = parents[:top_parents]

        out_parents = []
        for score, key, idxs in parents:
            ch = [self.chunks[i] for i in idxs]
            head = ch[0]
            out_parents.append({
                "key": key, "kind": head["kind"], "score": round(score, 4),
                "section": head.get("section"), "schedule": head.get("schedule"),
                "title": head["title"], "breadcrumb": head["breadcrumb"],
                "pinned": any(c["id"] in pinned for c in ch),
                "chunks": [{
                    "id": c["id"], "label": c["label"], "page_start": c["page_start"], "page_end": c["page_end"],
                    "score": round(final[self.corpus.idx[c["id"]]], 4),
                    "bm25_rank": int(rs_main[self.corpus.idx[c["id"]]]),
                    "dense_rank": int(rd_main[self.corpus.idx[c["id"]]]),
                    "rerank": round(rr.get(self.corpus.idx[c["id"]], -1), 4) if rr else None,
                } for c in ch],
            })

        # 5. definitions (query terms + terms recurring in the top provisions)
        top_text = " ".join(self.chunks[i]["text"].lower() for _, _, idxs in parents[:3] for i in idxs[:2])
        have = {p["key"] for p in out_parents}
        defs, qlow = [], query.lower()
        for d, rx in self._def_rx:
            if d["id"] in have:
                continue
            if rx.search(qlow):
                defs.append((2, d))
            elif len(rx.findall(top_text)) >= 3 and len(d["term"]) >= 6:
                defs.append((1, d))
        defs = [d for _, d in sorted(defs, key=lambda x: -x[0])[:4]]

        # 6. cross-reference expansion (sections + schedules cited by the top-3 provisions)
        cite_count, sched_refs = Counter(), Counter()
        for _, key, idxs in parents[:3]:
            for i in idxs[:3]:
                for s in self.chunks[i]["refs_sections"]:
                    cite_count[s] += 1
                for s in self.chunks[i]["refs_schedules"]:
                    sched_refs[s] += 1
        in_ctx_sections = {p["section"] for p in out_parents if p["section"]}
        xrefs = []
        for s, cnt in sorted(cite_count.items(), key=lambda kv: (-kv[1], -self._best_fused(fused, kv[0]))):
            if s in in_ctx_sections or s == 318 or s not in self.corpus.by_section:
                continue
            xrefs.append({"type": "section", "section": s, "chunks": [c["id"] for c in self.corpus.by_section[s][:2]],
                          "title": self.corpus.sections[s]["title"], "cited_by": cnt})
            if len(xrefs) >= config.XREF_EXPANSION:
                break
        have_sched = {p["schedule"] for p in out_parents if p["schedule"]}
        for so in sched_refs:
            if so in have_sched or so not in self.corpus.by_schedule:
                continue
            best = max(self.corpus.by_schedule[so], key=lambda c: fused[self.corpus.idx[c["id"]]])
            xrefs.append({"type": "schedule", "schedule": so, "chunks": [best["id"]], "title": best["title"], "cited_by": sched_refs[so]})

        timings["total_ms"] = int((time.time() - t0) * 1000)
        return {"query": query, "rewrites": rewrites or [], "pinned": pinned, "parents": out_parents,
                "definitions": [d["id"] for d in defs], "xrefs": xrefs, "timings": timings, "status": self.status}

    def _best_fused(self, fused, section):
        return max((fused[self.corpus.idx[c["id"]]] for c in self.corpus.by_section[section]), default=0)


if __name__ == "__main__":
    r = Retriever(use_dense=False, use_rerank=False)
    for q in sys.argv[1:] or ["What royalty rate applies to onshore crude oil production?"]:
        res = r.search(q)
        print("\nQ:", q, res["timings"])
        for p in res["parents"]:
            print(f"  {p['score']:.3f} {p['key']:10s} {p['title'][:70]}")
