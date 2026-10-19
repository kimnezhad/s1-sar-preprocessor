"""
Batch preprocessing of Sentinel-1 GRD products into geocoded, calibrated
linear sigma0 GeoTIFFs with ESA SNAP (snappy / esa_snappy).

Processing chain, per product:
    Apply-Orbit-File -> Remove-GRD-Border-Noise -> ThermalNoiseRemoval
    -> Calibration (sigma0, linear) -> Ellipsoid-Correction-RD

Outputs, per product:
    <output>/<product_id>/linear_sigma0_geotiffs/<product_id>_Sigma0_<POL>.tif
    <output>/<product_id>/linear_sigma0_geotiffs/<product_id>_IncidenceAngle.tif
"""

import argparse
import sys
import zipfile
from pathlib import Path

# SNAP 10+ ships the Python bindings as `esa_snappy`; older versions use `snappy`.
try:
    from esa_snappy import GPF, HashMap, ProductIO
except ImportError:
    from snappy import GPF, HashMap, ProductIO

DEFAULT_CRS = "EPSG:3413"  # NSIDC Sea Ice Polar Stereographic North
DEFAULT_POLS = "HH,HV"


def find_products(input_path):
    """Return a sorted list of Sentinel-1 products (.zip or .SAFE) at input_path.

    input_path may be a directory of products or a single product.
    """
    if input_path.suffix.lower() in (".zip", ".safe"):
        return [input_path]
    products = list(input_path.glob("*.zip")) + list(input_path.glob("*.SAFE"))
    return sorted(products)


