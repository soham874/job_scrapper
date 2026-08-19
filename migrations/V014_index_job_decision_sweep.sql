-- V014: Index to support the stale-job sweep
--
-- The sweeper (common/sweeper.py) runs every SWEEP_INTERVAL_SECONDS and scans
-- for undecided jobs older than the response window:
--
--     WHERE user_decision IS NULL AND created_ts < NOW() - INTERVAL n HOUR
--
-- job_info only grows, and after the sweep ships the overwhelming majority of
-- rows carry a non-NULL user_decision — so leading on user_decision lets the
-- index skip nearly the whole table and range-scan created_ts over the small
-- undecided remainder.

CREATE INDEX idx_job_info_decision_created
    ON job_info (user_decision, created_ts);
