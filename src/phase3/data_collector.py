"""
Phase 3 — data_collector.py

Goal:
Fetch clean Sentinel-2 NDVI image from GEE
Save every pixel with lat/lng to ndvi_pixels table
Save summary stats to ndvi_results table
Print stdout output for PHP same format as Phase 2
"""

import sys
import json
import os
from dotenv import load_dotenv
load_dotenv()
import mysql.connector
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

# Initialize GEE
ee.Initialize(project=os.getenv('GEE_PROJECT_ID'))

# ── DB CONFIG ──────────────────────────────────────────────
DB_CONFIG = {
    'host'    : os.getenv('DB_HOST', 'localhost'),
    'user'    : os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'ndvi')
}

# ── FUNCTION 1 — Parse Arguments ──────────────────────────
def parse_arguments():
    """
    Parses command line arguments passed by PHP via shell_exec.

    Supports two input formats:

    OLD FORMAT (82 arguments — fixed slots):
        args[0:40]  >> 40 latitude slots  (unused slots = 0.0)
        args[40:80] >>40 longitude slots (unused slots = 0.0)
        args[80]    >> cnt (how many points actually used)
        args[81]    >> fid (field ID)
        Total: always 82 args regardless of polygon size
        Limitation: maximum 40 polygon points

    NEW FORMAT (variable arguments — unlimited points):
        args[0]              >> cnt (how many points)
        args[1 : cnt+1]      >> latitude values
        args[cnt+1 : cnt*2+1] >> longitude values
        args[cnt*2+1]        >> fid (field ID)
        args[cnt*2+2]        >> start_date (optional, YYYY-MM-DD)
        Example with cnt=5:
            args[0]    = 5        (cnt)
            args[1:6]  = 5 lats
            args[6:11] = 5 lngs
            args[11]   = fid
            args[12]   = start_date (if provided)


    Returns:
        lat_values  (list of float) : latitude coordinates
        lng_values  (list of float) : longitude coordinates
        cnt         (int)           : number of polygon points
        fid         (float)         : field ID
        start_date  (str or None)   : search start date YYYY-MM-DD
                                      None means search last 30 days
    """
    args = sys.argv[1:]

    if len(args) == 82:
        lat_values = [float(str(a).replace('"','')) for a in args[0:40]]
        lng_values = [float(str(b).replace('"','')) for b in args[40:80]]
        cnt        = int(float(str(args[80]).replace('"','')))
        fid        = float(str(args[81]).replace('"',''))
        start_date = None
    else:
        cnt        = int(float(str(args[0]).replace('"','')))
        lat_values = [float(str(a).replace('"','')) for a in args[1:cnt+1]]
        lng_values = [float(str(b).replace('"','')) for b in args[cnt+1:(cnt*2)+1]]
        fid        = float(str(args[(cnt*2)+1]).replace('"',''))
        start_date = str(args[(cnt*2)+2]) if len(args) > (cnt*2)+2 else None

    return lat_values, lng_values, cnt, fid, start_date

# ── FUNCTION 2 — Build Coordinate Arrays ──────────────────
def build_coordinate_arrays(lat_values, lng_values, cnt):
    """
    Extracts the actual polygon coordinates from the full input lists.

     FUNCTION Description:
        In old format PHP always sends 40 lat and 40 lng values.
        But only the first cnt values are real polygon points.
        The rest are 0.0 placeholders.
        This function extracts only the real points.

        In new format PHP will  sends exactly cnt values.
        Slicing to cnt still works correctly for both formats.

    Example with cnt=4:
        lat_values = [31.20, 31.20, 31.19, 31.19, 0.0, 0.0 ,0.0...]
        lng_values = [71.00, 71.01, 71.01, 71.00, 0.0, 0.0,0.0 ...]

        x_coord = [71.00, 71.01, 71.01, 71.00]  ← first 4 lngs
        y_coord = [31.20, 31.20, 31.19, 31.19]  ← first 4 lats
        

    Args:
        lat_values (list of float) : full latitude list from parse_arguments()
        lng_values (list of float) : full longitude list from parse_arguments()
        cnt        (int)           : how many points are real polygon points

    Returns:
        x_coord (list of float) : longitude values for polygon
        y_coord (list of float) : latitude values for polygon
        """
    x_coord = lng_values[:cnt]
    y_coord = lat_values[:cnt]
    return x_coord, y_coord

