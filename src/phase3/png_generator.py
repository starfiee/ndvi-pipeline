"""
Phase 3 — png_generator.py

Goal:
Read pixel data from ndvi_pixels database table
Reconstruct 2D NDVI grid
Generate PNG on demand
No permanent storage — called only when needed

Called by PHP like:
python png_generator.py [field_id] [image_date] [png_type]

png_type options:
  ndvi      → NDVI color map
  ndviclass → vegetation classification map
  cropcover → crop cover map
  all       → generate all 3 PNGs
"""

import sys
import os
import mysql.connector
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch
import os
from dotenv import load_dotenv
import numpy as np
load_dotenv()
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
    Reads 3 command line arguments from PHP.

    Args:
        args[0] → field_id   (int)
        args[1] → image_date (YYYY-MM-DD)
        args[2] → png_type   (ndvi / ndviclass / cropcover / all)

    Returns:
        field_id   (int)
        image_date (str)
        png_type   (str)
    """
    args = sys.argv[1:]

    if len(args) < 3:
        raise Exception(
            "Usage: python png_generator.py [field_id] [image_date] [png_type]"
        )

    field_id   = int(args[0])
    image_date = str(args[1])
    png_type   = str(args[2])

    return field_id, image_date, png_type

# ── FUNCTION 2 — Load Pixels From Database ─────────────────
def load_pixels_from_db(field_id, image_date):
    """
    Reads all pixels for a field and date from ndvi_pixels table.
    Reconstructs 2D numpy grid from pixel_row and pixel_col.

    Returns:
        band      (numpy 2D array) : NDVI values grid
        lat_grid  (numpy 2D array) : latitude per pixel
        lng_grid  (numpy 2D array) : longitude per pixel
        west      (float)          : min longitude
        east      (float)          : max longitude
        south     (float)          : min latitude
        north     (float)          : max latitude
    """
    conn   = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT pixel_row, pixel_col, latitude, longitude, ndvi_value
        FROM ndvi_pixels
        WHERE field_id  = %s
        AND   image_date = %s
        ORDER BY pixel_row, pixel_col
    """, (field_id, image_date))

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        raise Exception(
            f"No pixels found for field {field_id} on {image_date}"
        )

    # Find grid dimensions
    max_row = max(r[0] for r in rows)
    max_col = max(r[1] for r in rows)

    # Build 2D grids filled with NaN
    band     = np.full((max_row + 1, max_col + 1), np.nan)
    lat_grid = np.full((max_row + 1, max_col + 1), np.nan)
    lng_grid = np.full((max_row + 1, max_col + 1), np.nan)

    # Fill grids with pixel values
    for pixel_row, pixel_col, lat, lng, ndvi_val in rows:
        band    [pixel_row][pixel_col] = ndvi_val
        lat_grid[pixel_row][pixel_col] = lat
        lng_grid[pixel_row][pixel_col] = lng

    # Get extent for map axes
    west  = float(np.nanmin(lng_grid))
    east  = float(np.nanmax(lng_grid))
    south = float(np.nanmin(lat_grid))
    north = float(np.nanmax(lat_grid))

    print(f"Loaded {len(rows)} pixels | Grid: {max_row+1} x {max_col+1}",
          file=sys.stderr)

    return band, lat_grid, lng_grid, west, east, south, north

