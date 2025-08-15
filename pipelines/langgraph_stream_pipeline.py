# pipelines/langgraph_stream_pipeline.py
from typing import Generator, Iterator, Union, List, Optional, Any, Tuple
from pydantic import BaseModel, Field
import requests, json, uuid, os, posixpath, re
import time, hashlib

"""
LangGraph Stream v2 (fingerprint + join-window)
- 目标：
  1) 同一轮 inlet/outlet 合并为一次（短合并窗 JOIN_TTL）
  2) 同一对话里、甚至新开会话里“同样的问题” → 也要产生全新的 session_id（指纹引入时间窗口/消息id）
  3) 长流程不在结束后“自动重跑”（/analyze 长窗口防抖 START_TTL）
"""

# ---------- 配置 ----------
# 合并窗口：仅用于把“同一条用户消息”的 inlet/outlet 合并为一次；超过窗口就视为新一轮
_JOIN_TTL   = float(os.getenv("LG_JOIN_TTL",  "10"))    # 秒，默认 10s
# 启动防抖：同一“消息指纹”在该窗口内只触发一次 /analyze（避免长流程末尾再次触发）
_START_TTL  = float(os.getenv("LG_START_TTL", "3600"))  # 秒，默认 1h

# ---------- 全局映射/去重 ----------
# 指纹 -> (session_id, 首次分配时间)   （短合并窗）
_FP2SID: dict[str, tuple[str, float]] = {}
# 已触发过 /analyze 的指纹 -> 首次触发时间（长窗口）
_STARTED: dict[str, float] = {}

def _started_recent(fp: str, ttl: float = _START_TTL) -> bool:
    """长窗口防抖：同一条消息（指纹）在 ttl 内只触发一次 /analyze。"""
    now = time.time()
    for k, t in list(_STARTED.items()):
        if now - t > ttl:
            _STARTED.pop(k, None)
    if fp in _STARTED and (now - _STARTED[fp]) < ttl:
        return True
    _STARTED[fp] = now
    return False

def _last_user_id(msgs: list) -> str:
    """尽量取最后一条 user 消息的 id；没有就空字符串。"""
    for m in reversed(msgs):
        if m.get("role") == "user":
            v = m.get("id")
            if isinstance(v, str) and v:
                return v
    return ""

