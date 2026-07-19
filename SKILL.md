---
name: download-dem
description: Select, discover, download, resume, tile, mosaic, crop, and validate digital elevation data from Microsoft Planetary Computer, AWS Open Data, OpenTopography, USGS 3DEP, and NASA Earthdata. Use for city-to-country AOIs; Copernicus DEM, SRTM, NASADEM, ALOS AW3D30, ASTER GDEM V3, and US 3DEP; exact vector masks; large-area native tile downloads; parallel or interrupted transfers; GeoTIFF generation; and reporting CRS, vertical datum, resolution, coverage, NoData, provenance, license, and attribution.
---

# Download DEM

Use `scripts/dem_download.py` for deterministic planning, discovery, transfer, mosaicking, and validation. Keep provider details in [references/sources.md](references/sources.md). Read [references/large-area-workflow.md](references/large-area-workflow.md) for provincial, national, interrupted, or high-resolution jobs.

## Workflow

1. Establish the WGS84 bbox or vector AOI, requested resolution, surface type, output path, and intended use. Ask for the AOI only when neither a geometry nor an unambiguous place boundary is available.
2. Read [references/source-selection.md](references/source-selection.md), then run `plan`. Report AOI area, bbox-grid pixels, estimated asset count, chosen source, product class, vertical datum, credentials, and selected output mode.
3. Keep `--mode auto` unless the user explicitly needs a mosaic or raw tiles. Auto-select a mosaic only when AOI area is at most `10,000 km2` and the bbox grid is at most 100 million pixels; otherwise download source assets without mosaicking.
4. Run `download`. For large jobs, keep the same output path between attempts so `manifest.json` and `.part` files can resume. Use 2-6 workers; start with 4. Do not delete a partial job after a transient failure.
5. Run `validate` on the output GeoTIFF or tile directory. Treat missing CRS, corrupt files, empty rasters, incomplete assets, or no AOI overlap as failures.
6. Deliver the GeoTIFF or tile directory plus its JSON provenance. State whether it is a DSM/DTM, identify the vertical datum, and disclose raw-tile overcoverage, fallbacks, skipped assets, or resampling.

## Commands

Run from this skill directory:

```powershell
python scripts/dem_download.py sources
python scripts/dem_download.py plan --aoi city.geojson --source auto --dataset cop-dem-glo-30 --mode auto
python scripts/dem_download.py download --aoi city.geojson --source mpc --dataset cop-dem-glo-30 --mode auto --output city_dem.tif
python scripts/dem_download.py download --aoi province.geojson --source aws --dataset cop-dem-glo-30 --mode auto --output province_tiles --workers 4
python scripts/dem_download.py download --aoi country.geojson --source mpc --dataset cop-dem-glo-90 --mode tiles --output country_tiles --workers 6
python scripts/dem_download.py download --bbox 86.7 27.8 87.1 28.1 --source opentopography --dataset SRTMGL1 --output srtm.tif
python scripts/dem_download.py download --bbox 120 30 122 32 --source earthdata --dataset aster-gdem-v3 --output aster.tif
python scripts/dem_download.py validate city_dem.tif
python scripts/dem_download.py validate province_tiles
```

## Output Modes

- `auto`: Select `mosaic` only when both area and pixel limits pass; otherwise select `tiles`.
- `mosaic`: Stream COG ranges when possible, write the mosaic in bounded-memory windows, and mask vector AOIs block by block. If streaming fails, stage assets with resumable downloads and retry locally.
- `tiles`: Download provider assets concurrently into `<output>/<source>/`, retain `manifest.json`, preserve `.part` files after interruption, and do not mosaic or apply an exact AOI mask. Raw assets may extend beyond the AOI.

Use `--mosaic-max-area-km2` to change the 10,000 km2 threshold and `--max-pixels` for the bbox-grid limit. Require `--allow-large` for an explicitly oversized mosaic. Do not use it merely to bypass planning.

## Resume And Failure Rules

- Re-run the identical command and output path to resume. Completed assets are skipped; partial HTTP downloads use Range requests when supported.
- Use `--verify-existing` when storage corruption is a concern. Use `validate <tile-dir> --verify-checksums` for a full manifest checksum audit.
- Keep provider, dataset, bbox, and mode identical to the manifest. Use a new output directory for a different job.
- Use `--no-resume` only to restart transfers. Use `--stage-assets` when reproducible local inputs matter more than COG range-read efficiency. Use `--keep-cache` to retain staged mosaic inputs.
- Never persist signed MPC query strings, OpenTopography keys, Earthdata tokens, or Authorization headers. Sidecars and manifests store sanitized URLs only.
- Do not mix partial tiles from different providers. Auto-fallback from MPC to AWS is allowed only before resumable MPC assets have completed.

## Provider Rules

- Use MPC for the default global Copernicus GLO-30/GLO-90 workflow and AWS for direct anonymous Copernicus tiles.
- Use OpenTopography for SRTM, NASADEM, AW3D30, COP30/COP90, or EU-DEM subsets. Set `OPENTOPOGRAPHY_API_KEY`. The adapter splits requests into geographic chunks and limits concurrency to two.
- Use USGS 3DEP for US `10m` or `1m` products. Product archives remain raw in tile mode and are securely extracted only for mosaicking.
- Use NASA Earthdata for `aster-gdem-v3`. Set `EARTHDATA_TOKEN`; discovery uses official CMR collection `ASTGTM.003` and selects only `_dem.tif`, not the quality-count band.
- Do not silently substitute resolution, DSM/DTM class, vertical datum, or geographic coverage.

## Data Integrity

- Treat Copernicus DEM and ASTER GDEM as DSMs. Buildings and vegetation may remain.
- Do not infer accuracy from pixel spacing. Do not merge different vertical datums without a documented vertical transformation.
- Interpret bbox input as EPSG:4326. Split antimeridian-crossing AOIs before download.
- Preserve native values. Do not fill voids, smooth, resample, or derive terrain products unless requested and recorded.
- Verify current license and attribution before publication, redistribution, or commercial use.

## Dependencies

Require Python 3.10+ and `rasterio`; vector AOIs require `fiona`; MPC requires `pystac-client` and `planetary-computer`. Other providers use the Python standard library. Do not install missing packages without user authorization.

## Output Contract

Return the effective output path, mode, source, dataset, access time, AOI area, bbox, dimensions or tile count, CRS, resolution, surface type, vertical datum, validation status, NoData/sample statistics when mosaicked, manifest/checksum information when tiled, official source URLs, and any fallback or limitation.