# ── FUNCTION 3 — Build Polygon ─────────────────────────────
def build_polygon(x_coord, y_coord):
    """
    Converts longitude and latitude lists into a polygon geometry
    with real-world coordinate reference system (CRS).

    TWO THINGS THIS FUNCTION CREATES:

    1. polygon (Shapely Polygon):
       Pure geometry shape — just connected points in space.
       Has no knowledge of where on Earth it is.
       Used for GEE operations and pixel calculations.

    2. gdf (GeoDataFrame):
       Same polygon but with CRS attached (EPSG:4326 = WGS84).
       Now it knows it exists on Earth at real GPS coordinates.
       Used for raster clipping in Phase 1 (tif file approach).

    HOW COORDINATES ARE PAIRED:
        x_coord (lng) = [71.00, 71.01, 71.01, 71.00]
        y_coord (lat) = [31.20, 31.20, 31.19, 31.19]

        zip() pairs them by index:
        [(71.00, 31.20),   point 1: lng, lat
         (71.01, 31.20),   point 2
         (71.01, 31.19),   point 3
         (71.00, 31.19)]   point 4

        Shapely connects these points in order
        and closes the shape automatically.

     EPSG:4326:
        EPSG:4326 is the standard GPS coordinate system (WGS84).
        Same system used by Google Maps, Sentinel-2, and our tif file.
        Without this the polygon has no real-world location.

    Args:
        x_coord (list of float) : longitude values
        y_coord (list of float) : latitude values

    Returns:
        polygon (Shapely Polygon)    : geometry shape only
        gdf     (GeoDataFrame)       : polygon with WGS84 CRS attached
    """
    coords  = list(zip(x_coord, y_coord))
    polygon = Polygon(coords)
    gdf     = gpd.GeoDataFrame(
        [{'geometry': polygon, 'f': 99.9}],
        crs='EPSG:4326'
    )
    return polygon, gdf

# ── FUNCTION 4 — Fetch From GEE ────────────────────────────
def fetch_from_gee(polygon, start_date=None):
    """
    Connects to GEE and finds first clean Sentinel-2 image
    after start_date. Returns pixel data and metadata.

    Cloud threshold: 25% field level via Cloud Score Plus.
    Searches oldest to newest — returns first clean image found.

    Returns:
        nd_values        : 2D list of NDVI pixel values
        cloud_prob_array : 2D numpy array of cloud probabilities
        field_cloud_prob : float average cloud for whole field
        image_date       : str date of selected image YYYY-MM-DD
        transform        : Affine transform for pixel coordinates
        gee_polygon      : GEE geometry object
    """
    # Convert Shapely polygon to GEE geometry
    coords      = list(polygon.exterior.coords)
    gee_polygon = ee.Geometry.Polygon(coords)

    # Set date range
    today = datetime.now().strftime('%Y-%m-%d')

    if start_date is None:
        search_from = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    else:
        next_day    = (datetime.strptime(start_date, '%Y-%m-%d')
                      + timedelta(days=1)).strftime('%Y-%m-%d')
        search_from = next_day

    # Get all images oldest first
    collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
        .filterBounds(gee_polygon) \
        .filterDate(search_from, today) \
        .sort('system:time_start', True)

    image_list = collection.toList(collection.size())
    total      = collection.size().getInfo()

    if total == 0:
        raise Exception(f"No images found after {search_from}")

    # Loop through images find first clean one
    image      = None
    image_date = None

    for i in range(total):
        candidate      = ee.Image(image_list.get(i))
        candidate_date = candidate.date().format('YYYY-MM-dd').getInfo()
        tile_cloud     = candidate.get('CLOUDY_PIXEL_PERCENTAGE').getInfo()

        # Get field level cloud probability
        cs_plus = ee.ImageCollection(
            'GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED') \
            .filterBounds(gee_polygon) \
            .filterDate(candidate_date,
                       (datetime.strptime(candidate_date, '%Y-%m-%d')
                       + timedelta(days=1)).strftime('%Y-%m-%d')) \
            .first()

        cloud_score      = cs_plus.select('cs_cdf')
        cloud_result     = cloud_score.sampleRectangle(region=gee_polygon)
        cs_values        = cloud_result.getInfo()['properties']['cs_cdf']
        cloud_prob_array = 1 - np.array(cs_values)
        field_cloud_prob = float(np.mean(cloud_prob_array))

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

    # Calculate NDVI
    ndvi   = image.normalizedDifference(['B8', 'B4'])
    result = ndvi.sampleRectangle(region=gee_polygon)
    nd_values = result.getInfo()['properties']['nd']

    # Build transform
    height = len(nd_values)
    width  = len(nd_values[0])
    west, south, east, north = polygon.bounds
    transform = from_bounds(west, south, east, north, width, height)

    return nd_values, cloud_prob_array, field_cloud_prob, image_date, transform, gee_polygon

