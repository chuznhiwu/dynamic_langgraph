"""Statistical calculation tools exposed to the LLM."""
import json
from langchain_core.tools import tool
from ..utils import load_df

@tool
def calc_mean(path: str) -> str:
    """Return per‑column means as JSON."""
    return json.dumps({"mean": load_df(path).mean().to_dict()})

@tool
def calc_std(path: str) -> str:
    """Return per‑column standard deviations as JSON."""
    return json.dumps({"std": load_df(path).std().to_dict()})

@tool
def calc_var(path: str) -> str:
    """Return per‑column variances as JSON."""
    return json.dumps({"var": load_df(path).var().to_dict()})
