import asyncio
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

class State(TypedDict):
    messages: list

async def async_llm(prompt: str):
    """模拟异步 LLM 逐 token 返回"""
    for tok in ["Hello", " ", "world", "!"]:
        await asyncio.sleep(0.1)
        yield tok

def streaming_node(state: State):
    """同步生成器节点"""
    prompt = state["messages"][-1]
    loop = asyncio.get_event_loop()

    async_gen = async_llm(prompt)           # 拿到异步生成器
    gen = loop.run_until_complete(
        loop.create_task(async_gen.__aiter__().__anext__())
    )  # 简单示例，实际请用 async for + queue

    # 下面仅做演示：一次性返回，避免阻塞
    # 真实场景请用同步队列或把异步逻辑拆出去
    yield {"messages": ["Hello world!"]}

# 建图
builder = StateGraph(State)
builder.add_node("stream", streaming_node)
builder.set_entry_point("stream")
graph = builder.compile()

# 运行
for chunk in graph.stream({"messages": ["hi"]}, stream_mode="updates"):
    print(chunk)