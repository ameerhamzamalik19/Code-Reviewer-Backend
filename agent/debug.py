import time
import json
from datetime import datetime
from typing import Any, Dict
from state import PythonReviewState

class GraphDebugger:
    """Utility for debugging LangGraph execution."""
    
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.execution_history = []
        self.start_time = None
        self.node_times = {}
    
    def log_node_start(self, node_name: str, state: PythonReviewState):
        """Log when a node starts executing."""
        if not self.enabled:
            return
        
        timestamp = time.time()
        if self.start_time is None:
            self.start_time = timestamp
        
        self.node_times[node_name] = {
            "start": timestamp,
            "end": None,
            "duration": None
        }
        
        print(f"\n{'='*80}")
        print(f"🚀 [NODE START] {node_name.upper()} at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        print(f"{'='*80}")
        
        # Show relevant state keys
        self._print_state_summary(state, node_name)
    
    def log_node_end(self, node_name: str, state: PythonReviewState):
        """Log when a node finishes executing."""
        if not self.enabled:
            return
        
        timestamp = time.time()
        if node_name in self.node_times:
            self.node_times[node_name]["end"] = timestamp
            self.node_times[node_name]["duration"] = timestamp - self.node_times[node_name]["start"]
            duration = self.node_times[node_name]["duration"]
        
        print(f"\n✅ [NODE END] {node_name.upper()} completed in {duration:.3f}s")
        print(f"{'='*80}\n")
    
    def log_edge(self, from_node: str, to_node: str, condition: str = None):
        """Log when traversing an edge."""
        if not self.enabled:
            return
        
        condition_info = f" (condition: {condition})" if condition else ""
        print(f"➡️  [EDGE] {from_node} → {to_node}{condition_info}")
    
    def log_state_change(self, state: PythonReviewState, key: str, old_value: Any, new_value: Any):
        """Log when a state key changes."""
        if not self.enabled:
            return
        
        print(f"📝 [STATE CHANGE] {key}:")
        print(f"   Old: {self._truncate(str(old_value), 100)}")
        print(f"   New: {self._truncate(str(new_value), 100)}")
    
    def log_error(self, node_name: str, error: Exception):
        """Log errors during execution."""
        if not self.enabled:
            return
        
        print(f"❌ [ERROR] in {node_name}: {str(error)}")
    
    def _print_state_summary(self, state: PythonReviewState, node_name: str):
        """Print a summary of the state."""
        print(f"📊 State Summary:")
        print(f"   - PR URL: {state.get('pr_url', 'N/A')}")
        print(f"   - Changed files: {len(state.get('changed_files', {}))}")
        print(f"   - Error: {state.get('error', 'None')}")
        print(f"   - Temp dir: {state.get('temp_dir', 'N/A')}")
        
        # Show error counts per tool
        tool_fields = ["ruff_errors", "mypy_errors", "bandit_issues", 
                      "eslint_errors", "golangci_errors", "checkstyle_errors"]
        for field in tool_fields:
            count = self._count_issues(state.get(field, {}))
            if count > 0 or node_name in ["aggregator", "generate_comments"]:
                print(f"   - {field}: {count} issues")
        
        # Show final comments count if available
        if state.get("final_comments"):
            print(f"   - Final comments: {len(state['final_comments'])}")
    
    def _count_issues(self, issues_dict: Dict) -> int:
        """Count total issues across all files."""
        total = 0
        for file, issues in issues_dict.items():
            if isinstance(issues, list):
                total += len(issues)
        return total
    
    def _truncate(self, text: str, max_length: int = 100) -> str:
        """Truncate long strings for display."""
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."
    
    def print_execution_summary(self):
        """Print a summary of the entire execution."""
        if not self.enabled:
            return
        
        print(f"\n{'='*80}")
        print(f"📊 EXECUTION SUMMARY")
        print(f"{'='*80}")
        print(f"Total execution time: {time.time() - self.start_time:.3f}s")
        print(f"\nNode timings:")
        for node, times in self.node_times.items():
            if times["duration"]:
                print(f"   - {node}: {times['duration']:.3f}s")
        print(f"{'='*80}\n")

# Create a global debugger instance
debugger = GraphDebugger(enabled=True)