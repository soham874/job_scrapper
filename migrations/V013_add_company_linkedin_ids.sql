-- V013: Add LinkedIn org id(s) to company_info
--
-- Used to scope referral search URLs to people currently working at a given
-- company (LinkedIn's `currentCompany` search facet takes this internal id,
-- e.g. "2029" for a given org — not the same as company_info.id).
--
-- Stored as a comma-separated list because the same real-world company can
-- exist as multiple distinct entities on LinkedIn (e.g. "Walmart Global
-- Tech" and "Walmart Global Tech India" have different ids) — all of them
-- get OR'd together in the search.
--
-- Sheet-managed like the other company fields: fetch_companies() reads it
-- from an optional "LinkedIn Company Id" column and upsert_company() writes
-- it on every sync. Nullable because it's optional in the sheet and existing
-- rows predate this column; companies without a value simply fall back to
-- the unscoped referral search.

ALTER TABLE company_info
    ADD COLUMN linkedin_company_ids VARCHAR(255) NULL DEFAULT NULL;
