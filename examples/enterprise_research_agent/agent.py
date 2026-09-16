"""The agent itself: context, memory, RAG, skills, MCP, graph, subagent, approval.

The shape follows spec §52. A planner routes the request, a research
subagent gathers evidence from the CRM and the document store, a policy
skill and a risk tool constrain the analysis, and a human signs off before
the recommendation is final.

Every stage is an ordinary Rewyn primitive, so every stage is an event and
the whole thing is replayable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rewyn import Agent, Graph, tool
from rewyn.context import Context, ContextKind, TrustLevel, text_item
from rewyn.core.state import State
from rewyn.graphs import END
from rewyn.guardrails.validators import ProhibitedContentGuardrail
from rewyn.human import approve
from rewyn.mcp import connect, serve_tools
from rewyn.memory import Memory
from rewyn.rag import RAGPipeline
from rewyn.skills import load_skill
from rewyn.testing import FakeModel

from .data import BUREAU_SCORES, CRM, CUSTOMER_EMAIL, DOCUMENTS, PRIOR_DEALING

SKILL_PATH = Path(__file__).parent / "skills" / "credit-policy"


# Tools -------------------------------------------------------------------------
@tool
def lookup_account(company: str) -> dict[str, Any]:
    """Look a customer up in the CRM by company name."""
    return dict(CRM.get(company.strip().lower(), {})) or {"error": f"no account for {company!r}"}


@tool(risk_level="medium", permissions=["bureau:read"])
def credit_bureau_score(account_id: str) -> dict[str, Any]:
    """Fetch the current credit bureau score for an account."""
    score = BUREAU_SCORES.get(account_id)
    if score is None:
        return {"error": f"no bureau record for {account_id}"}
    band = "prime" if score >= 750 else "near-prime" if score >= 650 else "subprime"
    return {"account_id": account_id, "score": score, "band": band}


@tool(risk_level="high", permissions=["credit:write"])
def set_credit_limit(account_id: str, limit: int) -> str:
    """Apply a new credit limit. Requires human approval before it runs."""
    return f"limit for {account_id} set to {limit}"


# Building blocks ---------------------------------------------------------------
def build_rag() -> RAGPipeline:
    """An indexed document corpus, retrievable with provenance."""
    pipeline = RAGPipeline(name="internal-docs", top_k=2)
    pipeline.index_documents_sync(DOCUMENTS)
    return pipeline


async def build_memory() -> Memory:
    """What we already knew about this customer before today."""
    memory = Memory(name="account-memory")
    await memory.remember(PRIOR_DEALING, importance=0.8, tags=["acme", "credit"])
    return memory


def build_context(rag: RAGPipeline, memory: Memory) -> Context:
    """Assemble the prompt from sources that carry trust and provenance.

    The customer's email goes in as untrusted. It is visible to the model,
    boxed and labelled, and the policy skill outranks it. That is the whole
    point of the trust level: hostile input is data, never instruction.
    """
    context = Context([rag.as_context_source(), memory.as_context_source()], budget=2500)
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


def research_agent(model: Any) -> Agent:
    """Gathers evidence. Has the CRM over MCP and the bureau as a tool."""
    crm_server = serve_tools([lookup_account], name="salesforce")
    return Agent(
        model=model,
        name="researcher",
        version="2",
        instructions=(
            "Gather the evidence a credit decision needs: account facts, payment "
            "history, bureau score and open disputes. Report facts, not opinions."
        ),
        tools=[credit_bureau_score],
        # The prefix keeps the origin of a tool visible in the transcript and
        # in the run's dependency list: salesforce_lookup_account, not lookup_account.
        mcp=[connect(crm_server, name="salesforce", tool_prefix="salesforce_")],
        max_iterations=6,
    )


def analyst_agent(model: Any, context: Context, memory: Memory) -> Agent:
    """Decides. Constrained by the policy skill and a guardrail."""
    return Agent(
        model=model,
        name="credit-analyst",
        version="4",
        instructions="You are a careful credit analyst. Follow the credit policy exactly.",
        context=context,
        memory=memory,
        skills=[load_skill(SKILL_PATH)],
        tools=[set_credit_limit],
        guardrails=[ProhibitedContentGuardrail(["guaranteed approval"], stage="output")],
        max_iterations=4,
    )


# The graph ---------------------------------------------------------------------
def build_graph(models: dict[str, FakeModel], context: Context, memory: Memory) -> Graph:
    """Planner, research, analysis, risk, approval (spec §52)."""
    graph = Graph("acme-credit", version="1")
    researcher = research_agent(models["researcher"])
    analyst = analyst_agent(models["analyst"], context, memory)

    async def plan(state: State) -> dict[str, Any]:
        """Decide whether this request needs the full credit workflow."""
        request = str(state["input"])
        needs_credit = "credit" in request.lower() or "limit" in request.lower()
        return {"route": "credit" if needs_credit else "general", "question": request}

    async def research(state: State) -> dict[str, Any]:
        result = await researcher.arun(str(state["question"]))
        return {"evidence": result.output}

    async def analyze(state: State) -> dict[str, Any]:
        prompt = f"{state['question']}\n\nEvidence gathered:\n{state['evidence']}"
        result = await analyst.arun(prompt)
        return {"analysis": result.output, "proposed_limit": _proposed_limit(result.output)}

    async def risk(state: State) -> dict[str, Any]:
        """A deterministic check the model cannot talk its way past."""
        limit = int(state.get("proposed_limit") or 0)
        account = CRM["acme corporation"]
        score = BUREAU_SCORES[str(account["account_id"])]
        ceiling = 250_000 if score >= 750 else 100_000 if score >= 650 else 0
        return {
            "risk_ok": limit <= ceiling,
            "ceiling": ceiling,
            "bureau_score": score,
        }

    async def sign_off(state: State) -> str:
        """A human decides before anything is applied."""
        if not state["risk_ok"]:
            return (
                f"Declined: proposed {state['proposed_limit']} exceeds the "
                f"{state['ceiling']} ceiling for a bureau score of {state['bureau_score']}."
            )
        decision = await approve(
            "raise credit limit",
            risk="high",
            account="ACC-4417",
            proposed_limit=state["proposed_limit"],
            ceiling=state["ceiling"],
        )
        if not decision.approved:
            return f"Held for review: {decision.reason or 'not approved'}"
        return str(state["analysis"])

    async def general(state: State) -> str:
        return "This request does not need a credit review."

    graph.add_node("plan", plan)
    graph.add_node("research", research)
    graph.add_node("analyze", analyze)
    graph.add_node("risk", risk)
    graph.add_node("sign_off", sign_off, output_key="recommendation")
    graph.add_node("general", general, output_key="recommendation")

    # The router returns the name of the next node, straight from state.
    graph.branch("plan", lambda s: "research" if s["route"] == "credit" else "general")
    graph.connect("research", "analyze")
    graph.connect("analyze", "risk")
    graph.connect("risk", "sign_off")
    graph.connect("sign_off", END)
    graph.connect("general", END)
    return graph


def _proposed_limit(text: str) -> int:
    """Pull the recommended number out of the analyst's answer."""
    import re

    found = re.findall(r"\$?([0-9][0-9,]{4,})", text)
    return int(found[0].replace(",", "")) if found else 0
