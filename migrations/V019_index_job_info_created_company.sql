-- V019: Index to support the analytics page
--
-- Every query behind /analytics is a date range over job_info grouped by
-- company or by ATS:
--
--     WHERE created_ts >= CURDATE() - INTERVAL n DAY  GROUP BY DATE(created_ts), company_id
--
-- job_info only grows and is never pruned, so without an index on created_ts
-- each of those is a full table scan — and the page runs four of them on every
-- load. V014's (user_decision, created_ts) cannot serve this: it leads on a
-- column the analytics queries do not constrain.
--
-- company_id rides along as the second column so the per-day-per-company
-- rollup is answered from the index alone, without touching the rows.

CREATE INDEX idx_job_info_created_company
    ON job_info (created_ts, company_id);
