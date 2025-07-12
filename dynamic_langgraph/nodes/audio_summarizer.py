"""
节点：对已转写的中文语音文本做 200 字摘要
"""
from dynamic_langgraph.data_state import DataState
from dynamic_langgraph.utils import debug_ai_message
from ..llms import llm_main
from langchain_core.messages import AIMessage

_MAX_INPUT_CHARS = 4000   # 防止超上下文
_PROMPT_HEADER = (
    "你是一名资深会议纪要撰写者，请严格遵守以下要求：\n"
    "1. 用 **中文** 写作\n"
    "2. 长度控制在 200 字以内\n"
    "仅输出摘要正文，不要标题或解释。\n\n"
)

def audio_summarizer(state: DataState) -> DataState:
    if not state.asr_text:
        state.error_msg = "音频未转写成功，无法生成摘要"
        return state

    text = state.asr_text[:_MAX_INPUT_CHARS]
    prompt = _PROMPT_HEADER + text

    ai: AIMessage = llm_main.invoke(prompt)
    debug_ai_message(ai)

    state.summary = ai.content.strip()
    return state