def zip_is_valid(path):
    """Return True if the zip archive opens and passes a CRC check."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.testzip() is None
    except zipfile.BadZipFile:
        return False


def write_band_to_geotiff(product, band_name, out_path):
    """Write a single band of `product` to a BigTIFF GeoTIFF."""
    params = HashMap()
    params.put("sourceBands", band_name)
    params.put("copyMetadata", True)
    subset = GPF.createProduct("Subset", params, product)
    try:
        # BigTIFF avoids the 4 GB limit of classic TIFF for large swaths.
        ProductIO.writeProduct(subset, str(out_path), "GeoTIFF-BigTIFF")
    finally:
        subset.dispose()


def process_product(prod_path, output_root, pols, pixel_spacing, crs):
    """Run the processing chain on one product and write its GeoTIFFs.

    Returns "processed" or "skipped". Raises on processing errors.
    """
    image_id = prod_path.stem if prod_path.suffix.lower() == ".zip" else prod_path.name[:-5]
    out_dir = output_root / image_id / "linear_sigma0_geotiffs"
    out_paths = {f"Sigma0_{p}": out_dir / f"{image_id}_Sigma0_{p}.tif" for p in pols}
    ia_path = out_dir / f"{image_id}_IncidenceAngle.tif"

    if all(p.exists() for p in out_paths.values()) and ia_path.exists():
        print("   All outputs already exist, skipping.")
        return "skipped"
    out_dir.mkdir(parents=True, exist_ok=True)

    # SNAP reads an unpacked product through its manifest file.
    read_path = prod_path / "manifest.safe" if prod_path.is_dir() else prod_path

    # Every product in the chain holds Java resources, so keep references
    # to all of them and dispose of each one at the end, not only the last.
    chain = []
    try:
        product = ProductIO.readProduct(str(read_path))
        if product is None:
            raise RuntimeError(f"SNAP could not read {read_path}")
        chain.append(product)

        def step(operator, params):
            chain.append(GPF.createProduct(operator, params, chain[-1]))

        pol_string = ",".join(pols)

        # 1. Precise orbit; if no orbit file is available, SNAP keeps the
        #    orbit state vectors from the product annotation.
        p = HashMap()
        p.put("continueOnFail", True)
        step("Apply-Orbit-File", p)

        # 2. Border noise at the swath edges
        p = HashMap()
        p.put("selectedPolarisations", pol_string)
        step("Remove-GRD-Border-Noise", p)

        # 3. Thermal noise
        p = HashMap()
        p.put("selectedPolarisations", pol_string)
        step("ThermalNoiseRemoval", p)

        # 4. Radiometric calibration to sigma0, kept in linear units
        p = HashMap()
        p.put("outputSigmaBand", True)
        p.put("selectedPolarisations", pol_string)
        step("Calibration", p)

        # 5. Geocoding on the ellipsoid (no DEM) and reprojection
        p = HashMap()
        p.put("pixelSpacingInMeter", float(pixel_spacing))
        p.put("mapProjection", crs)
        p.put("imgResamplingMethod", "BILINEAR_INTERPOLATION")
        p.put("saveIncidenceAngleFromEllipsoid", True)
        step("Ellipsoid-Correction-RD", p)

        final = chain[-1]
        band_names = list(final.getBandNames())
        print(f"   Bands: {band_names}")

        # 6. Mark no-data as NaN in the band metadata
        for name in band_names:
            band = final.getBand(name)
            band.setNoDataValue(float("nan"))
            band.setNoDataValueUsed(True)

        # 7. Write each sigma0 band
        for band_name, path in out_paths.items():
            if path.exists():
                continue
            if band_name not in band_names:
                print(f"   WARNING: {band_name} not in product.")
                continue
            write_band_to_geotiff(final, band_name, path)
            print(f"   Saved {band_name}")

        # 8. Write the incidence angle band
        if not ia_path.exists():
            ia_band = next((b for b in band_names if "incidence" in b.lower()), None)
            if ia_band is None:
                print("   WARNING: no incidence angle band found.")
            else:
                write_band_to_geotiff(final, ia_band, ia_path)
                print("   Saved incidence angle")

        return "processed"
    finally:
        for prod in reversed(chain):
            prod.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="Preprocess Sentinel-1 GRD products into geocoded linear sigma0 GeoTIFFs with ESA SNAP."
    )
    parser.add_argument("-i", "--input", required=True, type=Path,
                        help="A Sentinel-1 product (.zip or .SAFE) or a directory of products.")
    parser.add_argument("-o", "--output-dir", required=True, type=Path,
                        help="Root directory for the output GeoTIFFs.")
    parser.add_argument("-p", "--pixel-spacing", type=float, default=90.0,
                        help="Output pixel spacing in meters (default: 90).")
    parser.add_argument("--pols", default=DEFAULT_POLS,
                        help="Comma-separated polarizations to process (default: HH,HV).")
    parser.add_argument("--crs", default=DEFAULT_CRS,
                        help="Output map projection (default: EPSG:3413).")
    parser.add_argument("--check-zips", action="store_true",
                        help="Check .zip archives for corruption before processing.")
    args = parser.parse_args()

    pols = [p.strip().upper() for p in args.pols.split(",") if p.strip()]
    products = find_products(args.input)
    if not products:
        print(f"No Sentinel-1 products found at {args.input}")
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(f"{len(products)} product(s) | pols {pols} | {args.pixel_spacing} m | {args.crs}")

    counts = {"processed": 0, "skipped": 0, "failed": 0}
    for prod_path in products:
        print(f"\n--- {prod_path.name} ---")
        if args.check_zips and prod_path.suffix.lower() == ".zip" and not zip_is_valid(prod_path):
            print("   WARNING: corrupted zip, skipping.")
            counts["skipped"] += 1
            continue
        try:
            counts[process_product(prod_path, args.output_dir, pols,
                                   args.pixel_spacing, args.crs)] += 1
        except Exception as e:
            print(f"   ERROR: {e}")
            counts["failed"] += 1

    print("\n" + "=" * 40)
    print(f"Products : {len(products)}")
    print(f"Processed: {counts['processed']}")
    print(f"Skipped  : {counts['skipped']}")
    print(f"Failed   : {counts['failed']}")
    print("=" * 40)

    # Non-zero exit status so batch schedulers (e.g. SLURM) flag failed runs.
    sys.exit(1 if counts["failed"] else 0)


if __name__ == "__main__":
    main()
