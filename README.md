# Dynamic LangGraph Project

This repository demonstrates how to build dynamic LangGraphs planned by an LLM
to chain together statistical analysis, visualization, and CNN‑based fault
diagnosis for vibration‑signal TXT/CSV files.

## Quickstart

```bash
pip install -r requirements.txt
python scripts/main.py data/ball501.txt   -q "请全面分析这个数据"
```

*Put your pre‑trained CNN weights as `simple_cnn.pth` in the project root.*
