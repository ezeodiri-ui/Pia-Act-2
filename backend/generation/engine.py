"""RAG engine: query understanding -> hybrid retrieval -> grounded generation (Claude) -> verification."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import config  # noqa: E402
from backend.generation.context import build_context  # noqa: E402
from backend.generation.prompts import REWRITE_SYSTEM, SYSTEM_PROMPT, USER_TEMPLATE  # noqa: E402
from backend.generation.verify import norm, verify  # noqa: E402
from backend.retrieval.retriever import Retriever  # noqa: E402
from backend.retrieval.text import tokenize  # noqa: E402

REWRITE_MODEL = __import__("os").getenv("PIA_REWRITE_MODEL", "claude-haiku-5-5")


class Engine:
    def __init__(self, use_dense=True, use_rerank=True):
        self.retriever = Retriever(use_dense=use_dense, use_rerank=use_rerank)
        self.corpus = self.retriever.corpus
        self.client = None
        if config.ANTHROPIC_API_KEY:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=2, timeout=60.0)
            except Exception as e:
                print(f"[engine] Anthropic client unavailable: {e}")
        self._rw_cache: dict[str, dict] = {}
        # reverse cross-reference index ("cited by")
        self.cited_by: dict[int, set[int]] = {}
        for c in self.corpus.chunks:
            if c["kind"] == "section":
                for s in c["refs_sections"]:
                    self.cited_by.setdefault(s, set()).add(c["section"])

    @property
    def llm_ready(self) -> bool:
        return self.client is not None

    # ---------------------------------------------------------------- query understanding
    def rewrite(self, question: str) -> dict:
        if question in self._rw_cache:
            return self._rw_cache[question]
        out = {"rewrites": [], "sub_questions": []}
        if self.client is not None:
            try:
                r = self.client.messages.create(model=REWRITE_MODEL, max_tokens=400, system=REWRITE_SYSTEM,
                                                messages=[{"role": "user", "content": question}])
                txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
                m = re.search(r"\{.*\}", txt, re.S)
                if m:
                    j = json.loads(m.group(0))
                    out["rewrites"] = [x for x in j.get("rewrites", []) if isinstance(x, str)][:3]
                    out["sub_questions"] = [x for x in j.get("sub_questions", []) if isinstance(x, str)][:3]
            except Exception as e:
                print(f"[engine] rewrite skipped: {type(e).__name__}")
        self._rw_cache[question] = out
        return out

    def condense(self, question: str, history: list[dict]) -> str:
        """Resolve follow-ups ('and for offshore?') into a standalone question."""
        if not history or self.client is None:
            return question
        convo = "\n".join(f"{h['role'].upper()}: {h['content'][:600]}" for h in history[-4:])
        try:
            r = self.client.messages.create(
                model=REWRITE_MODEL, max_tokens=200,
                system="Rewrite the final user question as a standalone question about the Petroleum Industry Act 2021, resolving pronouns and omitted context from the conversation. If it is already standalone, return it unchanged. Output only the question.",
                messages=[{"role": "user", "content": f"{convo}\nUSER: {question}"}])
            t = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
            return t or question
        except Exception:
            return question

    # ---------------------------------------------------------------- retrieval
    def retrieve(self, question: str, use_rewrite: bool = True):
        rw = self.rewrite(question) if use_rewrite else {"rewrites": [], "sub_questions": []}
        extra = rw["rewrites"] + rw["sub_questions"]
        res = self.retriever.search(question, rewrites=extra[:3])
        ctx = build_context(res, self.corpus)
        return res, ctx, rw

    def sources_payload(self, res, ctx):
        by_key = {p["key"]: p for p in res["parents"]}
        out = []
        for b in ctx["blocks"]:
            p = by_key.get(b["key"])
            body = b["body"].replace("\n", " ")
            out.append({
                "n": b["n"], "kind": b["kind"], "header": b["header"].split(" [")[0], "section": b.get("section"),
                "schedule": b.get("schedule"), "para": b.get("para"), "page": b.get("page"),
                "score": p["score"] if p else None, "pinned": bool(p and p.get("pinned")),
                "role": "cross-reference" if b.get("xref") else ("definition" if b.get("attached") else "retrieved"),
                "snippet": body[:260] + ("…" if len(body) > 260 else ""),
                "chunks": (p["chunks"] if p else []),
            })
        return out

    # ---------------------------------------------------------------- generation
    def ask_stream(self, question: str, history: list[dict] | None = None, use_rewrite: bool = True):
        """Generator of (event, payload) tuples for SSE."""
        t0 = time.time()
        history = history or []
        standalone = self.condense(question, history)
        res, ctx, rw = self.retrieve(standalone, use_rewrite=use_rewrite)
        yield "retrieval", {
            "standalone_question": standalone, "rewrites": rw["rewrites"], "sub_questions": rw["sub_questions"],
            "sources": self.sources_payload(res, ctx), "timings": res["timings"], "pipeline": res["status"],
            "pinned": res["pinned"],
        }
        if self.client is None:
            ans = self.extractive_answer(standalone, ctx)
            yield "token", {"text": ans}
            yield "verification", verify(ans, ctx, self.corpus)
            yield "done", {"mode": "retrieval-only", "total_ms": int((time.time() - t0) * 1000)}
            return
        msgs = [{"role": h["role"], "content": h["content"]} for h in history[-4:] if h["role"] in ("user", "assistant")]
        msgs.append({"role": "user", "content": USER_TEMPLATE.format(question=question if not history else f"{question}\n(Standalone form: {standalone})", context=ctx["text"])})
        text_parts = []
        try:
            kwargs = dict(model=config.LLM_MODEL, max_tokens=1800, system=SYSTEM_PROMPT, messages=msgs, temperature=0)
            try:
                stream_cm = self.client.messages.stream(**kwargs)
                stream = stream_cm.__enter__()
            except Exception as e:
                if "temperature" in str(e).lower():
                    kwargs.pop("temperature")
                    stream_cm = self.client.messages.stream(**kwargs)
                    stream = stream_cm.__enter__()
                else:
                    raise
            try:
                for t in stream.text_stream:
                    text_parts.append(t)
                    yield "token", {"text": t}
            finally:
                stream_cm.__exit__(None, None, None)
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
            fallback = self.extractive_answer(standalone, ctx)
            yield "notice", {"message": f"The language model could not be reached ({err}). Showing retrieval-only answer."}
            yield "token", {"text": fallback}
            yield "verification", verify(fallback, ctx, self.corpus)
            yield "done", {"mode": "retrieval-only (fallback)", "total_ms": int((time.time() - t0) * 1000)}
            return
        answer = "".join(text_parts)
        yield "verification", verify(answer, ctx, self.corpus)
        yield "done", {"mode": "generated", "model": config.LLM_MODEL, "total_ms": int((time.time() - t0) * 1000)}

    # ---------------------------------------------------------------- offline fallback
    def extractive_answer(self, question: str, ctx: dict) -> str:
        """Retrieval-only answer: the most relevant verbatim sentences, each cited. Never fabricates."""
        qt = set(tokenize(question))
        lines = ["**Retrieval-only mode** — no language model is connected, so these are the most relevant verbatim provisions, not an interpretation.\n"]
        shown = 0
        for b in ctx["blocks"]:
            if b.get("xref") or b.get("attached") or shown >= 4:
                continue
            sents = [s.strip() for s in re.split(r"(?<=[;.])\s+(?=[A-Z(“])|\n", b["body"])
                     if len(s.strip()) > 25 or (len(s.strip()) > 10 and re.search(r"\d", s))]
            wants_num = bool(re.search(r"\b(rate|percent|per cent|how much|penalt|fee|amount|period|months|days)\b", question, re.I))

            def score(s):
                v = len(qt & set(tokenize(s)))
                if wants_num and re.search(r"\d", s):
                    v += 2
                return -v
            scored = sorted(sents, key=score)[:3]
            if not scored:
                continue
            if b["kind"] == "section":
                cite = f"[s.{b['section']}]"
            elif b["kind"] == "schedule":
                nm = b["header"].split(" – ")[0]
                cite = f"[{nm}, para. {b['para']}]" if b.get("para") else f"[{nm}]"
            else:
                cite = f"[s.318 “{b['header'].split('“')[-1].rstrip('”')}”]"
            title = b["header"].split(" [")[0]
            lines.append(f"**{title}**")
            for s in scored:
                s = s.strip().replace('"', "”")
                lines.append(f"> “{s[:420]}” {cite}")
            lines.append("")
            shown += 1
        if shown == 0:
            lines.append("No sufficiently relevant provision was found. Try rephrasing, or cite a section number.")
        return "\n".join(lines)
