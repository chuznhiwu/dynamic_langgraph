# dynamic_langgraph/nodes/analysis.py
import json
import asyncio
from typing import AsyncGenerator, Dict, Any

from langchain_core.messages import HumanMessage, AIMessage

from ..data_state import DataState
from ..llms import llm_analysis
from ..tools import stats_tools
from ..utils import split_thought_and_answer

from scripts.recorder import record_and_stream

_MAP = {
    "calc_mean": stats_tools.calc_mean,
    "calc_std": stats_tools.calc_std,
    "calc_var": stats_tools.calc_var,
}

async def _invoke_llm(prompt: str) -> AIMessage:
    """
    统一封装一次，llm_analysis.invoke 大概率是同步调用；
    用 to_thread 避免阻塞事件循环。
    """
    return await asyncio.to_thread(llm_analysis.invoke, [HumanMessage(content=prompt)])

async def analysis(state: DataState) -> AsyncGenerator[dict, None]:
    """
    节点语义：
      - 输入: state.path, state.user_input, state.session_id
      - 过程: LLM 选择工具 -> 执行工具 -> 汇总特征
      - 输出: {"features": {...}}（供下游节点使用）
      - 事件: planner/thought, tool_call, tool_result, result
    """
    print("🚩 进入 analysis 节点")
    session_id = state.session_id

    prompt = (
        "你是统计专家，只负责选择并调用合适的统计工具；"
        "必须以工具调用完成分析，避免自然语言解释。\n"
        f"FILE_PATH={state.path}\nUSER_NEED={state.user_input}"
    )

    try:
        # 1) 调 LLM 产生工具调用计划 + 思考
        ai: AIMessage = await _invoke_llm(prompt)
        thought, answer = split_thought_and_answer(ai.content)

        # 推送 LLM 思考
        async for event in record_and_stream(
            session_id=session_id,
            node="analysis",
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
        feats: Dict[str, Any] = {}

        # 2) 依次执行工具（先推 tool_call，再执行并推 tool_result）
        for call in tool_calls:
            name = call.get("name")
            args = call.get("args", {})

            # tool_call 事件（参数可视化）
            async for event in record_and_stream(
                session_id=session_id,
                node="analysis",
                step_type="tool_call",
                type="trace",
                tool_name=name,
                content=args,
            ):
                yield event

            func = _MAP.get(name)
            if func is None:
                # 未注册的工具，直接报一个结果事件，方便排查
                async for event in record_and_stream(
                    session_id=session_id,
                    node="analysis",
                    step_type="tool_result",
                    type="error",
                    tool_name=name,
                    content={"error": f"Unknown tool: {name}"},
                ):
                    yield event
                continue

            # 执行工具
            try:
                result_json = await asyncio.to_thread(func.invoke, args)
                parsed = json.loads(result_json)
            except Exception as e:
                parsed = {"error": str(e)}

            # 汇总特征
            if isinstance(parsed, dict):
                feats.update(parsed)

            # tool_result 事件
            async for event in record_and_stream(
                session_id=session_id,
                node="analysis",
                step_type="tool_result",
                type="tool_result",
                tool_name=name,
                content=parsed,
            ):
                yield event

        # 3) 推送本节点最终结果
        async for event in record_and_stream(
            session_id=session_id,
            node="analysis",
            step_type="result",
            type="trace",
            result={"features": feats},
        ):
            yield event

        # 4) 返回给 LangGraph 的状态更新
        yield {"features": feats}

    except Exception as e:
        # 兜底异常
        async for event in record_and_stream(
            session_id=session_id,
            node="analysis",
            step_type="exception",
            type="error",
            content=str(e),
        ):
            yield event
        # 节点出错时也返回一个占位，避免下游崩溃
        yield {"features": {"error": str(e)}}
