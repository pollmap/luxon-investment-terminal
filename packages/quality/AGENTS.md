# Quality Agent Rules

- Quality checks attach flags; they do not silently repair source facts.
- Preserve missing, stale, conflicting unit/currency, period mismatch,
  restatement, inferred, and fallback conditions as auditable flags.
- No-source production values must be rejected or clearly blocked.
- Add regression tests for each new quality flag or gate.
