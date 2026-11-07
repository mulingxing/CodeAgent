"""Start the SDK subprocess without sending a model request or spending API credit."""

import asyncio
import os
import tempfile
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

ROOT = Path(__file__).resolve().parents[1]


async def check() -> None:
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_ANTHROPIC_AWS"):
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="preflight-", dir=runtime) as temp:
        options = ClaudeAgentOptions(
            cwd=temp,
            tools=[],
            strict_mcp_config=True,
            setting_sources=[],
            env={
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:1",
                "ANTHROPIC_AUTH_TOKEN": "local-placeholder",
                "ANTHROPIC_API_KEY": "",
                "CLAUDE_CONFIG_DIR": str(Path(temp) / "config"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            stderr=lambda _line: None,
        )
        async with ClaudeSDKClient(options=options):
            print("PASS: SDK subprocess initialized without a model request.")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(check(), timeout=25))
    except Exception as exc:
        print(f"FAIL: SDK subprocess initialization ({type(exc).__name__}).")
        raise SystemExit(1)
