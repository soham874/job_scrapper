-- V007: Add user_decision column to job_info for apply/reject tracking

ALTER TABLE job_info ADD COLUMN user_decision VARCHAR(20) DEFAULT NULL;
