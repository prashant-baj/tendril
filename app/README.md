# app/

Application services (Lambda-based): ingestion, orchestrator glue, tracker/scheduler, notification, and shared code.

- `common/` — shared helpers (config loading, logging). No secrets in code — config comes from env / SSM / Secrets Manager.

See [architecture](../docs/architecture/architecture.md) and [ADR-0004](../docs/architecture/ADRs/0004-backend-api-serverless-storage.md).
