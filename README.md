# DEM Downloader / DEM 下载器

A Codex skill for selecting, discovering, downloading, resuming, tiling, mosaicking, cropping, and validating digital elevation models from multiple official platforms.

一个面向 Codex 的多源 DEM 技能，用于从多个官方平台选择、检索、下载、断点续传、分块、拼接、裁剪和验证数字高程数据。

## Features / 功能

- Supports Microsoft Planetary Computer, AWS Open Data, OpenTopography, USGS 3DEP, and NASA Earthdata.
- Covers Copernicus DEM GLO-30/GLO-90, SRTM, NASADEM, ALOS AW3D30, ASTER GDEM V3, EU-DEM, and US 3DEP.
- Automatically mosaics small AOIs and keeps native source tiles for large AOIs.
- Uses bounded-memory windowed mosaicking, parallel transfers, HTTP Range resume, manifests, and checksums.
- Validates CRS, coverage, dimensions, NoData, tile completeness, and file integrity.

- 支持 Microsoft Planetary Computer、AWS Open Data、OpenTopography、USGS 3DEP 和 NASA Earthdata。
- 覆盖 Copernicus DEM GLO-30/GLO-90、SRTM、NASADEM、ALOS AW3D30、ASTER GDEM V3、EU-DEM 和 US 3DEP。
- 小范围自动拼接，大范围保留原始数据源瓦片。
- 支持受控内存窗口拼接、并行传输、HTTP Range 断点续传、任务清单和校验和。
- 验证 CRS、覆盖范围、尺寸、NoData、瓦片完整性和文件完整性。

## Output Strategy / 输出策略

With `--mode auto`, a mosaic is produced only when the AOI is at most `10,000 km2` and the estimated grid is at most 100 million pixels. Larger jobs download resumable native assets without forcing an expensive mosaic.

使用 `--mode auto` 时，仅当 AOI 不超过 `10,000 km2` 且预计栅格不超过 1 亿像元才生成拼接结果。更大任务下载可续传的原始资产，避免强制执行高成本拼接。

## Quick Start / 快速开始

Requirements: Python 3.10+ and `rasterio`. Vector AOIs also require `fiona`; Microsoft Planetary Computer requires `pystac-client` and `planetary-computer`.

依赖：Python 3.10+ 和 `rasterio`。矢量 AOI 还需要 `fiona`；Microsoft Planetary Computer 需要 `pystac-client` 和 `planetary-computer`。

```powershell
python scripts/dem_download.py sources
python scripts/dem_download.py plan --aoi city.geojson --source auto --dataset cop-dem-glo-30 --mode auto
python scripts/dem_download.py download --aoi city.geojson --source mpc --dataset cop-dem-glo-30 --mode auto --output city_dem.tif
python scripts/dem_download.py download --aoi province.geojson --source aws --dataset cop-dem-glo-30 --mode auto --output province_tiles --workers 4
python scripts/dem_download.py validate city_dem.tif
```

Re-run the same download command with the same output path to resume an interrupted job. Credentials are read from environment variables and are never stored in manifests or provenance files.

下载中断后，使用相同输出路径重新运行同一命令即可续传。凭据从环境变量读取，不会写入任务清单或溯源文件。

## Data Sources / 数据源

- [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/)
- [AWS Open Data Registry](https://registry.opendata.aws/)
- [OpenTopography](https://opentopography.org/)
- [USGS 3DEP](https://www.usgs.gov/3d-elevation-program)
- [NASA Earthdata](https://www.earthdata.nasa.gov/)

Each dataset retains its original license, attribution requirements, product class, and vertical datum. Confirm current source terms before redistribution or commercial use.

每个数据集保留其原始许可、署名要求、产品类型和垂直基准。再分发或商业使用前，请核验数据源的最新条款。

## Installation / 安装

Place the `download-dem` directory under your Codex skills directory, then invoke it with `$download-dem`.

将 `download-dem` 目录放入 Codex skills 目录，然后使用 `$download-dem` 调用。

The published package is also available on [ClawHub](https://clawhub.ai/ruiduobao/skills/download-dem).

发布版本也可从 [ClawHub](https://clawhub.ai/ruiduobao/skills/download-dem) 获取。
