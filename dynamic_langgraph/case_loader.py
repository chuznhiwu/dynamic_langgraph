# dynamic_langgraph/case_loader.py
# ============================================================================
# Imports
# ============================================================================
import json
import logging
import re
from pathlib import Path
from typing import Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from dynamic_langgraph.utils import debug_ai_message
from .llms import llm_planner
from .config import CASE_PATH, DEFAULT_TASK_FLOW
from .tools.task_selector import choose_tasks  # 现版仅需数字序号
# ============================================================================


# ----------------------------------------------------------------------------
# 读取 case.json
# ----------------------------------------------------------------------------
def load_case_templates(path: Path = CASE_PATH) -> List[Dict]:
    """加载模板；若文件缺失则返回空列表，后续走默认流程。"""
    if not path.exists():
        logging.warning("case.json not found → fallback to default flow.")
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# LLM 选模板序号 → choose_tasks(index) → tasks 列表
# ----------------------------------------------------------------------------
def llm_pick_tasks(user_input: str, cases: List[Dict]) -> List[str]:
    """
    1) 让 LLM 查看每个模板的描述，只输出数字序号或 NONE。
    2) 抽取首个数字，传给 choose_tasks()，拿到最终任务列表。
    """
    if not cases:                      # 无模板文件 → 默认流程
        return DEFAULT_TASK_FLOW

    # ---------- 1) 构造给 LLM 的 prompt ----------
    template_lines = [
        f"{idx}. — {c.get('description', '')}"
        for idx, c in enumerate(cases, 1)
    ]
    prompt = (
        "你是流程规划助手。\n"
        "根据【用户需求】从下列模板中选出最合适的一项。\n"
        "⚠️ 只输出对应的数字序号（如 2），不要添加解释或其他文字。\n"
        "若没有任何模板合适，请输出 NONE。\n\n"
        + "\n".join(template_lines)
    )

    ai_msg = llm_planner.invoke(
        [
            SystemMessage(content=prompt),
            HumanMessage(content=f"用户需求：{user_input}"),
        ]
    )
    #debug_ai_message(ai_msg)
    
    print("=========1111===========")
    print(ai_msg)

    # ---------- 2) 清洗并抓取数字序号 ----------
    raw = (ai_msg.content or "").strip()
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.I | re.S)
    raw = raw.strip('＂"“”\'')          # 去各类引号
    if not raw or raw.upper() == "NONE":
        logging.warning("LLM 返回 NONE 或空 → 默认流程")
        return DEFAULT_TASK_FLOW

    match = re.search(r"\d+", raw)      # 抓首个数字
    if not match:
        logging.warning("无法从输出 '%s' 提取数字 → 默认流程", raw)
        return DEFAULT_TASK_FLOW

    index_str = match.group()           # 如 "2"
    logging.info("LLM 选中模板序号：%s", index_str)
    print("=========6666===========")
    print(index_str)
    # ---------- 3) 交给 choose_tasks(index) ----------
    return choose_tasks.invoke(index_str)
