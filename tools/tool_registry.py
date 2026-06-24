from tools.tools import (
    run_ruff,
    run_mypy,
    run_bandit,
    run_eslint,
    run_golangci,
    run_checkstyle,
)

TOOL_REGISTRY = {
    "ruff": {
        "languages": ["python"],
        "run": run_ruff,
        "parallel_safe": True,
    },
    "mypy": {
        "languages": ["python"],
        "run": run_mypy,
        "parallel_safe": True,
    },
    "bandit": {
        "languages": ["python"],
        "run": run_bandit,
        "parallel_safe": True,
    },
    "eslint": {
        "languages": ["javascript", "typescript"],
        "run": run_eslint,
        "parallel_safe": True,
    },
    "golangci": {
        "languages": ["go"],
        "run": run_golangci,
        "parallel_safe": True,
    },
    "checkstyle": {
        "languages": ["java"],
        "run": run_checkstyle,
        "parallel_safe": True,
    },
}