# scripts/api_core.py
# 统一的分析编排入口：校验参数 -> 准备文件 -> 调 LangGraph 流程 -> 推送事件（SSE/trace） -> 收尾
import os
import asyncio
import tempfile
import logging
from typing import Any, Dict, Optional

from dotenv import load_dotenv
load_dotenv()  # 读取项目根目录的 .env

from scripts.recorder import record_step  # 事件推送（SSE & trace）
from minio_client import download_file_from_minio

logger = logging.getLogger("dynamic-langgraph.api_core")
logging.basicConfig(level=logging.INFO)

# ——— 这里按你的实际 pipeline 名称做自动适配 ———
# 优先顺序：run_pipeline -> run -> analyze
_PIPELINE_CALLABLE_CACHE = None
def _resolve_pipeline_callable():
    global _PIPELINE_CALLABLE_CACHE
    if _PIPELINE_CALLABLE_CACHE is not None:
        return _PIPELINE_CALLABLE_CACHE

    import importlib
    try:
        pl = importlib.import_module("dynamic_langgraph.pipeline")
    except Exception as e:
        raise RuntimeError(f"无法导入 dynamic_langgraph.pipeline：{e}")

    for name in ("run_pipeline", "run", "analyze"):
        fn = getattr(pl, name, None)
        if callable(fn):
            _PIPELINE_CALLABLE_CACHE = fn
            logger.info("使用 pipeline 入口函数：dynamic_langgraph.pipeline.%s", name)
            return fn

    raise RuntimeError("在 dynamic_langgraph.pipeline 中未找到 run_pipeline/run/analyze 任一可调用入口函数")


def _is_local_path(path: str) -> bool:
    try:
        return os.path.isabs(path) or os.path.exists(path)
    except Exception:
        return False


async def _maybe_await(fn, *args, **kwargs):
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    # 如果 pipeline 是同步函数，放线程池避免阻塞事件循环
    return await asyncio.to_thread(fn, *args, **kwargs)


async def analyze_with_streaming_path(
    file_path: str,
    query: str,
    session_id: str,
) -> Dict[str, Any]:
    """
    你在 api_server.py 中后台调用的主入口：
    - 接收 MinIO 对象键或本地路径的 file_path
    - 统一推送 start/input/call/result/end 事件
    - 返回 pipeline 的最终结果（dict）
    """
    # 1) start 事件
    record_step(
        session_id=session_id,
        node="api",
        step_type="start",
        type="trace",
        content="analysis started",
    )

    # 2) 规范化输入 & 下载文件（若是 MinIO 对象键）
    tmp_local: Optional[str] = None
    local_path: Optional[str] = None
    try:
        if _is_local_path(file_path):
            local_path = file_path
        else:
            # 当 file_path 是形如 "uploaded/xxx" 或 "trace/xxx" 的对象键时，下载到临时文件
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp_local = tmp.name
            tmp.close()
            download_file_from_minio(file_path, tmp_local)
            local_path = tmp_local

        # 2.1 input 事件（让前端能看到当前输入）
        record_step(
            session_id=session_id,
            node="api",
            step_type="input",
            type="trace",
            result={
                "file_path": file_path,
                "local_path": local_path,
                "query": query,
            },
        )

        # 3) 解析并调用 pipeline
        fn = _resolve_pipeline_callable()
        record_step(
            session_id=session_id,
            node="api",
            step_type="call",
            type="trace",
            content=f"calling pipeline: {fn.__module__}.{fn.__name__}",
        )

        # 你自己的 pipeline 函数请确保接受这些参数（或按需在这里调整传参）
        result = await _maybe_await(fn, file_path=local_path, query=query, session_id=session_id)

        # 4) 输出结果事件
        record_step(
            session_id=session_id,
            node="api",
            step_type="result",
            type="trace",
            result=result,
        )
        return result

    except Exception as e:
        logger.exception("analyze_with_streaming_path 异常：%s", e)
        # 推送异常事件（SSE/trace 前端能立即看到错误）
        record_step(
            session_id=session_id,
            node="api",
            step_type="exception",
            type="error",
            content=str(e),
        )
        # 继续抛出给上层（api_server 会捕获并记录）
        raise

    finally:
        # 5) 清理 & end 事件
        if tmp_local and os.path.exists(tmp_local):
            try:
                os.remove(tmp_local)
            except Exception:
                pass

        record_step(
            session_id=session_id,
            node="api",
            step_type="end",
            type="trace",
            content="analysis finished",
        )