# ── FUNCTION 3 — Load Polygon From Database ────────────────
def load_polygon_from_db(field_id, image_date):
    """
    Reads boundary pixels to reconstruct polygon outline.
    Uses min/max lat/lng from pixel data as approximate boundary.

    In future Phase 4 we will store actual polygon coordinates.
    For now we use pixel extent as boundary approximation.

    Returns:
        poly_x (list) : longitude boundary points
        poly_y (list) : latitude boundary points
    """
    conn   = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT MIN(longitude), MAX(longitude),
               MIN(latitude),  MAX(latitude)
        FROM ndvi_pixels
        WHERE field_id   = %s
        AND   image_date = %s
    """, (field_id, image_date))

    result = cursor.fetchone()
    cursor.close()
    conn.close()

    west, east, south, north = result

    # Simple rectangle boundary from pixel extent
    poly_x = [west, east, east, west, west]
    poly_y = [north, north, south, south, north]

    return poly_x, poly_y

# ── FUNCTION 4 — Generate NDVI PNG ────────────────────────
def generate_ndvi_png(band, west, east, south, north,
                      poly_x, poly_y, field_id, image_date):
    """
    Generates NDVI color map PNG.
    Red = low NDVI, Yellow = medium, Green = high.
    Matches R script output format.

    Saves to: ndvi-{field_id}.png
    """
    fig, ax     = plt.subplots(figsize=(7, 7))
    band_masked = np.ma.masked_invalid(band)
    cmap_ndvi   = plt.cm.RdYlGn.copy()
    cmap_ndvi.set_bad(color='white')

    im = ax.imshow(
        band_masked,
        cmap    = cmap_ndvi,
        extent  = [west, east, south, north],
        origin  = 'upper'
    )
    ax.plot(poly_x, poly_y, color='black', linewidth=2)
    ax.set_title(f'NDVI - Field {field_id} - {image_date}')
    plt.colorbar(im, ax=ax)

    fname = f"ndvi-{field_id}.png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

# ── FUNCTION 5 — Generate NDVI Class PNG ──────────────────
def generate_ndviclass_png(band, west, east, south, north,
                           field_id, image_date):
    """
    Generates NDVI vegetation classification PNG.
    7 categories from yellow to dark green.
    Matches R script reclass_matrix output.

    Saves to: ndviclass-{field_id}.png
    """
    vegc = np.full(band.shape, np.nan, dtype=float)
    vegc[~np.isnan(band) & (band <= 0.25)]                  = 1
    vegc[~np.isnan(band) & (band > 0.25) & (band <= 0.30)] = 2
    vegc[~np.isnan(band) & (band > 0.30) & (band <= 0.40)] = 3
    vegc[~np.isnan(band) & (band > 0.40) & (band <= 0.50)] = 4
    vegc[~np.isnan(band) & (band > 0.50) & (band <= 0.60)] = 5
    vegc[~np.isnan(band) & (band > 0.60) & (band <= 0.80)] = 6
    vegc[~np.isnan(band) & (band > 0.80)]                  = 7

    vegc_masked    = np.ma.masked_invalid(vegc)
    terrain_colors = [
        '#F2F2A0',  # 1 pale yellow
        '#D4C878',  # 2 yellow green
        '#A8C878',  # 3 light green
        '#78C878',  # 4 green
        '#50A050',  # 5 medium green
        '#287828',  # 6 dark green
        '#005000',  # 7 very dark green
    ]
    cmap_vegc = mcolors.ListedColormap(terrain_colors)
    cmap_vegc.set_bad(color='white')

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(
        vegc_masked,
        cmap   = cmap_vegc,
        vmin   = 1,
        vmax   = 7,
        extent = [west, east, south, north],
        origin = 'upper'
    )
    ax.set_title(f'NDVI Classification - Field {field_id} - {image_date}')

    fname = f"ndviclass-{field_id}.png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

# ── FUNCTION 6 — Generate Crop Cover PNG ──────────────────
def generate_cropcover_png(band, west, east, south, north,
                           poly_x, poly_y, field_id, image_date):
    """
    Generates crop cover PNG.
    Brown = no crop (NDVI <= 0.2)
    Green = crop present (NDVI > 0.2)
    Matches R script crop_cover output.

    Saves to: cropcover-{field_id}.png
    """
    crop_cover = np.full(band.shape, np.nan, dtype=float)
    valid_mask = ~np.isnan(band)
    crop_cover[valid_mask & (band <= 0.2)] = 0
    crop_cover[valid_mask & (band > 0.2)]  = 1

    crop_masked = np.ma.masked_invalid(crop_cover)
    cmap_crop   = mcolors.ListedColormap(['#A0522D', '#00BB00'])
    cmap_crop.set_bad(color='white')

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.imshow(
        crop_masked,
        cmap   = cmap_crop,
        vmin   = 0,
        vmax   = 1,
        extent = [west, east, south, north],
        origin = 'upper'
    )
    ax.plot(poly_x, poly_y, color='black', linewidth=2)

    legend_elements = [
        Patch(facecolor='brown', label='No Crop'),
        Patch(facecolor='green', label='Crop Cover')
    ]
    ax.legend(handles=legend_elements, loc='upper right')
    ax.set_title(f'Crop Cover - Field {field_id} - {image_date}')

    fname = f"cropcover-{field_id}.png"
    plt.savefig(fname, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"Saved: {fname}")

# ── MAIN ───────────────────────────────────────────────────
if __name__ == "__main__":

    # Parse arguments
    field_id, image_date, png_type = parse_arguments()

    print(f"Generating {png_type} PNG for field {field_id} on {image_date}",
          file=sys.stderr)

    # Load pixel data from database
    band, lat_grid, lng_grid, west, east, south, north = \
        load_pixels_from_db(field_id, image_date)

    # Load polygon boundary
    poly_x, poly_y = load_polygon_from_db(field_id, image_date)

    # Generate requested PNG
    if png_type == 'ndvi' or png_type == 'all':
        generate_ndvi_png(
            band, west, east, south, north,
            poly_x, poly_y, field_id, image_date
        )

    if png_type == 'ndviclass' or png_type == 'all':
        generate_ndviclass_png(
            band, west, east, south, north,
            field_id, image_date
        )

    if png_type == 'cropcover' or png_type == 'all':
        generate_cropcover_png(
            band, west, east, south, north,
            poly_x, poly_y, field_id, image_date
        )

    print("Done", file=sys.stderr)