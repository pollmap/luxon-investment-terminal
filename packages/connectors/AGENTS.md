# Connectors Agent Rules

- Connectors collect source evidence; they do not decide valuation conclusions.
- Store raw payload metadata with source, URL/document id, retrieval time, hash,
  unit/currency context, and parser version where applicable.
- Respect source terms and rate limits. Do not scrape licensed premium services.
- Keep tests offline with fixtures unless a command is explicitly a live dry run.
