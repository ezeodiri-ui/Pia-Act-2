# PIA 2021 Legislative Research Assistant
Grounded, citation-verified RAG over the **Petroleum Industry Act, 2021** (Act No. 6 of 2021, 27 Aug 2021 Gazette).
Prepared for demonstration to the House Committee on Finance and the House Committee on Petroleum Resources.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-neural.txt        # BGE-M3 embedder + BGE reranker (downloads ~2.5 GB once; needs internet)
cp .env.example .env                           # put your ANTHROPIC_API_KEY in it
./run.sh                                       # -> http://localhost:8000
```
First run parses the PDF, chunks it and caches embeddings (a few minutes on CPU). Afterwards start-up takes seconds.
**Demo safety net:** with no API key the app runs in *retrieval-only mode* (verbatim, cited provisions); without the neural
models it falls back to BM25 + LSA. The header pills always show which mode is live.

## How it works
| Stage | What it does | Why |
|---|---|---|
| Parse | PyMuPDF at line level; strips running headers and the shifting marginal side-notes; rebuilds Chapter > Part > Section > Subsection > Paragraph from the Act's own *Arrangement of Sections* (validated: sections 1-319 found in strict sequence; one misprint in the gazetted ToC—"235" printed twice—is handled) | Clean text; accurate section titles and page cites |
| Chunk | Never cuts across a legal unit. Small sections stay whole; large ones split only **between subsections** (to ~380 tokens); an oversize subsection splits between lettered paragraphs with the **lead-in repeated**; s.318 is split into **190 per-term definitions**; Schedules per numbered paragraph with Part and side-heading; each chunk carries a breadcrumb that is embedded but not displayed | Provisos, exceptions and lead-ins stay attached to the rule they qualify |
| Cross-reference graph | Extracts "section N", "paragraph N of the Seventh Schedule" links in both directions | Pulls in provisions a rule depends on (e.g. s.306 → Seventh Schedule) |
| Retrieve | Explicit-citation pinning ("section 104", "para 10 of the Seventh Schedule") + BM25 (title-boosted, bigrams, legal lexicon) + dense BGE-M3 + optional LLM query rewrite/decomposition → weighted RRF → cross-encoder rerank → parent aggregation → definitions + cross-reference expansion | Hybrid covers exact legal terms and plain-language questions |
| Generate | Claude answers **only** from the supplied verbatim provisions, cites every statement `[s.104(2)]`, quotes exactly, flags gaps, provisos and delegated detail | Interpretation, not invention |
| Verify | Every citation must resolve to a supplied provision/subsection/paragraph; every “quotation” must appear verbatim in the supplied text. Shown under each answer; unverifiable citations turn red | The Committee can see the answer is checked |

## Interface
Ask tab (sample questions grouped for revenue / governance / licensing / citations), Browse-the-Act tree, click-through citations opening the
full provision (with the cited subsection highlighted, "refers to" / "cited by" links, Gazette page), Inspector toggle (retrieval ranks, rewrites, timings), Print/PDF.

## Evaluation (`python -m backend.eval.run_eval [--set heldout]`, `python -m pytest -q`)
Offline pipeline only (BM25 + LSA, no neural models), parent-level hit = an expected provision in the top 6:
| Set | n | Recall@6 | Recall@3 | MRR |
|---|---|---|---|---|
| Dev (used for tuning) | 49 | 1.00 | 0.84 | 0.74 |
| Held-out (written before tuning) | 20 | 0.95 | 0.80 | 0.73 |
Run the same command on your machine with the neural layers enabled and report those numbers.

## Limits to state openly in the demo
Text is the 2021 gazette; amendments, regulations and court decisions are not indexed. Tables are not used in the Act body (rates are text).
Answers are research support, not legal advice. The gazetted text contains a few typographical errors (e.g. "this act" in s.81(2)), reproduced verbatim.
