import json
from typing import TypedDict, List, Dict, Optional, Any
from langgraph.graph import StateGraph, START, END
import subprocess
import requests
import re
from langgraph.checkpoint.memory import MemorySaver
from collections import defaultdict
import os
from pydantic import BaseModel


class FileInfo(BaseModel):
    lang: str
    status: str

class PythonReviewState(TypedDict):
    pr_url: str
    diff_text: str                # raw diff or just file path for now
    py_files: List[str]
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

# Dummy fetch that just sets a test file
def fetch_pr(state: PythonReviewState) -> PythonReviewState:
    pr_url = state["pr_url"]

    if not isinstance(pr_url, str) or not pr_url.strip():
        state["error"] = "Invalid PR URL: empty or not a string."
        state["diff_text"] = ""
        return state

    if "/pull/" not in pr_url:
        state["error"] = f"Invalid GitHub PR URL: '{pr_url}' does not contain '/pull/'."
        state["diff_text"] = ""
        return state

    # Convert https://github.com/owner/repo/pull/123 to diff URL
    # Example: https://patch-diff.githubusercontent.com/raw/owner/repo/pull/123.diff

    try: 
        parts = pr_url.replace("https://github.com/", "").split("/pull/")
        owner_repo = parts[0]
        pr_number = parts[1]
        diff_url = f"https://patch-diff.githubusercontent.com/raw/{owner_repo}/pull/{pr_number}.diff"
        response = requests.get(diff_url, timeout=30)

        if response.status_code != 200:
                state["error"] = f"Failed to fetch diff. HTTP {response.status_code}: {diff_url}"
                state["diff_text"] = ""
                return state
        
        diff_text = response.text
        state["diff_text"] = diff_text
        state["error"] = None 

    except requests.exceptions.RequestException as e:
        state["error"] = f"Network error while fetching diff: {str(e)}"
        state["diff_text"] = ""
        return state
    except Exception as e:
        state["error"] = f"Unexpected error parsing PR URL: {str(e)}"
        state["diff_text"] = ""
        return state

    # Parse diff to find changed .py files
    py_files = []
    changed_files = {}

    for line in diff_text.splitlines():
        if line.startswith("diff --git a/"):
            # Extract file path from b/ part
            file_path = line.split(" b/")[1]
            # py_files.append(file_path)
            ext = os.path.splitext(file_path)[1]
            if ext in EXT_TO_LANG:
                lang = EXT_TO_LANG[ext]
                changed_files[file_path] = {"lang": EXT_TO_LANG[ext], "status": "modified"}

                if lang == "python":
                    py_files.append(file_path)

    # state["py_files"] = py_files
    state["changed_files"] = changed_files
    state["py_files"] = py_files
    print("Changed files: ", state["changed_files"])

    return state

# def fetch_pr(state: PythonReviewState) -> PythonReviewState:
#     pr_url = state.get("pr_url", "")
    
#     # Validate input
#     if not isinstance(pr_url, str) or not pr_url.strip():
#         state["error"] = "Invalid PR URL: empty or not a string."
#         state["diff_text"] = ""
#         return state

#     if "/pull/" not in pr_url:
#         state["error"] = f"Invalid GitHub PR URL: '{pr_url}' does not contain '/pull/'."
#         state["diff_text"] = ""
#         return state

#     try:
#         parts = pr_url.replace("https://github.com/", "").split("/pull/")
#         if len(parts) != 2:
#             state["error"] = f"Malformed PR URL: '{pr_url}'. Expected format: https://github.com/owner/repo/pull/123"
#             state["diff_text"] = ""
#             return state

#         owner_repo = parts[0]
#         pr_number = parts[1].split("/")[0]  # handle trailing slashes or ?...
#         diff_url = f"https://patch-diff.githubusercontent.com/raw/{owner_repo}/pull/{pr_number}.diff"

#         response = requests.get(diff_url, timeout=30)
#         if response.status_code != 200:
#             state["error"] = f"Failed to fetch diff. HTTP {response.status_code}: {diff_url}"
#             state["diff_text"] = ""
#             return state

#         diff_text = response.text
#         state["diff_text"] = diff_text
#         state["error"] = None  # clear any previous error

#     except requests.exceptions.RequestException as e:
#         state["error"] = f"Network error while fetching diff: {str(e)}"
#         state["diff_text"] = ""
#         return state
#     except Exception as e:
#         state["error"] = f"Unexpected error parsing PR URL: {str(e)}"
#         state["diff_text"] = ""
#         return state

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

    if state.get("error"):
        return "end"

    total_errors = 0
    tool_fields = ["ruff_errors", "mypy_errors", "bandit_issues", 
                   "eslint_errors", "golangci_errors", "checkstyle_errors"]
    
    for tool in tool_fields:
        for file, issues in state.get(tool, {}).items():
            total_errors += len(issues)
    if total_errors > 0:
        return "ask_human"   # will be replaced later, but for now go to generate_comments
    else:
        return "end"


