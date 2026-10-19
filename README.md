# s1-sar-preprocessor

Batch preprocessing of Sentinel-1 Ground Range Detected (GRD) products into calibrated, geocoded backscatter (σ⁰) GeoTIFFs, using ESA SNAP through its Python interface (`esa_snappy` / `snappy`).

I wrote this to prepare Sentinel-1 imagery of polar sea ice for machine learning classification and for comparison with other sensors. It runs as a command-line script on a single product or on a directory of products, and I use it for batch jobs on an HPC cluster.

## Processing chain

Each product goes through the following SNAP operators:

1. **Apply-Orbit-File**: applies precise orbit state vectors. If no orbit file is available, the orbit from the product annotation is kept and processing continues.
2. **Remove-GRD-Border-Noise**: removes low-intensity noise at the swath edges.
3. **ThermalNoiseRemoval**: subtracts the thermal noise floor.
4. **Calibration**: converts digital numbers to σ⁰. Output stays in linear units; convert to dB downstream if needed.
5. **Ellipsoid-Correction-RD**: geocodes the image onto the ellipsoid and reprojects it, by default to NSIDC Polar Stereographic North (EPSG:3413), with bilinear resampling. The incidence angle is saved as a separate band.

The output bands are written as BigTIFF GeoTIFFs, with no-data marked as NaN.

**Note on geocoding:** ellipsoid correction does not use a DEM. That is appropriate for sea ice and open ocean, which lie close to the ellipsoid, but not for land or ice sheet surfaces with real topography. For those, replace step 5 with SNAP's `Terrain-Correction` operator.


## Installation

The script needs ESA SNAP (version 9 or later) with its Python interface configured.

```bash
git clone https://github.com/kimnezhad/s1-sar-preprocessor.git
cd s1-sar-preprocessor
conda env create -f environment.yml
conda activate s1-preproc
```

Then link SNAP to this environment. For SNAP 10 and later, follow ESA's instructions for configuring `esa_snappy`; for SNAP 9, run from SNAP's `bin` directory:

```bash
./snappy-conf <path-to-env>/bin/python <path-to-env>/lib/python3.9/site-packages
```

## Usage

Process a directory of products:

```bash
python preprocess.py \
    --input /path/to/sentinel1_zips \
    --output-dir /path/to/output \
    --pixel-spacing 90 \
    --check-zips
```

Or a single product, which is convenient for running one product per job in a SLURM job array:

```bash
python preprocess.py --input /path/to/S1A_EW_GRDM_1SDH_....zip --output-dir /path/to/output
```

| Argument | Default | Description |
| :--- | :---: | :--- |
| `-i`, `--input` | required | A Sentinel-1 product (`.zip` or `.SAFE`) or a directory of products |
| `-o`, `--output-dir` | required | Root directory for the outputs |
| `-p`, `--pixel-spacing` | `90` | Output pixel spacing in meters |
| `--pols` | `HH,HV` | Polarizations to process, e.g. `VV,VH` for most land acquisitions |
| `--crs` | `EPSG:3413` | Output map projection |
| `--check-zips` | off | Check `.zip` archives for corruption before processing |

Products whose outputs already exist are skipped, so an interrupted run can be restarted with the same command. The script exits with a non-zero status if any product fails.

## Output

```text
<output-dir>/<product_id>/linear_sigma0_geotiffs/
├── <product_id>_Sigma0_HH.tif
├── <product_id>_Sigma0_HV.tif
└── <product_id>_IncidenceAngle.tif
```

## Memory

SNAP holds each intermediate product in Java memory. The script keeps a reference to every product in the processing chain and disposes of all of them after each scene, including when a scene fails, so memory use stays stable over long batch runs.

## License and contact

MIT License. Kimia Mohammadnezhad, kimia_mohammadnezhad@brown.edu
