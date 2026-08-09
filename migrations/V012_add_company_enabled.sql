-- V012: Add tracking toggle to company_info
--
-- The Google Sheet becomes the source of truth for which companies are
-- tracked. `enabled` mirrors the sheet's "Enable in tracker" column and is
-- what load_companies_by_ats() filters on; `synced_at` records the last time
-- a row was reconciled against the sheet.
--
-- Existing rows default to enabled = 1 so behaviour is unchanged between this
-- migration running and the first successful sheet sync.

ALTER TABLE company_info
    ADD COLUMN enabled TINYINT(1) NOT NULL DEFAULT 1;

ALTER TABLE company_info
    ADD COLUMN synced_at TIMESTAMP NULL DEFAULT NULL;
