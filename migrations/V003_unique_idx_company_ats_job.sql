-- V003: Add unique composite index on (company_id, ats_job_id) in job_info
-- Ensures no duplicate job per company, even if ats_job_id alone could collide across companies

CREATE UNIQUE INDEX idx_job_info_company_ats_job
    ON job_info (company_id, ats_job_id);
