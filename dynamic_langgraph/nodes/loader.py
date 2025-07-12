import logging
from pathlib import Path
from ..data_state import DataState
from dynamic_langgraph.utils import debug_ai_message

def loader(state: DataState):
    if not Path(state.path).exists():
        raise FileNotFoundError(state.path)
    logging.info(f" File confirmed: {state.path}")
    return {}
