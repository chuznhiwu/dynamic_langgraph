# minio_client.py
# 统一封装 MinIO 访问（上传/下载/直链生成/trace URL 构造/删除）
# 环境变量（可选）：
#   MINIO_ENDPOINT      默认为 "192.168.100.1:9000"
#   MINIO_ACCESS_KEY    必填（建议走凭证；即便桶已匿名开放删除，SDK 也更可靠）
#   MINIO_SECRET_KEY    必填
#   MINIO_SECURE        "0"|"1"，默认 "0"（HTTP）
#   MINIO_BUCKET        默认 "dynamic-langgraph"
#   MINIO_BASE_URL      若提供，用于拼公开直链，如 "http://192.168.100.1:9000"
#   MINIO_PRESIGN_HOURS 预签名有效期小时数（无 BASE_URL 时生效），默认 24

import os
import posixpath
import mimetypes
from datetime import timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Iterable
from minio import Minio
from minio.error import S3Error, InvalidResponseError

# -------- 配置 ----------
MINIO_ENDPOINT   = os.getenv("MINIO_ENDPOINT", "192.168.100.1:9000").strip()
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "").strip()
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "").strip()
MINIO_SECURE     = os.getenv("MINIO_SECURE", "0").strip() in ("1", "true", "TRUE", "True")
MINIO_BUCKET     = os.getenv("MINIO_BUCKET", "dynamic-langgraph").strip()
MINIO_BASE_URL   = os.getenv("MINIO_BASE_URL", "").strip().rstrip("/")
PRESIGN_HOURS    = int(os.getenv("MINIO_PRESIGN_HOURS", "24"))

# 暴露给外部（api_server 会引用这两个）
MINIO_BASE_URL = MINIO_BASE_URL
MINIO_BUCKET   = MINIO_BUCKET

_client: Optional[Minio] = None


