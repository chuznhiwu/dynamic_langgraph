# api_server.py
# FastAPI service for Dynamic-LangGraph
# - SSE streaming:   GET /stream/{session_id}
# - File upload:     POST /upload
# - Ingest uploads:  POST /ingest/openwebui   <-- 智能匹配 + 可选 filename
# - Start analyze:   POST /analyze   (alias: /analyze-path)
# - Trace URL:       GET /trace/{session_id}
# - Health check:    GET /health

import re, os
import asyncio
import logging
import tempfile
from typing import Optional
from pathlib import Path
from fastapi.responses import PlainTextResponse
import requests
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from functools import lru_cache
import time
from threading import Lock
import hashlib


load_dotenv()  # 自动读取 .env
_lru_recent = {}  # {session_id: last_ts}
# --- 日志 ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("dynamic-langgraph.api")

_RUN_GUARD: dict[str, float] = {}
_RUN_LOCK = Lock()

def _run_key(session_id: str, file_path: str, query: str) -> str:
    # 组合 key，避免只按 session_id 判断不准
    digest = hashlib.md5(f"{file_path}|{query}".encode("utf-8")).hexdigest()[:16]
    return f"{session_id}:{digest}"

def _recently_started(key: str, ttl: float = 10.0) -> bool:
    now = time.time()
    with _RUN_LOCK:
        # 清理过期
        for k, t in list(_RUN_GUARD.items()):
            if now - t > ttl:
                _RUN_GUARD.pop(k, None)
        last = _RUN_GUARD.get(key, 0.0)
        if now - last < ttl:
            return True
        _RUN_GUARD[key] = now
        return False
    
def sid_core(session_id: str) -> str:
    return re.sub(r"^owui-", "", session_id or "")

def strip_webui_uuid_prefix(name: str) -> str:
    """
    去掉 OpenWebUI 在 uploads/ 里添加的 '<uuid>_' 前缀。
    形如 'f922703e-e810-4f13-9bc2-49f6468f1604_ball501.txt' -> 'ball501.txt'
    """
    base = os.path.basename(name or "")
    m = re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_(.+)$", base, re.I)
    return m.group(1) if m else base

# --- MinIO 封装 ---
try:
    from minio_client import (
        upload_file_to_minio,
        download_file_from_minio,  # 未使用但保留
        object_exists,  
    )
except Exception as e:
    logger.warning("minio_client 未能完整导入：%s", e)
    upload_file_to_minio = None
    download_file_from_minio = None

MINIO_BASE_URL = os.getenv("MINIO_BASE_URL", "").rstrip("/")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "").strip()
try:
    from minio_client import MINIO_BASE_URL as _MBU  # type: ignore
    if _MBU:
        MINIO_BASE_URL = _MBU.rstrip("/")
except Exception:
    pass
try:
    from minio_client import MINIO_BUCKET as _MB  # type: ignore
    if _MB:
        MINIO_BUCKET = _MB.strip()
except Exception:
    pass

# 可选：若在 minio_client 中实现了 get_trace_url(session_id)
try:
    from minio_client import get_trace_url as _get_trace_url  # type: ignore
except Exception:
    _get_trace_url = None

# 事件记录 & SSE
from scripts.recorder import stream_event_generator, record_step

# 分析入口
from scripts.api_core import analyze_with_streaming_path

# --- OpenWebUI 数据根与 uploads 目录（用于 /ingest/openwebui）---
OPENWEBUI_DATA_DIR = Path(
    os.environ.get("OPENWEBUI_DATA_DIR")
    or os.environ.get("DATA_DIR")
    or "/home/wucz/openwebui-data"  # 你当前环境更常用这个
).resolve()
OPENWEBUI_UPLOADS = (OPENWEBUI_DATA_DIR / "uploads").resolve()
logger.info(f"[Ingest] OPENWEBUI_DATA_DIR={OPENWEBUI_DATA_DIR}")
logger.info(f"[Ingest] OPENWEBUI_UPLOADS={OPENWEBUI_UPLOADS}")