# def generate_comments(state: PythonReviewState) -> PythonReviewState:
#     comments = []
    
#     # Process Ruff errors
#     for file, issues in state.get("ruff_errors", {}).items():
#         for issue in issues:
#             # Safely get fields with defaults
#             message = issue.get("message", "Unknown Ruff issue")
#             code = issue.get("code", "?")
#             line = issue.get("line", 0) if isinstance(issue.get("line"), int) else 0
#             comments.append({
#                 "file": file,
#                 "line": line,
#                 "body": f"Ruff: {message} (code {code})"
#             })
    
#     # Process Mypy errors
#     for file, issues in state.get("mypy_errors", {}).items():
#         for issue in issues:
#             message = issue.get("message", "Unknown mypy issue")
#             code = issue.get("code", "?")
#             line = issue.get("line", 0)
#             comments.append({
#                 "file": file,
#                 "line": line,
#                 "body": f"Mypy: {message} [code {code}]"
#             })
    
#     # Process Bandit issues
#     for file, issues in state.get("bandit_issues", {}).items():
#         for issue in issues:
#             message = issue.get("message", "Unknown bandit issue")
#             severity = issue.get("severity", "UNKNOWN")
#             code = issue.get("code", "?")
#             line = issue.get("line", 0)
#             comments.append({
#                 "file": file,
#                 "line": line,
#                 "body": f"Bandit ({severity}): {message} [test {code}]"
#             })
    
#     state["final_comments"] = comments
#     return state

def generate_comments(state: PythonReviewState) -> PythonReviewState:
    comments = []

    if state.get("error"):
        comments.append({
            "file": "SYSTEM",
            "line": 0,
            "body": f"❌ Review aborted due to error: {state['error']}",
            "tool": "system",
            "severity": "error"
        })
        state["final_comments"] = comments
        return state
    
    # ----- Python tools -----
    # Ruff errors
    for file, issues in state.get("ruff_errors", {}).items():
        for issue in issues:
            message = issue.get("message", "Unknown Ruff issue")
            code = issue.get("code", "?")
            line = issue.get("line", 0) if isinstance(issue.get("line"), int) else 0
            comments.append({
                "file": file,
                "line": line,
                "body": f"Ruff: {message} (code {code})",
                "tool": "ruff"
            })
    
    # Mypy errors
    for file, issues in state.get("mypy_errors", {}).items():
        for issue in issues:
            message = issue.get("message", "Unknown mypy issue")
            code = issue.get("code", "?")
            line = issue.get("line", 0)
            comments.append({
                "file": file,
                "line": line,
                "body": f"Mypy: {message} [code {code}]",
                "tool": "mypy"
            })
    
    # Bandit issues
    for file, issues in state.get("bandit_issues", {}).items():
        for issue in issues:
            message = issue.get("message", "Unknown bandit issue")
            severity = issue.get("severity", "UNKNOWN")
            code = issue.get("code", "?")
            line = issue.get("line", 0)
            comments.append({
                "file": file,
                "line": line,
                "body": f"Bandit ({severity}): {message} [test {code}]",
                "tool": "bandit",
                "severity": severity
            })
    
    # ----- JavaScript / TypeScript (ESLint) -----
    for file, issues in state.get("eslint_errors", {}).items():
        for issue in issues:
            message = issue.get("message", "Unknown ESLint issue")
            code = issue.get("code", "?")
            line = issue.get("line", 0)
            severity = issue.get("severity", 2)  # 2 = error, 1 = warning
            severity_label = "error" if severity == 2 else "warning"
            comments.append({
                "file": file,
                "line": line,
                "body": f"ESLint: {message} (rule {code})",
                "tool": "eslint",
                "severity": severity_label
            })
    
    # ----- Go (golangci-lint) -----
    for file, issues in state.get("golangci_errors", {}).items():
        for issue in issues:
            message = issue.get("message", "Unknown golangci-lint issue")
            code = issue.get("code", "?")
            line = issue.get("line", 0)
            severity = issue.get("severity", "")
            comments.append({
                "file": file,
                "line": line,
                "body": f"golangci-lint: {message} [{code}]",
                "tool": "golangci-lint",
                "severity": severity
            })
    
    # ----- Java (Checkstyle) -----
    for file, issues in state.get("checkstyle_errors", {}).items():
        for issue in issues:
            message = issue.get("message", "Unknown Checkstyle issue")
            code = issue.get("code", "?")
            line = issue.get("line", 0)
            severity = issue.get("severity", "")
            comments.append({
                "file": file,
                "line": line,
                "body": f"Checkstyle: {message} (rule {code})",
                "tool": "checkstyle",
                "severity": severity
            })
    
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


