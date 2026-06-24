from state import PythonReviewState
from tools.tool_registry import TOOL_REGISTRY

def run_tool(state: PythonReviewState) -> PythonReviewState:
    """
    Executes a specific tool by name.
    This node is called in parallel for each tool.
    """
    # Get the tool metadata

    tool_name = state.get("current_tool")
    if not tool_name:
        print("❌ [NODE: run_tool] No tool specified in state")
        return state
    
    tool_meta = TOOL_REGISTRY.get(tool_name)
    
    if not tool_meta:
        # Unknown tool - return state unchanged
        return state
    
    # Get the tool function
    tool_func = tool_meta.get("run")
    if not tool_func:
        return state
    
    try:
        # Execute the tool
        print(f"Running tool: {tool_name}")

        result = tool_func(state)

        # print(f"Finished tool: {tool_name}")
        # print(result)

        result.pop("current_tool", None)

        return result
    except Exception as e:
        # If a tool fails, add error to state but continue with other tools
        error_state = state.copy()
        error_state["error"] = f"Tool '{tool_name}' failed: {str(e)}"
        return error_state