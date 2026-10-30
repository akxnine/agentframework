"""The orchestrator itself: a Claude Agent SDK agent loop that drives the whole reconciliation
workflow (extract -> compare -> Salesforce notes -> report -> compliance email) by calling the
tools in orchestrator/tools.py in the order fixed by agents.ORCHESTRATOR_SYSTEM_PROMPT."""
import logging
import os

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from .agents import AGENTS, ORCHESTRATOR_SYSTEM_PROMPT
from .config import Settings
from .console import console, step
from .events import emit
from .tools import allowed_tool_names, build_mcp_server

log = logging.getLogger("recon")


def _run_prompt(settings: Settings, dry_run: bool) -> str:
    lines = [
        "Run the reconciliation workflow now.",
        f"docs_folder={settings.docs_folder}",
        f"output_folder={settings.output_folder}",
        f"in_place={settings.in_place}",
    ]
    if dry_run:
        lines.append(
            "DRY RUN: do every stage through building the reports and recording compliance cases, "
            "but do NOT call send_compliance_email."
        )
    return "\n".join(lines)


async def run_pipeline(settings: Settings, dry_run: bool = False) -> str:
    settings.require_anthropic_env()
    os.makedirs(settings.output_folder, exist_ok=True)
    emit("run_start", dry_run=dry_run, folder=settings.docs_folder)

    server = build_mcp_server(settings)
    env = {"ANTHROPIC_API_KEY": settings.anthropic_api_key}
    options = ClaudeAgentOptions(
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        mcp_servers={"recon": server},
        allowed_tools=allowed_tool_names() + ["Task"],
        agents=AGENTS,
        permission_mode="bypassPermissions",
        max_turns=60,
        model=settings.model,
        cli_path=settings.claude_cli_path or None,
        env=env,
    )

    step(0, "Orchestrator run (Claude Agent SDK)")
    summary_lines: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(_run_prompt(settings, dry_run))
        async for message in client.receive_response():
            _handle_message(message, summary_lines)

    result = "\n".join(summary_lines) if summary_lines else "Orchestrator finished with no summary text."
    emit("run_end", status="done", result=result)
    return result


def _handle_message(message, summary_lines: list[str]) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock) and block.text.strip():
                log.info(block.text.strip())
                emit("agent_text", text=block.text.strip())
            elif isinstance(block, ToolUseBlock):
                log.info("[cyan]-> %s[/](%s)", block.name, _short(block.input))
                emit("tool_use", name=block.name, input=block.input)
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                emit("tool_result", is_error=bool(block.is_error), content=_short(block.content))
                if block.is_error:
                    log.warning("[red]tool error:[/] %s", _short(block.content))
    elif isinstance(message, ResultMessage):
        if message.result:
            summary_lines.append(message.result)
        emit("agent_result", subtype=message.subtype, cost_usd=message.total_cost_usd, turns=message.num_turns,
            is_error=message.is_error)
        console.print()
        log.info("[green]Orchestrator finished[/] (%s) in %d turns, est. cost $%s", message.subtype,
                 message.num_turns, f"{message.total_cost_usd:.4f}" if message.total_cost_usd else "n/a")


def _short(obj, limit: int = 300) -> str:
    s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"
