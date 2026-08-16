# API Agent Rules

- API responses for financial values must preserve source_trace, formula,
  method, confidence, quality flags, and period/currency/unit fields.
- Do not invent or backfill financial numbers in route handlers.
- Keep fixture responses clearly labeled as non-production.
- Vercel request paths should read internal DB/warehouse rows, not perform live
  long-running ingestion.
- Run focused pytest coverage for touched routes and contracts before commit.
