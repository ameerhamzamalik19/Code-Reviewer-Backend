from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from tools.tools import (
    fetch_pr,
    generate_comments,
    decide
)
from state import PythonReviewState
from agent.planner import planner
from agent.runner import run_tool
from langgraph.types import Send
from database.models import User
import uuid
from sqlalchemy.orm import Session

def route_to_tools(state: PythonReviewState) -> list[Send] | str:
    """Route to parallel tools or skip to aggregator."""
    tools = state.get("tools_to_run", [])
    if not tools:
        print("⚠️ No tools to run, going to aggregator")
        return "aggregator"
    
    print(f"🔀 Routing to {len(tools)} tools in parallel")
    return [Send("run_tool", {**state, "current_tool": tool}) for tool in tools]

def generate_graph():
    print("🔍 [NODE: fetch_pr] Starting...")        
    builder = StateGraph(PythonReviewState)

    builder.add_node("fetch_pr", fetch_pr)
    builder.add_node("planner", planner)
    builder.add_node("run_tool", run_tool)          # This node is called multiple times in parallel
    builder.add_node("aggregator", lambda state: state)  # no‑op, just to join
    builder.add_node("generate_comments", generate_comments)
    # builder.add_node("decide", decide)  # actually a conditional edge function

    builder.add_edge(START, "fetch_pr")
    builder.add_edge("fetch_pr", "planner")
    # builder.add_edge("planner", "aggregator")
    builder.add_conditional_edges(
        "planner",
        route_to_tools,
        {
            "run_tool": "run_tool",
            "aggregator": "aggregator"
        }
    )

    builder.add_edge("run_tool", "aggregator")

    # After planner returns a list of Send, LangGraph will execute run_tool in parallel
    # builder.add_conditional_edges("planner", lambda state: "run_tool")  # not used; actually uses Send

    # We need to define that after all parallel run_tool complete, we go to aggregator
    # In LangGraph, you can use a special edge: the "planner" node returns a list of Send,
    # and you can set a "reducer" edge to go to aggregator after all are done.
    # Alternatively, you can use a "wait" node pattern: planner sends to run_tool, and after all, go to decide.
    # LangGraph has built‑in support: if you use `Send` from a node, the graph will wait for all parallel branches to finish before continuing.
    # So just add an edge from planner to aggregator (which will be executed after all Send tasks are complete).
    # builder.add_edge("planner", "aggregator")   # This edge is taken after all parallel tasks finish

    # Then from aggregator to decide (which can be a conditional edge function)
    # builder.add_conditional_edges("aggregator", decide, {
    #     "ask_human": "generate_comments",
    #     "end": END
    # })

    # builder.add_edge("generate_comments", END)

    # return builder

    def should_generate_comments(state: PythonReviewState) -> str:
        """Determine if we should generate comments or end."""
        if state.get("error"):
            return "generate_comments"
        
        # Check if any tool found issues
        tool_fields = [
            "ruff_errors", "mypy_errors", "bandit_issues",
            "eslint_errors", "golangci_errors", "checkstyle_errors"
        ]
        total_issues = 0
        for field in tool_fields:
            for file, issues in state.get(field, {}).items():
                total_issues += len(issues) if isinstance(issues, list) else 0
        
        if total_issues > 0:
            return "generate_comments"
        else:
            return "end"
    
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

def run(db: Session, pr_url: str, user: User):

    def after_fetch_pr(state: PythonReviewState) -> str:
        return "generate_comments" if state.get("error") else "continue"

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
    # pr_url = "https://github.com/techwithtim/PythonAIAgentFromScratch/pull/7"

    initial_state = {
        "session_id": str(uuid.uuid4()),
        "pr_url": pr_url,
        "diff_text": "",
        "ruff_errors": {},
        "mypy_errors": {},
        "bandit_issues": {},
        "eslint_errors": {},       # new
        "golangci_errors": {},     # new
        "checkstyle_errors": {},   # new
        "human_question": None,
        "human_answer": None,
        "final_comments": [],
        "changed_files": {},        # new
        "error": None,
        "current_tool": None,
        "tools_to_run": [],
        "working_dir": None,
    }

    try:
        result = graph.invoke(initial_state, config=config)
        # pprint(result)
        final_comments = result.get("final_comments", [])
        
    except Exception as e:
        # Fallback error handling
        return [{"file": "SYSTEM", "line": 0, "body": f"❌ Review failed: {str(e)}", "tool": "system", "severity": "error"}]
    
    return final_comments