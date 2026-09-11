-- EasyAgent knowledge schema v3: admin-managed team-space manager allowlist.
-- Only active users with a department are effective managers; that rule is
-- enforced by the repository so disabling an account revokes access at once.

CREATE TABLE IF NOT EXISTS knowledge_team_space_managers (
    user_id VARCHAR(255) PRIMARY KEY,
    granted_by VARCHAR(255) NOT NULL,
    granted_at VARCHAR(50) NOT NULL,
    updated_at VARCHAR(50) NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
