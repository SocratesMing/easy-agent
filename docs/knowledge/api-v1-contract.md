# Knowledge Engineering API v1 — P0 production baseline

Status: **implemented for the controlled-production P0 baseline**. The browser and Agent runtime use
this EasyAgent BFF; RAGFlow-specific identifiers and compatibility parameters
remain server-side.

## Boundary rules

- Prefix: `/api/knowledge/v1`.
- Browser and Agent tools submit only EasyAgent-local IDs. RAGFlow Dataset,
  Document, Chunk IDs, endpoints, error codes, and parser fields are never public.
- Every resource response carries backend-computed `allowed_actions`; the frontend
  does not reproduce authorization policy.
- All errors use `code/message/details/retryable/request_id`.
- Empty explicit document filters are rejected. The backend never broadens an
  empty authorized scope to a Dataset-wide retrieval.
- Cursor/page state is stable and supplier-independent.

The executable DTO definitions live in
`easy_agent/knowledge/models.py`; state vocabularies live in
`easy_agent/knowledge/domain/models.py`.

## Bank upstream contract

The server-side upstream client follows `ragflow接口文档/行内api.docx` as its
canonical contract. Dataset and document management use the bank RAG service;
retrieval uses the separately configured AaaS `rag_retrieval` endpoint. Every
upstream request includes the allocated uppercase `sys-code`. Retrieval also
adds `stdAuthToken`, `stdRqtSyt`, `stdRspSys`, `stdTransId`,
`stdApplySystTmtp`, and `stdBankNum` on the server. None of these values can be
supplied by the browser.

Local RAGFlow v0.17 testing is implemented only by
`easy_agent/knowledge/ragflow/local_v017_bridge.py`. The bridge redirects the
bank routes to the local API and converts only the known local parser/model
differences. It must be disabled for in-bank deployment.

## Endpoint inventory

| Area | Method and path | Initial response |
|---|---|---|
| Capability | `GET /capabilities`, `GET /status` | Capability and upstream health |
| Bases | `GET /bases` | `KnowledgeBaseListResponse` |
| Bases | `POST /bases` | `201 + KnowledgeBaseSummary` |
| Bases | `GET/PATCH/DELETE /bases/{base_id}` | Base detail / `204` |
| Bases | `POST /bases/{base_id}/restore` | Restored base detail |
| Folders | `GET/POST /bases/{base_id}/folders` | Folder list/detail |
| Folders | `PATCH/DELETE /folders/{folder_id}` | Folder detail / `204` |
| Documents | `GET /bases/{base_id}/documents` | Cursor document page |
| Documents | `POST /bases/{base_id}/documents` | `202 + DocumentUploadAccepted` |
| Documents | `GET /documents/{document_id}/content` | Authorized byte stream |
| Documents | `PATCH/DELETE /documents/{document_id}` | Move / `204` |
| Documents | `POST /documents/{document_id}/retry` | `202 + DocumentUploadAccepted` |
| Documents | `POST /documents/{document_id}/restore` | `202 + DocumentUploadAccepted` |
| Operations | `GET /operations` | Cursor operation page |
| Operations | `GET /operations/{operation_id}` | `OperationSummary` |
| Permission | `GET/PUT /bases/{base_id}/permissions` | Permission set |
| Permission | `GET /bases/{base_id}/permission-subjects` | User/department candidates |
| Retrieval | `POST /bases/{base_id}/retrieve` | `RetrieveResponse` |
| Ask | `POST /bases/{base_id}/ask` | `AskResponse` with evidence |
| Session scope | `GET/PUT /sessions/{session_id}/knowledge-scope` | Local base IDs |
| Administration | `GET /admin/health`, `/admin/metrics` | Health JSON / Prometheus text |
| Administration | `GET /admin/audits`, `/admin/alerts`, `/admin/reconciliation/*` | Admin-only operational records |

## Module capability behavior

`GET /capabilities` is always registered and does not require RAGFlow to be
reachable. When no independent config is mounted it returns:

```json
{
  "schema_version": 1,
  "api_version": "v1",
  "enabled": false,
  "status": "disabled",
  "upstream_capabilities": [],
  "features": {}
}
```

The response never includes endpoint, credential, certificate, tenant Header, or
raw compatibility fields. Authenticated `GET /status` performs the safe upstream
probe and reports `ready` or `degraded`.

## Upload and operation tracking

The first-phase upload accepts one multipart `file` and optional `folder_id` per
request. It creates a durable local document and operation, then returns both in
`DocumentUploadAccepted` with HTTP 202. The UI uploads a multi-file selection as
independent requests and monitors their operation IDs in the task center.

The server reports `uploading`, `configuring`, and `parsing` as operation phases.
Failed documents can be retried explicitly through `/documents/{id}/retry`.
Upload, retry and delete side effects are persisted in MySQL and processed by
the separate Worker. Documents and bases are soft-deleted first; their restore
routes remain available until `purge_after`.

## Ask and chat streaming

`POST /bases/{base_id}/ask` accepts `AskRequest` and returns one `AskResponse`
containing the answer and its authorized evidence. The regular Agent chat stream
uses the saved session knowledge scope and emits knowledge events in this order:

1. `metadata` — stable `request_id`;
2. zero or more `answer.delta` events;
3. one `evidence` event, with only authorized local documents;
4. zero or more `warning` events;
5. exactly one terminal `done` or `error` event.

Cancellation uses the browser request abort signal. The first phase does not store
a separate Ask history. Page is nullable; the UI must not synthesize one when the
upstream position cannot be mapped reliably.

## Content proxy

`GET /documents/{document_id}/content` is a BFF stream after tenant and resource
authorization. The implementation must preserve safe `Content-Type`, sanitized
`Content-Disposition`, Range semantics, inline/download mode, and a citation
locator. It never accepts a filesystem path or remote RAGFlow ID from the client.

## Deployment-specific acceptance still required

- the bank gateway's final certificate, authentication values, timeout and traffic-control values;
- the first controlled-production identity source (approved roster import or SSO);
- business sign-off for representative financial-report answers and citations.

These values are deployment configuration or business acceptance; the API never
accepts them from the browser and the local v0.17 bridge remains removable.