# ── FUNCTION 5 — Calculate Pixel Lat/Lng ──────────────────
def calculate_pixel_coordinates(row, col, transform):
    """
    Converts pixel grid position to real world lat/lng.

    Uses transform from rasterio.from_bounds.
    Each pixel center = top-left corner + offset.

    Formula:
        lng = west  + (col + 0.5) * pixel_width
        lat = north - (row + 0.5) * pixel_height

    Args:
        row       : pixel row index (0 = top)
        col       : pixel column index (0 = left)
        transform : Affine transform from from_bounds()

    Returns:
        lat (float), lng (float)
    """
    pixel_width  = abs(transform[0])
    pixel_height = abs(transform[4])
    west         = transform[2]
    north        = transform[5]

    lng = west  + (col + 0.5) * pixel_width
    lat = north - (row + 0.5) * pixel_height

    return round(lat, 6), round(lng, 6)

# ── FUNCTION 6 — Classify NDVI Value ──────────────────────
def classify_ndvi(value):
    """
    Assigns NDVI class label based on value.
    Breaks: [-inf, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, inf]
    right=True means: value <= break goes to LEFT category
    Example: 0.7 exactly → Low vegetation (not Sparse)
    """
    if value <= 0.2:  return "Water"
    if value <= 0.3:  return "Builtup Area"
    if value <= 0.4:  return "Barren Land"
    if value <= 0.5:  return "Agri Barren Land"
    if value <= 0.6:  return "Clouds"
    if value <= 0.7:  return "Sparse vegetation"
    if value <= 0.8:  return "Low vegetation"
    if value <= 0.9:  return "Moderate healthy vegetation"
    if value <= 0.95: return "High vegetation"
    return "Extremely High vegetation"
# ── FUNCTION 7 — Calculate Stats ──────────────────────────
def calculate_stats(nd_values, transform):
    """
    Calculates NDVI statistics and vegetation breakdown.

    Returns:
        stats dict with ndvi_min, ndvi_max, ndvi_mean,
        freq_table, crop_cover_pct, total_pixels, valid_pixels
    """
    arr   = np.array(nd_values, dtype=float)
    flat  = arr.flatten()
    valid = flat[~np.isnan(flat)]

    ndvi_min  = float(np.min(valid))
    ndvi_max  = float(np.max(valid))
    ndvi_mean = float(np.mean(valid))

    # Pixel area in hectares
    pixel_width   = abs(transform[0])
    pixel_height  = abs(transform[4])
    pixel_area_m2 = (pixel_width * 111320) * (pixel_height * 111320)
    pixel_area_ha = pixel_area_m2 / 10000

    # NDVI breaks matching R script
    ndvi_breaks = [-np.inf, 0.2, 0.3, 0.4, 0.5,
                    0.6, 0.7, 0.8, 0.9, 0.95, np.inf]
    ndvi_labels = [
        "Water", "Builtup Area", "Barren Land",
        "Agri Barren Land", "Clouds", "Sparse vegetation",
        "Low vegetation", "Moderate healthy vegetation",
        "High vegetation", "Extremely High vegetation"
    ]

    indices = np.digitize(valid, ndvi_breaks, right=True)
    indices = np.clip(indices, 1, len(ndvi_labels))
    

    freq_table = []
    for i, label in enumerate(ndvi_labels):
        count   = int(np.sum(indices == i + 1))
        area_ha = round(count * pixel_area_ha, 4)
        freq_table.append({"class": label, "area_ha": area_ha})

    # Crop cover percentage
    crop_pixels    = int(np.sum(valid > 0.2))
    crop_cover_pct = round((crop_pixels / len(valid)) * 100, 2)

    return {
        "ndvi_min"      : ndvi_min,
        "ndvi_max"      : ndvi_max,
        "ndvi_mean"     : ndvi_mean,
        "freq_table"    : freq_table,
        "crop_cover_pct": crop_cover_pct,
        "total_pixels"  : len(flat),
        "valid_pixels"  : len(valid)
    }

