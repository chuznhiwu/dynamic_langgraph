# dynamic_langgraph/case_loader.py
# ============================================================================
# Imports
# ============================================================================
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from .llms import llm_planner
from .config import CASE_PATH, DEFAULT_TASK_FLOW
from .tools.task_selector import choose_tasks  # 现版仅需数字序号
# from dynamic_langgraph.utils import debug_ai_message  # 如需调试可启用
# ============================================================================

logger = logging.getLogger("dynamic-langgraph.case_loader")


# ----------------------------------------------------------------------------
# 读取 case.json
# ----------------------------------------------------------------------------
def load_case_templates(path: Path = CASE_PATH) -> List[Dict[str, Any]]:
    """
    加载模板；若文件缺失则返回空列表，后续走默认流程。
    每个模板建议包含：
      - description: 对应流程模板的一句话描述
      - 其他字段由 choose_tasks 决定是否使用
    """
    if not path.exists():
        logger.warning("case.json not found → fallback to default flow.")
        return []
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            if not isinstance(data, list):
                logger.warning("case.json 格式异常（非 list），将忽略并使用默认流程")
                return []
            return data
        except Exception as e:
            logger.warning("读取 case.json 失败（%s），将忽略并使用默认流程", e)
            return []


# ----------------------------------------------------------------------------
# 选模板序号 → choose_tasks(index) → tasks 列表
# ----------------------------------------------------------------------------
async def llm_pick_tasks(user_input: str, cases: List[Dict[str, Any]]) -> Tuple[List[Any], Optional[AIMessage]]:
    """
    让 LLM 根据【用户需求】从模板中选出最合适的一项（仅输出数字序号或 NONE）；
    然后把数字传给 choose_tasks.invoke(index)，得到最终任务列表。

    返回: (tasks, ai_msg)
      - tasks: List[...]（供 build_graph 使用）
      - ai_msg: AIMessage（上层用于记录 planner 的思考；无模板时为 None）
    """
    # 无模板 → 默认流程
    if not cases:
        return DEFAULT_TASK_FLOW, None

    # ---------- 1) 构造给 LLM 的 prompt ----------
    template_lines = [
        f"{idx}. {c.get('description', '').strip()}"
        for idx, c in enumerate(cases, 1)
    ]
    sys_prompt = (
        "你是流程规划助手。\n"
        "任务：根据【用户需求】从下列模板中选出最合适的一项。\n"
        "要求：只输出对应的数字序号（如 2），不要添加解释或其他文字。\n"
        "若没有任何模板合适，请输出 NONE。\n\n"
        "候选模板：\n" + "\n".join(template_lines)
    )

    try:
        ai_msg: AIMessage = await llm_planner.ainvoke([
            SystemMessage(content=sys_prompt),
            HumanMessage(content=f"用户需求：{user_input}"),
        ])
        # debug_ai_message(ai_msg)  # 如需调试解开
    except Exception as e:
        logger.warning("llm_planner 调用失败（%s）→ 使用默认流程", e)
        return DEFAULT_TASK_FLOW, None

    raw = (ai_msg.content or "").strip()
    # 去掉 <think> ... </think> 段落 & 外层引号
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.I | re.S).strip()
    raw = raw.strip('＂"“”\'').strip()

    if not raw or raw.upper() == "NONE":
        logger.warning("LLM 返回 NONE 或空 → 默认流程")
        return DEFAULT_TASK_FLOW, ai_msg

    # ---------- 2) 抓首个数字，并做边界检查 ----------
    m = re.search(r"\d+", raw)
    if not m:
        logger.warning("无法从输出 '%s' 提取数字 → 默认流程", raw)
        return DEFAULT_TASK_FLOW, ai_msg

    idx = int(m.group())
    if idx < 1 or idx > len(cases):
        logger.warning("序号 %s 越界（1..%d）→ 默认流程", idx, len(cases))
        return DEFAULT_TASK_FLOW, ai_msg

    index_str = str(idx)
    logger.info("LLM 选中模板序号：%s（%s）", index_str, cases[idx - 1].get("description", "").strip())

    # ---------- 3) 交给 choose_tasks(index) ----------
    try:
        selection = choose_tasks.invoke(index_str)
        # 兼容：若工具返回 JSON 字符串，尝试解析；否则直接用
        if isinstance(selection, str):
            try:
                parsed = json.loads(selection)
            except Exception:
                parsed = selection
        else:
            parsed = selection

        # 常见几种返回形态的适配
        if isinstance(parsed, dict) and "tasks" in parsed:
            tasks = parsed["tasks"]
        elif isinstance(parsed, list):
            tasks = parsed
        else:
            logger.warning("choose_tasks 返回格式非常规（%r）→ 默认流程", type(parsed))
            tasks = DEFAULT_TASK_FLOW

        return tasks, ai_msg

    except Exception as e:
        logger.warning("choose_tasks.invoke 失败（%s）→ 默认流程", e)
        return DEFAULT_TASK_FLOW, ai_msg
