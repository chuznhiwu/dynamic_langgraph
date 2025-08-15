# scripts/recorder.py
import os
import time
import json
import queue
import asyncio
from typing import Dict, Any, List, AsyncGenerator
from threading import Lock

from minio_client import upload_file_to_minio, get_trace_object_key

# ================== 可调参数（也可用环境变量覆盖） ==================
# 每写入多少条事件再上传一次 MinIO（1 = 每条都传，开发期推荐；线上可调大）
UPLOAD_EVERY_N = int(os.getenv("TRACE_UPLOAD_EVERY_N", "1"))

# 每个 session 在内存里最多保留多少条事件（防止长会话内存暴涨）
MAX_IN_MEMORY_EVENTS = int(os.getenv("TRACE_MAX_IN_MEMORY", "5000"))

# SSE 的空闲等待（秒）
SSE_IDLE_SLEEP = float(os.getenv("TRACE_SSE_IDLE_SLEEP", "0.2"))

# 发送心跳的间隔（秒）
HEARTBEAT_SECONDS = float(os.getenv("TRACE_HEARTBEAT_SECONDS", "10"))

# ===================================================================

event_queues: Dict[str, queue.Queue] = {}
event_queues_lock = Lock()

# 内存事件轨迹；用于快速回放 + 写 jsonl
event_trace: Dict[str, List[Dict[str, Any]]] = {}

# 累计计数：用来决定什么时候上传 MinIO
_event_counter: Dict[str, int] = {}

def _ensure_session(session_id: str) -> None:
    with event_queues_lock:
        event_queues.setdefault(session_id, queue.Queue())
        event_trace.setdefault(session_id, [])
        _event_counter.setdefault(session_id, 0)

def _trim_if_needed(session_id: str) -> None:
    """
    防止事件无限增长导致内存占用过大，只保留最近 MAX_IN_MEMORY_EVENTS 条。
    """
    buf = event_trace.get(session_id)
    if not buf:
        return
    extra = len(buf) - MAX_IN_MEMORY_EVENTS
    if extra > 0:
        # 丢弃最早的 extra 条
        del buf[0:extra]

def _upload_trace_to_minio(session_id: str) -> None:
    """
    把当前内存的 event_trace 写入临时 jsonl 文件后上传到 MinIO。
    """
    object_key = get_trace_object_key(session_id)  # 标准化：trace/{session_id}.jsonl
    tmp_path = f"/tmp/{session_id}.jsonl"

    # 写本地 jsonl（全量覆盖；简单稳妥）
    with open(tmp_path, "w", encoding="utf-8") as f:
        for e in event_trace.get(session_id, []):
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # 上传 MinIO（忽略返回 url；api_server /trace/{session_id} 统一给直链）
    upload_file_to_minio(tmp_path, object_key)

def record_step(
    session_id: str,
    node: str,
    step_type: str,
    *,
    thought: str | None = None,
    tool_calls: Any | None = None,
    result: Any | None = None,
    model: str | None = None,
    usage: Any | None = None,
    tool_name: str | None = None,
    type: str = "trace",
    content: Any | None = None,
) -> None:
    """
    记录单条事件：
    - 推进 SSE 队列
    - 追加到内存 trace
    - 按策略上传 MinIO 的 jsonl（全量覆盖）
    """
    _ensure_session(session_id)

    event: Dict[str, Any] = {
        "timestamp": time.time(),
        "type": type,
        "session_id": session_id,
        "node": node,
        "step_type": step_type,
    }
    # 可选字段
    if thought is not None:
        event["thought"] = thought
    if tool_calls is not None:
        event["tool_calls"] = tool_calls
    if result is not None:
        event["result"] = result
    if model is not None:
        event["model"] = model
    if usage is not None:
        event["usage"] = usage
    if tool_name is not None:
        event["tool_name"] = tool_name
    if content is not None:
        event["content"] = content

    # 入队 + 入内存
    with event_queues_lock:
        event_queues[session_id].put(event)
        event_trace[session_id].append(event)
        _event_counter[session_id] += 1

        # 控制内存
        _trim_if_needed(session_id)

    # 刷新 MinIO（按批次）
    try:
        if _event_counter[session_id] >= UPLOAD_EVERY_N:
            _upload_trace_to_minio(session_id)
            _event_counter[session_id] = 0
    except Exception as e:
        # 不抛出，避免影响主流程；打印即可
        print(f"❌ 写入 MinIO trace 出错: {e}")

async def stream_event_generator(session_id: str) -> AsyncGenerator[str, None]:
    """
    SSE 事件生成器：
      - 从 session 对应的队列中取出事件
      - 每条以 Server-Sent Events 的格式发送：`data: {...}\n\n`
      - 定期发送注释心跳，防止代理或浏览器断开
    """
    _ensure_session(session_id)
    last_heartbeat = time.time()

    while True:
        try:
            with event_queues_lock:
                q = event_queues.get(session_id)

            # 理论上不会为 None，因为 _ensure_session 已经创建
            if q is None:
                await asyncio.sleep(SSE_IDLE_SLEEP)
                continue

            try:
                event = q.get_nowait()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except queue.Empty:
                await asyncio.sleep(SSE_IDLE_SLEEP)

            # 定时心跳（SSE 注释行）
            if time.time() - last_heartbeat > HEARTBEAT_SECONDS:
                # 注释不会被前端 onmessage 接收，但能保持连接活跃
                yield f": keep-alive {int(time.time())}\n\n"
                last_heartbeat = time.time()

        except asyncio.CancelledError:
            # 客户端断开
            break
        except Exception as e:
            print(f"❌ SSE 错误: {e}")
            break

def get_trace(session_id: str) -> List[Dict[str, Any]]:
    """
    仅供调试或本地快速回看；生产环境建议通过 MinIO 上的 jsonl。
    """
    _ensure_session(session_id)
    return event_trace.get(session_id, [])

async def record_and_stream(
    session_id: str,
    node: str,
    step_type: str,
    **kwargs
) -> AsyncGenerator[dict, None]:
    """
    节点/管道中常用的便捷函数：
      - 先用线程安全方式记录事件（包含 MinIO 刷新与 SSE 入队）
      - 再把事件结构 yield 一份给调用者（方便本地日志）
    """
    await asyncio.to_thread(
        record_step,
        session_id=session_id,
        node=node,
        step_type=step_type,
        **kwargs
    )

    event = {
        "timestamp": time.time(),
        "type": kwargs.get("type", "trace"),
        "session_id": session_id,
        "node": node,
        "step_type": step_type,
    }
    for key in ("thought", "tool_calls", "result", "model", "usage", "tool_name", "content"):
        if key in kwargs:
            event[key] = kwargs[key]

    yield event

def close_session(session_id: str) -> None:
    """
    可选：结束后清理内存里的队列与轨迹（如果你的会话是一次性的，建议调用）。
    注意：不会删除 MinIO 上的 jsonl。
    """
    with event_queues_lock:
        event_queues.pop(session_id, None)
        event_trace.pop(session_id, None)
        _event_counter.pop(session_id, None)
