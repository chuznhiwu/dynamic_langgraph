"""Project‑wide constants and paths."""
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent
PLOTS_DIR: Path = ROOT / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

CASE_PATH: Path = ROOT / "case.json"

DEFAULT_TASK_FLOW = ["analysis", "viz", "diagnosis","summarizer"]
