# dynamic_langgraph/tools/task_selector.py
from typing import List, Dict
from langchain_core.tools import tool

@tool
def choose_tasks(user_need: str, templates: List[Dict]) -> List[str]:
    """
    根据用户需求和预设模板，返回任务列表。

    参数
    ----
    user_need : 用户自然语言需求
    templates : case.json 里加载的模板列表，每个元素形如
        { "name": str, "description": str, "tasks": [str, ...] }

    返回
    ----
    List[str] : 如 ["analysis", "viz", "diagnosis"]
    """
    # 注意：函数体留空！模型会“虚拟实现”它。
    pass
