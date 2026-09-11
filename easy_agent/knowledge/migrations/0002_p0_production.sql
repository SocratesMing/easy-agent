-- EasyAgent knowledge schema v2: controlled-production baseline.
-- Statements intentionally use the SQLite/MySQL common subset. Existing-table
-- columns and indexes are added by the migration runner after catalog checks.

CREATE TABLE IF NOT EXISTS knowledge_tasks (
    id VARCHAR(255) PRIMARY KEY,
    operation_id VARCHAR(255) NOT NULL,
    task_type VARCHAR(40) NOT NULL,
    resource_type VARCHAR(40) NOT NULL,
    resource_id VARCHAR(255) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    status VARCHAR(30) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at VARCHAR(50) NOT NULL,
    locked_by VARCHAR(255),
    locked_at VARCHAR(50),
    heartbeat_at VARCHAR(50),
    timeout_at VARCHAR(50),
    request_id VARCHAR(128) NOT NULL,
    created_by VARCHAR(255) NOT NULL,
    last_error_code VARCHAR(100),
    last_error_message TEXT,
    created_at VARCHAR(50) NOT NULL,
    updated_at VARCHAR(50) NOT NULL,
    completed_at VARCHAR(50),
    FOREIGN KEY (operation_id) REFERENCES knowledge_operations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_audit_events (
    id VARCHAR(255) PRIMARY KEY,
    request_id VARCHAR(128) NOT NULL,
    actor_user_id VARCHAR(255) NOT NULL,
    actor_username VARCHAR(255) NOT NULL,
    action VARCHAR(80) NOT NULL,
    object_type VARCHAR(40) NOT NULL,
    object_id VARCHAR(255) NOT NULL,
    base_id VARCHAR(255),
    outcome VARCHAR(20) NOT NULL,
    reason_code VARCHAR(100),
    details_json TEXT NOT NULL,
    created_at VARCHAR(50) NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_reconciliation_runs (
    id VARCHAR(255) PRIMARY KEY,
    status VARCHAR(30) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    summary_json TEXT NOT NULL,
    error_message TEXT,
    started_at VARCHAR(50) NOT NULL,
    completed_at VARCHAR(50)
);

CREATE TABLE IF NOT EXISTS knowledge_reconciliation_issues (
    id VARCHAR(255) PRIMARY KEY,
    run_id VARCHAR(255) NOT NULL,
    issue_key VARCHAR(512) NOT NULL,
    issue_type VARCHAR(60) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    base_id VARCHAR(255),
    document_id VARCHAR(255),
    status VARCHAR(20) NOT NULL,
    details_json TEXT NOT NULL,
    first_seen_at VARCHAR(50) NOT NULL,
    last_seen_at VARCHAR(50) NOT NULL,
    resolved_at VARCHAR(50),
    resolution_note TEXT,
    FOREIGN KEY (run_id) REFERENCES knowledge_reconciliation_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_runtime_heartbeats (
    component VARCHAR(80) NOT NULL,
    instance_id VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL,
    details_json TEXT NOT NULL,
    last_seen_at VARCHAR(50) NOT NULL,
    PRIMARY KEY (component, instance_id)
);

CREATE TABLE IF NOT EXISTS knowledge_alerts (
    id VARCHAR(255) PRIMARY KEY,
    dedup_key VARCHAR(255) NOT NULL UNIQUE,
    alert_type VARCHAR(80) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    summary VARCHAR(500) NOT NULL,
    details_json TEXT NOT NULL,
    first_seen_at VARCHAR(50) NOT NULL,
    last_seen_at VARCHAR(50) NOT NULL,
    resolved_at VARCHAR(50)
);
