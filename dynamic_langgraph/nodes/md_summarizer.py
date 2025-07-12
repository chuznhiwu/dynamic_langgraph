"""
节点：对 Markdown（含 OCR 文字）生成摘要
"""
from pathlib import Path
from typing import List

from ..data_state import DataState
from ..llms import llm_main
from dynamic_langgraph.utils import debug_ai_message
from langchain_core.messages import AIMessage

_MAX_CHARS = 10000          # 长文截断上限
_SUMMARY_LANG = "zh"       # 摘要语言

# ---------- 内部辅助 ----------

def _load_text(path: str, limit: int) -> str:
    txt = Path(path).read_text(encoding="utf-8")
    return txt[:limit] + ("\n\n...(内容过长已截断)..." if len(txt) > limit else "")

def _combine_for_llm(md_text: str, ocr: List[str]) -> str:
    """把 Markdown 和 OCR 列表融合为 LLM 输入"""
    if not ocr:
        return md_text
    ocr_block = "\n\n".join(f"[图{i+1}] {t}" for i, t in enumerate(ocr) if t.strip())
    return f"{md_text}\n\n---\n## OCR 文字汇总\n{ocr_block}"

# ---------- 节点主体 ----------

def md_summarizer(state: DataState) -> DataState:
    if not state.markdown_path:
        raise ValueError("markdown_path 为空，无法做摘要")

    md_text = _load_text(state.markdown_path, _MAX_CHARS)
    full_text = _combine_for_llm(md_text, state.ocr_snippets)

    ...
    PROMPT_HEADER = (
        "你是一名资深技术编辑，请严格遵守以下要求：\n"
        "1. 用 **中文** 写作\n"
        "2. 长度一定要控制在 200 字以内（不要超过 400 字符）\n"
        "3. 原文中的英文请先翻译后再概括\n"
        "仅输出摘要正文，不要任何解释或标题。\n\n"
    )
    prompt = PROMPT_HEADER + full_text

    ai: AIMessage = llm_main.invoke(prompt)

    debug_ai_message(ai)

    state.summary = ai.content
    return state
