# dynamic_langgraph/pipeline.py
import logging
import json
import asyncio
import os
import re, time  # 新增
import uuid
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .case_loader import load_case_templates, llm_pick_tasks
from .graph_builder import build_graph
from .utils import split_thought_and_answer

from scripts.recorder import record_step
from minio_client import upload_file_to_minio  # 你的封装：可返回直链/预签名/None

# ---------------- 环境 & 本地目录 ----------------
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "dynamic-langgraph")
MINIO_BASE_URL = os.getenv("MINIO_BASE_URL", "").rstrip("/")

# 若 minio_client 里也定义了常量，优先使用
try:
    from minio_client import MINIO_BUCKET as _MB  # type: ignore
    if _MB:
        MINIO_BUCKET = _MB
except Exception:
    pass
try:
    from minio_client import MINIO_BASE_URL as _MBU  # type: ignore
    if _MBU:
        MINIO_BASE_URL = _MBU.rstrip("/")
except Exception:
    pass

_LOG_DIR = (Path(__file__).resolve().parent.parent / "log")
_LOG_DIR.mkdir(exist_ok=True)
_GRAPHS_DIR = (Path(__file__).resolve().parent.parent / "graphs")
_GRAPHS_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("dynamic-langgraph.pipeline")
logging.basicConfig(level=logging.INFO)

