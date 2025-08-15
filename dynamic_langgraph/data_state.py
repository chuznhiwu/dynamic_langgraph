# dynamic_langgraph/data_state.py
"""Dataclass that defines the shared LangGraph state."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DataState:
    # 基础输入
    path: Path
    user_input: str
    session_id: Optional[str] = "default"

    # loader
    loaded: bool = False

    # analysis
    features: Optional[Dict[str, Any]] = None

    # viz
    viz_paths: List[str] = field(default_factory=list)
    viz_summary: Optional[str] = None

    # diagnosis
    diag: Optional[Dict[str, Any]] = None

    # summarizer
    summary: Optional[str] = None

    # 其他可能的下游产物（按你的原始设计保留）
    markdown_path: Optional[str] = None
    ocr_snippets: Optional[List[str]] = None
    has_ocr: bool = False
    asr_text: Optional[str] = None
