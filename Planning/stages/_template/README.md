# Stage folder template

At the start of a stage, after its gate check passes (Project.md Rule 4), the stage chat copies these files into its own folder `Planning/stages/stage-NN-<slug>/`, next to `brief.md`:

| File | Written when | Contents |
|---|---|---|
| `brief.md` | Already present | The stage brief: goal, scope, tasks, acceptance criteria, Verify commands |
| `plan.md` | Before any code | The step-by-step TDD plan expanded from the brief |
| `worklog.md` | During the stage | A dated, in-order record of what was done, what failed and how it was fixed |
| `verification.md` | Before claiming DONE | Real command output from this chat plus evidence for each acceptance criterion |
| `artifacts/` | As produced | Logs, test and coverage output, reports, screenshots and other non-code outputs of the stage |

Code and tests do **not** go here. They live in the shared `src/atlas/` and `tests/` trees, because later stages import earlier code.

Never create these files for a stage that has not started (Project.md Rule 4).