def _client_instance() -> Minio:
    """单例 Minio 客户端；确保桶存在。"""
    global _client
    if _client is None:
        if not MINIO_ACCESS_KEY or not MINIO_SECRET_KEY:
            raise RuntimeError("MINIO_ACCESS_KEY / MINIO_SECRET_KEY 未设置，请在环境变量中配置。")
        _client = Minio(
            endpoint=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
        _ensure_bucket(_client, MINIO_BUCKET)
    return _client


def _ensure_bucket(cli: Minio, bucket: str) -> None:
    if not cli.bucket_exists(bucket):
        cli.make_bucket(bucket)


def _guess_content_type(path: str) -> str:
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def _path_join(*parts: str) -> str:
    """使用 / 连接对象键片段；过滤空段。"""
    parts = [str(p).strip("/") for p in parts if p and str(p).strip("/")]
    return "/".join(parts)


def _object_public_url(object_key: str) -> Optional[str]:
    """若配置 MINIO_BASE_URL，则拼公开直链 {BASE}/{bucket}/{object_key}。"""
    if MINIO_BASE_URL:
        return f"{MINIO_BASE_URL}/{MINIO_BUCKET}/{object_key}"
    return None


def _object_presigned_url(object_key: str, hours: int = PRESIGN_HOURS) -> str:
    """生成预签名 URL（当没有 MINIO_BASE_URL 时使用）。"""
    cli = _client_instance()
    return cli.presigned_get_object(MINIO_BUCKET, object_key, expires=timedelta(hours=hours))


def object_url(object_key: str) -> str:
    """返回对象可访问的 URL（优先公开直链，否则预签名）。"""
    pub = _object_public_url(object_key)
    if pub:
        return pub
    return _object_presigned_url(object_key)


# -------------- 上传/下载 ----------------

def upload_file_to_minio(local_path: str, object_key: str, content_type: Optional[str] = None) -> str:
    """上传本地文件到 MinIO；返回可访问 URL。"""
    cli = _client_instance()
    local_path = str(local_path)
    if content_type is None:
        content_type = _guess_content_type(local_path)

    size = os.path.getsize(local_path)
    with open(local_path, "rb") as f:
        cli.put_object(
            MINIO_BUCKET,
            object_key,
            data=f,
            length=size,
            content_type=content_type,
        )
    return object_url(object_key)


def upload_bytes_to_minio(data: bytes, object_key: str, content_type: str = "application/octet-stream") -> str:
    """上传内存 bytes；返回可访问 URL。"""
    from io import BytesIO
    cli = _client_instance()
    bio = BytesIO(data)
    cli.put_object(
        MINIO_BUCKET,
        object_key,
        data=bio,
        length=len(data),
        content_type=content_type,
    )
    return object_url(object_key)


def download_file_from_minio(object_key: str, local_path: str) -> str:
    """下载对象到本地路径；返回本地路径。"""
    cli = _client_instance()
    cli.fget_object(MINIO_BUCKET, object_key, local_path)
    return local_path


# -------------- Trace 相关 ----------------

def get_trace_object_key(session_id: str) -> str:
    """标准化 trace 文件对象键。"""
    return _path_join("trace", f"{session_id}.jsonl")


def get_trace_url(session_id: str) -> str:
    """返回 trace/{session_id}.jsonl 的可访问 URL。"""
    return object_url(get_trace_object_key(session_id))


# -------------- 产物快捷上传（供节点调用） --------------

def save_and_yield(
    local_path: str,
    session_id: str,
    node: str,
    step_type: str,
    file_type: str = "artifact",
    object_prefix: str = "artifacts",
    keep_basename: bool = True,
) -> Dict[str, Any]:
    """
    常用的“上传并返回事件荷载”的小工具：
      - 把 local_path 上传到 MinIO，路径：{object_prefix}/{session_id}/{basename}
      - 返回 dict，便于 recorder 直接 record_and_stream 推送
    """
    basename = Path(local_path).name if keep_basename else ""
    object_key = _path_join(object_prefix, session_id, basename or "file.bin")
    url = upload_file_to_minio(local_path, object_key)

    payload = {
        "type": "image" if file_type == "image" else "artifact",
        "session_id": session_id,
        "node": node,
        "step_type": step_type,
        "content": url,
        "object_key": object_key,
    }
    return payload


# -------------- 删除 / 清理 ----------------

def delete_object(object_key: str, *, missing_ok: bool = True) -> bool:
    """
    删除单个对象。
    返回 True 表示调用未抛出异常；当对象不存在且 missing_ok=True 时也视为成功。
    """
    cli = _client_instance()
    try:
        cli.remove_object(MINIO_BUCKET, object_key)
        return True
    except S3Error as e:
        # 对象不存在：NoSuchKey
        if missing_ok and getattr(e, "code", "") in ("NoSuchKey", "NoSuchObject"):
            return True
        raise
    except InvalidResponseError:
        # 某些匿名/策略场景会走到这里；如果你已开放匿名删除，请确认策略
        raise


def delete_objects(object_keys: Iterable[str], *, missing_ok: bool = True) -> int:
    """
    批量删除对象（逐个删除，便于兼容所有部署；量大时可改用批量 API）。
    返回成功删除的数量（不存在但 missing_ok=True 的也计入）。
    """
    n = 0
    for key in object_keys:
        try:
            if delete_object(key, missing_ok=missing_ok):
                n += 1
        except Exception:
            # 想更严格可改成 raise
            pass
    return n


def delete_prefix(prefix: str) -> int:
    """
    删除某个前缀下所有对象（如 pic/<session_id>/、processfiles/<session_id>/）。
    返回删除数量。
    """
    cli = _client_instance()
    count = 0
    for obj in cli.list_objects(MINIO_BUCKET, prefix=prefix, recursive=True):
        try:
            cli.remove_object(MINIO_BUCKET, obj.object_name)
            count += 1
        except Exception:
            # 不中断；按需记录日志
            pass
    return count


def delete_session_artifacts(session_id: str) -> Dict[str, int]:
    """
    清理一个会话的所有产物（慎用）：
      - trace/<session_id>.jsonl
      - pic/<session_id>/...
      - processfiles/<session_id>/...
      - graphs/graph_<session_id>.png（如果有）
    返回每个类别删除数量统计。
    """
    stats = {"trace": 0, "pic": 0, "processfiles": 0, "graphs": 0}

    # trace
    try:
        if delete_object(_path_join("trace", f"{session_id}.jsonl")):
            stats["trace"] = 1
    except Exception:
        pass

    # pic/
    try:
        stats["pic"] = delete_prefix(_path_join("pic", session_id))
    except Exception:
        pass

    # processfiles/
    try:
        stats["processfiles"] = delete_prefix(_path_join("processfiles", session_id))
    except Exception:
        pass

    # graphs/
    try:
        key = _path_join("graphs", f"graph_{session_id}.png")
        if delete_object(key):
            stats["graphs"] = 1
    except Exception:
        pass

    return stats

def object_exists(object_key: str) -> bool:
    """
    判断对象是否存在。存在 -> True；不存在 -> False。
    若匿名策略不允许 stat，请确保使用带 AK/SK 的客户端。
    """
    cli = _client_instance()
    try:
        cli.stat_object(MINIO_BUCKET, object_key)
        return True
    except S3Error as e:
        # MinIO/ S3 标准：不存在会抛 NoSuchKey/NoSuchObject
        if getattr(e, "code", "") in ("NoSuchKey", "NoSuchObject"):
            return False
        raise
