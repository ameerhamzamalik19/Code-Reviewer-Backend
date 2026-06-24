import json
import logging
from typing import List, Dict, Optional, Any

from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

# Set up logging
logger = logging.getLogger(__name__)


def build_prompt(report: str, changed_files: Optional[List[str]] = None,
                 code_snippets: Optional[Dict[str, str]] = None) -> tuple:
    """
    Construct the system and user prompts for the LLM.

    Args:
        report: The aggregated static analysis report (Ruff, MyPy, etc.)
        changed_files: List of file paths changed in the PR
        code_snippets: Dict mapping file path to code snippet content

    Returns:
        (system_prompt, user_prompt)
    """
    system = """You are an expert code reviewer. You will receive a static analysis report for a Pull Request.

Your job is to:
1. Prioritize issues by severity: Critical, Warning, Suggestion.
2. Explain each issue in plain English—don't just repeat error codes.
3. Provide concrete, actionable fixes (show bad vs good code).
4. Give an overall quality summary and any architectural advice.

**Output must be a valid JSON object** with this schema:
{
  "overall_quality": "Good"|"Average"|"Needs Improvement",
  "summary": "1-2 sentence summary",
  "issues": [
    {
      "file": "path/to/file.py",
      "line": 42,
      "ruff_code": "F401",
      "title": "Unused import",
      "severity": "Critical"|"Warning"|"Suggestion",
      "explanation": "Why it matters",
      "suggested_fix": "Corrected code or removal steps"
    }
  ],
  "general_advice": "Broader feedback"
}
Reply ONLY with the JSON, no extra text."""

    context = f"### Static Analysis Report:\n{report}\n\n"
    if changed_files:
        context += f"### Changed Files:\n{', '.join(changed_files)}\n\n"
    if code_snippets:
        context += "### Code Snippets (grouped by file):\n"
        for path, code in code_snippets.items():
            context += f"--- {path} ---\n```python\n{code}\n```\n\n"

    user = f"Here is the report for the current PR:\n\n{context}\n\nAnalyze and return JSON."
    return system, user


def llm_reviewer(report_text: str,
                   changed_files: Optional[List[str]] = None,
                   code_snippets: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Send the report and snippets to the LLM and return the parsed JSON response.

    Args:
        report_text: The aggregated tool report (Ruff, MyPy, etc.)
        changed_files: List of changed file paths
        code_snippets: Dict of file path -> code snippet

    Returns:
        A dictionary with the parsed JSON response, or an error dict.
    """
    system, user = build_prompt(report_text, changed_files, code_snippets)

    # Initialize the OpenAI client for Groq
    client = OpenAI(
        api_key=os.environ.get("GROQ_API_KEY"),
        base_url="https://api.groq.com/openai/v1",
    )

    try:
        # Use the Chat Completions API (Groq‑compatible)
        response = client.chat.completions.create(
            model="qwen/qwen3-32b",  # or "llama3-70b-8192", "mixtral-8x7b-32768"
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            temperature=0.3,          # Low for deterministic JSON
            max_tokens=4096,
            response_format={"type": "json_object"}  # Forces JSON (if model supports it)
        )

        raw = response.choices[0].message.content
        logger.debug(f"Raw LLM response: {raw}")

        # Clean the response in case the model wraps JSON in markdown
        cleaned = raw.strip()
        if "```json" in cleaned:
            cleaned = cleaned.split("```json")[1].split("```")[0].strip()
        elif "```" in cleaned:
            cleaned = cleaned.split("```")[1].split("```")[0].strip()

        # Parse JSON
        result = json.loads(cleaned)

        # Validate minimal expected structure
        if not isinstance(result, dict):
            raise ValueError("LLM response is not a JSON object")

        return result

    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}\nRaw response: {raw}")
        return {
            "overall_quality": "Error",
            "summary": "Failed to parse LLM response as JSON",
            "issues": [],
            "general_advice": raw,
            "raw": raw,
            "error": str(e)
        }
    except Exception as e:
        logger.exception("LLM call failed")
        return {
            "overall_quality": "Error",
            "summary": f"LLM call error: {str(e)}",
            "issues": [],
            "general_advice": "",
            "error": str(e)
        }





# from openai import OpenAI
# import os
# from dotenv import load_dotenv

# load_dotenv()

# def build_prompt(ruff_report: str, changed_files: list = None, code_snippets: dict = None):
#     system = """You are an expert code reviewer. You will receive a static analysis report (Ruff) for a Pull Request.

# Your job is to:
# 1. Prioritize issues by severity: Critical, Warning, Suggestion.
# 2. Explain each issue in plain English—don't just repeat error codes.
# 3. Provide concrete, actionable fixes (show bad vs good code).
# 4. Give an overall quality summary and any architectural advice.

# **Output must be a valid JSON object** with this schema:
# {
#   "overall_quality": "Good"|"Average"|"Needs Improvement",
#   "summary": "1-2 sentence summary",
#   "issues": [
#     {
#       "file": "path/to/file.py",
#       "line": 42,
#       "ruff_code": "F401",
#       "title": "Unused import",
#       "severity": "Critical"|"Warning"|"Suggestion",
#       "explanation": "Why it matters",
#       "suggested_fix": "Corrected code or removal steps"
#     }
#   ],
#   "general_advice": "Broader feedback"
# }
# Reply ONLY with the JSON, no extra text."""
    
#     context = f"### Ruff Report:\n{ruff_report}\n\n"
#     if changed_files:
#         context += f"### Changed Files:\n{', '.join(changed_files)}\n\n"
#     if code_snippets:
#         context += "### Code Snippets (from diff):\n"
#         for path, code in code_snippets.items():
#             context += f"--- {path} ---\n```python\n{code}\n```\n\n"
    
#     user = f"Here is the Ruff report for the current PR:\n\n{context}\n\nAnalyze and return JSON."
#     return system, user

# client = OpenAI(
#     api_key=os.environ.get("GROQ_API_KEY"),
#     base_url="https://api.groq.com/openai/v1",
# )

# response = client.responses.create(
#     input="Explain the importance of fast language models",
#     model="qwen/qwen3-32b",
# )
# print(response.output_text)