# ================== 小工具 ==================
def safe_serialize(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: safe_serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [safe_serialize(v) for v in obj]
    return obj

def _mk_key(prefix: str, session_id: str, name: str) -> str:
    """
    生成对象键：<prefix>/<session_id>/<ts>_<uuid8>_<name>
    例：pic/abcd.../1723530001_a1b2c3d4_chart.png
    """
    ts = int(time.time())
    uid = uuid.uuid4().hex[:8]
    safe_name = name.replace(" ", "_")
    return f"{prefix.strip('/')}/{session_id}/{ts}_{uid}_{safe_name}"

def _compose_url(object_key: str) -> str:
    """
    返回一个可访问 URL（优先 MINIO_BASE_URL，退化时返回 /bucket/object）。
    """
    if MINIO_BASE_URL:
        return f"{MINIO_BASE_URL}/{MINIO_BUCKET}/{object_key}"
    return f"/{MINIO_BUCKET}/{object_key}"

def _upload_and_get_url(local_path: str, object_key: str) -> str:
    """
    上传并尽力得到 URL：
    - upload_file_to_minio 返回 URL/路径则优先用；
    - 否则用 _compose_url(object_key) 拼。
    """
    ret = upload_file_to_minio(local_path, object_key)
    if isinstance(ret, str) and (ret.startswith("http://") or ret.startswith("https://") or ret.startswith("/")):
        return ret
    return _compose_url(object_key)


def sid_core(session_id: str) -> str:
    """去掉前缀 'owui-'，得到 b9d605d2002b4fb0975804ab 这种核心ID"""
    return re.sub(r"^owui-", "", session_id or "")


async def emit_image_from_file(session_id: str, local_path: str, *, prefix: str = "pic") -> str:
    """
    上传一张图片并推送事件（前端会内嵌显示）：
    - 事件：type="image"，content=<url>
    """
    name = Path(local_path).name
    object_key = _mk_key(prefix, session_id, name)
    url = await asyncio.to_thread(_upload_and_get_url, local_path, object_key)
    await asyncio.to_thread(
        record_step,
        session_id=session_id,
        node="viz",
        step_type="plot",
        type="image",
        content=url,
    )
    return object_key

async def emit_file_from_path(session_id: str, local_path: str, *, prefix: str = "processfiles") -> str:
    """
    上传任意文件并推送事件（前端显示下载链接）：
    - 事件：type="file"，content=<url>，result={"filename": "...", "object_key": "..."}
    """
    name = Path(local_path).name
    object_key = _mk_key(prefix, session_id, name)
    url = await asyncio.to_thread(_upload_and_get_url, local_path, object_key)
    await asyncio.to_thread(
        record_step,
        session_id=session_id,
        node="artifact",
        step_type="file",
        type="file",
        content=url,
        result={"filename": name, "object_key": object_key},
    )
    return object_key

async def emit_bytes_as_file(session_id: str, data: bytes, filename: str, *, prefix: str = "processfiles") -> str:
    """
    把内存字节保存为临时文件 → 上传 → 推送 file 事件。
    """
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        tmp.flush()
        tmp_path = tmp.name
    try:
        return await emit_file_from_path(session_id, tmp_path, prefix=prefix)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

# ============ 规划 + 生成流程图（上传到 pic/flowchart/...） ============
async def _plan_and_graph(user_input: str, session_id: str) -> Tuple[Any, List[Dict[str, Any]], Optional[str]]:
    """
    规划任务 + 生成并上传流程图
    返回: (compiled_graph, tasks, graph_url)
    """
    # 1) 任务规划
    cases = load_case_templates()
    tasks, ai_msg = await llm_pick_tasks(user_input, cases)

    # 推送 planner 思考（thought 放详细推理；content 可放简短结论/编号）
    if ai_msg:
        thought, answer = split_thought_and_answer(ai_msg.content)
        await asyncio.to_thread(
            record_step,
            session_id=session_id,
            node="planner",
            step_type="llm",
            type="thought",
            thought=thought,
            content=answer,  # 可能是 "2" 或一个简短结论
            model=(ai_msg.response_metadata or {}).get("model") if hasattr(ai_msg, "response_metadata") else None,
            usage=getattr(ai_msg, "usage_metadata", None),
        )
    else:
        logger.warning("planner 无 AIMessage，跳过记录思考")

    logger.info("LLM 规划任务流: %s", tasks)

    # 2) 构图
    compiled = build_graph(tasks)

    # 3) 流程图 → 本地 → MinIO（flowchart/...）→ 推 image 事件
    graph_url: Optional[str] = None
    try:
        core = sid_core(session_id)                       # ← 统一用核心ID
        ts = int(time.time())                             # ← 可选：时间戳避免覆盖
        graph_path = _GRAPHS_DIR / f"graph_{core}_{ts}.png"      # ← 本地文件名也统一

        png_bytes = compiled.get_graph(xray=True).draw_mermaid_png()
        graph_path.write_bytes(png_bytes)

        # 不再走 _mk_key("pic/flowchart", ...)，直接明确：flowchart/<core>/graph_<core>_<ts>.png
        object_key = f"flowchart/{core}/graph_{core}_{ts}.png"    # ← 目标对象键统一

        graph_url = await asyncio.to_thread(_upload_and_get_url, str(graph_path), object_key)

        await asyncio.to_thread(
            record_step,
            session_id=session_id,
            node="planner",
            step_type="flowchart",
            type="image",
            content=graph_url,
        )
    except Exception as e:
        logger.warning("流程图生成或上传失败：%s", e)


    # 4) 推 planner 阶段的任务列表（结构化）
    await asyncio.to_thread(
        record_step,
        session_id=session_id,
        node="planner",
        step_type="result",
        type="trace",
        result={"tasks": tasks},
    )

    return compiled, tasks, graph_url

# ================== 主流程（流式） ==================
async def run_dynamic_pipeline_streaming(
    txt_path: str | Path,
    user_input: str,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    流式版本：记录规划思考、上传流程图到 pic/...、执行各节点（节点内可用 emit_* 发图/文件）
    返回：简要结果字典（供 api_core 推 result 事件）
    """
    session_id = session_id or "default"
    logger.info("🟢 [Streaming] 调用 pipeline, file=%s, query=%s", txt_path, user_input)

    # 开始事件
    await asyncio.to_thread(
        record_step,
        session_id=session_id,
        node="pipeline",
        step_type="start",
        type="start",
        content=f"开始处理：{txt_path}",
    )

    try:
        # 1) 规划 + 流程图
        compiled, tasks, graph_url = await _plan_and_graph(user_input, session_id)

        # 2) 执行图；将每步输出实时写入 trace（分类为 call/result），前端即可流式显示
        async for output in compiled.astream({
            "path": Path(txt_path),
            "user_input": user_input,
            "session_id": session_id,
        }):
            output = safe_serialize(output)

            # ✅ 实时推流到 trace：尽量识别 工具调用/结果；过滤 /tmp 噪声
            try:
                step_type = None
                tool_name = None
                payload   = None

                if isinstance(output, dict):
                    ev = str(output.get("event") or "")
                    # 形如 {event: 'tool_call', tool_name: 'xxx', args: {...}}
                    if ev in ("tool_call", "call"):
                        step_type = "call"
                        tool_name = output.get("tool") or output.get("tool_name")
                        payload   = output.get("args") or {k: v for k, v in output.items() if k not in ("event",)}
                    # 形如 {event: 'tool_result', tool_name: 'xxx', result: {...}}
                    elif ev in ("tool_result", "result") or ("result" in output):
                        step_type = "result"
                        tool_name = output.get("tool") or output.get("tool_name")
                        payload   = output.get("result") or {k: v for k, v in output.items() if k not in ("event",)}
                    # 已经是图片事件（某些节点也可能主动产出），直接转发
                    elif (output.get("type") == "image") and output.get("content"):
                        record_step(
                            session_id=session_id,
                            node=output.get("node") or "graph",
                            step_type=output.get("step_type") or "plot",
                            type="image",
                            content=str(output.get("content")),
                        )
                        payload = None  # 已处理
                    else:
                        step_type = "result"
                        payload   = output
                else:
                    # 非 dict：也按 result 文本推一次
                    step_type = "result"
                    payload   = output

                # 过滤掉 {"path": "/tmp/tmpxxxx"} 这种噪声
                if isinstance(payload, dict) and set(payload.keys()) == {"path"} and str(payload.get("path", "")).startswith("/tmp/tmp"):
                    pass
                elif payload is not None:
                    record_step(
                        session_id=session_id,
                        node="graph",
                        step_type=step_type,
                        type="trace",
                        tool_name=tool_name,
                        result=payload if isinstance(payload, dict) else None,
                        content=None if isinstance(payload, dict) else str(payload),
                    )
            except Exception as _e:
                # 不要影响主流程
                logger.debug("stream push ignore error: %s", _e)

            # （可选）仍然写入本地 jsonl 备查
            log_path = _LOG_DIR / f"pipeline_{session_id}.jsonl"
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(output, ensure_ascii=False) + "\n")

    except Exception as e:
        logger.error("❌ Pipeline 执行异常: %s", e)
        await asyncio.to_thread(
            record_step,
            session_id=session_id,
            node="pipeline",
            step_type="exception",
            type="error",
            content=str(e),
        )
        return {
            "status": "error",
            "session_id": session_id,
            "message": str(e),
        }

    # 结束事件
    await asyncio.to_thread(
        record_step,
        session_id=session_id,
        node="pipeline",
        step_type="end",
        type="end",
        content="任务完成",
    )

    return {
        "status": "ok",
        "session_id": session_id,
        "graph_url": graph_url,
    }

# ---------- 供 api_core 自动解析的入口函数 ----------
async def run(file_path: str, query: str, session_id: str) -> Dict[str, Any]:
    return await run_dynamic_pipeline_streaming(
        txt_path=file_path,
        user_input=query,
        session_id=session_id,
    )

async def run_pipeline(file_path: str, query: str, session_id: str) -> Dict[str, Any]:
    return await run(file_path=file_path, query=query, session_id=session_id)

async def analyze(file_path: str, query: str, session_id: str) -> Dict[str, Any]:
    return await run(file_path=file_path, query=query, session_id=session_id)
