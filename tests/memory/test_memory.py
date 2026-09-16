from __future__ import annotations

from pathlib import Path

import pytest

from rewyn.context import Context
from rewyn.core.event import EventType, ListSink
from rewyn.core.run import start_run
from rewyn.memory import (
    FileStore,
    InMemoryStore,
    Memory,
    MemoryKind,
    ShortTermMemory,
    facts_about,
    long_term_memory,
    record_episode,
    remember_fact,
    remember_procedure,
    similar_episodes,
)
from rewyn.models.base import Message


async def test_remember_and_recall_emit_events() -> None:
    sink = ListSink()
    memory = Memory()
    async with start_run("t", sinks=[sink]) as run:
        await memory.remember("Acme prefers invoices on the 1st", importance=0.9, tags=["billing"])
        await memory.remember("The sky is blue", kind="semantic")
        hits = await memory.recall("Acme invoices")
    assert hits[0].item.content.startswith("Acme")
    assert hits[0].score > hits[-1].score if len(hits) > 1 else True
    write = sink.of_type(EventType.MEMORY_WRITE)[0]
    assert write.payload["kind"] == "semantic"
    assert write.payload["tags"] == ["billing"]
    read = sink.of_type(EventType.MEMORY_READ)[0]
    assert read.payload["query"] == "Acme invoices"
    assert read.payload["hits"][0]["content"].startswith("Acme")
    assert run.manifest.dependencies[0].kind == "memory"


async def test_kinds_are_gated_by_configuration() -> None:
    memory = Memory(long_term=False)
    assert memory.enabled == (MemoryKind.WORKING, MemoryKind.SHORT_TERM)
    with pytest.raises(KeyError, match="not enabled"):
        await memory.remember("x", kind="semantic")
    await memory.remember("note", kind="working")
    assert [i.content for i in await memory.items("working")] == ["note"]


async def test_forget_clear_and_sync_helpers() -> None:
    memory = Memory()
    item = memory.remember_sync("temporary")
    assert memory.recall_sync("temporary")[0].item.id == item.id
    assert await memory.forget(item.id)
    assert not await memory.forget(item.id)
    await memory.remember("a", kind="episodic")
    await memory.remember("b", kind="semantic")
    assert await memory.clear(kinds=[MemoryKind.EPISODIC]) == 1
    assert await memory.clear() == 1


async def test_file_store_persists_and_redacts(rewyn_home: Path) -> None:
    memory = long_term_memory("acct")
    await memory.remember("token sk-ant-api03-abcdefghijklmnopqrstuvwxyz belongs to bob")
    path = rewyn_home / "memory" / "acct.jsonl"
    assert path.exists()
    assert "sk-ant" not in path.read_text()
    reloaded = Memory(store=FileStore(path))
    hits = await reloaded.recall("bob")
    assert hits
    assert "[REDACTED]" in hits[0].item.content


async def test_separate_stores_per_kind() -> None:
    working = InMemoryStore()
    facts = InMemoryStore()
    memory = Memory(stores={MemoryKind.WORKING: working, MemoryKind.SEMANTIC: facts})
    await memory.remember("scratch", kind="working")
    await memory.remember("fact", kind="semantic")
    assert [i.content for i in await working.list_items()] == ["scratch"]
    assert [i.content for i in await facts.list_items()] == ["fact"]
    hits = await memory.recall("fact scratch")
    assert {h.item.content for h in hits} == {"scratch", "fact"}


async def test_memory_as_context_source() -> None:
    memory = Memory()
    await memory.remember("Customer Acme has a 50k credit limit", importance=0.9)
    context = Context([memory.as_context_source(limit=2)], budget=500)
    assembled = await context.assemble("Acme credit limit")
    assert assembled.items[0].kind.value == "memory"
    assert "50k" in assembled.text()
    assert assembled.items[0].provenance is not None
    assert assembled.items[0].provenance.source == "memory"


async def test_episodic_and_semantic_helpers() -> None:
    memory = Memory()
    await record_episode(memory, task="raise credit limit", outcome="approved 60k", success=True)
    await remember_fact(memory, "Acme was founded in 1999")
    await remember_procedure(memory, "credit review", ["pull history", "score", "decide"])
    episodes = await similar_episodes(memory, "credit limit")
    assert episodes[0].metadata["success"] is True
    facts = await facts_about(memory, "Acme founded")
    assert facts[0].content.startswith("Acme was")
    procedures = await memory.items("procedural")
    assert "1. pull history" in procedures[0].content


def test_short_term_window() -> None:
    stm = ShortTermMemory(max_messages=3, max_tokens=3)
    stm.append(Message.user("one one one"), Message.assistant("two"), Message.user("three"))
    stm.append(Message.assistant("four"))
    assert len(stm) == 3  # oldest dropped by max_messages
    window = stm.window()
    assert [m.text for m in window] == ["three", "four"]  # token budget keeps the newest
    assert stm.transcript() == "user: three\nassistant: four"
    stm.clear()
    assert stm.window() == []
