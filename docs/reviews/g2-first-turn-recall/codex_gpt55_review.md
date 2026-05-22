VERDICT: APPROVED

Non-blocking suggestions:

- Clarify mandatory no-candidate behavior: whether it injects an empty recall block, a degraded/no-results notice, or only emits `recall_skipped`.
- Define “bit-identical” rollback test scope as the assembled first API request payload, excluding unrelated timestamps/log metadata.
- Consider naming the config default explicitly as implementation-local to MacBook Rilo so it cannot be read as a repo-wide default for every Hermes instance.