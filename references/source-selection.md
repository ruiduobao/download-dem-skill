# DEM Source And Output Selection

## Dataset decision table

| Need | Preferred source | Dataset | Nominal posting | Product class | Vertical reference |
|---|---|---|---:|---|---|
| Global default | MPC | `cop-dem-glo-30` | 1 arc-second (~30 m) | DSM | EGM2008 |
| Global direct public tiles | AWS | `cop-dem-glo-30` | 1 arc-second (~30 m) | DSM | EGM2008 |
| Global smaller files | MPC or AWS | `cop-dem-glo-90` | 3 arc-seconds (~90 m) | DSM | EGM2008 |
| SRTM comparison or named subset | OpenTopography | `SRTMGL1`, `SRTMGL3` | ~30/90 m | DEM | EGM96 |
| NASA SRTM reprocessing | OpenTopography | `NASADEM` | ~30 m | DEM | EGM96 |
| ALOS comparison | OpenTopography | `AW3D30` | ~30 m | DSM | Dataset-specific |
| ASTER GDEM V3 | NASA Earthdata | `aster-gdem-v3` | 1 arc-second (~30 m) | DSM | EGM96 |
| United States regional terrain | USGS | `10m` | 1/3 arc-second (~10 m) | Bare-earth DEM | Product-specific, commonly NAVD88 |
| United States local high detail | USGS | `1m` | 1 m | Bare-earth DEM | Product-specific, commonly NAVD88 |

## Selection procedure

1. Decide whether analysis requires a DSM or bare-earth terrain. Do not treat Copernicus or ASTER DSMs as bare earth without disclosing surface bias.
2. Use the coarsest resolution that supports the analysis. Finer posting increases volume but does not guarantee better vertical accuracy.
3. Prefer one source for the whole AOI. Verify acquisition epoch, horizontal CRS, vertical datum, void handling, and license before combining products.
4. Use `auto` for conventional global requests. Explicitly select USGS, OpenTopography, or Earthdata for named products.
5. For hydrology, flooding, engineering, or change analysis, require documented vertical metadata and inspect artifacts beyond the automatic validation.

## Output mode decision

The planner calculates two independent risk measures:

- AOI area from vector geometry when available, otherwise from bbox.
- Output-grid pixels from the full bbox at native posting, because a sparse or island AOI still creates a rectangular GeoTIFF before masking.

Default `auto` behavior:

| Condition | Output |
|---|---|
| Area <= 10,000 km2 and bbox grid <= 100 million pixels | Windowed mosaic GeoTIFF |
| Area > 10,000 km2 | Resumable raw/source tile directory |
| Bbox grid > 100 million pixels | Resumable raw/source tile directory |
| Explicit `--mode mosaic` above pixel limit | Refuse unless `--allow-large` |

Use raw tiles for national, multi-province, high-resolution, or operational archive jobs. Mosaic only a downstream subregion when analysis requires a seamless raster.

## AOI rules

- Supply WGS84 bbox values in `west south east north` order.
- Reproject vector AOIs to EPSG:4326 for provider queries and apply the geometry as a block-wise mask only in mosaic mode.
- Raw provider tiles are not clipped and may extend beyond the AOI.
- Split antimeridian-crossing AOIs into west/east jobs.
- Reproject to a metric CRS downstream when metric cell size is required; record the resampling method.

