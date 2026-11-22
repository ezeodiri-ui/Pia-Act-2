"""Central configuration. Override any value with an environment variable (or a .env file)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_env = ROOT / ".env"
if _env.exists():
    for _l in _env.read_text().splitlines():
        if "=" in _l and not _l.lstrip().startswith("#"):
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"'))
DATA = ROOT / "data"
PDF_PATH = Path(os.getenv("PIA_PDF", DATA / "PIA_2021.pdf"))
PROVISIONS_JSON = DATA / "provisions.json"
CHUNKS_JSONL = DATA / "chunks.jsonl"
INDEX_DIR = DATA / "index"

# ---- Models -------------------------------------------------------------
# Dense embedder (long-context, strong on legal prose). Smaller alternative:
# BAAI/bge-base-en-v1.5
EMBED_MODEL = os.getenv("PIA_EMBED_MODEL", "BAAI/bge-m3")
# Cross-encoder reranker
RERANK_MODEL = os.getenv("PIA_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
# Generation model (Anthropic API)
LLM_MODEL = os.getenv("PIA_LLM_MODEL", "claude-sonnet-5-5")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ---- Chunking -----------------------------------------------------------
TARGET_TOKENS = 380          # preferred child-chunk size (approx. words * 1.3)
MAX_TOKENS = 560             # hard ceiling before a subsection is split at paragraph level
MIN_TOKENS = 60              # tiny neighbours are merged into the previous unit

# ---- Retrieval ----------------------------------------------------------
TOP_K_SPARSE = 40
TOP_K_DENSE = 40
RRF_K = 60
RERANK_CANDIDATES = 30
FINAL_SECTIONS = 6           # distinct sections/definitions given to the LLM
MAX_CONTEXT_TOKENS = 9000
XREF_EXPANSION = 3           # max cross-referenced provisions appended to context
