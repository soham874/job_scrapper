-- V010: Drop the column-level UNIQUE on job_info.ats_job_id
--
-- V001 declared `ats_job_id VARCHAR(255) UNIQUE`, which makes the id unique
-- across the whole table rather than per company. V003 added the intended
-- composite unique index on (company_id, ats_job_id) but never removed the
-- original, so two companies sharing a requisition id still collide and the
-- second job is silently skipped as a duplicate.
--
-- Oracle Recruiting Cloud makes this concrete: its requisition ids are short
-- per-tenant numerics (e.g. 339709), so collisions between Oracle tenants are
-- expected rather than hypothetical.
--
-- The composite index from V003 continues to enforce correct per-company dedup.

ALTER TABLE job_info DROP INDEX ats_job_id;
