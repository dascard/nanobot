import asyncio

import pytest


@pytest.mark.asyncio
async def test_buffered_output_write_stream_emits_delta_event():
    from nanobot_kt.output import BufferedOutput

    output = BufferedOutput()
    queue = asyncio.Queue()
    output.enable_stream(queue)

    await output.write_stream("你")
    await output.write_stream("好")

    first = await asyncio.wait_for(queue.get(), timeout=1)
    second = await asyncio.wait_for(queue.get(), timeout=1)

    assert output.get_response() == "你好"
    assert first == {"status": "delta", "text": "你"}
    assert second == {"status": "delta", "text": "好"}