def _make_dedupe_key(body: dict, msgs: list, effective_query: str) -> str:
    """
    生成“一轮消息”的指纹（作为 dkey）：
      fp = md5(conversation_id + last_user_id + normalized_query + time_window)
    说明：
      - time_window = floor(now / JOIN_TTL)
        * 保证 inlet/outlet 在 JOIN_TTL 内得到同一个指纹（同一个 session_id）
        * 超过 JOIN_TTL，即便文本相同，也会得到新的指纹 → 新的 session_id
      - 当 conversation_id 或 user.id 变化（新会话/新消息），指纹也自然不同
    """
    cid = body.get("conversation_id") or body.get("chat_id") or body.get("thread_id") or ""
    uid = _last_user_id(msgs)  # 可能为空；为空也没关系，time_window 可打破历史复用
    win = int(time.time() // max(1, int(_JOIN_TTL)))  # 合并时间窗编号
    seed = f"{cid}||uid:{uid}||q:{effective_query}||win:{win}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()

def _session_for_turn(dkey: str, ttl: float = _JOIN_TTL) -> str:
    """
    在 JOIN_TTL 窗口内为同一指纹复用同一 UUID；超出窗口则分配新 UUID。
    注意：沿用原函数名以最小化改动；这里 dkey 就是 fingerprint。
    """
    now = time.time()
    for k, (sid, ts) in list(_FP2SID.items()):
        if now - ts > ttl:
            _FP2SID.pop(k, None)
    if dkey in _FP2SID:
        sid, _ = _FP2SID[dkey]
        _FP2SID[dkey] = (sid, now)  # touch
        return sid
    sid = "owui-" + uuid.uuid4().hex[:24]
    _FP2SID[dkey] = (sid, now)
    return sid

def _touch_turn(dkey: str):
    """在流式过程中刷新指纹活跃时间，防止长流程时映射过期（保持 inlet/outlet 合并一致）。"""
    if dkey in _FP2SID:
        sid, _ = _FP2SID[dkey]
        _FP2SID[dkey] = (sid, time.time())

# 为避免 inlet/outlet 重复登记，缓存一次登记结果（按 session_id + filename + prefer_ext）
_INGEST_CACHE: dict[str, str] = {}
def _ingest_ck(session_id: str, filename: str | None, prefer_ext: str | None) -> str:
    return f"{session_id}:{filename or '_auto'}:{prefer_ext or ''}"


# ---------------- Pipeline ----------------
class Pipeline:
    class Valves(BaseModel):
        NAME: str = Field(default="LangGraph Stream v2", description="在模型下拉的显示名")
        BACKEND_BASE: str = Field(
            default=os.getenv("LG_BACKEND", "http://localhost:1050"),
            description="LangGraph 后端根地址；WebUI 在 Docker 时用 http://host.docker.internal:1050",
        )
        MODE: str = Field(default="sse", description="sse|trace （首选 sse，失败/空闲自动回退 trace）")
        TRACE_POLL_SEC: float = Field(default=1.0, description="trace 轮询间隔秒")
        SSE_CONNECT_TIMEOUT: float = Field(default=10.0, description="SSE 连接超时（秒）")
        SSE_IDLE_TIMEOUT: float = Field(default=12.0, description="SSE 无事件最大等待（秒），超过即回退 trace")
        DEFAULT_FILE_PATH: Optional[str] = Field(
            default=None,
            description="兜底对象键（如 uploaded/abc_demo.csv）；当没有任何文件可用时使用"
        )
        PREFER_EXT: Optional[str] = Field(
            default=None,
            description="当未提供文件名时，后端挑最近上传文件时优先该扩展名（如 .txt/.csv）"
        )
        INGEST_PATH: str = Field(default="/ingest/openwebui", description="登记接口路径")
        ANALYZE_PATH: str = Field(default="/analyze", description="启动分析接口路径")
        STREAM_PATH: str = Field(default="/stream", description="SSE 路径前缀")
        TRACE_PATH: str = Field(default="/trace", description="trace 路径前缀")

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self) -> List[dict]:
        return [{"id": "langgraph-stream-v2", "name": self.valves.NAME}]

    def pipe(self, body: dict, **kwargs) -> Union[str, Generator, Iterator]:
        # 1) 取“用户原话”作为 query；若被过滤器包裹，则还原 Chat History 的最后一条 USER
        msgs = body.get("messages", []) or []
        raw_user = next((m.get("content", "") for m in reversed(msgs) if m.get("role") == "user"), "")
        user_msg = _extract_last_user_from_history_block(raw_user) or raw_user
        effective_query = _clean_query_noise(user_msg)

        # 2) 基本参数 —— “同一轮消息的 inlet/outlet 共享一个 session_id（短合并窗）”
        base = self.valves.BACKEND_BASE.rstrip("/")
        want_stream = bool(body.get("stream", True))  # WebUI 聊天通常是 True
        dkey = _make_dedupe_key(body, msgs, effective_query)   # ← 指纹
        session_id = _session_for_turn(dkey, ttl=_JOIN_TTL)    # ← JOIN_TTL 内复用；过窗就新 UUID

        # 3) 决定 file_path（对象键优先），尽量从消息体/附件中获取
        file_path: Optional[str] = body.get("file_path") or _extract_file_tag(user_msg)  # FILE: uploaded/xxx 或 /abs/path
        filename_tag = _extract_filename_tag(user_msg)  # FILENAME: name.ext
        attachments = _extract_attachments_from_body(body, msgs)  # 可能拿到 ['uuid_xxx_name.ext', 'name.ext']

        candidate_names: List[str] = []
        if filename_tag:
            candidate_names.append(filename_tag)
        for n in attachments:
            if n and n not in candidate_names:
                candidate_names.append(n)

        # 4) 若还不是对象键，尝试登记（命中具名文件后，不再兜底覆盖）
        if not _looks_like_object_key(file_path):
            picked = False
            if candidate_names:
                for name in candidate_names:
                    ck = _ingest_ck(session_id, name, None)
                    if ck in _INGEST_CACHE:
                        file_path = _INGEST_CACHE[ck]; picked = True; break
                    ok, obj_or_err = _try_ingest(base, name, session_id, self.valves.INGEST_PATH, prefer_ext=None)
                    if ok:
                        _INGEST_CACHE[ck] = obj_or_err; file_path = obj_or_err; picked = True; break
            if not picked:
                ck = _INGEST_CACHE.get(_ingest_ck(session_id, None, self.valves.PREFER_EXT))
                if ck:
                    file_path = ck
                else:
                    ok, obj_or_err = _try_ingest(base, None, session_id, self.valves.INGEST_PATH, prefer_ext=self.valves.PREFER_EXT)
                    if ok:
                        _INGEST_CACHE[_ingest_ck(session_id, None, self.valves.PREFER_EXT)] = obj_or_err
                        file_path = obj_or_err

        # 5) 最终兜底
        if not file_path and self.valves.DEFAULT_FILE_PATH:
            file_path = self.valves.DEFAULT_FILE_PATH

        if not file_path:
            msg = (
                "未找到可用的上传文件。\n"
                "请先在聊天里用“+”上传，或提供对象键：`FILE: uploaded/xxx.ext`，"
                "或原始文件名：`FILENAME: name.ext`，"
                "也可在阀门里设置 DEFAULT_FILE_PATH。"
            )
            return msg if not want_stream else _as_generator([msg])

        # 6) 启动分析 —— 按“指纹”做长窗口防抖（默认 1 小时）
        try:
            if not _started_recent(dkey, ttl=_START_TTL):
                requests.post(
                    f"{base}{self.valves.ANALYZE_PATH}",
                    json={"file_path": file_path, "query": effective_query, "session_id": session_id},
                    timeout=15,
                )
        except Exception as e:
            msg = f"启动分析失败：{e}"
            return msg if not want_stream else _as_generator([msg])

        # 7) 输出：流式 / 非流式
        if not want_stream:
            chunks = [f"🟢 已启动（session={session_id}，file={file_path}）\n"]
            chunks += list(_drain_trace_short(base, session_id, self.valves.TRACE_PATH, tries=3, poll_sec=self.valves.TRACE_POLL_SEC))
            return "".join(chunks) or f"🟢 已启动（session={session_id}）。稍后查看过程…"

        # 流式：SSE 优先 → trace 回退
        def _stream():
            yield f"🟢 已启动 LangGraph（session={session_id}，file={file_path}）\n\n"
            used_sse, any_event, seen_end = False, False, False

            if self.valves.MODE.lower() == "sse":
                try:
                    for out, any_evt, end_flag in _pipe_from_sse_with_idle(
                        base, session_id, self.valves.STREAM_PATH,
                        connect_timeout=self.valves.SSE_CONNECT_TIMEOUT,
                        idle_timeout=self.valves.SSE_IDLE_TIMEOUT,
                        # 传入 keep-alive 钩子：每到一条事件，就 touch 一下指纹
                        on_event=lambda: _touch_turn(dkey),
                    ):
                        used_sse = True
                        if out:
                            any_event = any_event or any_evt
                            seen_end = seen_end or end_flag
                            yield out
                except Exception:
                    pass

            if (not used_sse) or (not seen_end):
                yield "（SSE 空闲/断开或未收到 end，回退为 trace 轮询…）\n"
                for out in _pipe_from_trace(
                    base, session_id, self.valves.TRACE_PATH, self.valves.TRACE_POLL_SEC,
                    on_event=lambda: _touch_turn(dkey),
                ):
                    if out:
                        yield out

        return _stream()

