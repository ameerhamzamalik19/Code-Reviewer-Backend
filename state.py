from pydantic import BaseModel
from enum import Enum
from typing import TypedDict, List, Dict, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FileInfo(BaseModel):
    lang: str
    status: str


class PythonReviewState(TypedDict):
    session_id: Optional[str | int]
    pr_url: str
    diff_text: str                # raw diff or just file path for now
    ruff_errors: Dict
    mypy_errors: Dict
    eslint_errors: Dict          # JavaScript/TypeScript
    golangci_errors: Dict        # Go
    checkstyle_errors: Dict      # Java
    bandit_issues: Dict
    human_question: Optional[str]
    human_answer: Optional[str]
    final_comments: List[Dict]    # each dict: {"file": str, "line": int, "body": str}
    changed_files: Dict[str, Dict[str, str]]
    error: Optional[str]
    tools_to_run: List[str]
    current_tool: Optional[str]
    working_dir: Optional[str]
