from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from orchestrator.extraction import extract_statement


@dataclass
class FakeToolUseBlock:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list
    usage: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(input_tokens=120, output_tokens=40))


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class FakeAnthropic:
    def __init__(self, response):
        self.messages = FakeMessages(response)


VALID_INPUT = {
    "statements": [{
        "broker": "Fidelity Investments", "account_number": "Z12-345678", "account_type": "Individual",
        "account_holder": "Margaret Chen", "related_persons": [],
        "trades": [{"symbol": "aapl", "trade_date": "2026-10-05", "side": "BUY", "quantity": 100, "price": 191.5}],
    }],
}


@pytest.mark.asyncio
async def test_extract_statement_parses_forced_tool_call():
    client = FakeAnthropic(FakeResponse(content=[FakeToolUseBlock(name="record_statements", input=VALID_INPUT)]))
    statements, usage = await extract_statement(client, "claude-sonnet-5-5", "document text")
    assert usage == {"input_tokens": 120, "output_tokens": 40}
    assert statements is not None and len(statements) == 1
    assert statements[0].account_number == "Z12-345678"
    assert statements[0].trades[0].symbol == "aapl"  # normalisation happens in _extract/_normalise, not here

    kwargs = client.messages.calls[0]
    assert kwargs["tool_choice"] == {"type": "tool", "name": "record_statements"}
    assert kwargs["temperature"] == 0


@pytest.mark.asyncio
async def test_extract_statement_returns_none_on_malformed_tool_input():
    bad_input = {"statements": [{"broker": "X"}]}  # missing required account_number/trades
    client = FakeAnthropic(FakeResponse(content=[FakeToolUseBlock(name="record_statements", input=bad_input)]))
    statements, usage = await extract_statement(client, "claude-sonnet-5-5", "document text")
    assert statements is None
    assert usage == {"input_tokens": 120, "output_tokens": 40}


@pytest.mark.asyncio
async def test_extract_statement_returns_none_when_no_tool_call():
    client = FakeAnthropic(FakeResponse(content=[]))
    statements, _ = await extract_statement(client, "claude-sonnet-5-5", "document text")
    assert statements is None
