from state import PythonReviewState
from tools.tool_registry import TOOL_REGISTRY

def planner(state: PythonReviewState) -> PythonReviewState:
    """
    Determines which tools to run based on changed files.
    Stores the list of tools in the state for the router to use.
    Returns the updated state.
    """
    print("📋 [NODE: planner] Planning tools...")
    
    # Check for errors from fetch_pr
    if state.get("error"):
        print("   ❌ Error detected, skipping tools")
        state["tools_to_run"] = []
        return state
    
    # Check if we have any changed files
    if not state.get("changed_files"):
        print("   ⚠️ No changed files found")
        state["tools_to_run"] = []
        return state
    
    # Determine which languages are present
    langs = set()
    for file_info in state["changed_files"].values():
        if isinstance(file_info, dict) and "lang" in file_info:
            langs.add(file_info["lang"])
    
    print(f"   Detected languages: {langs if langs else 'None'}")
    
    # If no languages detected, return empty list
    if not langs:
        state["tools_to_run"] = []
        return state
    
    # Collect tools to run based on languages
    tools_to_run = []
    for tool_name, meta in TOOL_REGISTRY.items():
        tool_langs = meta.get("languages", [])
        if any(lang in tool_langs for lang in langs):
            tools_to_run.append(tool_name)
    
    print(f"   Tools to run: {tools_to_run if tools_to_run else 'None'}")
    
    # Store in state (NOT return Send objects!)
    state["tools_to_run"] = tools_to_run
    return state