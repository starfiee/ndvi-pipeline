-- SAWIE NDVI Pipeline — Database Schema
-- Phase 3: Pixel-level storage

CREATE TABLE ndvi_pixels (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    field_id         INT NOT NULL,
    image_date       DATE NOT NULL,
    pixel_row        INT NOT NULL,
    pixel_col        INT NOT NULL,
    latitude         DOUBLE NOT NULL,
    longitude        DOUBLE NOT NULL,
    ndvi_value       FLOAT NOT NULL,
    cloud_prob       FLOAT,
    ndvi_class       VARCHAR(50),
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY unique_pixel (field_id, image_date, pixel_row, pixel_col),
    INDEX idx_field_date (field_id, image_date),
    INDEX idx_location   (latitude, longitude)
);

CREATE TABLE ndvi_results (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    field_id         INT NOT NULL,
    image_date       DATE NOT NULL,
    ndvi_min         FLOAT,
    ndvi_max         FLOAT,
    ndvi_mean        FLOAT,
    cloud_prob       FLOAT,
    total_pixels     INT,
    valid_pixels     INT,
    crop_cover_pct   FLOAT,
    status           VARCHAR(20),
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY unique_field_date (field_id, image_date),
    INDEX idx_field_id (field_id),
    INDEX idx_date     (image_date)
);