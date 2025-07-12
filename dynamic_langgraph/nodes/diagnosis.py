import json
from langchain_core.messages import HumanMessage, AIMessage
from ..data_state import DataState
from ..llms import llm_diagnosis
from ..tools.diagnosis_tools import diagnose_signal
from dynamic_langgraph.utils import debug_ai_message

def diagnosis(state: DataState):
    prompt = (
        "你是故障诊断专家，请仅调用诊断工具。\n"
        f"FILE_PATH={state.path}\nUSER_NEED={state.user_input}"
    )
    ai: AIMessage = llm_diagnosis.invoke([HumanMessage(content=prompt)])
    debug_ai_message(ai)
    call = ai.tool_calls[0]
    res = json.loads(diagnose_signal.invoke(call["args"]))
    return {"diag": res}
