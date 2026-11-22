"""Retrieval evaluation: Recall@k (any expected provision in top-k parents), strict recall, MRR, by query type."""
import json, sys, argparse
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.retrieval.retriever import Retriever


def hit(keys, expect):
    for rank, k in enumerate(keys, 1):
        if any(k == e or (e.endswith("-") and k.startswith(e)) for e in expect):
            return rank
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-dense", action="store_true"); ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--verbose", action="store_true"); ap.add_argument("--set", default="gold", choices=["gold","heldout"])
    a = ap.parse_args()
    r = Retriever(use_dense=not a.no_dense, use_rerank=not a.no_rerank)
    print("pipeline:", r.status)
    gold = [json.loads(l) for l in open(Path(__file__).with_name(a.set + ".jsonl"))]
    # sanity: every expected key must exist in the corpus
    keys = {c["id"] if c["kind"] == "definition" else c["parent_id"] for c in r.chunks}
    for g in gold:
        for e in g["expect"]:
            assert any(k == e or (e.endswith("-") and k.startswith(e)) for k in keys), f"gold key not in corpus: {e}"
    ks = (1, 3, 5, 6)
    agg = defaultdict(lambda: defaultdict(float)); n_by = defaultdict(int); fails = []
    for g in gold:
        res = r.search(g["q"], top_parents=6)
        got = [p["key"] for p in res["parents"]]
        rk = hit(got, g["expect"])
        for t in ("ALL", g["type"]):
            n_by[t] += 1
            for k in ks:
                agg[t][f"R@{k}"] += 1.0 if rk and rk <= k else 0.0
            agg[t]["MRR"] += (1.0 / rk) if rk else 0.0
        if not rk or a.verbose:
            fails.append((g["q"], g["expect"], got[:4], rk))
    print(f"\n{'type':12s} {'n':>3s} " + " ".join(f"R@{k:<4d}" for k in ks) + "  MRR")
    for t in ["ALL"] + sorted(x for x in n_by if x != "ALL"):
        n = n_by[t]
        print(f"{t:12s} {n:3d} " + " ".join(f"{agg[t][f'R@{k}']/n:6.2f}" for k in ks) + f" {agg[t]['MRR']/n:5.2f}")
    print("\nmisses / details:")
    for q, e, got, rk in fails:
        print(f" - rank={rk} | {q}\n     expected {e} | got {got}")

if __name__ == "__main__":
    main()
