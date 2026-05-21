-- Tune logging so slow queries and lock waits show up in container logs.
-- ALTER SYSTEM writes to postgresql.auto.conf and survives container
-- restarts (the file lives on the postgres_data volume).

-- Log statements that take longer than 500 ms.
ALTER SYSTEM SET log_min_duration_statement = '500ms';

-- Always log lock waits — they're a frequent source of multi-tenant
-- pain in phase 0 / 1.
ALTER SYSTEM SET log_lock_waits = on;

-- Log DDL only (avoids logging every SELECT in plain text).
ALTER SYSTEM SET log_statement = 'ddl';

-- Reload to apply.
SELECT pg_reload_conf();
