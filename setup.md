## 拉取docker
docker pull docker.1ms.run/pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

## 启动容器
docker run --gpus all -d -v /data/user:/data/user --network host --name wucz -it docker.1ms.run/pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

## 给挂载文件夹权限
chmod -R 777 /data/user

## 进入容器
docker exec -it wucz /bin/bash

## 删除容器（注意需要时操作）
docker stop wucz
docker rm wucz

## 安装必要工具（如 curl 和 bzip2）
apt-get update && apt-get install -y \
apt-get install curl && bzip2  
apt-get install git


## 如果需要重新安装虚拟环境，按照如下进行，若直接安装，跳过###内容
### 下载并安装 Miniconda
curl -o /tmp/miniconda.sh -L https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
    && bash /tmp/miniconda.sh -b -f -p /opt/conda \
    && rm /tmp/miniconda.sh

### 设置环境变量
PATH="/opt/conda/bin:$PATH"

### 新建conda环境并启动
conda create -n openweb python=3.11
conda activate openweb

### 安装pytorch
pip install --upgrade pip
pip install torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0 -i https://pypi.tuna.tsinghua.edu.cn/simple

## 安装vim
apt-get update
apt-get install -y vim
rm -rf /var/lib/apt/lists/*

## 设置pip源
mkdir -p ~/.pip
echo "[global]" > ~/.pip/pip.conf
echo "index-url = https://mirrors.aliyun.com/pypi/simple/" >> ~/.pip/pip.conf
or
echo "[global]" > ~/.pip/pip.conf
echo "index-url = https://pypi.tuna.tsinghua.edu.cn/simple" >> ~/.pip/pip.conf

## 安装whisper
pip install -U openai-whisper
apt update 
apt install ffmpeg
pip install setuptools-rust
pip install faster_whisper

## 安装依赖包
datasets fastapi transformers matplotlib 
langchain langchain-core langchain-ollama langchain-openai langchain-text-splitters
langgraph langgraph-checkpoint langgraph-prebuilt langgraph-sdk
pypandoc python-docx pdfminer.six pytesseract pillow pdf2image
apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim
apt-get install pandoc

## 安装 Poppler
apt install poppler-utils
export PATH="$PATH:/path/to/poppler/bin"

## 设置hf mirror
pip install -U huggingface_hub
echo "export HF_ENDPOINT=https://hf-mirror.com" >> ~/.bashrc
source ~/.bashrc

## 修复cudnn加速问题
pip uninstall nvidia-cudnn-cu12 -y
conda install -c nvidia cudnn=9.3.0.75
echo 'export LD_LIBRARY_PATH=/opt/conda/lib:$LD_LIBRARY_PATH' >> ~/.bashrc
source ~/.bashrc
ls /opt/conda/lib | grep cudnn


## 实时推送
pip install sse-starlette
pip install sseclient-py
