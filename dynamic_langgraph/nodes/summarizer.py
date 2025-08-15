# dynamic_langgraph/nodes/summarizer.py
import asyncio
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage

from ..data_state import DataState
from ..llms import llm_main
from ..utils import split_thought_and_answer
from scripts.recorder import record_and_stream


async def _invoke_llm(prompt: str) -> AIMessage:
    """避免阻塞事件循环；多数 LLM 客户端是同步的。"""
    # 如果你的 llm_main.invoke 本来就接受字符串，这里也能正常工作；
    # 为了与其他节点一致，这里用 HumanMessage 包装。
    return await asyncio.to_thread(llm_main.invoke, [HumanMessage(content=prompt)])


async def summarizer(state: DataState) -> AsyncGenerator[dict, None]:
    """
    汇总上游节点产出，生成最终摘要：
      输入: state.user_input, state.features, state.diag, state.viz_summary
      事件: llm/thought -> result
      输出: {"summary": <str>}
    """
    print("🚩 进入 summarizer 节点")

    # 容错处理，避免 None 拼接成 'None'
    features = getattr(state, "features", None) or {}
    diag = getattr(state, "diag", None) or {}
    viz_summary = getattr(state, "viz_summary", None) or ""

    prompt = (
        "你是机械振动信号分析专家，请根据以下信息输出结构化、简洁的综合总结：\n"
        "要求：先给出要点列表，再给出一段完整结论，避免堆砌冗余。\n"
        f"- 用户需求: {state.user_input}\n"
        f"- 统计特征: {features}\n"
        f"- 诊断结果: {diag}\n"
        f"- 图像分析: {viz_summary}\n"
    )

    try:
        # 1) 调 LLM
        ai: AIMessage = await _invoke_llm(prompt)
        thought, answer = split_thought_and_answer(ai.content)

        # 推送思考过程（含模型/用量等元数据）
        async for event in record_and_stream(
            session_id=state.session_id,
            node="summarizer",
            step_type="llm",
            type="thought",
            thought=thought,
            content=answer,
            model=(getattr(ai, "response_metadata", {}) or {}).get("model"),
            usage=getattr(ai, "usage_metadata", None),
        ):
            yield event

        # 2) 最终结果事件
        async for event in record_and_stream(
            session_id=state.session_id,
            node="summarizer",
            step_type="result",
            type="trace",
            result={"summary": answer},
        ):
            yield event

        # 3) 传递给下游
        yield {"summary": answer}

    except Exception as e:
        # 兜底异常：推送 error 事件，并返回占位
        async for event in record_and_stream(
            session_id=state.session_id,
            node="summarizer",
            step_type="exception",
            type="error",
            content=str(e),
        ):
            yield event
        yield {"summary": f"[ERROR] {e}"}
