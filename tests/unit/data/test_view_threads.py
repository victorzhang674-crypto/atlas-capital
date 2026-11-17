"""Phase 3 checkpoint (Stage 13 follow-up): one `DataView` read from several threads.

The Stage 28 research graph runs agents on worker threads (`orchestration.max_concurrency`),
and they share the day's view. Its caches must then hold: no error under eviction, one
computation per key, the same values as a single reader.
"""

import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from test_view_pit import METHODS, _norm
from view_helpers import WORLD_AS_OF, build_world, repo_concept_map, world_catalog, world_lexicon

from atlas.core.config import AtlasConfig
from atlas.core.storage import DEFAULT_REGISTRY, Lake
from atlas.data.view import _LRU, DataView

THREADS = 4


@pytest.fixture
def fast_switching() -> Iterator[None]:
    """Make the interpreter switch threads very often, so races show up within a short test."""
    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        yield
    finally:
        sys.setswitchinterval(before)


def _run(workers: list[Callable[[], None]]) -> list[BaseException]:
    """Run `workers` on one thread each; return what they raised."""
    raised: list[BaseException] = []

    def guard(work: Callable[[], None]) -> None:
        try:
            work()
        except BaseException as exc:
            raised.append(exc)

    threads = [threading.Thread(target=guard, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a reader thread is stuck"
    return raised


def test_lru_survives_concurrent_readers_under_eviction(fast_switching: None) -> None:
    lru = _LRU(2)  # five keys through two slots: every reader keeps evicting the others' keys

    def reader(offset: int) -> Callable[[], None]:
        def work() -> None:
            for i in range(50_000):
                key = (i + offset) % 5
                assert lru.get(key, lambda key=key: key * 10) == key * 10

        return work

    assert _run([reader(n) for n in range(THREADS)]) == []
    info = lru.info()
    assert info.size <= 2 and info.hits + info.misses == THREADS * 50_000


def test_lru_computes_a_key_once_for_concurrent_callers() -> None:
    lru = _LRU(8)
    started, release = threading.Event(), threading.Event()
    computed: list[int] = []
    got: list[object] = []

    def compute() -> object:
        computed.append(1)
        started.set()
        assert release.wait(timeout=30)
        return object()

    def first() -> None:
        got.append(lru.get("k", compute))

    def later() -> None:
        assert started.wait(timeout=30)
        got.append(lru.get("k", compute))

    def opener() -> None:
        assert started.wait(timeout=30)
        # The later readers are (about to be) waiting for the first one's result.
        threading.Event().wait(0.2)
        release.set()

    assert _run([first, later, later, later, opener]) == []
    assert len(computed) == 1  # the three later readers waited instead of computing again
    assert len(got) == 4 and all(value is got[0] for value in got)
    assert lru.info().misses == 1 and lru.info().hits == 3


def test_lru_does_not_block_other_keys_while_one_is_computed() -> None:
    lru = _LRU(8)
    started, release = threading.Event(), threading.Event()

    def slow() -> None:
        def compute() -> int:
            started.set()
            assert release.wait(timeout=30)
            return 1

        assert lru.get("slow", compute) == 1

    def other() -> None:
        assert started.wait(timeout=30)
        assert lru.get("fast", lambda: 2) == 2  # must not wait for "slow"
        release.set()

    assert _run([slow, other]) == []


def test_lru_failed_computation_is_not_cached_and_does_not_strand_waiters() -> None:
    lru = _LRU(8)
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def failing() -> int:
        calls.append("failing")
        started.set()
        assert release.wait(timeout=30)
        raise RuntimeError("boom")

    def working() -> int:
        calls.append("working")
        return 7

    outcome: dict[str, object] = {}

    def first() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            lru.get("k", failing)

    def waiter() -> None:
        assert started.wait(timeout=30)
        outcome["waiter"] = lru.get("k", working)  # waits, then computes itself

    def opener() -> None:
        assert started.wait(timeout=30)
        threading.Event().wait(0.2)
        release.set()

    assert _run([first, waiter, opener]) == []
    assert outcome == {"waiter": 7} and calls == ["failing", "working"]
    assert lru.get("k", failing) == 7  # the working result is what stays cached


def test_lru_nested_computations_of_other_keys_work() -> None:
    lru = _LRU(8)
    assert lru.get("outer", lambda: lru.get("inner", lambda: 3) + 1) == 4
    assert lru.get("inner", lambda: 0) == 3


@pytest.fixture(scope="module")
def lake(tmp_path_factory: pytest.TempPathFactory) -> tuple[Lake, Any, Any, Any]:
    root: Path = tmp_path_factory.mktemp("threads-world")
    lake = Lake(root / "clean", registry=DEFAULT_REGISTRY.copy())
    build_world(lake, future=False)
    return lake, world_lexicon(root), repo_concept_map(), world_catalog()


def _view(lake: tuple[Lake, Any, Any, Any], **view_settings: int) -> DataView:
    store, lexicon, concept_map, catalog = lake
    config = AtlasConfig()
    if view_settings:
        view = config.data.view.model_copy(update=view_settings)
        config = config.model_copy(update={"data": config.data.model_copy(update={"view": view})})
    return DataView(
        store, WORLD_AS_OF, config, concept_map=concept_map, catalog=catalog, lexicon=lexicon
    )


def test_every_view_method_gives_the_same_answer_on_four_threads(
    lake: tuple[Lake, Any, Any, Any], fast_switching: None
) -> None:
    expected = {name: _norm(call(_view(lake))) for name, call in METHODS.items()}
    # Two cache slots for 26 methods: the readers evict each other's results all the time.
    shared = _view(lake, cache_size=2, fundamentals_cache_size=2)
    names = sorted(METHODS)

    def reader(offset: int) -> Callable[[], None]:
        def work() -> None:
            for round_ in range(3):
                for i in range(len(names)):
                    name = names[(i + offset * 7 + round_) % len(names)]
                    assert _norm(METHODS[name](shared)) == expected[name], name

        return work

    assert _run([reader(n) for n in range(THREADS)]) == []


def test_a_view_builds_each_reader_once_for_concurrent_callers(
    lake: tuple[Lake, Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import atlas.data.view as view_module

    built: list[int] = []
    gate = threading.Barrier(THREADS)
    real = view_module.News

    def counting(*args: Any, **kwargs: Any) -> Any:
        built.append(1)
        threading.Event().wait(0.05)  # keep the build open while the others arrive
        return real(*args, **kwargs)

    monkeypatch.setattr(view_module, "News", counting)
    shared = _view(lake)

    def reader() -> None:
        gate.wait(timeout=30)
        shared.news("AAA")

    assert _run([reader] * THREADS) == []
    assert len(built) == 1


def test_lru_refuses_a_key_whose_computation_needs_itself() -> None:
    lru = _LRU(8)
    with pytest.raises(RuntimeError, match="needs itself"):
        lru.get("k", lambda: lru.get("k", lambda: 1))
    assert lru.get("k", lambda: 2) == 2  # nothing half-done was left behind