# --------------- helpers ---------------
def _sticky_session_id(body: dict, msgs: list) -> str:
    """
    旧的“粘性”推导，留作兜底（本 v2 默认不用它来区分一轮消息）
    注意：不再把 "id" 当候选，避免 inlet/outlet 的阶段性 id 抖动
    """
    for k in ("session_id", "conversation_id", "chat_id", "thread_id"):
        v = body.get(k)
        if isinstance(v, str) and len(v) >= 8:
            return v
    for m in msgs:
        for k in ("session_id", "conversation_id", "chat_id", "thread_id"):
            v = m.get(k)
            if isinstance(v, str) and len(v) >= 8:
                return v
    first_user = next((m for m in msgs if m.get("role") == "user"), {})
    seed = (first_user.get("content") or "")[:512]
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return f"owui-{digest[:24]}"

def _as_generator(lines: List[str]) -> Generator[str, None, None]:
    def gen():
        for x in lines:
            yield x
    return gen()

def _extract_last_user_from_history_block(text: str) -> Optional[str]:
    """
    兼容“### Task … ### Chat History … <chat_history> …”格式。
    取历史里的最后一条 USER 作为真实用户意图。
    """
    if "### Chat History:" not in text:
        return None
    m = re.search(r"<chat_history>(.*)", text, re.S)
    if not m:
        return None
    hist = m.group(1)
    users = re.findall(r"\bUSER:\s*(.+)", hist)
    return users[-1].strip() if users else None

