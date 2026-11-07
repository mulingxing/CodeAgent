"""Run the controlled Agent check used in Stage 0."""

import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from codeagent.runner import AgentExecutionError, run_controlled_repair

ROOT = Path(__file__).resolve().parent


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        print("SKIP: 未配置 DEEPSEEK_API_KEY。请在项目根目录的 .env 中填写。")
        return 2
    try:
        result = run_controlled_repair(
            task_id=str(uuid4()),
            repo_id="age-boundary-demo",
            prompt="Fix the age boundary so age 18 is treated as an adult.",
        )
    except AgentExecutionError as exc:
        print(f"FAIL: {exc.code}: {exc.public_message}")
        return 1
    print("PASS: DeepSeek completed the controlled code repair through Claude Agent SDK.")
    print("PASS: The fixed sample passed all tests.")
    print(result.diff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
