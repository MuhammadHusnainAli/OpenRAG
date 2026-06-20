
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "citext";     -- case-insensitive email column

--  Enums 
DO $$ BEGIN
    CREATE TYPE auth_provider AS ENUM ('password', 'google', 'microsoft', 'github');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE doc_status AS ENUM ('pending', 'processing', 'ready', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE agent_visibility AS ENUM ('private', 'restricted', 'public');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE agent_version_status AS ENUM ('draft', 'test', 'live', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

--  Core: users 
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           CITEXT NOT NULL UNIQUE,
    email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
    password_hash   VARCHAR(255),                       -- NULL for OAuth-only accounts
    display_name    VARCHAR(120),
    avatar_url      VARCHAR(500),                        -- from OAuth provider profile
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    is_superuser    BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);

--  OAuth account links 
CREATE TABLE IF NOT EXISTS oauth_accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider            auth_provider NOT NULL,
    provider_account_id VARCHAR(255) NOT NULL,          -- 'sub'/'oid'/id from the IdP
    email               CITEXT,                          -- provider-reported email
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_oauth_provider_account UNIQUE (provider, provider_account_id)
);
CREATE INDEX IF NOT EXISTS ix_oauth_user_id ON oauth_accounts (user_id);

--  Conversations (chat sessions) 
CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(200) NOT NULL DEFAULT 'New chat',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_conversations_user_id ON conversations (user_id);

--  Documents (one row per uploaded file, vectors live in Qdrant) 
CREATE TABLE IF NOT EXISTS documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename        VARCHAR(255) NOT NULL,
    content_type    VARCHAR(100) NOT NULL,
    size_bytes      INTEGER NOT NULL,
    sha256          VARCHAR(64) NOT NULL,
    status          doc_status NOT NULL DEFAULT 'pending',
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    storage_path    VARCHAR(500) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- dedupe identical files within one conversation
    CONSTRAINT uq_document_conv_sha UNIQUE (conversation_id, sha256)
);
CREATE INDEX IF NOT EXISTS ix_documents_conversation_id ON documents (conversation_id);
CREATE INDEX IF NOT EXISTS ix_documents_user_id ON documents (user_id);
CREATE INDEX IF NOT EXISTS ix_documents_sha256 ON documents (sha256);

--  Messages (chat history persisted in Postgres) 
CREATE TABLE IF NOT EXISTS messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL,               -- user | assistant | tool | system
    content         TEXT NOT NULL,
    citations       JSONB,                              -- [{document_id, chunk_index, score, source}]
    token_usage     JSONB,                              -- {input, output, total}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id);
CREATE INDEX IF NOT EXISTS ix_messages_conv_created ON messages (conversation_id, created_at);



-- CUSTOM AGENTS

