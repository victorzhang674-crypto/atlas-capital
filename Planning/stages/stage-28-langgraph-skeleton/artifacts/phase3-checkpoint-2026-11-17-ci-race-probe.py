"""Probe: the Stage 28 kill-resume test against a worker that sleeps 3 s between
logging "EEE director done" and returning (so the director's write is saved 3 s later)."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/zzjsmacbookair/Projects/atlas-capital/.claude/worktrees/phase-3-checkpoint-stages-24-28-e324d2/tests/unit/orchestration")
import test_orchestration_checkpoint as t

def test_kill_resume_with_a_slow_write(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "HERE", Path(__file__).resolve().parent)
    t.test_a_killed_process_resumes_from_its_last_checkpoint(tmp_path)
