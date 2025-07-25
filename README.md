# Dynamic LangGraph Project

This repository demonstrates how to build dynamic LangGraphs planned by an LLM
to chain together statistical analysis, visualization, and CNN‑based fault
diagnosis for vibration‑signal TXT/CSV files.

## Quickstart

```bash
pip install -r requirements.txt
python scripts/main.py data/ball501.txt   -q "请全面分析这个数据" # 请统计这个数据的特征值
python scripts/main.py data/docx_test.docx -q "请把该文档转 Markdown 并总结内容"
python scripts/main.py data/test.pdf -q "请把该文档转 Markdown 并总结内容"
python scripts/main.py data/Airplane.mp3 -q "请把这段音频转文字并总结要点"
```

*Put your pre‑trained CNN weights as `simple_cnn.pth` in the project root.*


## 在docker中作为服务启动
安装依赖
pip install --upgrade pip
pip install "fastapi[all]" "uvicorn[standard]"

启动服务
uvicorn api_server:app --host 0.0.0.0 --port 1050

在宿主机访问
curl -X POST http://127.0.0.1:1050/analyze-path\
  -H "Content-Type: application/json" \
  -d '{"file_path":"/data/user/wucz/dynamic_langgraph_project_v1.3/data/Airplane.mp3",
       "query":"请把这段音频转文字并总结要点"}' | jq .



curl -X POST http://127.0.0.1:1050/analyze-path\
  -H "Content-Type: application/json" \
  -d '{"file_path":"/data/user/wucz/dynamic_langgraph_project_v1.3/data/ball501.txt",
       "query":"请全面分析这个数据"}' | jq .

