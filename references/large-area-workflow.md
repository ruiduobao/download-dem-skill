# Large-Area And Resumable Workflow

## Purpose

Use this workflow for AOIs above 10,000 km2, bbox grids above 100 million pixels, province/country jobs, 1-10 m products, unreliable networks, or more than a few dozen source assets.

## Plan before transfer

Run `plan` and inspect:

- `aoi_area_km2` and `area_method`;
- `estimated_pixels_for_bbox_grid` and uncompressed float32 size;
- `estimated_asset_count`;
- `selected_mode` and `mode_reason`;
- provider credentials, surface class, and vertical datum.

Do not estimate memory from AOI land area alone. A masked GeoTIFF still uses the full rectangular bbox grid.

## Tile strategy

`--mode auto` selects `tiles` when area or pixel limits are exceeded. Native one-degree tiles are used for MPC, AWS, and ASTER. USGS uses TNM products. OpenTopography has no native public tile listing, so the adapter requests non-mosaicked geographic subset chunks controlled by `--chunk-degrees`.

Tile mode creates:

```text
<output>/
  dem-job.json
  <provider>/
    manifest.json
    source-file-1.tif
    source-file-2.tif.part
```

It does not crop, mask, or mosaic. This preserves provider inputs, bounds memory, and makes failure recovery local to an asset.

## Resume and concurrency

- Re-run the identical command to resume. The manifest identity includes provider, dataset, bbox, and mode.
- Completed files are skipped when recorded byte size matches.
- Add `--verify-existing` to hash completed files before skipping. Run `validate <tile-dir> --verify-checksums` for a final full audit.
- Partial files use `.part`; HTTP Range is used when supported, otherwise that asset restarts.
- Each completed file records SHA-256. Manifests never record credentials or signed URL queries.
- Use `--workers 4` initially. Increase to 6 only for stable public object storage. OpenTopography is capped at two concurrent requests.
- Use `--retries 4 --timeout 120` as defaults. Retryable HTTP status codes use exponential backoff.
- Keep `--max-assets` and `--max-asset-gb` safeguards unless the plan justifies overriding them.

## Windowed mosaic strategy

Mosaic mode uses `rasterio.merge` with `dst_path` and `mem_limit`; it writes bounded chunks directly to a temporary GeoTIFF. Exact AOI masking then updates one internal raster block at a time. The old implementation allocated the entire mosaic array and a full-size geometry mask simultaneously.

For MPC/AWS, the default attempts remote COG range reads. If this fails, the script stages all assets through the resumable downloader and retries locally. Use `--stage-assets` to choose that path immediately. Successful output atomically replaces an existing file only after the temporary mosaic is complete.

## Recovery matrix

| Failure | Action |
|---|---|
| Timeout, 429, or 5xx | Re-run identical command; partial asset resumes |
| 401/403 from Earthdata | Refresh `EARTHDATA_TOKEN`; re-run |
| OpenTopography quota/API error | Reduce `--workers`, split AOI, or wait for quota reset |
| Manifest identity mismatch | Use a new output directory or restore original parameters |
| One or more corrupt tiles | Remove only named corrupt file and its `.part`, mark/re-run job |
| Mosaic fails after downloads | Re-run with `--stage-assets --keep-cache`; downloaded assets remain reusable |
| Too many assets | Split AOI or intentionally use `--allow-many-assets` after reviewing storage |

## Post-download use

Validate the tile directory before downstream processing. Mosaic only the analysis subregion, keep data from one dataset/version/vertical datum, and build a VRT or cloud-native index when a seamless virtual view is sufficient. Avoid a country-scale physical GeoTIFF unless the consumer explicitly requires it.
