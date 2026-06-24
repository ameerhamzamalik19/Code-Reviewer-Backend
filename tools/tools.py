import json
import subprocess
import requests
import re
import os
from allowed_files import EXT_TO_LANG
from state import PythonReviewState
from tools.helper_functions import(
    get_files_by_language
)
import shutil
import tempfile
from datetime import datetime
from dotenv import load_dotenv
import urllib.request
import urllib.error
from database.crud import create_review_session, update_ReviewSession
from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session
from database.models import User
from pathlib import Path

load_dotenv()

def log_clone_directory(temp_dir: str, pr_url: str) -> None:
    """
    Appends the clone directory path to repository_clones.txt.
    Maintains all historical clone paths.
    """
    log_file = "repository_clones.txt"
    
    try:
        # Get current timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare log entry
        log_entry = f"{timestamp} | {pr_url} | {temp_dir}\n"
        
        # Append to file (creates file if it doesn't exist)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)
        
        print(f"📝 Logged clone: {temp_dir}")
        
    except Exception as e:
        print(f"⚠️ Failed to log clone directory: {e}")

def get_github_repo_size(owner: str, repo: str) -> int:
    """
    Fetches repository size from GitHub API.
    Returns size in KB (as provided by GitHub), or -1 on failure.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github.v3+json"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != 200:
                return -1
            data = json.loads(response.read().decode("utf-8"))
            size = data.get("size")
            if size is None:
                return -1
            return int(size)
    except (urllib.error.URLError, urllib.error.HTTPError,
            json.JSONDecodeError, KeyError, ValueError):
        return -1

# fetch github repository and clone it
def fetch_pr(state: PythonReviewState, config: RunnableConfig) -> PythonReviewState:
    """
    Fetches a PR by cloning the repository into a persistent directory based on session_id.
    The repository is stored permanently under /data/repositories/<session_id>.
    Metadata (owner, repo name, PR number, relative repo path, absolute path) is added to state.
    Repositories are never deleted and existing sessions are never overwritten.
    """
    pr_url = state.get("pr_url", "").strip()
    session_id = state.get("session_id")
    db: Session = config["configurable"]["db"]
    user: User = config["configurable"]["user"]

    # Validate PR URL
    if not pr_url:
        state["error"] = "Invalid PR URL: empty or not a string."
        return state

    if "/pull/" not in pr_url:
        state["error"] = f"Invalid GitHub PR URL: '{pr_url}' does not contain '/pull/'."
        return state

    # Validate session_id – must be an integer for safe filesystem usage

    # if not isinstance(session_id, int):
    #     state["error"] = "Invalid session_id: must be an integer."
    #     return state

    # Parse PR URL to extract repo_owner, repo_name, pr_number
    try:
        url = pr_url.rstrip("/")
        path = url.replace("https://github.com/", "")
        parts = path.split("/pull/")
        if len(parts) != 2:
            raise ValueError("Invalid GitHub PR URL format.")
        owner_repo = parts[0]
        pr_number_str = parts[1]

        owner_repo_parts = owner_repo.split("/")
        if len(owner_repo_parts) != 2:
            raise ValueError("Invalid owner/repo format.")
        repo_owner, repo_name = owner_repo_parts
        pr_number = int(pr_number_str)

    except Exception as e:
        state["error"] = f"Failed to parse PR URL: {str(e)}"
        return state

    # ----------------------------
    # Repository size validation
    # ----------------------------
    MAX_REPO_SIZE_MB = int(os.getenv("MAX_REPO_SIZE_MB", 300))
    MAX_REPO_SIZE_KB = MAX_REPO_SIZE_MB * 1024

    repo_size_kb = get_github_repo_size(repo_owner, repo_name)
    if repo_size_kb != -1 and repo_size_kb > MAX_REPO_SIZE_KB:
        state["error"] = (
            f"Repository too large: {repo_size_kb / 1024:.2f} MB. "
            f"Max allowed: {MAX_REPO_SIZE_MB} MB."
        )
        return state

    # Persistent storage setup
    REPO_STORAGE = Path(os.getenv("REPO_STORAGE", "/data/repositories"))

    repo_type = "pr" # PULL REQUEST

    try:
        session_id = create_review_session(db, user, repo_type, pr_url)
    except Exception as e:
        state["error"] = f"Failed to create a review session: {str(e)}"
        return state

    repo_dir = os.path.join(REPO_STORAGE, str(session_id))
    # Ensure the storage root exists
    try:
        os.makedirs(REPO_STORAGE, exist_ok=True)
    except Exception as e:
        state["error"] = f"Failed to create storage directory: {str(e)}"
        return state

    # Do NOT overwrite an existing repository – preserve previous reviews
    if os.path.exists(repo_dir):
        state["error"] = f"Repository directory already exists: {repo_dir}"
        return state

    # Create the repository directory before cloning (git clone . will work)
    try:
        os.makedirs(repo_dir, exist_ok=False)  # ensure we don't create if already exists (but we already checked)
    except Exception as e:
        state["error"] = f"Failed to create repository directory: {str(e)}"
        return state

    # Populate state with repository metadata for later persistence
    state["temp_dir"] = repo_dir
    state["working_dir"] = repo_dir
    state["repo_owner"] = repo_owner
    state["repo_name"] = repo_name
    state["repo_full_name"] = f"{repo_owner}/{repo_name}"
    state["pr_number"] = pr_number
    state["repo_path"] = str(session_id)      # relative path stored in DB
    state["repo_dir"] = repo_dir              # absolute path for direct access

    # Log the chosen directory (if log_clone_directory is defined)
    log_clone_directory(repo_dir, pr_url)  # assuming it exists

    try:
        # Clone the repository (shallow clone) directly into the session directory
        repo_url = f"https://github.com/{repo_owner}/{repo_name}.git"
        print(f"📦 Cloning {repo_url} into {repo_dir}...")

        # Use cwd=repo_dir and clone into '.' (repo_dir must exist)
        clone_result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, "."],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=repo_dir
        )

        if clone_result.returncode != 0:
            state["error"] = f"Failed to clone repository: {clone_result.stderr}"
            # Repository is left in place; no deletion
            return state

        # Fetch the PR branch – use cwd=repo_dir
        print(f"🔍 Fetching PR #{pr_number}...")
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=repo_dir
        )

        if fetch_result.returncode != 0:
            state["error"] = f"Failed to fetch PR: {fetch_result.stderr}"
            return state

        # Checkout the PR branch
        checkout_result = subprocess.run(
            ["git", "checkout", f"pr-{pr_number}"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=repo_dir
        )

        if checkout_result.returncode != 0:
            state["error"] = f"Failed to checkout PR: {checkout_result.stderr}"
            return state

        # Get the list of changed files in this PR
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_dir
        )

        if diff_result.returncode != 0:
            # Fallback: compare with main branch
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", "origin/main", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=repo_dir
            )

        # Parse changed files
        changed_files = {}
        diff_text = ""

        # Also get the full diff for context
        full_diff_result = subprocess.run(
            ["git", "diff", "HEAD~1", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_dir
        )

        if full_diff_result.returncode == 0:
            diff_text = full_diff_result.stdout

        print("=== DIFF OUTPUT ===")
        print(diff_result.stdout)

        # Process changed files
        for file_path in diff_result.stdout.splitlines():
            if not file_path.strip():
                continue

            ext = os.path.splitext(file_path)[1]
            if ext in EXT_TO_LANG:
                lang = EXT_TO_LANG[ext]
                changed_files[file_path] = {
                    "lang": lang,
                    "status": "modified"
                }

        # Update state with diff results
        state["changed_files"] = changed_files
        state["diff_text"] = diff_text
        state["error"] = None
        column_name = "repo_path"
        print("This is my repo path: ", repo_dir)

        try:
            update_ReviewSession(session_id, column_name, repo_dir, db)
        except Exception as e:
            state["error"] = f"Repository cloning correct path wasn't updated. {str(e)}"

        print(f"✅ PR #{pr_number} fetched successfully. {len(changed_files)} files changed.")
        print(f"📁 Working directory: {repo_dir}")

    except subprocess.TimeoutExpired as e:
        state["error"] = f"Timeout while fetching PR: {str(e)}"
        # Repository is left in place; no deletion
    except Exception as e:
        state["error"] = f"Unexpected error fetching PR: {str(e)}"
        # Repository is left in place; no deletion

    return state

def run_ruff(state: PythonReviewState) -> PythonReviewState:
    errors = {}
    py_files = get_files_by_language(state, "python")

    print("RUN_RUFF_FILES: ", py_files)

    if not py_files:
        state["ruff_errors"] = {}
        return state
    
    working_dir = state["working_dir"]
    
    for py_file in py_files:
        full_path = os.path.join(working_dir, py_file)
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", full_path],
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
    # state["ruff_errors"] = errors
    return {
        "ruff_errors": errors
    }

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
    
    py_files = get_files_by_language(state, "python")

    if not py_files:
        state["mypy_errors"] = {}
        return state
    
    working_dir = state["working_dir"]

    full_paths = [
        os.path.join(working_dir, f)
        for f in py_files
    ]

    # Mypy command: show error codes, no color, ignore missing imports (optional)
    cmd = ["mypy", "--show-error-codes", "--no-color", "--ignore-missing-imports"] + full_paths

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
        for f in py_files:
            mypy_errors[f] = [{"message": "mypy timed out after 60s", "tool": "mypy"}]
    except FileNotFoundError:
        # mypy not installed
        for f in py_files:
            mypy_errors[f] = [{"message": "mypy not found. Install with `pip install mypy`", "tool": "mypy"}]
    except Exception as e:
        for f in py_files:
            mypy_errors[f] = [{"message": f"Unexpected error: {str(e)}", "tool": "mypy"}]

    state["mypy_errors"] = mypy_errors
    return {
        "mypy_errors": mypy_errors
    }


import json

def run_bandit(state: PythonReviewState) -> PythonReviewState:
    """
    Runs bandit on all Python files in state["py_files"].
    Stores results in state["bandit_issues"] as dict: {filepath: list_of_issue_dicts}
    """
    bandit_issues = {}
    py_files = get_files_by_language(state, "python")

    if not py_files:
        state["bandit_issues"] = {}
        return state
    
    working_dir = state["working_dir"]

    full_paths = [
        os.path.join(working_dir, f)
        for f in py_files
    ]

    # Bandit command: output JSON, only show HIGH and MEDIUM severity (optional)
    cmd = ["bandit", "-f", "json", "-ll"] + full_paths  # -ll = only HIGH and MEDIUM

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
        for f in py_files:
            bandit_issues[f] = [{"message": "bandit timed out after 60s", "tool": "bandit"}]
    except FileNotFoundError:
        for f in py_files:
            bandit_issues[f] = [{"message": "bandit not found. Install with `pip install bandit`", "tool": "bandit"}]
    except json.JSONDecodeError:
        for f in py_files:
            bandit_issues[f] = [{"message": "bandit returned invalid JSON", "tool": "bandit"}]
    except Exception as e:
        for f in py_files:
            bandit_issues[f] = [{"message": f"Unexpected error: {str(e)}", "tool": "bandit"}]

    # state["bandit_issues"] = bandit_issues
    return {
        "bandit_issues": bandit_issues
    }



def run_eslint(state: PythonReviewState) -> PythonReviewState:
    # js_files = [f for f, info in state["changed_files"].items() 
    #             if info["lang"] in ("javascript", "typescript")]
    js_files = get_files_by_language(state, "javascript") + get_files_by_language(state, "typescript")

    eslint_errors = {}
    if not js_files:
        state["eslint_errors"] = {}
        return state
    
    working_dir = state["working_dir"]

    full_paths = [
        os.path.join(working_dir, f)
        for f in js_files
    ]

    try:
        # Run eslint with JSON output
        result = subprocess.run(
            ["npx", "eslint", "--format=json"] + full_paths,
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
    
    # state["eslint_errors"] = eslint_errors
    return {
        "eslint_errors": eslint_errors
    }

def run_golangci(state: PythonReviewState) -> PythonReviewState:
    # go_files = [f for f, info in state["changed_files"].items() if info["lang"] == "go"]
    go_files = get_files_by_language(state, "go")
    golangci_errors = {}
    if not go_files:
        state["golangci_errors"] = {}
        return state
    
    working_dir = state["working_dir"]

    full_paths = [
        os.path.join(working_dir, f)
        for f in go_files
    ]

    # Run on the directory containing the files (or each file individually)
    # Simpler: run on each file, but golangci-lint works better on packages.
    # For demonstration, run on each file's directory (deduplicate)
    dirs = set(os.path.dirname(f) for f in full_paths)
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
    
    # state["golangci_errors"] = golangci_errors
    return {
        "golangci_errors": golangci_errors
    }

def run_checkstyle(state: PythonReviewState) -> PythonReviewState:
    # java_files = [f for f, info in state["changed_files"].items() if info["lang"] == "java"]
    java_files = get_files_by_language(state, "java")
    checkstyle_errors = {}
    if not java_files:
        state["checkstyle_errors"] = {}
        return state

    # Checkstyle command: requires a config file. We'll use a simple built-in config.
    # For production, provide a default config path.
    config_path = "checkstyle.xml"  # You should provide this file

    working_dir = state["working_dir"]
    try:
        for java_file in java_files:
            full_path = os.path.join(working_dir, java_file)
            result = subprocess.run(
                ["checkstyle", "-c", config_path, "-f", "json", full_path],
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
    
    # state["checkstyle_errors"] = checkstyle_errors
    return {
        "checkstyle_errors": checkstyle_errors
    }