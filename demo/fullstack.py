"""The whole stack, assembled into one support workflow.

Act two of the demo needs the primitives doing something rather than being
listed, so this builds the agent a real deployment would have: retrieval
over the help centre, memory of prior contacts, the orders service behind
MCP, a policy skill, a guardrail on the way out, a subagent for the
research, and a graph holding the shape.

The customer's email goes in as untrusted. It is shown to the model, boxed
and labelled, and the policy outranks it. That is the point of the trust
level, and it is the part of this file worth reading.
"""

from __future__ import annotations

from typing import Any

from demo.scenario import (
    CUSTOMER_EMAIL,
    HELP_CENTRE,
    PRIOR_CONTACT,
    REFUND_POLICY,
    issue_refund,
    lookup_order,
    researcher_model,
    resolver_model,
)
from rewyn import Agent, Graph
from rewyn.context import Context, ContextKind, TrustLevel, text_item
from rewyn.core.state import State
from rewyn.graphs import END
from rewyn.guardrails import ProhibitedContentGuardrail
from rewyn.human import AutoApprove, approve
from rewyn.mcp import connect, serve_tools
from rewyn.memory import Memory
from rewyn.rag import RAGPipeline
from rewyn.skills import Skill
from rewyn.tools import MaxRiskLevel, RiskLevel

REFUND_SKILL = Skill(
    name="refund-policy",
    description="How to decide a refund.",
    instructions=REFUND_POLICY,
    version="2",
)


def build_rag() -> RAGPipeline:
    """The help centre, indexed and retrievable with provenance."""
    pipeline = RAGPipeline(name="helpcentre", version="3", top_k=2)
    pipeline.index_documents_sync(HELP_CENTRE)
    return pipeline


async def build_memory() -> Memory:
    """What we already knew about this customer, isolated to their tenant."""
    memory = Memory(name="support-memory", namespace="tenant:acme")
    await memory.remember(PRIOR_CONTACT, importance=0.7, tags=["dana", "history"])
    return memory


def build_context(rag: RAGPipeline, memory: Memory) -> Context:
    """Assemble the prompt from sources that carry trust and provenance."""
    context = Context(
        [rag.as_context_source(), memory.as_context_source()],
        name="support-context",
        budget=3000,
    )
    context.add(
        text_item(
            CUSTOMER_EMAIL,
            kind=ContextKind.RUNTIME,
            source="inbound-email",
            title="Customer email",
            trust_level=TrustLevel.UNTRUSTED,
        )
    )
    return context


def researcher() -> Agent:
    """Gathers the facts. The orders service reaches it over MCP."""
    orders = serve_tools([lookup_order], name="orders")
    return Agent(
        model=researcher_model(),
        name="researcher",
        version="2",
        instructions="Gather the facts a refund decision needs. Report facts, not opinions.",
        mcp=[connect(orders, name="orders", tool_prefix="orders_")],
        max_iterations=4,
    )


def resolver(answer: str, context: Context, memory: Memory) -> Agent:
    """Decides. Constrained by the policy skill, a guardrail and an approval."""
    return Agent(
        model=resolver_model(answer),
        name="support",
        version="3",
        instructions="You are a support agent. Follow the refund policy exactly.",
        context=context,
        memory=memory,
        skills=[REFUND_SKILL],
        tools=[issue_refund],
        guardrails=[ProhibitedContentGuardrail(["guaranteed refund"], stage="output")],
        permission_policy=MaxRiskLevel(approve_above=RiskLevel.MEDIUM),
        approval_handler=AutoApprove(by="dana.ops"),
        max_cost=0.50,
        stream_model=True,
    )


def build_graph(answer: str, context: Context, memory: Memory) -> Graph:
    """Triage, research, resolve, sign off."""
    graph = Graph("support-desk", version="2")
    research_agent = researcher()
    resolve_agent = resolver(answer, context, memory)

    async def triage(state: State) -> dict[str, Any]:
        """Route: only refund requests take the full path."""
        message = str(state["input"]).lower()
        return {"route": "refund" if "refund" in message else "general"}

    async def research(state: State) -> dict[str, Any]:
        result = await research_agent.arun(str(state["input"]))
        return {"facts": result.output}

    async def resolve(state: State) -> dict[str, Any]:
        prompt = f"{state['input']}\n\nFacts:\n{state['facts']}"
        result = await resolve_agent.arun(prompt)
        return {"answer": result.output}

    async def sign_off(state: State) -> str:
        """A person confirms before the answer goes to the customer."""
        decision = await approve("send refund decision", risk="medium", to="Dana Whitfield")
        if not decision.approved:
            return f"Held for review: {decision.reason or 'not approved'}"
        return str(state["answer"])

    async def general(state: State) -> str:
        return "Routed to the general queue."

    graph.add_node("triage", triage)
    graph.add_node("research", research)
    graph.add_node("resolve", resolve)
    graph.add_node("sign_off", sign_off, output_key="reply")
    graph.add_node("general", general, output_key="reply")

    graph.branch("triage", lambda s: "research" if s["route"] == "refund" else "general")
    graph.connect("research", "resolve")
    graph.connect("resolve", "sign_off")
    graph.connect("sign_off", END)
    graph.connect("general", END)
    return graph


async def assemble(answer: str) -> Graph:
    """Everything, wired together."""
    rag = build_rag()
    memory = await build_memory()
    return build_graph(answer, build_context(rag, memory), memory)