def _clean_query_noise(q: str) -> str:
    """
    清理噪音：
    - 去掉 FILE:/FILENAME: 标签
    - 去掉像 xxxxx_ball501.txt 这类文件名（避免指纹被文件名影响）
    """
    q = re.sub(r"\b(FILE|FILENAME)\s*:\s*\S+", "", q, flags=re.I)
    q = re.sub(r"\b\S*\d{3,}\S*\.\w+\b", "", q)
    return q.strip()

def _extract_file_tag(text: str) -> Optional[str]:
    """支持：FILE: uploaded/xxx.ext 或 FILE: /abs/path.ext"""
    if not text:
        return None
    m = re.search(r"\bFILE\s*:\s*(\S+)", text, re.I)
    return m.group(1) if m else None

def _extract_filename_tag(text: str) -> Optional[str]:
    """支持：FILENAME: name.ext"""
    if not text:
        return None
    m = re.search(r"\bFILENAME\s*:\s*([^\s]+)", text, re.I)
    return m.group(1) if m else None

def _looks_like_object_key(s: Optional[str]) -> bool:
    return bool(s and s.startswith("uploaded/"))

def _extract_attachments_from_body(body: dict, messages: List[dict]) -> List[str]:
    """
    尽力从请求体/消息里找附件信息，返回“文件名（basename）”列表。
    兼容常见字段：files / attachments / images；每项尝试读 name/filename/file_name/path/url。
    """
    names: List[str] = []
    def add_name(x: Any):
        n = None
        if isinstance(x, str):
            n = posixpath.basename(x)
        elif isinstance(x, dict):
            n = x.get("name") or x.get("filename") or x.get("file_name")
            if not n:
                p = x.get("path") or x.get("url")
                if p:
                    n = posixpath.basename(p)
        if n:
            n = n.split("?")[0]
            n = posixpath.basename(n)
            names.append(n)
    for k in ("files", "attachments", "images"):
        v = body.get(k)
        if isinstance(v, list):
            for item in v: add_name(item)
    for m in messages:
        for k in ("files", "attachments", "images"):
            v = m.get(k)
            if isinstance(v, list):
                for item in v: add_name(item)
    seen = set(); uniq = []
    for n in names:
        if n not in seen:
            seen.add(n); uniq.append(n)
    return uniq

def _try_ingest(base: str, filename: Optional[str], session_id: str, ingest_path: str, prefer_ext: Optional[str]) -> Tuple[bool, str]:
    """
    调后端 /ingest/openwebui 登记到 MinIO。
    filename 可为 None：由后端自动选择 uploads/ 中最新文件；prefer_ext 可选。
    返回 (ok, object_key_or_error)
    """
    try:
        payload = {"session_id": session_id}
        if filename:   payload["filename"]    = filename
        if prefer_ext: payload["prefer_ext"]  = prefer_ext
        r = requests.post(f"{base}{ingest_path}", json=payload, timeout=20)
        j = r.json()
        if r.status_code == 200 and j.get("ok"):
            return True, j.get("object")
        return False, j.get("msg") or f"HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)