# --- FastAPI ---
app = FastAPI(title="Dynamic-LangGraph Service", version="2.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期放开；上线请收缩
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= 数据模型 =================
class AnalyzeIn(BaseModel):
    file_path: str = Field(..., description="MinIO 对象键或后端可访问的绝对路径，如 uploaded/123abc_xxx.txt")
    query: str = Field(..., description="分析问题或指令")
    session_id: str = Field(..., description="会话ID，用于聚合SSE与Trace")

class IngestIn(BaseModel):
    filename: Optional[str] = Field(
        default=None,
        description="聊天上传文件在 uploads/ 下的文件名（如 ball501.txt 或带UUID前缀的实际名）。省略=自动选最新上传"
    )
    session_id: str = Field(..., description="会话ID，用于对象键前缀")
    prefer_ext: Optional[str] = Field(
        default=None,
        description="可选：当 filename 省略时，优先选择该扩展名（如 .txt/.csv）"
    )

# ================= 工具函数 =================
def _compose_trace_url(session_id: str) -> Optional[str]:
    """生成 trace/{session_id}.jsonl 的可访问 URL。"""
    if _get_trace_url:
        try:
            url = _get_trace_url(session_id)  # type: ignore
            if url:
                return url
        except Exception as e:
            logger.warning("minio_client.get_trace_url 调用失败，降级拼接：%s", e)
    if MINIO_BASE_URL and MINIO_BUCKET:
        return f"{MINIO_BASE_URL}/{MINIO_BUCKET}/trace/{session_id}.jsonl"
    return None

async def _run_analyze_background(file_path: str, query: str, session_id: str) -> None:
    """后台运行 analyze_with_streaming_path。异常时推送 error 事件。"""
    try:
        if asyncio.iscoroutinefunction(analyze_with_streaming_path):
            await analyze_with_streaming_path(file_path=file_path, query=query, session_id=session_id)
        else:
            await asyncio.to_thread(analyze_with_streaming_path, file_path=file_path, query=query, session_id=session_id)
    except Exception as e:
        logger.exception("analyze 后台任务异常：%s", e)
        try:
            record_step(
                session_id=session_id,
                node="api",
                step_type="exception",
                type="error",
                content=str(e),
            )
        except Exception:
            pass

def _pick_latest(paths):
    return max(paths, key=lambda p: p.stat().st_mtime) if paths else None

# ================= 路由 =================
@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}

@app.post("/upload")
async def upload(
    file: UploadFile = File(..., description="要上传的文件"),
    query: str = Form(..., description="对该文件执行的任务或问题"),
    session_id: str = Form(..., description="会话ID，前后端一致"),
):
    """直接接收前端文件并上传 MinIO（multipart 表单）。"""
    if not upload_file_to_minio:
        return JSONResponse(status_code=500, content={"detail": "upload_file_to_minio 未定义，请检查 minio_client.py"})

    safe_name = strip_webui_uuid_prefix(safe_name)
    object_key = f"uploaded/{sid_core(session_id)}_{safe_name}"


    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        content = await file.read()
        tmp.write(content)

    try:
        upload_file_to_minio(tmp_path, object_key)
        logger.info("Uploaded to MinIO: %s", object_key)
    except Exception as e:
        logger.exception("上传 MinIO 失败：%s", e)
        return JSONResponse(status_code=500, content={"detail": f"上传失败: {e}"})
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    return {"file_path": object_key, "query": query, "session_id": session_id}

@app.post("/ingest/openwebui")
async def ingest_from_openwebui(payload: IngestIn):
    """
    从 OpenWebUI 的 uploads/ 目录搬运文件到 MinIO。
    - filename 提供：先精确匹配；失败则按 *_<filename> 结尾做智能匹配，取 mtime 最新
    - filename 省略：在 uploads/ 中自动选择“最新修改”的文件；若提供 prefer_ext，则优先按扩展名筛选后再取最新
    - 目标对象键：uploaded/{session_id}_{源文件名}
    - 幂等：若对象已存在，直接复用（不重复上传）
    """
    if not payload.session_id:
        return JSONResponse(status_code=400, content={"ok": False, "msg": "session_id required"})
    if not upload_file_to_minio:
        return JSONResponse(status_code=500, content={"ok": False, "msg": "upload_file_to_minio 未定义"})

    src: Optional[Path] = None

    # A) filename 给定：精确命中 -> 智能匹配 *_filename
    if payload.filename:
        requested = (OPENWEBUI_UPLOADS / payload.filename).resolve()
        try:
            requested.relative_to(OPENWEBUI_UPLOADS)
        except Exception:
            return JSONResponse(status_code=400, content={"ok": False, "msg": "invalid filename scope"})
        if requested.exists() and requested.is_file():
            src = requested
        else:
            import glob
            pattern = str(OPENWEBUI_UPLOADS / f"*_{payload.filename}")
            matches = [Path(p) for p in glob.glob(pattern)]
            matches = [p for p in matches if p.is_file()]
            src = _pick_latest(matches)
    else:
        # B) filename 省略：自动挑 uploads/ 最新文件；若设置 prefer_ext 则先筛扩展名
        files = [p for p in OPENWEBUI_UPLOADS.iterdir() if p.is_file()]
        if not files:
            return JSONResponse(
                status_code=404,
                content={
                    "ok": False,
                    "msg": f"no file found in {OPENWEBUI_UPLOADS}",
                    "hint": "确认 DATA_DIR/OPENWEBUI_DATA_DIR & 已上传文件",
                },
            )
        if payload.prefer_ext:
            ext = payload.prefer_ext.lower().lstrip(".")
            prefer = [p for p in files if p.suffix.lower().lstrip(".") == ext]
            src = _pick_latest(prefer) or _pick_latest(files)
        else:
            src = _pick_latest(files)

    if not src:
        return JSONResponse(
            status_code=404,
            content={"ok": False, "msg": "file not found (no match)", "hint": "检查文件名或尝试省略 filename 以自动挑最新上传"},
        )

    clean_name = strip_webui_uuid_prefix(src.name)
    object_key = f"uploaded/{sid_core(payload.session_id)}_{clean_name}"


    # ✅ 幂等：若对象已存在，直接复用，不重复上传
    try:
        from minio_client import object_exists  # 延迟导入，避免顶部依赖顺序问题
        if object_exists(object_key):
            return {
                "ok": True,
                "bucket": MINIO_BUCKET or os.getenv("MINIO_BUCKET", ""),
                "object": object_key,
                "source": src.name,
                "exists": True,
            }
    except Exception:
        # 若匿名策略不允许 stat，忽略检查，继续尝试上传（SDK 会覆盖/或直接成功）
        pass

    try:
        upload_file_to_minio(str(src), object_key)
        return {
            "ok": True,
            "bucket": MINIO_BUCKET or os.getenv("MINIO_BUCKET", ""),
            "object": object_key,
            "source": src.name,
            "exists": False,
        }
    except Exception as e:
        logger.exception("MinIO ingest 失败：%s", e)
        return JSONResponse(status_code=500, content={"ok": False, "msg": f"{e}"})

