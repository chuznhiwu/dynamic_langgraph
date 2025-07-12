"""Instantiate shared LLM objects and their tool bindings."""
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from .tools.stats_tools import calc_mean, calc_std, calc_var
from .tools.viz_tools import time_plot, freq_plot
from .tools.diagnosis_tools import diagnose_signal
from .tools.task_selector import choose_tasks

llm_main = ChatOllama(model="qwen3:32b", temperature=0.1)
llm_multimod = ChatOllama(model="qwen2.5vl:32b", temperature=0.1)
llm_analysis  = llm_main.bind_tools([calc_mean, calc_var, calc_std])
llm_viz       = llm_main.bind_tools([time_plot, freq_plot])
llm_diagnosis = llm_main.bind_tools([diagnose_signal])
llm_planner   = llm_main