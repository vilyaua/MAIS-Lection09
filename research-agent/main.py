"""REPL with HITL interrupt/resume loop for the multi-agent research system.

HITL is handled by HumanInTheLoopMiddleware — interrupts arrive as
action_requests and are resumed with decisions.

Requires MCP servers and ACP server to be running first.
Usage: python main.py
"""

import logging
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command, Interrupt

from config import APP_VERSION, Settings
from supervisor import supervisor

Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("logs/supervisor.log", maxBytes=5_000_000, backupCount=3),
    ],
)
logger = logging.getLogger("supervisor")

settings = Settings()


def _print_header():
    print(f"Multi-Agent Research System v{APP_VERSION} (MCP+ACP)")
    print(f"Model: {settings.model_powerful} / {settings.model_fast}")
    print(
        f"SearchMCP: :{settings.search_mcp_port} | ReportMCP: :{settings.report_mcp_port} | ACP: :{settings.acp_port}"
    )
    print("Type 'exit' to quit.\n" + "-" * 60)


def _format_tool_call(msg):
    if not isinstance(msg, AIMessage) or not msg.tool_calls:
        return None
    lines = []
    for tc in msg.tool_calls:
        name = tc.get("name", "?")
        args = tc.get("args", {})
        if name == "save_report":
            arg_str = args.get("filename", "")
        elif "request" in args:
            arg_str = (
                args["request"][:80] + "..."
                if len(args.get("request", "")) > 80
                else args.get("request", "")
            )
        elif "findings" in args:
            arg_str = args["findings"][:80] + "..."
        else:
            arg_str = str(args)[:80]
        lines.append(f"  >> {name}({arg_str})")
    return "\n".join(lines)


def _handle_interrupt(interrupts: list[Interrupt], thread_id: str) -> None:
    """Handle HITL interrupt from HumanInTheLoopMiddleware."""
    for intr in interrupts:
        payload = intr.value
        action_requests = payload.get("action_requests", [])
        review_configs = payload.get("review_configs", [])

        decisions = []
        for i, action in enumerate(action_requests):
            tool_name = action.get("name", "unknown")
            tool_args = action.get("arguments", action.get("args", {}))

            # Show approval prompt
            print("\n" + "=" * 60)
            print("  ACTION REQUIRES APPROVAL")
            print("=" * 60)
            print(f"  Tool:     {tool_name}")
            if tool_name == "save_report":
                print(f"  Filename: {tool_args.get('filename', '?')}")
                content_preview = tool_args.get("content", "")
                if content_preview:
                    print(f"  Preview:\n{content_preview[:500]}")
            else:
                print(f"  Args: {str(tool_args)[:300]}")
            print("=" * 60)

            # Get allowed decisions from review config
            allowed = ["approve", "edit", "reject"]
            if i < len(review_configs):
                allowed = review_configs[i].get("allowed_decisions", allowed)

            while True:
                choice = input(f"\n  {' / '.join(allowed)}: ").strip().lower()
                if choice in allowed:
                    break
                print(f"  Please enter one of: {', '.join(allowed)}")

            if choice == "approve":
                print("  Approved!")
                decisions.append({"type": "approve"})
            elif choice == "edit":
                feedback = input("  Your feedback: ").strip()
                print(f"  Sending feedback to Supervisor: {feedback}")
                decisions.append(
                    {"type": "edit", "edited_action": {**action, "feedback": feedback}}
                )
            else:
                input("  Reason (optional): ").strip()
                decisions.append({"type": "reject"})

        # Resume with decisions
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}
        result = supervisor.invoke(
            Command(resume={"decisions": decisions}),
            config=config,
        )
        _check_and_handle(result, thread_id)


def _check_and_handle(result: dict, thread_id: str):
    interrupts = result.get("__interrupt__", [])
    if interrupts:
        _handle_interrupt(interrupts, thread_id)
    else:
        _print_final_messages(result)


def _print_final_messages(result: dict):
    messages = result.get("messages", [])
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            print(f"\nAgent: {msg.content}")


def _stream_and_handle(thread_id: str, input_data: dict) -> None:
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

    for chunk in supervisor.stream(input_data, config=config, stream_mode="updates"):
        for node_name, node_output in chunk.items():
            if node_name == "__interrupt__":
                _handle_interrupt(node_output, thread_id)
                return

            messages = node_output.get("messages", [])
            for msg in messages:
                tool_info = _format_tool_call(msg) if isinstance(msg, AIMessage) else None
                if tool_info:
                    print(tool_info)

                if isinstance(msg, ToolMessage):
                    content = msg.content
                    if len(content) > 200:
                        content = content[:200] + "..."
                    print(f"  <- {content}")

                if (
                    isinstance(msg, AIMessage)
                    and msg.content
                    and not getattr(msg, "tool_calls", None)
                ):
                    print(f"\nAgent: {msg.content}")


def main():
    _print_header()

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            break

        thread_id = str(uuid.uuid4())
        _stream_and_handle(
            thread_id,
            {"messages": [("user", user_input)]},
        )


if __name__ == "__main__":
    main()
