"""
LangGraph 节点：音频文件 → 中文转写
依赖：
    pip install faster-whisper   # 推荐更快
    # 或 pip install openai-whisper
"""
from pathlib import Path
from dynamic_langgraph.data_state import DataState
from ..llms import llm_main

# ---------- Whisper 初始化 ----------
from faster_whisper import WhisperModel        # 若用官方 whisper 改这一行
_MODEL = WhisperModel("large-v3", device="auto", compute_type="int8")


def _transcribe_to_zh(audio_path: Path) -> str:
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    segments, info = _MODEL.transcribe(
        str(audio_path),
        vad_filter=True,         # 不显式指定 language，让模型自动识别
        beam_size=5,
    )
    raw = "".join(s.text for s in segments).strip()
    if not raw:
        raise RuntimeError("Whisper 未识别到有效语音内容")

    # 若已是中文，直接返回
    if info.language == "zh":
        return raw

    # 否则调用 LLM 翻译
    prompt = f"请将以下文字准确翻译成中文：\n\n{raw}"
    zh = llm_main.invoke(prompt).content.strip()
    return zh



# ---------- LangGraph 节点 ----------
def asr_convert(state: DataState) -> DataState:
    """
    1. 读取 state.path（音频文件）→ Whisper 转写
    2. 成功：写入 state.asr_text
    3. 失败：写入 state.error_msg，但不抛异常，保证流程继续
    """
    try:
        state.asr_text = _transcribe_to_zh(state.path)
    except Exception as err:
        state.error_msg = f"ASR 失败：{err}"
    return state