def _pipe_from_sse_with_idle(base: str, session_id: str, stream_path: str,
                             connect_timeout: float, idle_timeout: float,
                             on_event=lambda: None):
    """
    连接 SSE；若在 idle_timeout 内没有新的事件，就结束并由上层回退到 trace。
    产出：(text, any_event_flag, seen_end_flag)
    on_event(): 每有事件就调用一次，用于 keep-alive 指纹
    """
    url = f"{base}{stream_path}/{session_id}"
    last_evt = time.time()
    any_event = False
    seen_end = False
    timeout = (connect_timeout, idle_timeout + 5.0)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            now = time.time()
            # keep-alive / comment
            if raw is None or (isinstance(raw, str) and raw.startswith(":")):
                if (now - last_evt) > idle_timeout:
                    break  # 长时间没新事件 → 交给上层回退
                continue
            if not isinstance(raw, str) or not raw.startswith("data: "):
                continue
            try:
                evt = json.loads(raw[6:])
            except Exception:
                continue
            et   = (evt.get("type") or "").lower()
            node = (evt.get("node") or "").lower()
            if et == "end" and node in ("pipeline", "api", "service"):
                seen_end = True
            out = _format_event(evt)
            if out:
                any_event = True
                last_evt = now
                on_event()          # 🔸保持指纹活跃
                yield out, True, seen_end
    if not any_event:
        yield "", False, seen_end

def _pipe_from_trace(base: str, session_id: str, trace_path: str, poll_sec: float,
                     on_event=lambda: None):
    """
    持续轮询 trace（优先走后端代理 raw=1；失败再回退直链）。
    - 单条解析/渲染失败不会中断
    - 一旦看到 end 事件就停止生成（结束流）
    on_event(): 每解析一条就调用一次，用于 keep-alive 指纹
    """
    import json as _json
    proxy_url  = f"{base}{trace_path}/{session_id}?raw=1"
    use_proxy  = True
    direct_url = None
    seen       = 0
    while True:
        try:
            if use_proxy:
                resp = requests.get(proxy_url, timeout=10)
                if resp.status_code == 200 and resp.text:
                    txt = resp.text
                else:
                    use_proxy = False
                    try:
                        j = requests.get(f"{base}{trace_path}/{session_id}", timeout=10).json()
                        direct_url = j.get("trace_url")
                    except Exception as e:
                        yield f"获取 trace 直链失败（{e}）。\n"; return
                    if not direct_url:
                        yield "获取 trace 直链失败。\n"; return
                    continue
            else:
                if not direct_url:
                    try:
                        j = requests.get(f"{base}{trace_path}/{session_id}", timeout=10).json()
                        direct_url = j.get("trace_url")
                    except Exception as e:
                        yield f"获取 trace 直链失败（{e}）。\n"; return
                    if not direct_url:
                        yield "获取 trace 直链失败。\n"; return
                txt = requests.get(direct_url, timeout=10).text
            lines = txt.splitlines()
            for i in range(seen, len(lines)):
                try:
                    evt = _json.loads(lines[i])
                except Exception:
                    continue
                # 渲染
                try:
                    out = _format_event(evt)
                except Exception as e:
                    out = f"⚠️ 事件渲染失败：{e}\n\n```json\n" + _json.dumps(evt, ensure_ascii=False, indent=2) + "\n```\n"
                if out:
                    on_event()      # 🔸保持指纹活跃
                    yield out
                # 🛑 见到 end 立即结束
                et   = (evt.get("type") or "").lower()
                node = (evt.get("node") or "").lower()
                if et == "end" and node in ("pipeline", "api", "service"):
                    return
            seen = len(lines)
        except Exception as e:
            yield f"trace 读取失败，稍后重试…（{e}）\n"
        time.sleep(poll_sec)

def _drain_trace_short(base: str, session_id: str, trace_path: str, tries: int, poll_sec: float):
    """非流式场景：短轮询几次，把头几条事件带回，避免 UI 空白。"""
    import json as _json
    for attempt in range(max(1, tries)):
        try:
            proxy_url = f"{base}{trace_path}/{session_id}?raw=1"
            resp = requests.get(proxy_url, timeout=10)
            if resp.status_code == 200 and resp.text:
                txt = resp.text
            else:
                j = requests.get(f"{base}{trace_path}/{session_id}", timeout=10).json()
                direct_url = j.get("trace_url")
                if not direct_url: break
                txt = requests.get(direct_url, timeout=10).text
            lines = txt.splitlines()
            out_texts = []
            for ln in lines[:50]:
                try:
                    evt = _json.loads(ln)
                except Exception:
                    continue
                try:
                    out = _format_event(evt)
                except Exception as e:
                    out = f"⚠️ 事件渲染失败：{e}\n\n```json\n" + _json.dumps(evt, ensure_ascii=False, indent=2) + "\n```\n"
                if out: out_texts.append(out)
            if out_texts:
                for o in out_texts: yield o
                break
        except Exception:
            pass
        time.sleep(poll_sec)

