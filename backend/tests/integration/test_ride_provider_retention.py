"""Long-running no-key diagnostics are bounded without losing lifetime counts."""

import pytest
from ride_agent.integrations.model_provider import LocalModel


@pytest.mark.asyncio
async def test_demo_call_history_is_bounded_and_the_total_continues_increasing():
    model = LocalModel()
    for _ in range(600):
        await model.messages.create(messages=[dict(role="user", content='{"task":"intent","message":"Hello"}')])
    assert model.total_calls == 600 and len(model.calls) == 512
    assert all((set(record) <= {"id", "phase", "agent", "started", "finished"} for record in model.calls))
    before = model.total_calls
    await model.messages.create(messages=[dict(role="user", content='{"task":"intent","message":"Hello"}')])
    assert model.total_calls == before + 1 and len(model.calls) == 512
