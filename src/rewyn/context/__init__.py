"""Context engineering: sources, provenance, budgeting, ranking, compression."""

from rewyn.context.budget import BudgetDecision, allocate, estimate_tokens
from rewyn.context.compression import SummaryCompressor, TruncateCompressor, dedupe
from rewyn.context.freshness import FreshnessPolicy
from rewyn.context.manager import AssembledContext, Context, ContextEngine, render_items
from rewyn.context.provenance import diff_provenance, provenance_report
from rewyn.context.ranking import DefaultRanker, PriorityWeights
from rewyn.context.retrieval import RetrieverSource
from rewyn.context.source import (
    CallableSource,
    ContextItem,
    ContextKind,
    ContextSource,
    Provenance,
    Sensitivity,
    StaticSource,
    TrustLevel,
    text_item,
)

__all__ = [
    "AssembledContext",
    "BudgetDecision",
    "CallableSource",
    "Context",
    "ContextEngine",
    "ContextItem",
    "ContextKind",
    "ContextSource",
    "DefaultRanker",
    "FreshnessPolicy",
    "PriorityWeights",
    "Provenance",
    "RetrieverSource",
    "Sensitivity",
    "StaticSource",
    "SummaryCompressor",
    "TruncateCompressor",
    "TrustLevel",
    "allocate",
    "dedupe",
    "diff_provenance",
    "estimate_tokens",
    "provenance_report",
    "render_items",
    "text_item",
]
