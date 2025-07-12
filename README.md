# Dynamic LangGraph Project

This repository demonstrates how to build dynamic LangGraphs planned by an LLM
to chain together statistical analysis, visualization, and CNN‑based fault
diagnosis for vibration‑signal TXT/CSV files.

## Quickstart

```bash
pip install -r requirements.txt
python scripts/main.py data/ball501.txt   -q "请全面分析这个数据"
python scripts/main.py data/docx_test.docx -q "请把该文档转 Markdown 并总结内容"
python scripts/main.py data/test.pdf -q "请把该文档转 Markdown 并总结内容"
python scripts/main.py data/Airplane.mp3 -q "请把这段音频转文字并总结要点"
```
# 请统计这个数据的特征值
# 请全面分析这个数据
*Put your pre‑trained CNN weights as `simple_cnn.pth` in the project root.*
