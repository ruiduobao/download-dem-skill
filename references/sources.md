# Provider and Dataset Reference

Verified against official machine-readable or provider pages on 2026-07-19. Provider APIs and terms can change; re-check official links for publication or operational workflows.

## Microsoft Planetary Computer

- STAC API: <https://planetarycomputer.microsoft.com/api/stac/v1>
- Copernicus GLO-30 catalog: <https://planetarycomputer.microsoft.com/dataset/cop-dem-glo-30>
- Copernicus GLO-90 catalog: <https://planetarycomputer.microsoft.com/dataset/cop-dem-glo-90>
- Python SDK: <https://github.com/microsoft/planetary-computer-sdk-for-python>

Use STAC collections `cop-dem-glo-30` and `cop-dem-glo-90`. Public blob assets require short-lived signing; use `planetary_computer.sign_inplace` and never persist signed query strings. Dataset license and attribution remain those of Copernicus DEM.

## AWS Open Data

- Registry entry: <https://registry.opendata.aws/copernicus-dem/>
- Copernicus DEM product handbook and license links are provided by the registry entry.

Use public buckets `copernicus-dem-30m` and `copernicus-dem-90m`. The skill constructs official one-degree COG object paths and reads them anonymously over HTTPS. Availability in a public bucket does not remove license or attribution obligations.

## OpenTopography

- API documentation: <https://portal.opentopography.org/apidocs/>
- Developer information and API keys: <https://opentopography.org/developers>
- Global DEM endpoint: <https://portal.opentopography.org/API/globaldem>

The global DEM API requires a key and supports named products such as `SRTMGL3`, `SRTMGL1`, `SRTMGL1_E`, `AW3D30`, `AW3D30_E`, `SRTM15Plus`, `NASADEM`, `COP30`, `COP90`, and `EU_DTM`. Coverage, usage quota, acknowledgment, and license vary by product. Check the API documentation before relying on a dataset name.

## USGS 3D Elevation Program

- The National Map data delivery: <https://www.usgs.gov/tools/download-data-maps-national-map>
- TNM Access API: <https://tnmaccess.nationalmap.gov/api/v1/docs>
- 3DEP program: <https://www.usgs.gov/3d-elevation-program>

The script maps `10m` to the current TNM API compatibility tag `National Elevation Dataset (NED) 1/3 arc-second` and `1m` to `Digital Elevation Model (DEM) 1 meter`, discovers products through TNM Access, downloads returned archives or GeoTIFFs, and mosaics available raster files. The legacy NED tag remains the official API value even though the NED program name was retired in favor of 3DEP. Coverage is limited to USGS holdings. Inspect each product's metadata for vertical datum, quality, and publication date.

## NASA Earthdata ASTER GDEM V3

- DOI and product landing page: <https://doi.org/10.5067/ASTER/ASTGTM.003>
- CMR collection concept: `C1711961296-LPCLOUD`
- CMR granule API: <https://cmr.earthdata.nasa.gov/search/granules.json>
- ASTER GDEM V3 user guide: <https://lpdaac.usgs.gov/documents/434/ASTGTM_User_Guide_V3.pdf>

Use source `earthdata` and dataset `aster-gdem-v3`. The adapter queries official CMR short name `ASTGTM`, version `003`, filters one-degree granules by positive AOI overlap, and downloads only protected `_dem.tif` assets. It excludes `_num.tif`, which is a source-count/quality layer rather than elevation. Set `EARTHDATA_TOKEN`; never persist the token or Authorization header. ASTER GDEM is a DSM with known artifacts and should not be treated as bare earth.

## Citation and datum cautions

- Copernicus DEM is a surface model and commonly uses EGM2008 heights. Cite the Copernicus DEM license/source specified by the current catalog.
- ASTER GDEM V3 is a surface model referenced to EGM96 according to its product documentation; cite DOI `10.5067/ASTER/ASTGTM.003` and review LP DAAC terms.
- SRTM/NASADEM products commonly use EGM96 orthometric heights, but confirm the specific product documentation.
- USGS 3DEP vertical reference depends on the product and geography; NAVD88 is common in the conterminous US, but do not assume it without metadata.
- A horizontal CRS transformation does not change the vertical datum. Use a geoid/vertical grid and a compound CRS-aware workflow when height conversion is required.
