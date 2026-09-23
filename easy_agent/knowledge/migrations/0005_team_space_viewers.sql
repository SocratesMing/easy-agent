-- EasyAgent knowledge schema v5: admin-managed team-space viewer allowlist.
-- Team knowledge bases are visible only to granted viewers (plus managers,
-- owners and admin); the old "same department sees everything" default is
-- revoked in favour of this explicit fail-closed grant.

CREATE TABLE IF NOT EXISTS knowledge_team_space_viewers (
    user_id VARCHAR(255) PRIMARY KEY,
    granted_by VARCHAR(255) NOT NULL,
    granted_at VARCHAR(50) NOT NULL,
    updated_at VARCHAR(50) NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
