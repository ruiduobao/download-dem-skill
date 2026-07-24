# download-dem

> Select, discover, download, resume, tile, mosaic, crop, and validate DEM data from
> Microsoft Planetary Computer, AWS Open Data, OpenTopography, USGS 3DEP, and NASA Earthdata.
> Zero credentials required out of the box.
>
> 从微软 Planetary Computer、AWS Open Data、OpenTopography、USGS 3DEP 和 NASA Earthdata
> 选择、检索、下载、续传、分块、拼接、裁剪并验证 DEM。开箱即用、无需凭证。

## Features

- **Multi-source**: MPC, AWS, USGS 3DEP, OpenTopography, NASA Earthdata
- **Auto-fallback**: falls back to public Copernicus DEM when optional keys are missing
- **Chinese admin AOI**: resolve 省/市/区/县/镇/村 to bbox via map.ruiduobao.com
- **Resumable downloads**: `.part` files + `manifest.json` for interruption recovery
- **Mosaic / tiles**: auto-selects mode based on AOI size
- **Validation**: CRS, checksum, coverage checks
- **Zero credentials**: first three providers fully public

## Quick Start

```powershell
# Plan first
python scripts\dem_download.py plan --bbox 116.2 39.8 116.6 40.1

# Download a mosaic
python scripts\dem_download.py download --bbox 116.2 39.8 116.6 40.1 --output beijing.tif

# Download by Chinese admin name
python scripts\dem_download.py download --admin "海淀区" --admin-province "北京市" --output haidian.tif

# Validate
python scripts\dem_download.py validate beijing.tif
```

## Providers

| Source | Public | Best for |
|--------|--------|----------|
| Microsoft Planetary Computer | Yes | Global Copernicus GLO-30/90 |
| AWS Open Data | Yes | Anonymous Copernicus mirror |
| USGS 3DEP | Yes | US 10m / 1m |
| OpenTopography | Optional key | SRTM, NASADEM, AW3D30, EU-DEM |
| NASA Earthdata | Optional token | ASTER GDEM V3 |

## Docs

- [SKILL.md](SKILL.md) — full documentation
- [references/sources.md](references/sources.md) — provider details
- [references/large-area-workflow.md](references/large-area-workflow.md) — large-area jobs

## License

MIT-0