CREATE TABLE IF NOT EXISTS agents (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name               VARCHAR(150) NOT NULL,
    description        TEXT,
    visibility         agent_visibility NOT NULL DEFAULT 'private',
    public_slug        VARCHAR(64) UNIQUE,                 -- set when shared publicly
    public_key_hash    VARCHAR(64),                        -- sha256 of the access key
    default_version_id UUID,                               -- FK added after agent_versions
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agents_owner_id ON agents (owner_id);
CREATE INDEX IF NOT EXISTS ix_agents_public_slug ON agents (public_slug);

CREATE TABLE IF NOT EXISTS agent_versions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id           UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    parent_version_id  UUID REFERENCES agent_versions(id) ON DELETE SET NULL,
    version_number     INTEGER,                            -- NULL while draft; set on deploy
    status             agent_version_status NOT NULL DEFAULT 'draft',
    system_prompt      TEXT NOT NULL DEFAULT '',
    model              VARCHAR(128),                       -- NULL => global llm.yml chat model
    change_summary     TEXT,                               -- what changed vs parent
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_agent_versions_agent_id ON agent_versions (agent_id);
-- at most one published version per (agent, number); drafts have NULL number
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_version_number
    ON agent_versions (agent_id, version_number) WHERE version_number IS NOT NULL;
-- at most one draft per agent
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_single_draft
    ON agent_versions (agent_id) WHERE status = 'draft';

ALTER TABLE agents
    DROP CONSTRAINT IF EXISTS fk_agents_default_version;
ALTER TABLE agents
    ADD CONSTRAINT fk_agents_default_version
    FOREIGN KEY (default_version_id) REFERENCES agent_versions(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS agent_documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id     UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    version_id   UUID NOT NULL REFERENCES agent_versions(id) ON DELETE CASCADE,
    owner_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename     VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    size_bytes   INTEGER NOT NULL,
    sha256       VARCHAR(64) NOT NULL,
    status       doc_status NOT NULL DEFAULT 'pending',
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    storage_path VARCHAR(500) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_agent_doc_version_sha UNIQUE (version_id, sha256)
);
CREATE INDEX IF NOT EXISTS ix_agent_documents_version_id ON agent_documents (version_id);
CREATE INDEX IF NOT EXISTS ix_agent_documents_agent_id ON agent_documents (agent_id);

-- Explicit per-user access grants for `restricted` visibility.
CREATE TABLE IF NOT EXISTS agent_access (
    agent_id   UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (agent_id, user_id)
);

-- Conversations may be bound to an agent + the version they were chatting with.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_id UUID;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_version_id UUID;
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS fk_conversations_agent;
ALTER TABLE conversations
    ADD CONSTRAINT fk_conversations_agent
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_conversations_agent_id ON conversations (agent_id);


-- AUTH SUPPORT TABLES

-- Opaque refresh tokens: stored only as SHA-256 hash, rotated on every refresh,
-- grouped into a family so reuse of a rotated token revokes the whole family.
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    family_id   UUID NOT NULL,                          -- shared across a rotation chain
    token_hash  VARCHAR(64) NOT NULL UNIQUE,            -- sha256 hex of the opaque token
    revoked     BOOLEAN NOT NULL DEFAULT FALSE,
    used_at     TIMESTAMPTZ,                            -- set when rotated; reuse => family revoke
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_user_id ON refresh_tokens (user_id);
CREATE INDEX IF NOT EXISTS ix_refresh_tokens_family ON refresh_tokens (family_id);

-- Denylist for still-valid access-token JTIs (logout / forced revoke).
CREATE TABLE IF NOT EXISTS revoked_access_tokens (
    jti         VARCHAR(64) PRIMARY KEY,
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    expires_at  TIMESTAMPTZ NOT NULL,                   -- prune rows past this
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_revoked_access_expires ON revoked_access_tokens (expires_at);

-- Single-use, short-TTL, hashed email-verification tokens.
CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL UNIQUE,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_email_verif_user ON email_verification_tokens (user_id);

-- Single-use, short-TTL, hashed password-reset tokens.
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  VARCHAR(64) NOT NULL UNIQUE,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_pwd_reset_user ON password_reset_tokens (user_id);

-- Per-account login failure tracking for progressive backoff / lockout.
CREATE TABLE IF NOT EXISTS login_attempts (
    email           CITEXT PRIMARY KEY,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- REDIS-REPLACEMENT TABLES (rate limiting, daily token budget)
-- Fixed-window rate-limit counters. Key encodes route + IP and/or user; the
-- window_start bucketises time so old windows are simply ignored / pruned.
CREATE TABLE IF NOT EXISTS rate_limit_counters (
    bucket_key   VARCHAR(255) NOT NULL,                 -- e.g. "auth:login:ip:1.2.3.4"
    window_start TIMESTAMPTZ NOT NULL,                  -- start of the current window
    count        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket_key, window_start)
);
CREATE INDEX IF NOT EXISTS ix_rate_limit_window ON rate_limit_counters (window_start);

-- Per-user/day token budget counter (cost guardrail).
CREATE TABLE IF NOT EXISTS token_usage_daily (
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    usage_date   DATE NOT NULL,
    tokens_used  BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, usage_date)
);


-- Maintenance helper: prune expired ephemeral rows. Call from a periodic task.
CREATE OR REPLACE FUNCTION prune_expired() RETURNS void AS $$
BEGIN
    DELETE FROM revoked_access_tokens     WHERE expires_at < now();
    DELETE FROM refresh_tokens            WHERE expires_at < now();
    DELETE FROM email_verification_tokens WHERE expires_at < now();
    DELETE FROM password_reset_tokens     WHERE expires_at < now();
    DELETE FROM rate_limit_counters       WHERE window_start < now() - INTERVAL '1 day';
    DELETE FROM token_usage_daily         WHERE usage_date  < (now() - INTERVAL '7 days')::date;
END;
$$ LANGUAGE plpgsql;
