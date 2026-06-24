import ast
import json
import logging
import os
import uuid
from typing import Dict, List, Optional, Any, Tuple

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Send
from sqlalchemy.orm import Session

from tools.tools import fetch_pr, generate_comments, decide
from state import PythonReviewState
from agent.planner import planner
from agent.runner import run_tool
from database.models import User

# Import your LLM pipeline
from agent.chat_agent import llm_reviewer

# Set up logging
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Helper: extract code snippet around a given line
# ----------------------------------------------------------------------
def extract_snippet(file_path: str, line_num: int, context_lines: int = 10) -> Tuple[int, int, str]:
    """
    Extract a code snippet from a file around a specific line number.
    Returns (start_line, end_line, snippet_text).
    Falls back to a safe error message if the file cannot be read.
    """
    if not os.path.exists(file_path):
        logger.warning(f"File not found: {file_path}")
        return (line_num, line_num, f"# File not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return (line_num, line_num, f"# Error reading file: {e}")

    total_lines = len(lines)
    if line_num < 1 or line_num > total_lines:
        return (line_num, line_num, f"# Line {line_num} out of range (file has {total_lines} lines)")

    start = max(0, line_num - context_lines - 1)
    end = min(total_lines, line_num + context_lines)
    snippet = "".join(lines[start:end])
    return (start + 1, end, snippet)


def extract_snippets_for_issues(issues_by_file: Dict[str, List[Dict[str, Any]]],
                                working_dir: str,
                                context_lines: int = 10) -> Dict[str, str]:
    """
    For each file, collect snippets for all issues and return a dict
    file_path -> combined snippet text (grouped by file).
    """
    grouped_snippets = {}
    for file_path, issue_list in issues_by_file.items():
        # Resolve absolute path (if working_dir is given)
        full_path = os.path.join(working_dir, file_path) if working_dir else file_path
        # Collect unique line numbers (to avoid duplicate snippets)
        unique_lines = sorted({issue.get("line", 0) for issue in issue_list if issue.get("line")})
        if not unique_lines:
            # No line info – skip this file
            continue

        # Build a combined snippet block for this file
        snippets = []
        for line in unique_lines:
            start, end, code = extract_snippet(full_path, line, context_lines)
            snippet_header = f"Lines {start}–{end} (issue at line {line}):"
            snippet_body = code.rstrip()
            snippets.append(f"{snippet_header}\n```python\n{snippet_body}\n```\n")

        grouped_snippets[file_path] = "\n".join(snippets)

    return grouped_snippets


# ----------------------------------------------------------------------
# Graph construction (unchanged)
# ----------------------------------------------------------------------
def route_to_tools(state: PythonReviewState) -> list[Send] | str:
    """Route to parallel tools or skip to aggregator."""
    tools = state.get("tools_to_run", [])
    if not tools:
        logger.info("No tools to run, going to aggregator")
        return "aggregator"
    logger.info(f"Routing to {len(tools)} tools in parallel")
    return [Send("run_tool", {**state, "current_tool": tool}) for tool in tools]


def generate_graph():
    builder = StateGraph(PythonReviewState)

    builder.add_node("fetch_pr", fetch_pr)
    builder.add_node("planner", planner)
    builder.add_node("run_tool", run_tool)
    builder.add_node("aggregator", lambda state: state)
    builder.add_node("generate_comments", generate_comments)

    builder.add_edge(START, "fetch_pr")
    builder.add_edge("fetch_pr", "planner")

    builder.add_conditional_edges(
        "planner",
        route_to_tools,
        {
            "run_tool": "run_tool",
            "aggregator": "aggregator"
        }
    )

    builder.add_edge("run_tool", "aggregator")

    def should_generate_comments(state: PythonReviewState) -> str:
        """Determine if we should generate comments or end."""
        if state.get("error"):
            return "generate_comments"

        tool_fields = [
            "ruff_errors", "mypy_errors", "bandit_issues",
            "eslint_errors", "golangci_errors", "checkstyle_errors"
        ]
        total_issues = 0
        for field in tool_fields:
            for file, issues in state.get(field, {}).items():
                total_issues += len(issues) if isinstance(issues, list) else 0

        return "generate_comments" if total_issues > 0 else "end"

    builder.add_conditional_edges(
        "aggregator",
        should_generate_comments,
        {
            "generate_comments": "generate_comments",
            "end": END
        }
    )

    builder.add_edge("generate_comments", END)

    return builder


# ----------------------------------------------------------------------
# Main run function (with LLM post‑processing)
# ----------------------------------------------------------------------
def run(db: Session, pr_url: str, user: User) -> List[Dict[str, Any]]:
    """
    Execute the entire code‑review graph, then enhance the results
    with an LLM that receives the tool reports and relevant code snippets.
    """
    # --- 1. Build and run the graph ---
    builder = generate_graph()
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    config = {
        "configurable": {
            "thread_id": user.user_id,
            "db": db,
            "user": user
        }
    }

    initial_state = {
        "session_id": str(uuid.uuid4()),
        "pr_url": pr_url,
        "diff_text": "",
        "ruff_errors": {},
        "mypy_errors": {},
        "bandit_issues": {},
        "eslint_errors": {},
        "golangci_errors": {},
        "checkstyle_errors": {},
        "human_question": None,
        "human_answer": None,
        "final_comments": [],
        "changed_files": {},
        "error": None,
        "current_tool": None,
        "tools_to_run": [],
        "working_dir": None,
    }

    try:
        result = graph.invoke(initial_state, config=config)
        logger.info("Graph execution completed successfully.")
    except Exception as e:
        logger.exception("Graph execution failed")
        return [{
            "file": "SYSTEM",
            "line": 0,
            "body": f"❌ Graph execution failed: {str(e)}",
            "tool": "system",
            "severity": "error"
        }]

    # --- 2. Extract the tool reports ---
    final_comments = result.get("final_comments", [])
    working_dir = result.get("working_dir")
    if not working_dir:
        logger.warning("No working_dir found in state; snippets may not be resolved.")

    # Combine all issues into a single list with file path and line
    all_issues = []
    tool_fields = [
        ("ruff_errors", "Ruff"),
        ("mypy_errors", "MyPy"),
        ("bandit_issues", "Bandit"),
        ("eslint_errors", "ESLint"),
        ("golangci_errors", "GolangCI"),
        ("checkstyle_errors", "Checkstyle"),
    ]

    for field, tool_name in tool_fields:
        errors = result.get(field, {})
        if not errors:
            continue
        for file_path, issues in errors.items():
            if isinstance(issues, list):
                for issue in issues:
                    # Normalise the issue dict
                    if isinstance(issue, dict):
                        line = issue.get("line", 0)
                        message = issue.get("message", str(issue))
                        code = issue.get("code", "")
                    else:
                        line = 0
                        message = str(issue)
                        code = ""
                    all_issues.append({
                        "file": file_path,
                        "line": line,
                        "tool": tool_name,
                        "code": code,
                        "message": message,
                        "raw": issue
                    })
            elif isinstance(issues, dict):
                # Some tools may store {line: [messages]}
                for line, msgs in issues.items():
                    for msg in msgs:
                        all_issues.append({
                            "file": file_path,
                            "line": int(line),
                            "tool": tool_name,
                            "code": "",
                            "message": str(msg),
                            "raw": msg
                        })

    if not all_issues:
        logger.info("No tool issues found; returning graph comments only.")
        return final_comments

    # --- 3. Group issues by file and extract snippets ---
    issues_by_file: Dict[str, List[Dict]] = {}
    for issue in all_issues:
        file_path = issue["file"]
        issues_by_file.setdefault(file_path, []).append(issue)

    snippets_by_file = extract_snippets_for_issues(
        issues_by_file=issues_by_file,
        working_dir=working_dir,
        context_lines=10
    )

    # --- 4. Build a full report for the LLM ---
    report_lines = []
    for file_path, issue_list in issues_by_file.items():
        report_lines.append(f"### {file_path}")
        for issue in issue_list:
            line = issue.get("line", 0)
            tool = issue.get("tool", "unknown")
            code = issue.get("code", "")
            msg = issue.get("message", "")
            report_lines.append(f"  Line {line} [{tool}] {code}: {msg}")
        report_lines.append("")  # blank line

    full_report = "\n".join(report_lines)

    # --- 5. Call the LLM ---
    try:
        llm_response = llm_reviewer(
            report_text=full_report,
            changed_files=list(result.get("changed_files", {}).keys()),
            code_snippets=snippets_by_file
        )
    except Exception as e:
        logger.exception("LLM call failed; continuing without LLM enhancements.")
        return final_comments

    # --- 6. Process LLM response – store the entire response as a single comment ---
    if llm_response.get("overall_quality") == "Error":
        logger.warning("LLM returned an error; skipping its suggestions.")
        return final_comments

    # Append one comment containing the full LLM response (pretty‑printed JSON)
    llm_comment = {
        "file": "SYSTEM",
        "line": 0,
        "body": json.dumps(llm_response, indent=2),
        "tool": "LLM",
        "severity": "info"
    }
    final_comments.append(llm_comment)

    logger.info("LLM response stored as a single comment.")
    return final_comments