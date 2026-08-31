"""Zero-dependency .env loader + settings. Must be imported before anything else."""
import os

def _load_env(path):
    path = os.path.abspath(path)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

for cand in (
    os.path.join(os.getcwd(), ".env"),
    os.path.join(os.path.dirname(__file__), ".env"),
    os.path.join(os.path.dirname(__file__), "..", ".env"),
):
    _load_env(cand)

# ---- Settings ----
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "atoms_native.db"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
OPENAI_COMPAT_BASE_URL = os.environ.get("OPENAI_COMPAT_BASE_URL", "")
OPENAI_COMPAT_API_KEY = os.environ.get("OPENAI_COMPAT_API_KEY", "")
OPENAI_COMPAT_MODEL = os.environ.get("OPENAI_COMPAT_MODEL", "")

# ---- Free community provider (OpenRouter): one key serves DeepSeek V4 Flash
#      free + Nous Hermes free, no credit card. Made PRIMARY when its key is set. ----
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "deepseek/deepseek-v4-flash:free"
)


def _default_provider() -> str:
    """Prefer the free community provider (OpenRouter) when its key is present,
    otherwise fall back to the direct DeepSeek key."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return "deepseek"


LLM_PROVIDER = os.environ.get("LLM_PROVIDER") or _default_provider()

# Simple per-token rate limiting for generations (protect API quota on public demo)
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", "20"))  # generations per token per hour
