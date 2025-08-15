# dynamic_langgraph/nodes/diagnosis.py
from typing import AsyncGenerator, Dict, Any, Optional
import json
import asyncio

from langchain_core.messages import HumanMessage, AIMessage

from ..data_state import DataState
from ..llms import llm_diagnosis
from ..tools.diagnosis_tools import diagnose_signal
from ..utils import split_thought_and_answer

from scripts.recorder import record_and_stream


async def _invoke_llm(prompt: str) -> AIMessage:
    """避免阻塞事件循环。"""
    return await asyncio.to_thread(llm_diagnosis.invoke, [HumanMessage(content=prompt)])


async def _invoke_tool(args: Dict[str, Any]) -> str:
    """封装工具调用，保持线程安全。"""
    return await asyncio.to_thread(diagnose_signal.invoke, args)


async def diagnosis(state: DataState) -> AsyncGenerator[dict, None]:
    """
    输入: state.path, state.user_input, state.session_id
    过程: LLM 选择诊断工具 -> 执行诊断 -> 输出诊断结果
    事件: llm/thought -> tool_call -> tool_result -> result (或 exception)
    输出: {"diag": <dict>}
    """
    print("🚩 进入 diagnosis 节点")
    session_id = state.session_id

    prompt = (
        "你是故障诊断专家，请仅调用诊断工具并给出结构化结果；"
        "避免纯自然语言解释。\n"
        f"FILE_PATH={state.path}\nUSER_NEED={state.user_input}"
    )

    try:
        # 1) LLM 规划工具调用
        ai: AIMessage = await _invoke_llm(prompt)
        thought, answer = split_thought_and_answer(ai.content)

        async for event in record_and_stream(
            session_id=session_id,
            node="diagnosis",
            step_type="llm",
            type="thought",
            thought=thought,
            content=answer,
            tool_calls=getattr(ai, "tool_calls", None),
            model=(getattr(ai, "response_metadata", {}) or {}).get("model"),
            usage=getattr(ai, "usage_metadata", None),
        ):
            yield event

        tool_calls = getattr(ai, "tool_calls", None) or []
        if not tool_calls:
            # 没有给出工具计划，直接报错并结束
            async for event in record_and_stream(
                session_id=session_id,
                node="diagnosis",
                step_type="exception",
                type="error",
                content="LLM 未返回任何 tool_calls，无法进行诊断。",
            ):
                yield event
            yield {"diag": {"error": "no_tool_calls"}}
            return

        # 仅取第一个调用（你的原逻辑如此；若需多工具可循环）
        call = tool_calls[0]
        name: Optional[str] = call.get("name")
        args: Dict[str, Any] = call.get("args", {}) or {}

        # 2) 推送 tool_call 事件（参数可见）
        async for event in record_and_stream(
            session_id=session_id,
            node="diagnosis",
            step_type="tool_call",
            type="trace",
            tool_name=name,
            content=args,
        ):
            yield event

        # 3) 执行工具
        try:
            raw = await _invoke_tool(args)
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = {"raw": raw}  # 兜底：非JSON也回传
        except Exception as e:
            parsed = {"error": str(e)}

        # 4) 推送 tool_result
        async for event in record_and_stream(
            session_id=session_id,
            node="diagnosis",
            step_type="tool_result",
            type="tool_result",
            tool_name=name,
            content=parsed,
        ):
            yield event

        # 5) 节点最终结果
        async for event in record_and_stream(
            session_id=session_id,
            node="diagnosis",
            step_type="result",
            type="trace",
            result={"diag": parsed},
        ):
            yield event

        yield {"diag": parsed}

    except Exception as e:
        # 兜底异常
        async for event in record_and_stream(
            session_id=session_id,
            node="diagnosis",
            step_type="exception",
            type="error",
            content=str(e),
        ):
            yield event
        yield {"diag": {"error": str(e)}}