# ── FUNCTION 8 — Save To Database ─────────────────────────
def save_to_database(field_id, image_date, nd_values,
                     cloud_prob_array, field_cloud_prob,
                     stats, transform):
    """
    Saves pixel data to ndvi_pixels table
    and summary to ndvi_results table.

    Duplicate prevention:
        UNIQUE KEY on (field_id, image_date) in ndvi_results
        UNIQUE KEY on (field_id, image_date, row, col) in ndvi_pixels
        Uses INSERT IGNORE to skip duplicates silently

    Args:
        field_id         : int farm ID
        image_date       : str YYYY-MM-DD
        nd_values        : 2D list of NDVI values
        cloud_prob_array : 2D numpy array cloud probabilities
        field_cloud_prob : float average field cloud
        stats            : dict from calculate_stats()
        transform        : Affine transform
    """
    conn   = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    # Save to ndvi_results first
    cursor.execute("""
        INSERT IGNORE INTO ndvi_results
        (field_id, image_date, ndvi_min, ndvi_max, ndvi_mean,
         cloud_prob, total_pixels, valid_pixels, crop_cover_pct, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        field_id, image_date,
        stats['ndvi_min'], stats['ndvi_max'], stats['ndvi_mean'],
        field_cloud_prob,
        stats['total_pixels'], stats['valid_pixels'],
        stats['crop_cover_pct'], 'clean'
    ))

    # Save each pixel to ndvi_pixels
    pixel_rows = []
    rows       = len(nd_values)
    cols       = len(nd_values[0])

    for r in range(rows):
        for c in range(cols):
            ndvi_val = nd_values[r][c]
            if ndvi_val is None or np.isnan(float(ndvi_val)):
                continue

            ndvi_val   = float(ndvi_val)
            cloud_prob = float(cloud_prob_array[r][c]) \
                         if r < cloud_prob_array.shape[0] \
                         and c < cloud_prob_array.shape[1] else None
            lat, lng   = calculate_pixel_coordinates(r, c, transform)
            ndvi_class = classify_ndvi(ndvi_val)

            pixel_rows.append((
                field_id, image_date,
                r, c,
                lat, lng,
                ndvi_val, cloud_prob,
                ndvi_class
            ))

    # Batch insert all pixels
    cursor.executemany("""
        INSERT IGNORE INTO ndvi_pixels
        (field_id, image_date, pixel_row, pixel_col,
         latitude, longitude, ndvi_value, cloud_prob, ndvi_class)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, pixel_rows)

    conn.commit()
    cursor.close()
    conn.close()

    print(f"Saved {len(pixel_rows)} pixels to database", file=sys.stderr)
    print(f"Saved summary to ndvi_results", file=sys.stderr)

