# Dynamic LangGraph Project

This repository demonstrates how to build dynamic LangGraphs planned by an LLM
to chain together statistical analysis, visualization, and CNN‑based fault
diagnosis for vibration‑signal TXT/CSV files.

## Quickstart

1.按照setup.md 拉取docker,安装必要的包，完成Langgraph后端的环境部署

启动后端：uvicorn api_server:app --host 0.0.0.0 --port 1050

2.参考https://github.com/minio/minio部署minio存放文件和中间过程

3.参考https://github.com/open-webui/open-webui 用docker方式部署openwebui（前端）

4.用docker方式部署openwebui的pipline功能(请自行查看相应教程)
docker run -d --name pipelines \
  -p 9099:9099 \
  --add-host=host.docker.internal:host-gateway \
  -v $PWD/pipelines:/app/pipelines \
  --restart always \
  ghcr.io/open-webui/pipelines:main

docker logs -f pipelines #看piplines的输出



