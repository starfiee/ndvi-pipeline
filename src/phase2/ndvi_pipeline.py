""" 
Phase 2 Goal:
Remove .tif file dependecy
unlimited point input 
"""

import sys
import json
from shapely.geometry import Polygon
import geopandas as gpd
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch
from datetime import datetime, timedelta
import ee
import numpy as np
from rasterio.transform import from_bounds

# Initialize google earth engine
import os
project_id = os.getenv('GEE_PROJECT_ID', 'your-project-id')
ee.Initialize(project=project_id)


# FUNCTION 1 — Parse Arguments

def parse_arguments():
    args = sys.argv[1:]

    if len(args) == 82:
        # OLD FORMAT
        lat_values = [float(str(a).replace('"','')) for a in args[0:40]]
        lng_values = [float(str(b).replace('"','')) for b in args[40:80]]
        cnt        = int(float(str(args[80]).replace('"','')))
        fid        = float(str(args[81]).replace('"',''))
        start_date = None  # no date given, use last 30 days

    else:
        # NEW FORMAT — unlimited points + date at end
        cnt        = int(float(str(args[0]).replace('"','')))
        lat_values = [float(str(a).replace('"','')) for a in args[1:cnt+1]]
        lng_values = [float(str(b).replace('"','')) for b in args[cnt+1:(cnt*2)+1]]
        fid        = float(str(args[(cnt*2)+1]).replace('"',''))
        start_date = str(args[(cnt*2)+2]) if len(args) > (cnt*2)+2 else None

    return lat_values, lng_values, cnt, fid, start_date
    
# ═══════════════════════════════════════════════════════════


# FUNCTION 2 — Build Coordinate Arrays
def build_coordinate_arrays(lat_values,lng_values,cnt):
 """
    Extracts first cnt values from lat and lng lists.
    x_coord = longitude (same as R's x_coord)
    y_coord = latitude  (same as R's y_coord)
    Replaces all 37 if-else blocks with 2 lines.
    """
 x_coord=lng_values[:cnt]
 y_coord=lat_values[:cnt]
 return x_coord,y_coord

# ═══════════════════════════════════════════════════════════
# Function 3: build Polygon  
def build_polygon(x_coord, y_coord):
    coords  = list(zip(x_coord, y_coord))
    polygon = Polygon(coords)
    
    
    #  CRS as R: WGS84
    gdf = gpd.GeoDataFrame(
        [{'geometry': polygon, 'f': 99.9}],
        crs='EPSG:4326'   # WGS84 = EPSG:4326
    )
    return polygon, gdf

