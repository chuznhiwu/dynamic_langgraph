# dynamic_langgraph/nodes/loader.py
import os
import mimetypes
import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator

from ..data_state import DataState
from scripts.recorder import record_and_stream

logger = logging.getLogger("dynamic-langgraph.nodes.loader")

_MAX_PREVIEW_BYTES = 4096  # 预览最多读取的字节数

def _is_text_mime(mime: str | None) -> bool:
    if not mime:
        return False
    return mime.startswith("text/") or any(
        mime.endswith(suf) for suf in ("/json", "/xml", "/csv", "/yaml", "/x-yaml")
    )

async def _read_preview(path: Path, max_bytes: int = _MAX_PREVIEW_BYTES) -> str:
    """尽量安全地读一小段文本预览。"""
    try:
        return await asyncio.to_thread(
            lambda: Path(path).read_text(encoding="utf-8", errors="replace")[:max_bytes]
        )
    except Exception:
        # 退回二进制方式尽力读一段
        try:
            def _read_bin():
                with open(path, "rb") as f:
                    return f.read(max_bytes).decode("utf-8", errors="replace")
            return await asyncio.to_thread(_read_bin)
        except Exception:
            return ""

async def loader(state: DataState) -> AsyncGenerator[dict, None]:
    """
    - 校验文件是否存在
    - 上报文件元信息（大小 / MIME）
    - 如果是文本类，推送一段内容预览
    - 返回 {"loaded": True}
    """
    print("🚩 进入 loader 节点")
    session_id = state.session_id
    path = Path(state.path)

    # 1) 文件存在性检查
    if not path.exists():
        # 推送异常事件后抛错
        async for event in record_and_stream(
            session_id=session_id,
            node="loader",
            step_type="file_check",
            type="error",
            content=f"文件不存在: {str(path)}",
        ):
            yield event
        raise FileNotFoundError(str(path))

    # 存在：推送确认
    async for event in record_and_stream(
        session_id=session_id,
        node="loader",
        step_type="file_check",
        type="trace",
        content=f"确认文件路径存在: {str(path)}",
    ):
        yield event

    # 2) 元信息：大小 / MIME
    try:
        size = await asyncio.to_thread(os.path.getsize, path)
    except Exception:
        size = None
    mime, _ = mimetypes.guess_type(str(path))

    async for event in record_and_stream(
        session_id=session_id,
        node="loader",
        step_type="file_meta",
        type="trace",
        result={"path": str(path), "size": size, "mime": mime},
    ):
        yield event

    # 3) 文本预览（若是文本/JSON/CSV等）
    if _is_text_mime(mime):
        preview = await _read_preview(path)
        if preview:
            async for event in record_and_stream(
                session_id=session_id,
                node="loader",
                step_type="preview",
                type="trace",
                content=preview,
            ):
                yield event

    # 4) 本节点最终结果
    async for event in record_and_stream(
        session_id=session_id,
        node="loader",
        step_type="result",
        type="trace",
        result={"loaded": True},
    ):
        yield event

    # 5) 传递给下游节点使用的增量状态
    yield {"loaded": True}
