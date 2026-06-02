# NDVI Field Analysis Pipeline

Automated satellite imagery pipeline that accepts field polygon coordinates,
finds the most recent cloud-free Sentinel-2 image using field-level cloud
scoring, and computes NDVI statistics with vegetation classification maps.

Built for production use at a precision agriculture startup.

---

## What it does

- Accepts any number of polygon coordinates as input (unlimited points)
- Searches Sentinel-2 archive and scores cloud cover at **field level**
  using Google Cloud Score Plus — more accurate than tile-level percentage
- Computes NDVI min / max / mean across the field
- Classifies pixels into 10 vegetation categories
- Outputs 3 georeferenced PNG maps: raw NDVI, vegetation class, crop cover
- Handles backward-compatible argument formats (old 82-arg and new variable format)

---

## Example output

| NDVI Map | Vegetation Classes | Crop Cover |
|---|---|---|
| ![ndvi](demo/sample_output/ndvi_example.png) | ![class](demo/sample_output/ndviclass_example.png) | ![crop](demo/sample_output/cropcover_example.png) |

---

## Tech stack

`Python` `Google Earth Engine` `Sentinel-2` `GeoPandas` `Rasterio` `NumPy` `Matplotlib`

---

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/[your-username]/ndvi-pipeline.git
cd ndvi-pipeline
```

**2. Install dependencies**
```bash
pip install -r requirements.txt
```

**3. Authenticate Google Earth Engine**
```bash
earthengine authenticate
```

**4. Set your environment variable**
```bash
cp .env.example .env
# Edit .env and add your GEE project ID
```

**5. Run the test harness**
```bash
python ndvi_pipeline.py
```

---

## How cloud filtering works

Each candidate image is scored at two levels:
1. **Tile level** — `CLOUDY_PIXEL_PERCENTAGE` from Sentinel-2 metadata
2. **Field level** — Google Cloud Score Plus (`cs_cdf` band), averaged over
   the exact polygon area

Only images with field-level cloud probability below 25% are accepted.
This prevents cases where a tile is mostly clear but the specific field is cloudy.

---

## Output format

```
Sawie-ndvi-parameters
{min} {max} {mean}
[{"class":"Water","area_ha":0}, ...]
image_date:2026-05-12
cloud_prob:0.0812
status:clean
created_at:2026-05-12 14:33:01
```