# ═══════════════════════════════════════════════════════════
#function 4:
def load_and_clip_raster(polygon, gdf, start_date=None):

    # Step 1 — Convert polygon to GEE geometry
    coords      = list(polygon.exterior.coords)
    gee_polygon = ee.Geometry.Polygon(coords)

    # Step 2 — Set date range
    today = datetime.now().strftime('%Y-%m-%d')

    if start_date is None:
        search_from = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    else:
        next_day    = (datetime.strptime(start_date, '%Y-%m-%d') 
                      + timedelta(days=1)).strftime('%Y-%m-%d')
        search_from = next_day

    # Step 3 — Get ALL images in date range oldest first
    collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
        .filterBounds(gee_polygon) \
        .filterDate(search_from, today) \
        .sort('system:time_start', True)

    # Step 4 — Get list of all images
    image_list = collection.toList(collection.size())
    total      = collection.size().getInfo()

    if total == 0:
        raise Exception(f"No images found after {search_from}")

    # Step 5 — Loop through images, find first clean one
    image      = None
    image_date = None

    for i in range(total):
        candidate      = ee.Image(image_list.get(i))
        candidate_date = candidate.date().format('YYYY-MM-dd').getInfo()

        # Tile level cloud percentage
        tile_cloud = candidate.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()

        # Field level cloud probability via s2cloudless
        cs_plus = ee.ImageCollection(
            'GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED') \
            .filterBounds(gee_polygon) \
            .filterDate(candidate_date, 
                       (datetime.strptime(candidate_date, '%Y-%m-%d') 
                       + timedelta(days=1)).strftime('%Y-%m-%d')) \
            .first()

        cloud_score  = cs_plus.select('cs_cdf')
        cloud_result = cloud_score.sampleRectangle(region=gee_polygon)
        cs_values    = cloud_result.getInfo()['properties']['cs_cdf']

        cloud_prob_array = 1 - np.array(cs_values)
        field_cloud_prob = float(np.mean(cloud_prob_array))

        # Show both tile and field level
        print(f"Date: {candidate_date} | Tile: {tile_cloud:.1f}% | Field: {field_cloud_prob*100:.1f}%",
              file=sys.stderr)

        if field_cloud_prob < 0.25:
            image      = candidate
            image_date = candidate_date
            print(f"Clean image found: {image_date}", file=sys.stderr)
            break
        else:
            print(f"Too cloudy, skipping {candidate_date}", file=sys.stderr)

    if image is None:
        raise Exception(f"No clean image found after {search_from}")

    # Step 6 — Calculate NDVI on clean image
    ndvi = image.normalizedDifference(['B8', 'B4'])

    # Step 7 — Sample pixel values
    result    = ndvi.sampleRectangle(region=gee_polygon)
    nd_values = result.getInfo()['properties']['nd']

    # Step 8 — Convert to numpy array
    arr            = np.array(nd_values)
    clipped_raster = arr[np.newaxis, :, :]

    # Step 9 — Build transform and raster_meta
    height = len(nd_values)
    width  = len(nd_values[0])
    west, south, east, north = polygon.bounds
    transform   = from_bounds(west, south, east, north, width, height)
    raster_meta = {
        'height'   : clipped_raster.shape[1],
        'width'    : clipped_raster.shape[2],
        'transform': transform
    }

    return clipped_raster, transform, raster_meta, polygon, \
           cloud_prob_array, field_cloud_prob, image_date
#Function 5: Calculate Stats and print output
def calculate_stats(clipped_raster, raster_meta):
    """
    - getValues(r2)
    - min/max/mean with na.rm=TRUE
    - cut() with ndvi_breaks and ndvi_labels
    - table() frequency count
    - area and percentage calculation
    """

    # ── Part A ─────────────────────────────────────────

    flat      = clipped_raster.flatten()
    valid     = flat[~np.isnan(flat)]
    ndvi_min  = float(np.min(valid))
    ndvi_max  = float(np.max(valid))
    ndvi_mean = float(np.mean(valid))

    # ── Part B ─────────────────────────────────────────
    
    ndvi_breaks = [-1, -0.6, -0.4, -0.2, 0, 0.1, 0.2, 0.4, 0.6, 0.8, 1]
    ndvi_labels = [
        "Water",
        "Builtup Area",
        "Barren Land",
        "Agri Barren Land",
        "Clouds",
        "Sparse vegetation",
        "Low vegetation",
        "Moderate healthy vegetation",
        "High vegetation",
        "Extremely high vegetation"
    ]

    # np.digitize 
    # right=False means left edge included — matches R's include.lowest=TRUE
    indices = np.digitize(valid, ndvi_breaks, right=False)

    # Clip to valid range 1-10
    # Handles edge case when pixel value == exactly 1.0 or exactly -1.0
    indices = np.clip(indices, 1, len(ndvi_labels))

    # ── Part C ─────────────────────────────────────────
    # area_ha = area_m2 / 10000
    # percent_ha = area_ha / total_area_ha * 100
    transform    = raster_meta['transform']
    pixel_width  = abs(transform[0])
    pixel_height = abs(transform[4])
    pixel_area_m2 = pixel_width * pixel_height

    total_pixels   = len(valid)
    total_area_m2  = total_pixels * pixel_area_m2
    total_area_ha  = total_area_m2 / 10000

    freq_table = []
    for i, label in enumerate(ndvi_labels):
        #  count = number of pixels in this bin
        count    = int(np.sum(indices == i + 1))

        area_m2  = count * pixel_area_m2

        # area_ha = area_m2 / 10000
        area_ha  = area_m2 / 10000

        #  percent_ha = area_ha / total_area_ha * 100
        percent  = (area_ha / total_area_ha * 100) if total_area_ha > 0 else 0.0

        #  round(percent_ha, 2)
        percent  = round(percent, 2)

        # setNames(freq_table, c("class","area_ha"))
        # note: R names the column area_ha but stores percentage value
        freq_table.append({
            "class"  : label,
            "area_ha": percent
        })

    return {
        "ndvi_min"  : ndvi_min,
        "ndvi_max"  : ndvi_max,
        "ndvi_mean" : ndvi_mean,
        "freq_table": freq_table
    }

