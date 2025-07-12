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
from .nodes.summarizer import summarizer        # ← 显式注册
from .nodes.markdown_convert import markdown_convert 
from .nodes.md_summarizer    import md_summarizer
from .nodes.asr_convert     import asr_convert
from .nodes.audio_summarizer     import audio_summarizer
# ─────────────────────────────────────────────────────────
# 映射 task_name → 节点函数
# ※ 以后新增节点记得在此添加
# ─────────────────────────────────────────────────────────
_TASK_NODE_MAP = {
    "analysis":   analysis,
    "viz":        viz,
    "diagnosis":  diagnosis,
    "summarizer": summarizer,
    "convert_md": markdown_convert,
    "md_summary": md_summarizer,
    "asr": asr_convert,
    "audio_summary": audio_summarizer, 
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
    按 task_list 顺序构建 *串行* LangGraph，
    并将流程图保存到 graphs/flow_<timestamp>_<uid>.png。

    Parameters
    ----------
    task_list : List[str]
        例如 ["analysis", "viz", "diagnosis", "summarizer"]

    Returns
    -------
    compiled : langgraph.graph.CompiledGraph
    """
    G = StateGraph(DataState)

    # ① loader 入口 ------------------------------------------------------------
    G.add_node("loader", loader)
    prev = "loader"

    # ② 逐任务串联 -------------------------------------------------------------
    for t in task_list:
        if t not in _TASK_NODE_MAP:
            raise ValueError(f"Unknown task name: {t}")
        G.add_node(t, _TASK_NODE_MAP[t])
        G.add_edge(prev, t)
        prev = t                      # 往下接链

    # 如果 task_list 为空，就让流程只有 loader
    finish_node = prev               # 最后一个节点就是收尾
    G.set_entry_point("loader")
    G.set_finish_point(finish_node)

    compiled = G.compile()

    # ③ 输出 Mermaid PNG -------------------------------------------------------
    try:
        out_png = _new_png_name()
        png_bytes = compiled.get_graph(xray=True).draw_mermaid_png()
        out_png.write_bytes(png_bytes)
        logging.info("流程图已保存：%s", out_png)
    except Exception as e:
        logging.warning("流程图生成失败：%s", e)

    return compiled
