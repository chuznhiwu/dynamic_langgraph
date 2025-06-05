# ---------------------------------------------
# choose_tasks – 让函数体真正“跑一次”模板匹配
# ---------------------------------------------
import json, logging
from pathlib import Path
from typing import List
from langchain_core.tools import tool
from ..config import CASE_PATH, DEFAULT_TASK_FLOW

_VALID_TASKS = {"analysis", "viz", "diagnosis"}


@tool
def choose_tasks(template: str) -> List[str]:
    """
    根据模板名返回任务列表。

    Parameters
    ----------
    template : str
        模板名称，例如 "仅统计"

    Returns
    -------
    list[str]  任务节点顺序；不存在或格式非法就回退 DEFAULT_TASK_FLOW
    """
    # 1) 读取 case.json（每次调用都读一次，保证最新）
    try:
        with Path(CASE_PATH).open("r", encoding="utf-8") as f:
            templates = json.load(f)
    except FileNotFoundError:
        logging.warning("case.json not found → 回退默认流程")
        return DEFAULT_TASK_FLOW
    except Exception as exc:
        logging.warning("case.json 解析失败: %s → 回退默认流程", exc)
        return DEFAULT_TASK_FLOW

    # 2) 建立 name → tasks 映射
    mapping = {
        t["name"]: t["tasks"]
        for t in templates
        if isinstance(t, dict) and "name" in t and "tasks" in t
    }

    tasks = mapping.get(template)
    if not (isinstance(tasks, list) and tasks and all(t in _VALID_TASKS for t in tasks)):
        logging.warning("模板 %s 不存在或任务非法 → 回退默认流程", template)
        return DEFAULT_TASK_FLOW

    return tasks
