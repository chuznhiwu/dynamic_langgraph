"""Dataclass that defines the shared LangGraph state."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

@dataclass
class DataState:
    path: Path
    user_input: str
    features: Optional[Dict] = None
    viz_paths: List[str] = field(default_factory=list)
    viz_summary: Optional[str] = None
    diag: Optional[Dict] = None
    summary: Optional[str] = None
    markdown_path: Optional[str] = None
    ocr_snippets: Optional[List[str]] = None
    has_ocr: bool = False                       # 是否执行过 OCR
    asr_text: Optional[str] = None 