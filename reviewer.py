import json
from typing import TypedDict, List, Dict, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
import subprocess
import requests
import re
from langgraph.checkpoint.memory import MemorySaver

class PythonReviewState(TypedDict):
    pr_url: str
    diff_text: str                # raw diff or just file path for now
    py_files: List[str]
    ruff_errors: Dict
    mypy_errors: Dict
    bandit_issues: Dict
    human_question: Optional[str]
    human_answer: Optional[str]
    final_comments: List[Dict]    # each dict: {"file": str, "line": int, "body": str}

# Dummy fetch that just sets a test file
def fetch_pr(state: PythonReviewState) -> PythonReviewState:
    pr_url = state["pr_url"]
    # Convert https://github.com/owner/repo/pull/123 to diff URL
    # Example: https://patch-diff.githubusercontent.com/raw/owner/repo/pull/123.diff
    parts = pr_url.replace("https://github.com/", "").split("/pull/")
    owner_repo = parts[0]
    pr_number = parts[1]
    diff_url = f"https://patch-diff.githubusercontent.com/raw/{owner_repo}/pull/{pr_number}.diff"
    response = requests.get(diff_url)
    diff_text = response.text
    state["diff_text"] = diff_text

    # Parse diff to find changed .py files
    py_files = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git a/") and line.endswith(".py"):
            # Extract file path from b/ part
            file_path = line.split(" b/")[1]
            py_files.append(file_path)
    state["py_files"] = py_files
    return state

def run_ruff(state: PythonReviewState) -> PythonReviewState:
    errors = {}
    for py_file in state["py_files"]:
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", py_file],
                capture_output=True, text=True
            )
            if result.stdout:
                data = json.loads(result.stdout)
                # data is a list of dicts with filename, line, message
                errors[py_file] = data
            else:
                errors[py_file] = []
        except Exception as e:
            errors[py_file] = [{"message": f"Ruff failed: {e}"}]
    state["ruff_errors"] = errors
    return state

def decide(state: PythonReviewState) -> str:
    # Check if any tool found errors
    total_errors = 0
    for tool in ["ruff_errors", "mypy_errors", "bandit_issues"]:
        for file, issues in state.get(tool, {}).items():
            total_errors += len(issues)
    if total_errors > 0:
        return "ask_human"   # will be replaced later, but for now go to generate_comments
    else:
        return "end"

def generate_comments(state: PythonReviewState) -> PythonReviewState:
    comments = []
    for file, issues in state["ruff_errors"].items():
        for issue in issues:
            comments.append({
                "file": file,
                "line": issue.get("line", 0),
                "body": f"Ruff: {issue['message']} (code {issue['code']})"
            })
    # Similarly for mypy and bandit
    state["final_comments"] = comments
    return state

