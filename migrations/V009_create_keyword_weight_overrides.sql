-- V009: Create keyword_weight_overrides table for adaptive keyword scoring

CREATE TABLE IF NOT EXISTS keyword_weight_overrides (
    keyword VARCHAR(100) PRIMARY KEY,
    multiplier FLOAT NOT NULL DEFAULT 1.0,
    accept_count INT NOT NULL DEFAULT 0,
    reject_count INT NOT NULL DEFAULT 0,
    sample_count INT NOT NULL DEFAULT 0,
    lift FLOAT NOT NULL DEFAULT 1.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
