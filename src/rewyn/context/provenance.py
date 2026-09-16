"""Provenance helpers: trace, verify and diff where context came from."""

from __future__ import annotations

from collections.abc import Sequence

from rewyn.context.source import ContextItem, Provenance
from rewyn.core.types import JSONObject


def provenance_report(items: Sequence[ContextItem]) -> list[JSONObject]:
    """One record per item: source, record, version, hash, verification.

    The record carries everything a context inspector has to show for an item
    (UI spec §11) and everything an authorization layer needs to decide
    whether to show it at all (UI spec §54), so a reader of the event log
    never has to go back to the original item.
    """
    report: list[JSONObject] = []
    for item in items:
        prov = item.provenance
        report.append(
            {
                "id": item.id,
                "kind": item.kind.value,
                "title": item.title,
                "source": prov.source if prov else None,
                "record": prov.record if prov else None,
                "version": prov.version if prov else None,
                "uri": prov.uri if prov else None,
                "retrieved_at": prov.retrieved_at.isoformat() if prov else None,
                "hash": prov.hash if prov else None,
                "tokens": item.tokens,
                "relevance": item.relevance,
                "authority": item.effective_authority,
                "trust_level": item.trust_level.value,
                "sensitivity": item.sensitivity.value,
                "permissions": list(item.permissions),
                "verified": prov.verify(item.content) if prov else None,
            }
        )
    return report


def diff_provenance(before: Sequence[ContextItem], after: Sequence[ContextItem]) -> JSONObject:
    """Which sources/records appeared, disappeared or changed content between two contexts."""

    def key(item: ContextItem) -> str:
        prov: Provenance | None = item.provenance
        if prov is None:
            return f"inline:{item.content_hash}"
        return f"{prov.source}:{prov.record or ''}"

    old = {key(i): i for i in before}
    new = {key(i): i for i in after}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k].content_hash != new[k].content_hash)
    return {"added": added, "removed": removed, "changed": changed}
