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


@pytest.mark.asyncio
async def test_buffered_output_write_final_emits_replace_event_without_mutating_buffer():
    from nanobot_kt.output import BufferedOutput

    output = BufferedOutput()
    queue = asyncio.Queue()
    output.enable_stream(queue)

    await output.write_stream("草稿")
    await output.write_final("最终回复", replace=True, source="bridge")

    first = await asyncio.wait_for(queue.get(), timeout=1)
    second = await asyncio.wait_for(queue.get(), timeout=1)

    assert output.get_response() == "草稿"
    assert first == {"status": "delta", "text": "草稿"}
    assert second == {
        "status": "final",
        "text": "最终回复",
        "replace": True,
        "source": "bridge",
    }


@pytest.mark.asyncio
async def test_buffered_output_drops_progress_when_stream_queue_is_full():
    from nanobot_kt.output import BufferedOutput

    output = BufferedOutput()
    queue = asyncio.Queue(maxsize=1)
    output.enable_stream(queue)
    await queue.put({"status": "delta", "text": "占位"})

    output.on_activity("tool_start", "[memory_read] query=hi")
    await asyncio.sleep(0)

    assert queue.qsize() == 1
    assert await asyncio.wait_for(queue.get(), timeout=1) == {"status": "delta", "text": "占位"}
    await asyncio.sleep(0)
    assert queue.empty()


@pytest.mark.asyncio
async def test_buffered_output_keeps_error_when_stream_queue_is_full():
    from nanobot_kt.output import BufferedOutput

    output = BufferedOutput()
    queue = asyncio.Queue(maxsize=1)
    output.enable_stream(queue)
    await queue.put({"status": "delta", "text": "占位"})

    output.on_activity("processing_error", "boom")
    await asyncio.sleep(0)

    assert await asyncio.wait_for(queue.get(), timeout=1) == {"status": "delta", "text": "占位"}
    assert await asyncio.wait_for(queue.get(), timeout=1) == {"status": "error", "message": "boom"}
