import re, json, logging
from pathlib import Path
from typing import List
from langchain_core.tools import tool
from ..config import CASE_PATH, DEFAULT_TASK_FLOW

_VALID_TASKS = {
    "analysis", "viz", "diagnosis",
    "summarizer", "convert_md", "md_summary","asr","audio_summary"
}

@tool
def choose_tasks(index: str) -> List[str]:
    """index 可以带杂字符，只抓里面第一串数字做序号"""
    # 1) 读模板
    try:
        templates = json.load(Path(CASE_PATH).open(encoding="utf-8"))
    except Exception as exc:
        logging.warning("case.json 读取失败：%s → 默认流程", exc)
        return DEFAULT_TASK_FLOW

    # 2) 提取数字序号（更宽容）
    m = re.search(r"\d+", index)
    if not m:
        logging.warning("字符串 '%s' 内未找到数字 → 默认流程", index)
        return DEFAULT_TASK_FLOW

    idx = int(m.group()) - 1           # 转 0-based
    if idx < 0 or idx >= len(templates):
        logging.warning("序号 %s 超出模板范围 → 默认流程", idx + 1)
        return DEFAULT_TASK_FLOW

    # 3) 拿 tasks 并校验
    tasks = templates[idx].get("tasks", [])
    if not (isinstance(tasks, list) and tasks and all(t in _VALID_TASKS for t in tasks)):
        logging.warning("模板 #%s 的 tasks 非法 → 默认流程", idx + 1)
        return DEFAULT_TASK_FLOW

    return tasks
