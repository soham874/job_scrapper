-- V004: Add created_ts column to job_info with default current timestamp

ALTER TABLE job_info ADD COLUMN created_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