from typing import List, Dict, Any
from collections import defaultdict

def format_review_report(comments: List[Dict[str, Any]]) -> str:
    """
    Generate a human-readable report from a list of comment dicts.
    Each comment dict may contain:
    - 'file': str (required)
    - 'line': int (optional, defaults to 0)
    - 'body': str (required)
    - 'tool': str (optional, will be shown if present)
    - 'severity': str (optional)
    Any other fields are safely ignored.
    """
    if not comments:
        return "\n✅ No issues found! Your code looks good.\n"

    # Group by file
    grouped = defaultdict(list)
    for comment in comments:
        if not isinstance(comment, dict):
            continue  # skip malformed entries
        file_path = comment.get("file", "unknown file")
        grouped[file_path].append(comment)

    lines = []
    lines.append("\n" + "=" * 80)
    lines.append("📋 CODE REVIEW REPORT")
    lines.append("=" * 80)

    for file_path, file_comments in grouped.items():
        lines.append(f"\n📁 File: {file_path}")
        lines.append("-" * 60)

        for idx, comment in enumerate(file_comments, 1):
            # Safely get line number
            line_num = comment.get("line", 0)
            line_info = f"Line {line_num}" if line_num and line_num > 0 else "General"

            # Build body (prefer 'body' field, else fallback to message)
            body = comment.get("body", "")
            if not body:
                body = comment.get("message", "No description provided")

            # Optionally include tool name or severity if available
            tool = comment.get("tool", "")
            severity = comment.get("severity", "")
            if tool:
                body = f"[{tool}] {body}"
            if severity and severity.upper() not in body.upper():
                body = f"({severity}) {body}"

            lines.append(f"  {idx}. [{line_info}] {body}")

        lines.append("")  # blank line after each file

    lines.append(f"\n📊 Summary: {len(comments)} issue(s) found.")
    lines.append("=" * 80)
    return "\n".join(lines)

def print_review_report(comments: List[Dict[str, Any]]) -> None:
    """Print the review report to console."""
    return format_review_report(comments)

def run_eslint(state: PythonReviewState) -> PythonReviewState:
    js_files = [f for f, info in state["changed_files"].items() 
                if info["lang"] in ("javascript", "typescript")]
    eslint_errors = {}
    if not js_files:
        state["eslint_errors"] = {}
        return state

    try:
        # Run eslint with JSON output
        result = subprocess.run(
            ["npx", "eslint", "--format=json"] + js_files,
            capture_output=True, text=True, timeout=60
        )
        if result.stdout:
            data = json.loads(result.stdout)
            # ESLint output is an array of file results
            for file_result in data:
                file_path = file_result.get("filePath")
                if not file_path:
                    continue
                messages = []
                for msg in file_result.get("messages", []):
                    messages.append({
                        "line": msg.get("line", 0),
                        "message": msg.get("message", ""),
                        "code": msg.get("ruleId", ""),
                        "severity": msg.get("severity", 2),  # 1=warning,2=error
                        "tool": "eslint"
                    })
                if messages:
                    eslint_errors[file_path] = messages
    except subprocess.TimeoutExpired:
        for f in js_files:
            eslint_errors[f] = [{"message": "ESLint timed out", "tool": "eslint"}]
    except FileNotFoundError:
        for f in js_files:
            eslint_errors[f] = [{"message": "ESLint not found. Run `npm install -g eslint`", "tool": "eslint"}]
    except Exception as e:
        for f in js_files:
            eslint_errors[f] = [{"message": f"ESLint error: {str(e)}", "tool": "eslint"}]
    
    state["eslint_errors"] = eslint_errors
    return state

