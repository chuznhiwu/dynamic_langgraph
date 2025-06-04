# dynamic_langgraph/graph_builder.py
"""
Assemble a LangGraph StateGraph *and* auto-export a PNG flowchart
to ./graphs/flow_<timestamp>_<uid>.png.
"""
from pathlib import Path
import datetime, uuid, logging

from langgraph.graph import StateGraph
from .data_state import DataState
from .nodes.loader import loader
from .nodes.analysis import analysis
from .nodes.viz import viz
from .nodes.diagnosis import diagnosis
from .nodes.summarizer import summarizer

# 映射任务 → 节点函数
_TASK_NODE_MAP = {
    "analysis":   analysis,
    "viz":        viz,
    "diagnosis":  diagnosis,
}

# graphs/ 目录（与 plots/ 同级）
_GRAPHS_DIR = Path(__file__).resolve().parent.parent / "graphs"
_GRAPHS_DIR.mkdir(exist_ok=True)

def _new_png_name() -> Path:
    ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:6]
    return _GRAPHS_DIR / f"flow_{ts}_{uid}.png"

def build_graph(task_list):
    """
    根据 LLM 返回的 task_list 顺序构建 *串行* LangGraph，
    并将流程图保存为 graphs/flow_<timestamp>_<uid>.png。

    Parameters
    ----------
    task_list : List[str]
        e.g. ["analysis", "viz", "diagnosis"]

    Returns
    -------
    compiled : langgraph.graph.CompiledGraph
        可直接 .invoke(...) 的管线。
    """
    G = StateGraph(DataState)

    # ── 1. loader 作为入口 ─────────────────────────────
    G.add_node("loader", loader)

    # ── 2. 按顺序逐个串联任务节点 ─────────────────────
    prev = "loader"
    for t in task_list:
        if t not in _TASK_NODE_MAP:
            raise ValueError(f"Unknown task name: {t}")
        G.add_node(t, _TASK_NODE_MAP[t])
        G.add_edge(prev, t)   # 串行：前一个 → 当前
        prev = t

    # ── 3. summarizer 收尾 ────────────────────────────
    G.add_node("summarizer", summarizer)
    G.add_edge(prev, "summarizer")

    G.set_entry_point("loader")
    G.set_finish_point("summarizer")

    compiled = G.compile()

    # ── 4. 输出流程图 PNG ─────────────────────────────
    try:
        out_png = _new_png_name()
        png_bytes = compiled.get_graph(xray=True).draw_mermaid_png()
        out_png.write_bytes(png_bytes)
        logging.info("📌 流程图已保存：%s", out_png)
    except Exception as e:
        logging.warning("⚠️ 流程图生成失败：%s", e)

    return compiled

