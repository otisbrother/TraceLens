# Contracts

Pydantic contracts live in `apps/api/contracts.py`; HTTP schema is served at
`/openapi.json`. TypeScript view types live in `apps/web/src/types.ts`.
Tenant/role/snapshot/actor are server-derived, not writable tool arguments.
