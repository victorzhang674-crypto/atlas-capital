# Stage 31 — mutation testing (audit, 2026-11-28)

**Result: 707 of 717 mutants killed (98.6%).** The 10 left are all equivalent (reasons in
[final-survivors-classified.tsv](final-survivors-classified.tsv)). No untested edge is left.

## Setup
- mutmut 3.8.1 as a throwaway tool on a scratch copy of the worktree ([mutmut-setup.cfg](mutmut-setup.cfg));
  installed with its dependencies into a scratch folder (`uv pip install --target`), run with the
  worktree's Python and `PYTHONPATH=<copy>/mutants/src:<mutlib>`.
- Mutated: `src/atlas/agents/tools/news_tools.py`, `src/atlas/agents/research/news.py`.
- Tests: `test_news.py`, `test_news_tools.py`, `test_news_tools_pit.py`, `test_news_config.py`.

## Runs
1. First run (719 mutants): 645 killed, 6 survived, 68 "suspicious" (mutmut's forked workers end with SIGTRAP on this Mac when the tests pass). Re-run in plain processes ([classify.sh](classify.sh), [classify-log-1.txt](classify-log-1.txt)): 72 of the 74 survived. Read one by one ([survivor-diffs-1.txt](survivor-diffs-1.txt)): mostly message text and API-default arguments, but also real gaps.
2. Tests and one cleanup added for the gaps: exact problem texts (they enter the repair turn and so the LLM cache key); the empty-news guard (`news_flow` only, problems added to the others, the cap boundary); risks and summary uncited numbers listed together; a custom tool registry kept; the memory asked for this agent, query and symbol; every tool refuses a blocked symbol; `ex-dividend` and unknown time-of-day texts; the limit passed to the view; a peer without volume ranks last; the smallest reaction keeps its sign and a zero return is not positive; prefetch gap names. The unused `settings` parameter of `catalyst_problems` was removed (a mutant showed nothing used it).
3. Second run on the changed code (717 mutants): 700 killed, 17 suspicious ([mutmut-results.txt](mutmut-results.txt)); plain re-run: 17 survived ([classify-log.txt](classify-log.txt)).
4. Five more tests; 7 of the 17 killed ([classify-log2.txt](classify-log2.txt)). 10 left, all equivalent.

Killed: 700 + 7 = 707.