# ═══════════════════════════════════════════════════════════
#Function 6: print outputs
def print_outputs(stats, image_date, field_cloud_prob):
    print("Sawie-ndvi-parameters")
    print(
        str(stats['ndvi_min'])  + " " +
        str(stats['ndvi_max'])  + " " +
        str(stats['ndvi_mean']) + " "
    )

    cleaned = []
    for row in stats['freq_table']:
        val = row['area_ha']
        if isinstance(val, float) and val == int(val):
            val = int(val)
        cleaned.append({"class": row['class'], "area_ha": val})

    print(json.dumps(cleaned, separators=(',', ':')))

    
    print(f"image_date:{image_date}")
    print(f"cloud_prob:{field_cloud_prob:.4f}")
    print(f"status:clean")
    print(f"created_at:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
   
  
# ═══════════════════════════════════════════════════════════

# FUNCTION 7 — Save PNGs

#   png(filename=fname, width=700, height=700)
#   plot(r2) + plot(spdf, add=TRUE, lwd=2)  
#   reclassify(r2, )  ndviclass-{fid}.png
#   crop_cover = r2 > 0.2  cropcover-{fid}.png
#   dev.off()

def save_pngs(clipped_raster, polygon, raster_meta, fid):
    """
    Saves exactly 3 PNG files matching R output exactly.
    Key fixes:
    - NaN pixels render as white (masked/outside polygon)
    - Correct colormap handling
    - vegc uses float64 for proper NaN support
    """

    fid_int = int(fid)
    band    = clipped_raster[0].astype(float)  # ensure float64

    # Spatial extent for axis labels
    transform = raster_meta['transform']
    height    = raster_meta['height']
    width     = raster_meta['width']
    lng_min   = transform[2]
    lat_max   = transform[5]
    lng_max   = lng_min + width  * transform[0]
    lat_min   = lat_max + height * transform[4]

    # Polygon boundary for overlay
    poly_x = list(polygon.exterior.coords.xy[0])
    poly_y = list(polygon.exterior.coords.xy[1])

    # ── PNG 1 — NDVI Map ─────────────────────────────────
    # R: plot(r2) uses data-driven color scale
    # Masked pixels (outside polygon) = white
    fig, ax = plt.subplots(figsize=(7, 7))

    # Create masked array — NaN becomes transparent/white
    band_masked = np.ma.masked_invalid(band)

    cmap_ndvi = plt.cm.RdYlGn.copy()
    cmap_ndvi.set_bad(color='white')  # NaN pixels = white like R

    im = ax.imshow(
        band_masked,
        cmap=cmap_ndvi,
        extent=[lng_min, lng_max, lat_min, lat_max],
        origin='upper'
    )
    ax.plot(poly_x, poly_y, color='black', linewidth=2)
    plt.colorbar(im, ax=ax)

    fname = f"ndvi- {fid_int} .png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

    # ── PNG 2 — NDVI Classification Map ──────────────────
    vegc = np.full(band.shape, np.nan, dtype=float)
    vegc[~np.isnan(band) & (band <= 0.25)]                  = 1
    vegc[~np.isnan(band) & (band > 0.25) & (band <= 0.30)] = 2
    vegc[~np.isnan(band) & (band > 0.30) & (band <= 0.40)] = 3
    vegc[~np.isnan(band) & (band > 0.40) & (band <= 0.50)] = 4
    vegc[~np.isnan(band) & (band > 0.50) & (band <= 0.60)] = 5
    vegc[~np.isnan(band) & (band > 0.60) & (band <= 0.80)] = 6
    vegc[~np.isnan(band) & (band > 0.80)]                  = 7

    vegc_masked = np.ma.masked_invalid(vegc)

    # R's terrain.colors reversed = yellow → green → dark green
        # This exactly matches R's rev(terrain.colors(4)) behavior
    terrain_colors = [
    '#F2F2A0',  # category 1 = pale yellow (matches R)
    '#D4C878',  # category 2 = yellow-green
    '#A8C878',  # category 3 = light green
    '#78C878',  # category 4 = green
    '#50A050',  # category 5 = medium green
    '#287828',  # category 6 = dark green
    '#005000',  # category 7 = very dark green
    ]
    cmap_vegc = mcolors.ListedColormap(terrain_colors)
    cmap_vegc.set_bad(color='white')

    fig, ax = plt.subplots(figsize=(7, 7))
    im = ax.imshow(
    vegc_masked,
    cmap=cmap_vegc,
    vmin=1, vmax=7,
    extent=[lng_min, lng_max, lat_min, lat_max],
    origin='upper'
    )
    ax.set_title('NDVI based thresholding')

    fname = f"ndviclass- {fid_int} .png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

    # ── PNG 3 — Crop Cover Map ────────────────────────────
    # R: crop_cover <- r2 > 0.2
    # White/outside polygon pixels must show as white
    # Only valid pixels get brown or green
    crop_cover = np.full(band.shape, np.nan, dtype=float)
    valid_mask = ~np.isnan(band)
    crop_cover[valid_mask & (band <= 0.2)] = 0  # no crop = brown
    crop_cover[valid_mask & (band > 0.2)]  = 1  # crop = green

    crop_masked = np.ma.masked_invalid(crop_cover)

    cmap_crop = mcolors.ListedColormap(['#A0522D', '#00BB00'])

    cmap_crop.set_bad(color='white')  # NaN = white like R masked pixels

    fig, ax = plt.subplots(figsize=(7, 7))
    im = ax.imshow(
        crop_masked,
        cmap=cmap_crop,
        vmin=0, vmax=1,
        extent=[lng_min, lng_max, lat_min, lat_max],
        origin='upper'
    )
    ax.plot(poly_x, poly_y, color='black', linewidth=2)

    legend_elements = [
        Patch(facecolor='brown', label='No Crop'),
        Patch(facecolor='green', label='Crop Cover')
    ]
    ax.legend(handles=legend_elements, loc='upper right')
    ax.set_title('Crop Cover Classification')

    fname = f"cropcover- {fid_int} .png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")
    
# -----------------------------
# Test
if __name__ == "__main__":

    lat_values = [31.200971, 31.200957, 31.20062, 31.200595] + [0.0] * 36
    lng_values = [71.00614,  71.006671, 71.006668, 71.006145] + [0.0] * 36
    cnt        = 4
    fid        = 9416.0
    start_date = '2026-04-01'  # MM provides this date

    x_coord, y_coord = build_coordinate_arrays(lat_values, lng_values, cnt)
    polygon, gdf     = build_polygon(x_coord, y_coord)

    clipped_raster, clipped_transform, raster_meta, polygon, \
    cloud_prob_array, field_cloud_prob, image_date = load_and_clip_raster(
        polygon, gdf, start_date
    )

    stats = calculate_stats(clipped_raster, raster_meta)
    print_outputs(stats, image_date, field_cloud_prob)
    save_pngs(clipped_raster, polygon, raster_meta, fid)