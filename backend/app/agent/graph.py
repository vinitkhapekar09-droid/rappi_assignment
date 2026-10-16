"""LangGraph workflow assembly.

One graph models the full lifecycle and enters at the node required by the
requested action (``recommend``, ``execute``, ``execute_and_validate``, or
``validate``). This mirrors the backend's separate endpoints while keeping a
single, inspectable state machine.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph

from app.agent.dependencies import AgentDependencies
from app.agent.nodes import WorkflowNodes
from app.agent.state import AgentState


def build_agent_graph(
    deps: AgentDependencies,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    nodes = WorkflowNodes(deps)
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node("load_case", nodes.load_case)
    graph.add_node("load_evidence", nodes.load_evidence)
    graph.add_node("investigate", nodes.investigate)
    graph.add_node("explain", nodes.explain)
    graph.add_node("assemble_recommendation", nodes.assemble_recommendation)
    graph.add_node("finalize_recommendation", nodes.finalize_recommendation)
    graph.add_node("guard_approval", nodes.guard_approval)
    graph.add_node("execute", nodes.execute)
    graph.add_node("validate", nodes.validate)
    graph.add_node("escalate", nodes.escalate)

    graph.set_conditional_entry_point(
        nodes.dispatch,
        {
            "load_case": "load_case",
            "guard_approval": "guard_approval",
            "validate": "validate",
        },
    )

    graph.add_edge("load_case", "load_evidence")
    graph.add_edge("load_evidence", "investigate")
    graph.add_edge("investigate", "explain")
    graph.add_edge("explain", "assemble_recommendation")
    graph.add_edge("assemble_recommendation", "finalize_recommendation")

    graph.add_conditional_edges(
        "finalize_recommendation",
        nodes.route_after_recommendation,
        {"end": END, "escalate": "escalate"},
    )
    graph.add_conditional_edges(
        "guard_approval",
        nodes.route_after_guard,
        {"execute": "execute", "escalate": "escalate"},
    )
    graph.add_conditional_edges(
        "execute",
        nodes.route_after_execute,
        {"validate": "validate", "end": END, "escalate": "escalate"},
    )
    graph.add_conditional_edges(
        "validate",
        nodes.route_after_validate,
        {"end": END, "escalate": "escalate"},
    )
    graph.add_edge("escalate", END)

    return graph.compile(checkpointer=checkpointer)
