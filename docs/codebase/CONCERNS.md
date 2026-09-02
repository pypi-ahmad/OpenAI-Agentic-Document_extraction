# Concerns

- Current calibration reports zero safe primary acceptances; cost/latency are therefore high.
- Human review is signaled but not durably recorded.
- Selected raster images remain in memory during a run, although a total pixel budget now
  rejects oversized batches.
- Live OCR regressions require credentialed GroundTruth evaluation.
- Compliance and retention depend on deployment context absent from this repository.

## Evidence

- Routing mode derives from calibration acceptance: `load_quality_profile` in
  `src/ade_app/quality.py` and
  `profiles/segment-quality-terra-v3.json` (`validation.accepted_count`).
- Batch pixel budget: `extract_documents` in `src/ade_app/batch.py`.
- Review state is manifest data, not a reviewer ledger: manifest models in
  `src/ade_app/provenance.py`.
- Credentialed evaluation entry point: `run_evaluation` in `src/ade_app/evaluation.py`.

## Checkpoints

- Recalibrate before claiming selective routing or improved cost.
- Treat `review_required` as a handoff signal, not proof that review occurred.
- Define retention, access control, and audit ownership before network deployment.
