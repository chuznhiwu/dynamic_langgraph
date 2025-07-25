# dynamic_langgraph/api_core.py
from pathlib import Path
from dynamic_langgraph.pipeline import run_dynamic_pipeline
from dataclasses import asdict

def analyze_with_path(file_path: str, query: str) -> dict:
    """
    参数:
        file_path: 容器内可访问的绝对或相对路径
        query:     自然语言任务描述
    返回:
        dict，可直接序列化为 JSON
    """
    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"{p} 不存在")
    state = run_dynamic_pipeline(p, query)
    # 假设 DataState 是 dataclass，否则按需手动拼
    return asdict(state)
