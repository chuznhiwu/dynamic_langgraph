"""Utility helpers used across the project."""
import base64, mimetypes
from pathlib import Path
import pandas as pd
from typing import Any
from pprint import pprint
from langchain_core.messages import AIMessage
import datetime, uuid, logging, os
import re

def load_df(path: str | Path) -> pd.DataFrame:
    """Robustly read numeric TXT/CSV into a DataFrame."""
    try:
        return pd.read_csv(path, sep=r"\s+", header=None).select_dtypes(include="number")
    except Exception:
        return pd.read_csv(path, header=None).select_dtypes(include="number")

def b64_image(image_path: str | Path) -> str:
    """Return data‑URL string for an image, convenient for multimodal LLMs."""
    image_path = Path(image_path)
    with image_path.open("rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    import mimetypes
    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    return f"data:{mime};base64,{b64}"

def debug_ai_message(ai):
    if not isinstance(ai, AIMessage):
        print("❗Not an AIMessage instance:", type(ai))
        return

    print(" content:")
    print(ai.content)
    print("\n tool_calls:")
    for call in ai.tool_calls:
        print(f"- Tool: {call['name']}, Args: {call['args']}")
    print("\n usage_metadata:")
    pprint(ai.usage_metadata)
    print("\n full dict:")
    pprint(ai.model_dump())



def split_thought_and_answer(content: str) -> tuple[str, str]:
    match = re.search(r"<think>(.*?)</think>", content, flags=re.S | re.I)
    thought = match.group(1).strip() if match else ""
    answer = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I).strip()
    return thought, answer
