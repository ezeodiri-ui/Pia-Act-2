"""One-shot build: parse PDF -> chunk -> (pre-compute dense embeddings). Run once: python -m backend.ingest.build_index"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend import config
from backend.ingest import parse_pdf, chunk

def main():
    parse_pdf.main()
    chunks, _ = chunk.build_chunks()
    print(f"chunks written: {len(chunks)}")
    try:
        from backend.retrieval.index import Dense
        Dense([c["embed_text"] for c in chunks], config.INDEX_DIR)
        print("dense embeddings cached")
    except Exception as e:
        print(f"[warn] dense embeddings not built ({type(e).__name__}: {e}). The system will use the offline LSA fallback.\n"
              "       Install requirements-neural.txt and ensure internet access to download the models once.")

if __name__ == "__main__":
    main()
