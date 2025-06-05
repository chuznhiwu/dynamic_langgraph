import json, logging, re
from pathlib import Path
from dynamic_langgraph.utils import debug_ai_message
import logging
from .llms import llm_planner
from typing import Dict, List, Tuple
from langchain_core.messages import HumanMessage, SystemMessage
from .config import CASE_PATH, DEFAULT_TASK_FLOW
from .tools.task_selector import choose_tasks   # 本地再跑一次，确保结果可信


# 允许的任务节点
_VALID_TASKS = {"analysis", "viz", "diagnosis"}

# --------------------------------------------------------------------------- #
# 模板加载
# --------------------------------------------------------------------------- #
def load_case_templates(path: Path = CASE_PATH) -> List[Dict]:
    """读取 case.json；缺失时回退为空列表（后面会用默认流程兜底）"""
    if not path.exists():
        logging.warning("case.json not found, fallback to default flow.")
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# LLM 规划
# --------------------------------------------------------------------------- #
def llm_pick_tasks(user_input: str, cases: List[Dict]) -> List[str]:
    """
    用 llm_planner 让大模型挑选模板，再本地 choose_tasks() 得到最终任务流。
    只返回任务列表，不再返回 reasoning 文本。
    """
    prompt = (
        "你是智能流程规划助手，请审阅模板并调用 choose_tasks。\n"
        " 注意：**最终答案必须采用 OpenAI JSON 工具调用格式**，例如：\n"
        "{\n"
        '  "name": "choose_tasks",\n'
        '  "args": { "template": "仅统计" }\n'
        "}\n"
        "不要再用 choose_tasks(template=\"…\") 这种纯文本。\n"
    )

    ai_msg = llm_planner.invoke([
        SystemMessage(content=prompt),                       # 真正 "system"
        HumanMessage(content=user_input),                    # "user"
        SystemMessage(content=f"TEMPLATES_JSON={json.dumps(cases, ensure_ascii=False)}"),
    ])
    debug_ai_message(ai_msg)
    # -------- 如果模型没有成功调用工具，直接回退 --------
    if not ai_msg.tool_calls:
        # ---------- 兜底：用正则从文本里提取 -----------------
        import re
        m = re.search(r'choose_tasks\(\s*template\s*=\s*["\'](.+?)["\']', ai_msg.content)
        if m:
            template_name = m.group(1)
            logging.info("Regex fallback → template=%s", template_name)
            return choose_tasks(template_name)
 
        logging.warning("No tool call returned, fallback to default flow.")
        return DEFAULT_TASK_FLOW

    # -------- 解析模板名并本地运行 choose_tasks --------
    call_args = ai_msg.tool_calls[0].get("args", {})
    template_name = call_args.get("template")

    tasks = choose_tasks(template_name)
    return tasks