def run_mypy(state: PythonReviewState) -> PythonReviewState:
    """
    Runs mypy on all Python files in state["py_files"].
    Stores results in state["mypy_errors"] as dict: {filepath: list_of_error_dicts}
    """
    mypy_errors = {}
    if not state["py_files"]:
        state["mypy_errors"] = {}
        return state

    # Mypy command: show error codes, no color, ignore missing imports (optional)
    cmd = ["mypy", "--show-error-codes", "--no-color", "--ignore-missing-imports"] + state["py_files"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # mypy outputs errors to stderr (and sometimes stdout). We'll combine.
        output = result.stdout + result.stderr

        # Parse output line by line
        # Typical line: "file.py:12: error: Unused variable 'x'  [unused-variable]"
        pattern = re.compile(r'^(.*?):(\d+): error: (.*?)\s+\[(.*?)\]$')
        for line in output.splitlines():
            match = pattern.match(line.strip())
            if match:
                file_path, line_num, message, code = match.groups()
                if file_path not in mypy_errors:
                    mypy_errors[file_path] = []
                mypy_errors[file_path].append({
                    "line": int(line_num),
                    "message": message,
                    "code": code,
                    "tool": "mypy"
                })
    except subprocess.TimeoutExpired:
        # Fallback: add a timeout error entry
        for f in state["py_files"]:
            mypy_errors[f] = [{"message": "mypy timed out after 60s", "tool": "mypy"}]
    except FileNotFoundError:
        # mypy not installed
        for f in state["py_files"]:
            mypy_errors[f] = [{"message": "mypy not found. Install with `pip install mypy`", "tool": "mypy"}]
    except Exception as e:
        for f in state["py_files"]:
            mypy_errors[f] = [{"message": f"Unexpected error: {str(e)}", "tool": "mypy"}]

    state["mypy_errors"] = mypy_errors
    return state


import json

def run_bandit(state: PythonReviewState) -> PythonReviewState:
    """
    Runs bandit on all Python files in state["py_files"].
    Stores results in state["bandit_issues"] as dict: {filepath: list_of_issue_dicts}
    """
    bandit_issues = {}
    if not state["py_files"]:
        state["bandit_issues"] = {}
        return state

    # Bandit command: output JSON, only show HIGH and MEDIUM severity (optional)
    cmd = ["bandit", "-f", "json", "-ll"] + state["py_files"]  # -ll = only HIGH and MEDIUM

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.stdout:
            data = json.loads(result.stdout)
            # Bandit JSON structure: {"results": [{"filename": "...", "line_number": ..., "issue_text": ..., "severity": "...", "confidence": "..."}]}
            for issue in data.get("results", []):
                file_path = issue.get("filename")
                if file_path:
                    if file_path not in bandit_issues:
                        bandit_issues[file_path] = []
                    bandit_issues[file_path].append({
                        "line": issue.get("line_number", 0),
                        "message": issue.get("issue_text", ""),
                        "severity": issue.get("severity", "UNKNOWN"),
                        "confidence": issue.get("confidence", "UNKNOWN"),
                        "code": issue.get("test_id", ""),
                        "tool": "bandit"
                    })
    except subprocess.TimeoutExpired:
        for f in state["py_files"]:
            bandit_issues[f] = [{"message": "bandit timed out after 60s", "tool": "bandit"}]
    except FileNotFoundError:
        for f in state["py_files"]:
            bandit_issues[f] = [{"message": "bandit not found. Install with `pip install bandit`", "tool": "bandit"}]
    except json.JSONDecodeError:
        for f in state["py_files"]:
            bandit_issues[f] = [{"message": "bandit returned invalid JSON", "tool": "bandit"}]
    except Exception as e:
        for f in state["py_files"]:
            bandit_issues[f] = [{"message": f"Unexpected error: {str(e)}", "tool": "bandit"}]

    state["bandit_issues"] = bandit_issues
    return state



builder = StateGraph(PythonReviewState)
builder.add_node("fetch_pr", fetch_pr)
builder.add_node("run_ruff", run_ruff)
builder.add_node("run_mypy", run_mypy)      # you'll implement
builder.add_node("run_bandit", run_bandit)  # you'll implement
builder.add_node("generate_comments", generate_comments)

builder.add_edge(START, "fetch_pr")
builder.add_edge("fetch_pr", "run_ruff")
builder.add_edge("run_ruff", "run_mypy")
builder.add_edge("run_mypy", "run_bandit")
# builder.add_edge("run_bandit", "decide")   # we need to add decide node

# Add conditional edge after decide
# builder.add_conditional_edges("decide", decide, {
#     "ask_human": "generate_comments",  # for now directly to comments
#     "end": END
# })

builder.add_conditional_edges("run_bandit", decide, {
    "ask_human": "generate_comments",
    "end": END
})

builder.add_edge("generate_comments", END)

# Add decide node
# builder.add_node("decide", decide)

# Compile with SQLite checkpointer
# memory = SqliteSaver.from_conn_string("checkpoints.db")
memory = MemorySaver()
graph = builder.compile(checkpointer=memory)

config = {"configurable": {"thread_id": "test1"}}
pr_url = "https://github.com/ed-donner/agents/pull/1227"
initial_state = {"pr_url": pr_url, "diff_text": "", "py_files": [], "ruff_errors": {}, "mypy_errors": {}, "bandit_issues": {}, "human_question": None, "human_answer": None, "final_comments": []}
result = graph.invoke(initial_state, config=config)
print(result["final_comments"])