import os
import json
import asyncio
from typing import Any, Dict, List, AsyncGenerator

from langchain_core.messages import HumanMessage, AIMessage

from ..data_state import DataState
from ..llms import llm_viz, llm_multimod
from ..tools import viz_tools
from ..utils import b64_image, split_thought_and_answer

from scripts.recorder import record_step, record_and_stream

# MinIO 上传：优先用你封装；常量从 env 兜底
from minio_client import upload_file_to_minio  # type: ignore

MINIO_BASE_URL = os.getenv("MINIO_BASE_URL", "").rstrip("/")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "dynamic-langgraph")

try:
    from minio_client import MINIO_BASE_URL as _MBU  # type: ignore
    if _MBU:
        MINIO_BASE_URL = _MBU.rstrip("/")
except Exception:
    pass
try:
    from minio_client import MINIO_BUCKET as _MB  # type: ignore
    if _MB:
        MINIO_BUCKET = _MB
except Exception:
    pass


# ---- 工具映射：LLM 只会调用这里列出的工具 ----
_MAP = {
    "time_plot": viz_tools.time_plot,
    "freq_plot": viz_tools.freq_plot,
}


# ---- 辅助：生成对象键 / 拼 URL / 上传取 URL ----
def _mk_key(prefix: str, session_id: str, filename: str) -> str:
    """
    生成对象键：<prefix>/<session_id>/<filename>
    这里 filename 建议带上你的随机名/时间戳，工具那边已用 uuid 命名，直接 basename 即可。
    """
    safe = os.path.basename(filename).replace(" ", "_")
    return f"{prefix.strip('/')}/{session_id}/{safe}"

def _compose_url(object_key: str) -> str:
    if MINIO_BASE_URL:
        return f"{MINIO_BASE_URL}/{MINIO_BUCKET}/{object_key}"
    return f"/{MINIO_BUCKET}/{object_key}"

def _upload_and_get_url(local_path: str, object_key: str) -> str:
    ret = upload_file_to_minio(local_path, object_key)
    if isinstance(ret, str) and (ret.startswith("http://") or ret.startswith("https://") or ret.startswith("/")):
        return ret
    return _compose_url(object_key)


# ==== 主节点：可流式产出事件 ====
async def viz(state: DataState) -> AsyncGenerator[dict, None]:
    """
    节点语义：
      - 输入: state.path, state.user_input, state.session_id
      - 过程: LLM 仅选择绘图工具 -> 执行 -> 把图片上传到 MinIO 的 pic/<session_id>/... -> 多模态总结
      - 推送事件: llm/thought, tool_call, tool_result, image, result, end
      - 输出状态: {"viz_paths": [<图片URL>], "viz_summary": "..."}  （兼容你下游使用）
    """
    session_id = state.session_id
    file_path = str(state.path)

    # 开始
    await asyncio.to_thread(
        record_step,
        session_id=session_id,
        node="viz",
        step_type="start",
        type="start",
        content=f"开始可视化：{file_path}",
    )

    # 1) 让 LLM 只选择绘图工具（不输出自然语言）
    prompt = (
        "你是绘图专家，只允许从以下工具中选择并调用，禁止输出自然语言解释：\n"
        f"- time_plot(path: str)\n- freq_plot(path: str)\n"
        f"FILE_PATH={file_path}\nUSER_NEED={state.user_input}\n"
        "请基于 FILE_PATH 选择合适工具并传入 path 参数。"
    )

    try:
        ai: AIMessage = await asyncio.to_thread(llm_viz.invoke, [HumanMessage(content=prompt)])
        # 记录 LLM 思考（若有）与工具调用计划
        thought, answer = split_thought_and_answer(ai.content or "")
        async for ev in record_and_stream(
            session_id=session_id,
            node="viz",
            step_type="llm",
            type="thought",
            thought=thought or "",
            content=answer or "",
            tool_calls=getattr(ai, "tool_calls", None),
            model=(getattr(ai, "response_metadata", {}) or {}).get("model"),
            usage=getattr(ai, "usage_metadata", None),
        ):
            yield ev

        tool_calls = getattr(ai, "tool_calls", None) or []
        local_imgs: List[str] = []
        img_urls: List[str] = []

        # 2) 依次执行工具：先推 tool_call，再执行，上传图片后推 image + tool_result
        for call in tool_calls:
            name = call.get("name")
            args = call.get("args", {}) or {}

            # tool_call 事件
            async for ev in record_and_stream(
                session_id=session_id,
                node="viz",
                step_type="tool_call",
                type="trace",
                tool_name=name,
                content=args,
            ):
                yield ev

            func = _MAP.get(name)
            if func is None:
                async for ev in record_and_stream(
                    session_id=session_id,
                    node="viz",
                    step_type="tool_result",
                    type="error",
                    tool_name=name,
                    content={"error": f"Unknown tool: {name}"},
                ):
                    yield ev
                continue

            # 真正执行工具（工具返回 {"image_path": "<本地路径>"} 的 JSON）
            try:
                result_json = await asyncio.to_thread(func.invoke, args)
                parsed = json.loads(result_json)
                img_local = parsed.get("image_path")
            except Exception as e:
                img_local = None
                parsed = {"error": str(e)}

            if img_local and os.path.exists(img_local):
                # 上传到 MinIO 的 pic/<session_id>/...，并拿到 URL
                object_key = _mk_key("pic", session_id, img_local)
                url = await asyncio.to_thread(_upload_and_get_url, img_local, object_key)

                # 推送“图片”事件（前端会内嵌显示）
                await asyncio.to_thread(
                    record_step,
                    session_id=session_id,
                    node="viz",
                    step_type="plot",
                    type="image",
                    content=url,
                )

                # tool_result 事件（带链接）
                async for ev in record_and_stream(
                    session_id=session_id,
                    node="viz",
                    step_type="tool_result",
                    type="tool_result",
                    tool_name=name,
                    content={"image_url": url, "object_key": object_key},
                ):
                    yield ev

                local_imgs.append(img_local)
                img_urls.append(url)
            else:
                # 工具执行失败
                async for ev in record_and_stream(
                    session_id=session_id,
                    node="viz",
                    step_type="tool_result",
                    type="tool_result",
                    tool_name=name,
                    content=parsed,
                ):
                    yield ev

        # 3) 多模态总结（对生成的图给一个简短趋势描述）
        viz_summary = ""
        if local_imgs:
            mm_prompt = [{"type": "text", "text": "请简要分析下列图像展示的数据总体趋势（尽量简洁）。"}] + [
                {"type": "image_url", "image_url": {"url": b64_image(p)}} for p in local_imgs
            ]
            mm_res: AIMessage = await asyncio.to_thread(llm_multimod.invoke, [HumanMessage(content=mm_prompt)])
            viz_summary = mm_res.content or ""

            # 推一个 result 事件（结构化）
            async for ev in record_and_stream(
                session_id=session_id,
                node="viz",
                step_type="result",
                type="trace",
                result={"images": img_urls, "viz_summary": viz_summary},
            ):
                yield ev

        # 4) LangGraph 状态更新（下游可能继续用）
        yield {"viz_paths": img_urls, "viz_summary": viz_summary}

    except Exception as e:
        # 节点级异常兜底
        async for ev in record_and_stream(
            session_id=session_id,
            node="viz",
            step_type="exception",
            type="error",
            content=str(e),
        ):
            yield ev
        yield {"viz_paths": [], "viz_summary": f"[viz error] {e}"}
