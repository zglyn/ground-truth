from __future__ import annotations

import json
import shutil


def claude_argv(prompt: str) -> list[str]:
    return ["claude", "-p", prompt]


def recording_status(claude_path: str | None) -> dict[str, str]:
    if not claude_path:
        return {"status": "INCONCLUSIVE", "detail": "claude is not installed"}
    return {
        "status": "UNRUN",
        "detail": "Run claude -p without --bare, then confirm the tool call in OpenCrawl fetch.",
    }


def main() -> None:
    print(json.dumps(recording_status(shutil.which("claude"))))


if __name__ == "__main__":
    main()
