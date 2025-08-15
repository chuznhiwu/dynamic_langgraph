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


docker run -d --name pipelines \
  -p 9099:9099 \
  --add-host=host.docker.internal:host-gateway \
  -v $PWD/pipelines:/app/pipelines \
  --restart always \
  ghcr.io/open-webui/pipelines:main


docker restart pipelines
docker logs -f pipelines


启动 WebUI：export DATA_DIR=/home/wucz/openwebui-data && open-webui serve --port 8081

启动后端：export OPENWEBUI_DATA_DIR=/home/wucz/openwebui-data && uvicorn api_server:app --host 0.0.0.0 --port 1050
