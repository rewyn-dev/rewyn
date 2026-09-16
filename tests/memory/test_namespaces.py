"""Namespaces are the tenant boundary (spec §55: tenant isolation)."""

from __future__ import annotations

import pytest

from rewyn.memory import FileStore, InMemoryStore, Memory, MemoryKind
from rewyn.memory.memory import NamespacedStore, scoped


async def test_a_shared_file_store_does_not_leak_between_namespaces():
    """The regression this exists for: two tenants, one store, no crosstalk."""
    a = Memory(namespace="tenant:a", store=FileStore())
    b = Memory(namespace="tenant:b", store=FileStore())

    await a.remember("Acme's margin is 42%", importance=0.9)

    assert await b.recall("margin") == []
    assert [h.item.content for h in await a.recall("margin")] == ["Acme's margin is 42%"]


async def test_a_shared_in_memory_store_does_not_leak_either():
    store = InMemoryStore()
    a = Memory(namespace="tenant:a", store=store)
    b = Memory(namespace="tenant:b", store=store)

    await a.remember("Globex pricing is confidential")

    assert await b.recall("pricing") == []
    assert await b.items(MemoryKind.SEMANTIC) == []
    assert len(await a.items(MemoryKind.SEMANTIC)) == 1


async def test_the_same_namespace_still_shares():
    store = InMemoryStore()
    first = Memory(namespace="tenant:a", store=store)
    second = Memory(namespace="tenant:a", store=store)

    await first.remember("shared between sessions of one tenant")
    assert len(await second.recall("shared")) == 1


async def test_items_are_stamped_with_their_namespace():
    memory = Memory(namespace="tenant:a")
    item = await memory.remember("a fact")
    assert item.namespace == "tenant:a"


async def test_another_tenant_cannot_read_or_delete_by_id():
    store = InMemoryStore()
    a = Memory(namespace="tenant:a", store=store)
    item = await a.remember("private")

    scoped_b = scoped(store, "tenant:b")
    assert await scoped_b.get(item.id) is None
    assert await scoped_b.delete(item.id) is False
    assert await scoped(store, "tenant:a").get(item.id) is not None


async def test_clearing_one_namespace_leaves_the_other_intact():
    store = InMemoryStore()
    a = Memory(namespace="tenant:a", store=store)
    b = Memory(namespace="tenant:b", store=store)
    await a.remember("a one")
    await a.remember("a two")
    await b.remember("b one")

    assert await scoped(store, "tenant:a").clear() == 2
    assert len(await b.items(MemoryKind.SEMANTIC)) == 1


async def test_a_namespace_limit_is_not_consumed_by_another_tenant():
    """A busy tenant must not push a quiet one out of its own result page."""
    store = InMemoryStore()
    noisy = Memory(namespace="tenant:noisy", store=store)
    quiet = Memory(namespace="tenant:quiet", store=store)
    for index in range(20):
        await noisy.remember(f"noisy invoice {index}")
    await quiet.remember("quiet invoice one")

    assert len(await quiet.items(MemoryKind.SEMANTIC, limit=5)) == 1
    assert [h.item.content for h in await quiet.recall("invoice")] == ["quiet invoice one"]


def test_the_file_store_scopes_natively_rather_than_filtering(rewyn_home):
    store = FileStore()
    other = scoped(store, "tenant:a")
    assert isinstance(other, FileStore)
    assert other.path.name == "tenant:a.jsonl"
    assert other.path != store.path


def test_an_explicit_file_path_falls_back_to_filtering(tmp_path):
    """One file for every tenant is a shared store, so it must be filtered."""
    store = FileStore(tmp_path / "everything.jsonl")
    assert isinstance(scoped(store, "tenant:a"), NamespacedStore)


def test_scoping_an_already_scoped_store_does_not_nest():
    store = InMemoryStore()
    once = scoped(store, "tenant:a")
    twice = scoped(once, "tenant:b")
    assert isinstance(twice, NamespacedStore)
    assert twice.inner is store
    assert twice.namespace == "tenant:b"


async def test_namespaced_stores_report_what_they_wrap():
    assert scoped(InMemoryStore(), "tenant:a").name == "in_memory[tenant:a]"


async def test_isolation_holds_across_every_memory_kind():
    store = InMemoryStore()
    a = Memory(namespace="tenant:a", store=store)
    b = Memory(namespace="tenant:b", store=store)
    kinds = (MemoryKind.SHORT_TERM, MemoryKind.EPISODIC, MemoryKind.SEMANTIC)
    for kind in kinds:
        await a.remember(f"{kind.value} secret", kind=kind)
    for kind in kinds:
        assert await b.items(kind) == [], kind
        assert len(await a.items(kind)) == 1, kind


@pytest.mark.parametrize("namespace", ["tenant:a", "tenant/b", "tenant.c", "default"])
async def test_namespaces_survive_a_round_trip_through_the_file_store(namespace, rewyn_home):
    memory = Memory(namespace=namespace, store=FileStore())
    await memory.remember("persisted fact")
    reopened = Memory(namespace=namespace, store=FileStore())
    assert len(await reopened.recall("persisted")) == 1
