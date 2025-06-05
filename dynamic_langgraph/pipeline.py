import logging
from pathlib import Path
from .case_loader import load_case_templates, llm_pick_tasks
from .graph_builder import build_graph

def run_dynamic_pipeline(txt_path: str | Path, user_input: str):
    cases = load_case_templates()
    tasks = llm_pick_tasks(user_input, cases)
    pipe = build_graph(tasks)
    result = pipe.invoke({"path": Path(txt_path), "user_input": user_input})
    logging.info("LLM 规划任务流: %s", tasks)
    return result, tasks
