from state import PythonReviewState
from typing import List

def get_files_by_language(state: PythonReviewState, language: str) -> List[str]:
    """
    Extract files of a specific language from changed_files.
    
    Args:
        state: The current state
        language: The language to filter by (e.g., "python", "javascript", "go", "java")
    
    Returns:
        List of file paths
    """
    return [f for f, info in state.get("changed_files", {}).items() 
            if info.get("lang") == language]