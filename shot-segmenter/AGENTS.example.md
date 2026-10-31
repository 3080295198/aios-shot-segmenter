# Project development rules

These rules are a sanitized example summarized during this assignment.

## Delivery

- Run the command against real sample inputs before claiming completion.
- Verify expected files exist and open local report links after generation.
- Run unit tests in addition to, not instead of, end-to-end checks.

## Batch failures

- Catch failures at one-video granularity and continue processing later videos.
- Record source file, processing stage, concise reason, and traceback in a machine-readable file.
- Write partial results to a temporary directory and publish only after validation.

## Data hygiene

- Never commit credentials, customer data, internal documents, or source recruitment materials.
- Keep test fixtures synthetic and minimal.
- Store thresholds in explicit parameters and record them in every result manifest.

## Reviewable outputs

- Keep timestamps, scores, decision method, and confidence for every boundary.
- Produce preview and before/after evidence so a human can audit the automatic result.
- Mark uncertain results for review instead of silently treating them as ground truth.

