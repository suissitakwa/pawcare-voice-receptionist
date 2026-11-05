# Evaluations

Three kinds of evidence are kept apart and are never reported as one another:

| Layer | Location | Measures | Status |
|---|---|---|---|
| Automated backend tests | `backend/tests/` | Rules and booking correctness, no model involved | Running in CI |
| Scripted agent evaluations | `evals/scenarios/` | Model and tool decisions from text transcripts, no audio | Planned |
| Manual audio tests | `evals/manual/` | Real browser voice sessions, logged by hand | Planned |

Scripted evaluation results are not voice-call measurements.
