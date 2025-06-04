import json, logging, re
from pathlib import Path
from typing import List, Dict
from .llms import llm_planner
from .config import CASE_PATH, DEFAULT_TASK_FLOW
from dynamic_langgraph.utils import debug_ai_message
from .tools.task_selector import choose_tasks
from langchain_core.messages import HumanMessage

_VALID_TASKS = {"analysis", "viz", "diagnosis"}

def load_case_templates(path: Path = CASE_PATH) -> List[Dict]:
    if not path.exists():
        logging.warning("⚠️ case.json not found, fallback to default flow.")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def llm_pick_tasks(user_input: str, cases: List[Dict]) -> List[str]:
    """
    使用函数调用模型，结构化返回任务列表。
    同时把 LLM 的推理文本 (content) 也返回，便于日志或展示。
    """
    prompt = (
        "你是智能流程规划助手，请审阅模板并调用 choose_tasks。"
        "在 content 中可输出你的思考过程，但最终必须以函数调用 choose_tasks 给出结果,一定注意输出的任务流程顺序,要符合逻辑。"
    )
    ai_msg = llm_planner.invoke([
        HumanMessage(content=prompt),
        HumanMessage(role="user", content=user_input),
        HumanMessage(role="system", content=f"TEMPLATES_JSON={json.dumps(cases, ensure_ascii=False)}")
    ])

    # -------- 获取结果 --------
    reasoning = ai_msg.content             # 大模型的 inner monologue
    if not ai_msg.tool_calls:
        logging.warning("No tool call returned, fallback.")
        return DEFAULT_TASK_FLOW, reasoning

    tasks = ai_msg.tool_calls[0]["args"]["return_value"]   # LangChain 已反序列化为 Python list
    if not (isinstance(tasks, list) and all(t in _VALID_TASKS for t in tasks)):
        logging.warning("Tool returned invalid tasks, fallback.")
        return DEFAULT_TASK_FLOW, reasoning

    return tasks, reasoning
