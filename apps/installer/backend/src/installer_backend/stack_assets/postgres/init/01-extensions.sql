-- Runs once on first DB initialization (when postgres_data volume is empty).
-- Enables the extensions the platform depends on. Idempotent: safe to
-- re-run, no-op if extensions already exist.

-- Vector store for embeddings (used by the memorizer service).
CREATE EXTENSION IF NOT EXISTS vector;

-- Trigram index for fuzzy ILIKE / similarity search.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- gen_random_uuid() for UUID v4 generation in defaults.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- uuid_generate_v7() will arrive natively in PG17+; for now we generate
-- UUID v7 in application code via the uuid7 library.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
