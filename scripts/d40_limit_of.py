"""A deployment's tokens-per-minute limit, from the headers of a one-token request. Prints the number, or 0."""
import os, sys
sys.path.insert(0, "src")
from errata_bench.llm import _load_dotenv
_load_dotenv()
from openai import OpenAI
model = sys.argv[1]
assert "claude" not in model.lower()
try:
    c = OpenAI(api_key=os.environ["AZURE_OPENAI_API_KEY"], base_url=os.environ["AZURE_OPENAI_BASE_URL"].rstrip("/"),
               timeout=60, max_retries=0)
    raw = c.chat.completions.with_raw_response.create(model=model, max_tokens=1, messages=[{"role": "user", "content": "OK"}])
    print(int(raw.headers.get("x-ratelimit-limit-tokens") or 0))
except Exception:  # noqa: BLE001 - a probe that fails reads as no quota change
    print(0)
