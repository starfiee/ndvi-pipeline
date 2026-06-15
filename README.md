# NDVI Field Analysis Pipeline

Automated satellite imagery pipeline that accepts field polygon 
coordinates, retrieves clean Sentinel-2 imagery using field-level 
cloud scoring, computes NDVI statistics, stores per-pixel data to 
a database, and generates vegetation maps on demand.

Built for production use at a precision agriculture startup in Pakistan.

---

## Pipeline Phases

### Phase 2 — Field NDVI with Cloud Filtering
`src/phase2/ndvi_pipeline.py`

- Accepts any number of polygon coordinates as input
- Searches Sentinel-2 archive oldest to newest from a given start date
- Scores cloud cover at field level using Google Cloud Score Plus
- Returns first clean image where field cloud probability is below 25%
- Computes NDVI min / max / mean across the field
- Classifies pixels into 10 vegetation categories with real hectare areas
- Outputs crop cover percentage
- Prints result to stdout for PHP integration
- Saves 3 georeferenced PNG maps automatically

### Phase 3 — Pixel Storage and On-Demand PNG Generation
`src/phase3/data_collector.py`
`src/phase3/png_generator.py`

Major architectural upgrade over Phase 2:

- Every pixel saved to database with its real-world latitude and longitude
- NDVI class assigned per pixel at storage time
- Summary statistics stored in separate results table
- PNG generation removed from automatic pipeline
- New on-demand PNG generator reads directly from pixel database
- No satellite call needed for PNG generation
- Duplicate prevention via UNIQUE KEY constraints

Why pixel-level storage matters:

1. Tree pixel identification — pixels that stay consistently above 
   0.6 NDVI across seasons can be flagged as permanent vegetation 
   and excluded from crop calculations improving accuracy

2. Time series per pixel — each pixel builds its own NDVI history 
   over time enabling crop growth stage detection and yield prediction

3. ML and deep learning ready — pixel data with lat/lng coordinates 
   and class labels is the exact format needed for training crop 
   classification and disease detection models

---

## How Cloud Filtering Works

Each candidate image is scored at two levels:

- Tile level — CLOUDY_PIXEL_PERCENTAGE from Sentinel-2 metadata 
  covers the entire 100km x 100km satellite tile
- Field level — Google Cloud Score Plus cs_cdf band averaged over 
  the exact polygon area only

Only images with field-level cloud probability below 25% are accepted.

This prevents cases where a tile appears mostly clear but the specific 
field polygon is under cloud cover — a critical distinction for small 
agricultural fields in Pakistan.

---

## Database Schema

See `database/schema.sql` for complete table definitions.

Two tables:

**ndvi_pixels** — one row per pixel per field per date