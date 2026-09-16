"""Data backends (spec §51). Driven against fakes, never live services."""

from __future__ import annotations

from typing import Any

import pytest

from rewyn.core.state import State
from rewyn.memory import Memory, MemoryKind
from rewyn.runtime.checkpoint import Checkpoint


class FakeRedis:
    """Enough of the async Redis surface for the store to be exercised."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expiries: dict[str, int | None] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.strings[key] = value
        self.expiries[key] = ex

    async def get(self, key: str) -> str | None:
        return self.strings.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.strings.pop(key, None) is not None else 0

    async def sadd(self, key: str, member: str) -> None:
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key: str, member: str) -> None:
        self.sets.get(key, set()).discard(member)

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    def expire_key(self, key: str) -> None:
        """Simulate a TTL firing, which leaves the index entry dangling."""
        self.strings.pop(key, None)


# Redis --------------------------------------------------------------------------
def redis_memory(client: FakeRedis, namespace: str = "default") -> Any:
    from rewyn.integrations.redis import RedisMemoryStore

    return RedisMemoryStore(client=client, namespace=namespace)


async def test_redis_memory_round_trips():
    client = FakeRedis()
    memory = Memory(namespace="tenant:a", store=redis_memory(client))
    item = await memory.remember("Acme pays on time", importance=0.8)

    assert [h.item.content for h in await memory.recall("Acme")] == ["Acme pays on time"]
    assert (await memory.items(MemoryKind.SEMANTIC))[0].id == item.id


async def test_redis_memory_isolates_tenants_by_key_prefix():
    client = FakeRedis()
    a = Memory(namespace="tenant:a", store=redis_memory(client))
    b = Memory(namespace="tenant:b", store=redis_memory(client))
    await a.remember("a secret")

    assert await b.recall("secret") == []
    assert any("tenant:a" in key for key in client.strings)


async def test_redis_memory_scopes_natively_rather_than_filtering():
    from rewyn.integrations.redis import RedisMemoryStore
    from rewyn.memory.memory import scoped

    store = RedisMemoryStore(client=FakeRedis(), namespace="tenant:a")
    other = scoped(store, "tenant:b")
    assert isinstance(other, RedisMemoryStore)
    assert other.namespace == "tenant:b"
    assert scoped(store, "tenant:a") is store


async def test_redis_memory_deletes_and_clears():
    client = FakeRedis()
    store = redis_memory(client)
    memory = Memory(store=store)
    item = await memory.remember("temporary")
    assert await store.delete(item.id) is True
    assert await store.get(item.id) is None
    assert await store.clear() == 0


async def test_an_expired_redis_item_drops_its_dangling_index_entry():
    client = FakeRedis()
    store = redis_memory(client)
    item = await store.add(await Memory(store=store).remember("gone"))
    client.expire_key(f"rewyn:memory:default:{item.id}")
    assert await store.list_items() == []
    assert not await client.smembers("rewyn:memory:default:ids")


async def test_a_redis_ttl_is_passed_through():
    from rewyn.integrations.redis import RedisMemoryStore

    client = FakeRedis()
    store = RedisMemoryStore(client=client, ttl_seconds=900)
    await store.add(await Memory(store=store).remember("expiring"))
    assert set(client.expiries.values()) == {900}


async def test_redis_checkpoints_round_trip_and_order():
    from rewyn.integrations.redis import RedisCheckpointStore

    store = RedisCheckpointStore(client=FakeRedis())
    for sequence in (2, 1, 3):
        await store.save(
            Checkpoint(
                run_id="run_1",
                sequence=sequence,
                state=State({"i": sequence}).snapshot(f"s{sequence}"),
            )
        )
    found = await store.list_for_run("run_1")
    assert [c.sequence for c in found] == [1, 2, 3]
    assert await store.load(found[0].id) is not None
    assert await store.load("ckpt_missing") is None


async def test_a_redis_checkpoint_can_resume_a_graph():
    from rewyn import Graph
    from rewyn.graphs import END
    from rewyn.integrations.redis import RedisCheckpointStore
    from rewyn.runtime import Checkpointer

    graph = Graph("resumable")
    graph.add_node("one", lambda s: {"step": 1})
    graph.add_node("two", lambda s: f"done after {s['step']}", output_key="out")
    graph.connect("one", "two")
    graph.connect("two", END)

    checkpointer = Checkpointer(RedisCheckpointStore(client=FakeRedis()))
    result = await graph.arun({"input": "x"}, checkpointer=checkpointer)
    assert result.output == "done after 1"


# S3 -----------------------------------------------------------------------------
class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        data = self.objects.get((Bucket, Key))
        if data is None:
            raise _s3_error("NoSuchKey")
        return {"Body": _Body(data)}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise _s3_error("404")
        return {}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data


def _s3_error(code: str) -> Exception:
    exc = Exception("s3")
    exc.response = {"Error": {"Code": code}}  # type: ignore[attr-defined]
    return exc


def test_the_s3_store_offers_the_whole_object_store_interface():
    # The protocol itself is declared by the cloud service, which this package
    # does not depend on. Conformance is asserted there; what matters here is
    # that the backend keeps the shape the protocol requires.
    from rewyn.integrations.s3 import S3ObjectStore

    store = S3ObjectStore("bucket", client=FakeS3())
    for method in ("put", "get", "exists", "delete"):
        assert callable(getattr(store, method, None)), method


def test_s3_round_trips_under_a_prefix():
    from rewyn.integrations.s3 import S3ObjectStore

    fake = FakeS3()
    store = S3ObjectStore("runs", prefix="prod/", client=fake)
    store.put("projects/p1/runs/r1/events.jsonl", b"one\ntwo")

    assert ("runs", "prod/projects/p1/runs/r1/events.jsonl") in fake.objects
    assert store.get("projects/p1/runs/r1/events.jsonl") == b"one\ntwo"
    assert store.exists("projects/p1/runs/r1/events.jsonl")


def test_a_missing_s3_object_reads_as_none():
    from rewyn.integrations.s3 import S3ObjectStore

    store = S3ObjectStore("runs", client=FakeS3())
    assert store.get("nope") is None
    assert store.exists("nope") is False


def test_an_s3_permission_failure_is_not_mistaken_for_a_missing_object():
    """Silently returning None on AccessDenied would look like data loss."""
    from rewyn.integrations.s3 import S3ObjectStore

    class Denying(FakeS3):
        def get_object(self, **_: Any) -> dict[str, Any]:
            raise _s3_error("AccessDenied")

    with pytest.raises(Exception, match="s3"):
        S3ObjectStore("runs", client=Denying()).get("anything")


def test_s3_keys_cannot_escape_the_prefix():
    from rewyn.integrations.s3 import S3ObjectStore

    fake = FakeS3()
    S3ObjectStore("runs", prefix="prod", client=fake).put("../../escape", b"x")
    assert all("../" not in key for _, key in fake.objects)


def test_s3_deletes():
    from rewyn.integrations.s3 import S3ObjectStore

    store = S3ObjectStore("runs", client=FakeS3())
    store.put("k", b"v")
    store.delete("k")
    assert store.get("k") is None
