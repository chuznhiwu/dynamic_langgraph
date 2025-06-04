import json
from langchain_core.messages import HumanMessage, AIMessage
from ..data_state import DataState
from ..llms import llm_viz, llm_multimod
from ..tools import viz_tools
from ..utils import b64_image
from dynamic_langgraph.utils import debug_ai_message

_MAP = {
    "time_plot": viz_tools.time_plot,
    "freq_plot": viz_tools.freq_plot,
}

def viz(state: DataState):
    prompt = (
        "你是绘图专家，只调用绘图工具。\n"
        f"FILE_PATH={state.path}\nUSER_NEED={state.user_input}"
    )
    ai: AIMessage = llm_viz.invoke([HumanMessage(content=prompt)])
    debug_ai_message(ai)
    imgs = []
    for call in ai.tool_calls:
        func = _MAP[call["name"]]
        img_path = json.loads(func.invoke(call["args"]))["image_path"]
        imgs.append(img_path)
    mm_prompt = [{"type": "text", "text": "请分析下列图像总体趋势"}] + [
        {"type": "image_url", "image_url": {"url": b64_image(p)}} for p in imgs
    ]
    mm_res: AIMessage = llm_multimod.invoke([HumanMessage(content=mm_prompt)])
    debug_ai_message(mm_res)
    return {"viz_paths": imgs, "viz_summary": mm_res.content}