def _format_event(evt: dict) -> Optional[str]:
    """
    渲染 recorder 事件为聊天文本（过滤 /tmp 噪声；工具调用/结果；图片/流程图；文件；思考；开始/结束/错误）
    """
    import json as _json
    t  = (evt.get("type") or "").lower()
    st = (evt.get("step_type") or "").lower()
    ev = (evt.get("event") or "").lower()
    content = evt.get("content"); message = evt.get("message"); text = evt.get("text")
    result  = evt.get("result") if isinstance(evt.get("result"), dict) else None
    tool    = evt.get("tool_name") or (result.get("tool_name") if result else None)
    # 噪声：只有 /tmp 路径
    def _is_tmp_path_only(x) -> bool:
        return isinstance(x, dict) and set(x.keys()) == {"path"} and isinstance(x.get("path"), str) and x["path"].startswith("/tmp/tmp")
    if _is_tmp_path_only(content) or _is_tmp_path_only(message) or _is_tmp_path_only(text) or _is_tmp_path_only(result):
        return None
    url = (evt.get("url") or evt.get("image_url") or (result.get("url") if result else None)
           or (content if isinstance(content, str) and content.startswith(("http://","https://","/")) else None))
    if t == "flowchart" and url: return f"### 流程图\n\n![]({url})\n\n"
    if (t == "image" or t == "plot") and url:
        title = evt.get("title") or "图像"; return f"**{title}**\n\n![]({url})\n\n"
    if t in ("file","document","artifact") and url:
        name = (result.get("filename") if result else None) or url.split("/")[-1]; return f"[下载 {name}]({url})\n\n"
    is_tool_call   = (st == "tool_call") or (ev == "tool_call")
    is_tool_result = (st == "tool_result") or (ev == "tool_result")
    if is_tool_call:
        args = None
        if isinstance(content,(dict,list)): args = content
        elif isinstance(message,(dict,list)): args = message
        elif isinstance(text,(dict,list)): args = text
        elif result and isinstance(result.get("args"),(dict,list)): args = result["args"]
        if args is not None:
            pretty = _json.dumps(args, ensure_ascii=False, indent=2)
            return f"🛠️ **调用工具：{tool or '未知工具'}**\n\n```json\n{pretty}\n```\n"
        return f"🛠️ **调用工具：{tool or '未知工具'}**\n\n"
    if is_tool_result:
        payload = result if result is not None else (content if isinstance(content,(dict,list)) else content)
        if isinstance(payload,(dict,list)):
            pretty = _json.dumps(payload, ensure_ascii=False, indent=2)
            return f"✅ **工具结果（{tool or '未知工具'}）**\n\n```json\n{pretty}\n```\n"
        if isinstance(payload,(str,int,float)):
            return f"✅ **工具结果（{tool or '未知工具'}）**\n\n{str(payload)}\n\n"
    if evt.get("thought") is not None:
        model = evt.get("model"); usage = evt.get("usage") if isinstance(evt.get("usage"), dict) else {}
        tok = usage.get("total_tokens") or usage.get("total") or ""
        meta = " (" + " | ".join([s for s in [str(model) if model else None, f"tokens={tok}" if tok else None] if s]) + ")" if (model or tok) else ""
        out = f"**[THOUGHT]{meta}**\n\n{evt['thought']}\n\n"
        if isinstance(content,(str,int,float)) and str(content).strip() and str(content).strip() != str(evt["thought"]).strip():
            out += f"**LLM 选择：** {str(content).strip()}\n\n"
        return out
    if (t in ("start","end","error","exception")) or (st in ("start","end","error","exception")):
        body = message if message is not None else (text if text is not None else content)
        if isinstance(body,(dict,list)): rendered = "```json\n" + _json.dumps(body, ensure_ascii=False, indent=2) + "\n```"
        elif body is None:             rendered = ""
        else:                          rendered = str(body)
        tag = (st or t).upper()
        return f"**[{tag}]** {rendered}\n\n"
    body = message if message is not None else (text if text is not None else content)
    if body is None: return None
    if isinstance(body,(dict,list)): return "```json\n" + _json.dumps(body, ensure_ascii=False, indent=2) + "\n```\n"
    return str(body) + "\n"
