from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from scripts.api_core import analyze_with_path

app = FastAPI(
    title="Dynamic-LangGraph Path API",
    description="输入文件路径 + 问句，返回分析 / 转换结果",
    version="1.0.0",
)

class AnalyzeReq(BaseModel):
    file_path: str = Field(..., description="容器内可访问的文件路径")
    query: str = Field(..., description="要执行的自然语言指令")

@app.post("/path")
def analyze(req: AnalyzeReq):
    try:
        return analyze_with_path(req.file_path, req.query)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
