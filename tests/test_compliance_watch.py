from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from orchestrator.compliance_watch import classify_deterministic, classify_with_claude


@pytest.mark.parametrize("body,decision", [
    ("Approve. Looks fine, go ahead.", "APPROVED"),
    ("approved - nothing unusual here", "APPROVED"),
    ("Reject, this needs escalation.", "REJECTED"),
    ("REJECTED", "REJECTED"),
])
def test_classify_deterministic_keyword_match(body, decision):
    result = classify_deterministic(body)
    assert result is not None
    assert result[0] == decision


@pytest.mark.parametrize("body", [
    "Can you tell me more about why this was flagged?",
    "",
    "Thanks for the report, I'll look at it later.",
])
def test_classify_deterministic_no_match_falls_through(body):
    assert classify_deterministic(body) is None


@dataclass
class FakeToolUseBlock:
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class FakeResponse:
    content: list
    usage: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(input_tokens=10, output_tokens=5))


class FakeMessages:
    def __init__(self, response):
        self._response = response

    async def create(self, **kwargs):
        return self._response


class FakeAnthropic:
    def __init__(self, response):
        self.messages = FakeMessages(response)


async def test_classify_with_claude_parses_decision():
    client = FakeAnthropic(FakeResponse(content=[
        FakeToolUseBlock(name="record_decision", input={"decision": "NEEDS_INFO", "notes": "Asked a question."}),
    ]))
    decision, notes = await classify_with_claude(client, "claude-sonnet-5-5", "What's the context here?")
    assert decision == "NEEDS_INFO"
    assert notes == "Asked a question."


async def test_classify_with_claude_defaults_to_needs_info_when_no_tool_call():
    client = FakeAnthropic(FakeResponse(content=[]))
    decision, notes = await classify_with_claude(client, "claude-sonnet-5-5", "huh?")
    assert decision == "NEEDS_INFO"
    assert notes