# ── FUNCTION 9 — Print Outputs ─────────────────────────────
def print_outputs(stats, image_date, field_cloud_prob):
    """
    Prints output to stdout in same format as R script.
    PHP reads this output.
    """
    print("Sawie-ndvi-parameters")
    print(
        str(stats['ndvi_min'])  + " " +
        str(stats['ndvi_max'])  + " " +
        str(stats['ndvi_mean']) + " "
    )
    print(json.dumps(stats['freq_table'], separators=(',', ':')))
    print("Sawie-crop-cover")
    print(stats['crop_cover_pct'])
    print(f"image_date:{image_date}")
    print(f"cloud_prob:{field_cloud_prob:.4f}")
    print(f"status:clean")
    print(f"created_at:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

# ── FUNCTION 10 — Save PNGs ────────────────────────────────
def save_pngs(nd_values, polygon, transform, fid):
    """
    Saves 3 PNG files matching R output.
    Called only when explicitly needed.
    In Phase 3 PNGs are generated on demand
    from database pixels via png_generator.py
    """
    fid_int = int(fid)
    arr     = np.array(nd_values, dtype=float)
    band    = arr

    pixel_width  = abs(transform[0])
    pixel_height = abs(transform[4])
    west         = transform[2]
    north        = transform[5]
    height, width = band.shape
    lng_min = west
    lat_max = north
    lng_max = lng_min + width  * pixel_width
    lat_min = lat_max - height * pixel_height

    poly_x = list(polygon.exterior.coords.xy[0])
    poly_y = list(polygon.exterior.coords.xy[1])

    # PNG 1 — NDVI map
    fig, ax     = plt.subplots(figsize=(7, 7))
    band_masked = np.ma.masked_invalid(band)
    cmap_ndvi   = plt.cm.RdYlGn.copy()
    cmap_ndvi.set_bad(color='white')
    im = ax.imshow(band_masked, cmap=cmap_ndvi,
                   extent=[lng_min, lng_max, lat_min, lat_max], origin='upper')
    ax.plot(poly_x, poly_y, color='black', linewidth=2)
    plt.colorbar(im, ax=ax)
    fname = f"ndvi-{fid_int}.png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

    # PNG 2 — NDVI classification
    vegc = np.full(band.shape, np.nan, dtype=float)
    vegc[~np.isnan(band) & (band <= 0.25)]                  = 1
    vegc[~np.isnan(band) & (band > 0.25) & (band <= 0.30)] = 2
    vegc[~np.isnan(band) & (band > 0.30) & (band <= 0.40)] = 3
    vegc[~np.isnan(band) & (band > 0.40) & (band <= 0.50)] = 4
    vegc[~np.isnan(band) & (band > 0.50) & (band <= 0.60)] = 5
    vegc[~np.isnan(band) & (band > 0.60) & (band <= 0.80)] = 6
    vegc[~np.isnan(band) & (band > 0.80)]                  = 7
    vegc_masked    = np.ma.masked_invalid(vegc)
    terrain_colors = ['#F2F2A0','#D4C878','#A8C878',
                      '#78C878','#50A050','#287828','#005000']
    cmap_vegc = mcolors.ListedColormap(terrain_colors)
    cmap_vegc.set_bad(color='white')
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(vegc_masked, cmap=cmap_vegc, vmin=1, vmax=7,
              extent=[lng_min, lng_max, lat_min, lat_max], origin='upper')
    ax.set_title('NDVI based thresholding')
    fname = f"ndviclass-{fid_int}.png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

    # PNG 3 — Crop cover
    crop_cover             = np.full(band.shape, np.nan, dtype=float)
    valid_mask             = ~np.isnan(band)
    crop_cover[valid_mask & (band <= 0.2)] = 0
    crop_cover[valid_mask & (band > 0.2)]  = 1
    crop_masked = np.ma.masked_invalid(crop_cover)
    cmap_crop   = mcolors.ListedColormap(['#A0522D', '#00BB00'])
    cmap_crop.set_bad(color='white')
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(crop_masked, cmap=cmap_crop, vmin=0, vmax=1,
              extent=[lng_min, lng_max, lat_min, lat_max], origin='upper')
    ax.plot(poly_x, poly_y, color='black', linewidth=2)
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='brown', label='No Crop'),
        Patch(facecolor='green', label='Crop Cover')
    ]
    ax.legend(handles=legend_elements, loc='upper right')
    ax.set_title('Crop Cover Classification')
    fname = f"cropcover-{fid_int}.png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

# ── MAIN ───────────────────────────────────────────────────
if __name__ == "__main__":

    lat_values = [
        31.181412, 31.181004, 31.180714, 31.180635,
        31.180370, 31.180285, 31.180252, 31.179164,
        31.179100, 31.179600, 31.179972, 31.179927,
        31.180291, 31.180273
    ] + [0.0] * 26

    lng_values = [
        70.872642, 70.871164, 70.871153, 70.869695,
        70.869708, 70.868820, 70.868386, 70.868454,
        70.869017, 70.871185, 70.871865, 70.872600,
        70.872497, 70.872891
    ] + [0.0] * 26

    cnt        = 14
    fid        = 12362.0
    start_date = '2026-05-06'

    x_coord, y_coord = build_coordinate_arrays(lat_values, lng_values, cnt)
    polygon, gdf     = build_polygon(x_coord, y_coord)

    nd_values, cloud_prob_array, field_cloud_prob, \
    image_date, transform, gee_polygon = fetch_from_gee(polygon, start_date)

    stats = calculate_stats(nd_values, transform)

    save_to_database(
        field_id         = int(fid),
        image_date       = image_date,
        nd_values        = nd_values,
        cloud_prob_array = cloud_prob_array,
        field_cloud_prob = field_cloud_prob,
        stats            = stats,
        transform        = transform
    )

    print_outputs(stats, image_date, field_cloud_prob)
    # PNGs generated on demand via png_generator.py
    # Not saved here