@app.post("/analyze")
async def analyze(payload: AnalyzeIn):
    """启动分析任务（后台运行），立即返回。"""
    key = _run_key(payload.session_id, payload.file_path, payload.query)
    if _recently_started(key, ttl=60.0):  # 60 秒内重复触发直接忽略
        return {"code": 0, "msg": "duplicate_ignored", "session_id": payload.session_id}

    asyncio.create_task(_run_analyze_background(
        file_path=payload.file_path,
        query=payload.query,
        session_id=payload.session_id,
    ))
    return {"code": 0, "msg": "started", "session_id": payload.session_id}


@app.post("/analyze-path")
async def analyze_path_alias(payload: AnalyzeIn):
    return await analyze(payload)

@app.get("/stream/{session_id}")
async def stream(session_id: str):
    """SSE 实时事件流（data: {json}\\n\\n）。"""
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    generator = stream_event_generator(session_id)
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)

@app.get("/trace/{session_id}")
async def get_trace_url(session_id: str, raw: int = 0):
    """
    返回：
      - raw=0（默认）：{"session_id": "...", "trace_url": "<可访问URL>"}  —— 兼容旧用法
      - raw=1：直接把 trace jsonl 原文返回（由后端代为读取 MinIO），避免容器访问直链失败
    """
    object_key = f"trace/{session_id}.jsonl"

    # raw 模式：尝试通过 SDK 直接读；若没有 download_file_from_minio，则回退用直链 GET
    if raw == 1:
        # 优先 SDK
        if download_file_from_minio:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
            try:
                download_file_from_minio(object_key, tmp_path)
                txt = Path(tmp_path).read_text(encoding="utf-8")
                return PlainTextResponse(txt, media_type="text/plain; charset=utf-8")
            except Exception as e:
                logger.warning("trace SDK 读取失败，将回退直链：%s", e)
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        # 回退直链
        url = _compose_trace_url(session_id)
        if not url:
            return JSONResponse(
                status_code=500,
                content={"detail": "无法拼出 Trace 直链，请配置 MINIO_BASE_URL 与 MINIO_BUCKET"},
            )
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                return PlainTextResponse(r.text, media_type="text/plain; charset=utf-8")
            return JSONResponse(status_code=r.status_code, content={"detail": f"get trace via url failed: HTTP {r.status_code}"})
        except Exception as e:
            return JSONResponse(status_code=502, content={"detail": f"fetch trace url error: {e}"})

    # 非 raw：保持旧行为
    url = _compose_trace_url(session_id)
    if not url:
        return JSONResponse(
            status_code=500,
            content={"detail": "无法拼出 Trace 直链，请配置 MINIO_BASE_URL 与 MINIO_BUCKET 或在 minio_client 中提供 get_trace_url(session_id)"},
        )
    return {"session_id": session_id, "trace_url": url}
