# RAGFlow v0.17.2 contract fixtures

Sanitized golden payloads for the API contract pinned by the knowledge-engineering
adapter. They intentionally cover behaviors that are easy to mis-handle:

- document `run` values `0..4`;
- retrieval uses `document_id` and request-side `document_ids`;
- delete success may omit `data`;
- authentication failure may be HTTP 200 with `code=0` and `data=false`.

These fixtures contain no bank endpoint, tenant identifier, document text, or
credential. Bank-specific captures belong in a separately controlled fixture set.