def run_golangci(state: PythonReviewState) -> PythonReviewState:
    go_files = [f for f, info in state["changed_files"].items() if info["lang"] == "go"]
    golangci_errors = {}
    if not go_files:
        state["golangci_errors"] = {}
        return state

    # Run on the directory containing the files (or each file individually)
    # Simpler: run on each file, but golangci-lint works better on packages.
    # For demonstration, run on each file's directory (deduplicate)
    dirs = set(os.path.dirname(f) for f in go_files)
    try:
        for d in dirs:
            result = subprocess.run(
                ["golangci-lint", "run", "--out-format=json", d],
                capture_output=True, text=True, timeout=60
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for issue in data.get("Issues", []):
                    file_path = issue.get("Pos", {}).get("Filename")
                    if file_path and file_path in state["changed_files"]:
                        if file_path not in golangci_errors:
                            golangci_errors[file_path] = []
                        golangci_errors[file_path].append({
                            "line": issue.get("Pos", {}).get("Line", 0),
                            "message": issue.get("Text", ""),
                            "code": issue.get("FromLinter", ""),
                            "severity": issue.get("Severity", ""),
                            "tool": "golangci-lint"
                        })
    except subprocess.TimeoutExpired:
        for f in go_files:
            golangci_errors[f] = [{"message": "golangci-lint timed out", "tool": "golangci-lint"}]
    except FileNotFoundError:
        for f in go_files:
            golangci_errors[f] = [{"message": "golangci-lint not found. Install from https://golangci-lint.run", "tool": "golangci-lint"}]
    except Exception as e:
        for f in go_files:
            golangci_errors[f] = [{"message": f"golangci-lint error: {str(e)}", "tool": "golangci-lint"}]
    
    state["golangci_errors"] = golangci_errors
    return state

def run_checkstyle(state: PythonReviewState) -> PythonReviewState:
    java_files = [f for f, info in state["changed_files"].items() if info["lang"] == "java"]
    checkstyle_errors = {}
    if not java_files:
        state["checkstyle_errors"] = {}
        return state

    # Checkstyle command: requires a config file. We'll use a simple built-in config.
    # For production, provide a default config path.
    config_path = "checkstyle.xml"  # You should provide this file
    try:
        for java_file in java_files:
            result = subprocess.run(
                ["checkstyle", "-c", config_path, "-f", "json", java_file],
                capture_output=True, text=True, timeout=60
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for file_result in data.get("files", []):
                    file_path = file_result.get("filename")
                    if file_path != java_file:
                        continue
                    errors = []
                    for err in file_result.get("errors", []):
                        errors.append({
                            "line": err.get("line", 0),
                            "message": err.get("message", ""),
                            "code": err.get("source", ""),
                            "severity": err.get("severity", ""),
                            "tool": "checkstyle"
                        })
                    if errors:
                        checkstyle_errors[java_file] = errors
    except subprocess.TimeoutExpired:
        for f in java_files:
            checkstyle_errors[f] = [{"message": "checkstyle timed out", "tool": "checkstyle"}]
    except FileNotFoundError:
        for f in java_files:
            checkstyle_errors[f] = [{"message": "checkstyle not found. Install from https://checkstyle.org", "tool": "checkstyle"}]
    except Exception as e:
        for f in java_files:
            checkstyle_errors[f] = [{"message": f"checkstyle error: {str(e)}", "tool": "checkstyle"}]
    
    state["checkstyle_errors"] = checkstyle_errors
    return state


EXT_TO_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".java": "java",
}

def run(pr_url: str):

    builder = StateGraph(PythonReviewState)

    builder.add_node("fetch_pr", fetch_pr)
    builder.add_node("run_ruff", run_ruff)
    builder.add_node("run_mypy", run_mypy)
    builder.add_node("run_bandit", run_bandit)
    builder.add_node("run_eslint", run_eslint)
    builder.add_node("run_golangci", run_golangci)
    builder.add_node("run_checkstyle", run_checkstyle)
    builder.add_node("generate_comments", generate_comments)

    def after_fetch_pr(state: PythonReviewState) -> str:
        return "generate_comments" if state.get("error") else "continue"
    
    builder.add_conditional_edges(
        "fetch_pr",
        after_fetch_pr,
        {
            "continue": "run_ruff",
            "generate_comments": "generate_comments"
        }
    )

    builder.add_edge(START, "fetch_pr")
    # builder.add_edge("fetch_pr", "run_ruff")
    builder.add_edge("run_ruff", "run_mypy")
    builder.add_edge("run_mypy", "run_bandit")
    builder.add_edge("run_bandit", "run_eslint")
    builder.add_edge("run_eslint", "run_golangci")
    builder.add_edge("run_golangci", "run_checkstyle")

    # builder.add_conditional_edges("run_bandit", decide, {
    #     "ask_human": "generate_comments",
    #     "end": END
    # })


    builder.add_conditional_edges(
        "run_checkstyle",
        decide,
        {
            "ask_human": "generate_comments",  # will go to comments generation
            "end": END
        }
    )

    builder.add_edge("generate_comments", END)

    # Add decide node
    # builder.add_node("decide", decide)

    # Compile with SQLite checkpointer
    # memory = SqliteSaver.from_conn_string("checkpoints.db")
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    config = {"configurable": {"thread_id": "test B307"}}
    # pr_url = "https://github.com/techwithtim/PythonAIAgentFromScratch/pull/7"

    initial_state = {
        "pr_url": pr_url,
        "diff_text": "",
        "py_files": [],
        "ruff_errors": {},
        "mypy_errors": {},
        "bandit_issues": {},
        "eslint_errors": {},       # new
        "golangci_errors": {},     # new
        "checkstyle_errors": {},   # new
        "human_question": None,
        "human_answer": None,
        "final_comments": [],
        "changed_files": {}        # new
    }

    result = graph.invoke(initial_state, config=config)

    # final_comments = print_review_report(result["final_comments"])
    final_comments = result["final_comments"]

    print(final_comments)

    return final_comments