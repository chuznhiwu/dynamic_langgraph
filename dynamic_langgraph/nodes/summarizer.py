from ..data_state import DataState
from ..llms import llm_main
from dynamic_langgraph.utils import debug_ai_message
from langchain_core.messages import AIMessage
def summarizer(state: DataState):
    prompt = (
        "你是机械振动信号分析专家，请综合总结：\n"
        f"用户需求:{state.user_input}\n统计:{state.features}\n诊断:{state.diag}\n图像分析:{state.viz_summary}"
    )
    ai: AIMessage = llm_main.invoke(prompt)
    debug_ai_message(ai)
    return {"summary": ai.content}
