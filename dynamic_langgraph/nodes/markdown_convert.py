"""
LangGraph 节点：文档 → Markdown（可选 OCR）
"""
from dynamic_langgraph.data_state import DataState
from dynamic_langgraph.tools import convert_to_markdown_with_ocr


def markdown_convert(state: DataState) -> DataState:
    """
    1. 调用 convert_to_markdown_with_ocr 把文件转 Markdown
    2. 成功时写入 markdown_path / ocr_snippets / has_ocr
    3. 若转换失败，捕获 RuntimeError -> 写入 error_msg
    """
    if not state.path:
        raise ValueError("DataState.path 为空，无法执行 Markdown 转换")

    try:
        md_path, snippets = convert_to_markdown_with_ocr(state.path)
        state.markdown_path = md_path
        state.ocr_snippets = snippets
        state.has_ocr = bool(snippets)
    except RuntimeError as err:
        # 记录错误信息，后续 summarizer 可提示用户
        state.error_msg = f"【Markdown 转换失败】{err}"
        # 其余字段保持 None / False，节点提前结束
    return state
