import asyncio
from typing import AsyncGenerator

async def record_and_stream() -> AsyncGenerator[dict, None]:
    yield {"data": 1}
    yield {"data": 2}

async def main():
    async for item in record_and_stream():
        print(item)

asyncio.run(main())
