from __future__ import annotations

import json
import shutil


def claude_argv(prompt: str) -> list[str]:
    return ["claude", "-p", prompt]


def recording_status(claude_path: str | None, *, recorded: bool | None = None) -> dict[str, str]:
    if not claude_path:
        return {
            "status": "FALLBACK",
            "detail": "No harness binary. Researcher and Analyst stay custom code. OpenCrawl carries the board.",
        }
    if recorded is True:
        return {"status": "VERIFIED", "detail": "A harness tool call is visible in OpenCrawl search."}
    if recorded is False:
        return {"status": "MISSING", "detail": "The harness is installed and OpenCrawl search has no recorded tool call."}
    return {
        "status": "UNRUN",
        "detail": "Run claude -p without --bare, then confirm the tool call in OpenCrawl search.",
    }


def allows_start(status: str) -> bool:
    return status in {"VERIFIED", "FALLBACK"}


def main() -> None:
    print(json.dumps(recording_status(shutil.which("claude"))))


if __name__ == "__main__":
    main()
