-- The self_json borg reads its request straight from ats_link.
--
-- ats_link already held whatever a borg needs to locate a board — a slug for
-- ashby, a tenant URL for workday, and for oracle a URL that parse_source_url
-- explicitly accepts still wrapped in a curl snippet. self_json follows that
-- pattern with the full curl, so the separate job_api_curl column added in
-- V016 is redundant and goes away again.
--
-- The widening is the load-bearing part: a browser-copied curl carries the
-- whole sec-ch-ua/sec-fetch header set and runs past 1024 bytes (KLM's is
-- ~1.3 KB), which VARCHAR(1024) would truncate or reject.
--
-- V016 is left untouched rather than rewritten so this applies correctly
-- whether or not it has already run.

ALTER TABLE company_info MODIFY ats_link TEXT;
ALTER TABLE company_info DROP COLUMN job_api_curl;
