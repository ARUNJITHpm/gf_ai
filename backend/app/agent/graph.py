"""The suggestion pipeline as a LangGraph graph (spec §8):

load_context -> retrieve_notes -> draft -> risk_check -> END
                                      └─(no drafts: declined)─> END
"""

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.state import AgentDeps, AgentState


def _after_draft(state: AgentState) -> str:
    return "risk_check" if state.get("drafts") else END


def build_graph(deps: AgentDeps) -> Any:
    graph = StateGraph(AgentState)
    graph.add_node("load_context", partial(nodes.load_context, deps=deps))
    graph.add_node("retrieve_notes", partial(nodes.retrieve, deps=deps))
    graph.add_node("draft", partial(nodes.draft, deps=deps))
    graph.add_node("risk_check", partial(nodes.risk_check, deps=deps))

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "retrieve_notes")
    graph.add_edge("retrieve_notes", "draft")
    graph.add_conditional_edges("draft", _after_draft, ["risk_check", END])
    graph.add_edge("risk_check", END)
    return graph.compile()


def run_agent(deps: AgentDeps, initial: AgentState) -> AgentState:
    result: AgentState = build_graph(deps).invoke(initial)
    return result
