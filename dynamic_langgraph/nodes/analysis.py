import json
from langchain_core.messages import HumanMessage, AIMessage
from ..data_state import DataState
from ..llms import llm_analysis
from ..tools import stats_tools
from dynamic_langgraph.utils import debug_ai_message

_MAP = {
    "calc_mean": stats_tools.calc_mean,
    "calc_std": stats_tools.calc_std,
    "calc_var": stats_tools.calc_var,
}

def analysis(state: DataState):
    prompt = (
        "你是统计专家，只负责选择&调用合适统计工具，不要用自然语言解释。\n"
        f"FILE_PATH={state.path}\nUSER_NEED={state.user_input}"
    )
    ai: AIMessage = llm_analysis.invoke([HumanMessage(content=prompt)])
    debug_ai_message(ai)
    feats = {}
    for call in ai.tool_calls:
        func = _MAP[call["name"]]
        feats.update(json.loads(func.invoke(call["args"])))
    return {"features": feats}
