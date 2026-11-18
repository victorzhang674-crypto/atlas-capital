#!/bin/zsh
# Phase 3 checkpoint, Tier 2: full checks. Run from the worktree root.
export UV_CACHE_DIR=${UV_CACHE_DIR:-$TMPDIR/uv-cache-p3} UV_NO_SYNC=1
A=Planning/stages/stage-28-langgraph-skeleton/artifacts; P=phase3-checkpoint-2026-11-17-tier2
F="grep -av -E nanobind|leaked|^ - |^See https"
run() { local out=$1; shift; "$@" > $A/$P-$out.txt 2>&1; echo "exit $?" >> $A/$P-$out.txt; }
run pytest uv run pytest -q -p no:cacheprovider
run make-check make check
run verify-llm uv run pytest tests/unit/llm -q -p no:cacheprovider
run verify-agents uv run pytest tests/unit/agents -q -p no:cacheprovider
run verify-memory uv run pytest tests/unit/agents/memory -q -p no:cacheprovider
run verify-orchestration uv run pytest tests/unit/orchestration -q -p no:cacheprovider
run integration uv run pytest -m integration -q -rs -p no:cacheprovider
# Coverage in two parts: the whole memory folder aborts under the coverage tracer (exit 134,
# chromadb / onnxruntime), so memory is measured on the files that reach the changed lines.
run coverage uv run pytest -q -p no:cacheprovider tests/unit/agents --ignore=tests/unit/agents/memory tests/unit/llm tests/unit/orchestration --cov=src/atlas/agents --cov=src/atlas/llm --cov=src/atlas/orchestration --cov-report=term-missing
run coverage-memory uv run pytest -q -p no:cacheprovider tests/unit/agents/memory/test_memory_seeded_ids.py tests/unit/agents/memory/test_memory_retrieval.py --cov=src/atlas/agents/memory --cov-report=term-missing
