---
name: download-dem
description: Select, discover, download, resume, tile, mosaic, crop, and validate DEM data from Microsoft Planetary Computer, AWS Open Data, OpenTopography, USGS 3DEP, and NASA Earthdata. Works out of the box with zero credentials — OpenTopography and Earthdata auto-fall back to public Copernicus DEM when keys are missing. Use for city-to-country AOIs, multi-source DEM selection, exact vector masks, resumable large-area downloads, GeoTIFF generation, and provenance reporting. 从微软 Planetary Computer、AWS Open Data、OpenTopography、USGS 3DEP 和 NASA Earthdata 选择、检索、下载、续传、分块、拼接、裁剪并验证 DEM；开箱即用、无需凭证（OpenTopography 和 Earthdata 在缺 key 时自动降级到公开 Copernicus DEM）。适用于城市到国家尺度、多源数据选择、矢量精确掩膜、大范围断点续传、GeoTIFF 生成和溯源报告。
---

# Download DEM / 下载 DEM

> **Zero-credential by default. / 默认零凭证。** Install the skill, run the command — no API key, no `.env`, no token. The first three providers (Microsoft Planetary Computer, AWS, USGS 3DEP) are fully public. OpenTopography and NASA Earthdata are supported but optional: if the corresponding environment variable is missing, requests auto-fall back to the public Microsoft Planetary Computer Copernicus DEM (typically `cop-dem-glo-30`), and the resulting `plan`/`download` output reports the substitution in a `credential_fallback` block.
>
> 装好即用，无需任何 API key 或 token。前 3 个数据源（Microsoft Planetary Computer、AWS、USGS 3DEP）完全公开。OpenTopography 与 NASA Earthdata 仍可指定，但若环境变量缺失，会自动降级到 Microsoft Planetary Computer 上的公开 Copernicus DEM（通常 `cop-dem-glo-30`），并由 `plan`/`download` 的 `credential_fallback` 字段如实回报。

Use `scripts/dem_download.py` for deterministic planning, discovery, transfer, mosaicking, and validation. Keep provider details in [references/sources.md](references/sources.md). Read [references/large-area-workflow.md](references/large-area-workflow.md) for provincial, national, interrupted, or high-resolution jobs.

使用 `scripts/dem_download.py` 完成可复现的规划、检索、传输、拼接和验证。数据源细节见 [references/sources.md](references/sources.md)；省级、国家级、中断恢复或高分辨率任务需阅读 [references/large-area-workflow.md](references/large-area-workflow.md)。

## Quickstart / 快速开始

```powershell
# Plan first — see what source/dataset/area/mode will be used.
# 先 plan，看实际会用的数据源、数据集、面积、模式。
python scripts\dem_download.py plan --bbox 116.2 39.8 116.6 40.1

# Download a windowed mosaic (Beijing, ~1100 km², 30 m, ~5 MB).
# 下载拼接结果（北京，约 1100 平方公里，30 米，约 5 MB）。
python scripts\dem_download.py download --bbox 116.2 39.8 116.6 40.1 --output beijing.tif --workers 4

# Validate the result.
python scripts\dem_download.py validate beijing.tif
```

No environment variable to set, no account to register. If you want to use OpenTopography's higher-fidelity EU-DEM or Earthdata's ASTER GDEM V3, set the matching variable and the skill uses the richer source automatically; otherwise it transparently uses public Copernicus DEM and tells you so in the JSON output.

不需要设任何环境变量，也不需要注册账号。如果想用 OpenTopography 的高精度 EU-DEM 或 Earthdata 的 ASTER GDEM V3，设上对应变量即可被自动启用；否则透明地使用公开 Copernicus DEM，并在 JSON 输出中告知。

## Workflow / 工作流程

1. Establish the WGS84 bbox or vector AOI, requested resolution, surface type, output path, and intended use. Ask for the AOI only when neither a geometry nor an unambiguous place boundary is available.
   明确 WGS84 边界框或矢量 AOI、目标分辨率、表面类型、输出路径和用途。仅当既无几何范围也无明确行政区时询问 AOI。
2. Read [references/source-selection.md](references/source-selection.md), then run `plan`. Report AOI area, bbox-grid pixels, estimated asset count, chosen source, product class, vertical datum, credentials, selected output mode, and any `credential_fallback` if the requested source was downgraded for missing keys.
   阅读 [references/source-selection.md](references/source-selection.md) 后运行 `plan`，报告 AOI 面积、边界框像元数、预计资产数、数据源、产品类型、垂直基准、认证要求、输出模式，若因缺 key 而降级还会显示 `credential_fallback`。
3. Keep `--mode auto` unless the user explicitly needs a mosaic or raw tiles. Auto-select a mosaic only when AOI area is at most `10,000 km2` and the bbox grid is at most 100 million pixels; otherwise download source assets without mosaicking.
   除非用户明确要求拼接或原始瓦片，否则保留 `--mode auto`。仅当 AOI 不超过 `10,000 km2` 且边界框不超过 1 亿像元时自动拼接，否则只下载原始资产。
4. Run `download`. For large jobs, keep the same output path between attempts so `manifest.json` and `.part` files can resume. Use 2-6 workers; start with 4. Do not delete a partial job after a transient failure.
   运行 `download`。大任务重试时保持相同输出路径，以便利用 `manifest.json` 和 `.part` 文件续传。并发数使用 2-6，默认从 4 开始；暂时性故障后不要删除未完成任务。
5. Run `validate` on the output GeoTIFF or tile directory. Treat missing CRS, corrupt files, empty rasters, incomplete assets, or no AOI overlap as failures.
   对输出 GeoTIFF 或瓦片目录运行 `validate`。缺少 CRS、文件损坏、空栅格、资产不完整或与 AOI 无重叠均视为失败。
6. Deliver the GeoTIFF or tile directory plus its JSON provenance. State whether it is a DSM/DTM, identify the vertical datum, and disclose raw-tile overcoverage, fallbacks, skipped assets, or resampling. Always include any `credential_fallback` that fired.
   交付 GeoTIFF 或瓦片目录及 JSON 溯源文件，说明 DSM/DTM 类型、垂直基准、原始瓦片范围外扩、回退、跳过资产或重采样，并始终附上发生的 `credential_fallback`。

## Commands / 命令

Run from this skill directory. 在技能目录中运行：

```powershell
# Discovery
python scripts\dem_download.py sources

# Plan
python scripts\dem_download.py plan --bbox 116.2 39.8 116.6 40.1
python scripts\dem_download.py plan --aoi city.geojson --source auto --dataset cop-dem-glo-30 --mode auto
python scripts\dem_download.py plan --bbox 86.7 27.8 87.1 28.1 --source opentopography --dataset SRTMGL1
python scripts\dem_download.py plan --bbox 121.0 30.0 121.5 30.5 --source earthdata --dataset aster-gdem-v3

# Download (mosaic, tiles, with optional staging)
python scripts\dem_download.py download --bbox 116.2 39.8 116.6 40.1 --output city_dem.tif
python scripts\dem_download.py download --aoi province.geojson --source aws --dataset cop-dem-glo-30 --mode auto --output province.tif --workers 4
python scripts\dem_download.py download --aoi country.geojson --source mpc --dataset cop-dem-glo-90 --mode tiles --output country_tiles --workers 6
python scripts\dem_download.py download --bbox 86.7 27.8 87.1 28.1 --source opentopography --dataset SRTMGL1 --output srtm.tif
python scripts\dem_download.py download --bbox 120 30 122 32 --source earthdata --dataset aster-gdem-v3 --output aster.tif
python scripts\dem_download.py download --bbox 116.2 39.8 116.6 40.1 --output city_dem.tif --stage-assets --keep-cache

# Validate
python scripts\dem_download.py validate city_dem.tif
python scripts\dem_download.py validate province_tiles --verify-checksums
```

## Providers / 数据源

| Source | Public? | Best for | Credential env (optional) |
| --- | --- | --- | --- |
| `mpc` Microsoft Planetary Computer | Public | Default global Copernicus GLO-30 / GLO-90 | — |
| `aws` AWS Open Data | Public | Direct anonymous Copernicus tiles, alternate mirror | — |
| `usgs` USGS 3DEP | Public | US 10 m / 1 m | — |
| `opentopography` | Public **with optional key** | SRTM, NASADEM, AW3D30, EU-DEM | `OPENTOPOGRAPHY_API_KEY` |
| `earthdata` NASA Earthdata | Public **with optional token** | ASTER GDEM V3 | `EARTHDATA_TOKEN` |

- `mpc`, `aws`, `usgs` always work out of the box.
- `opentopography` and `earthdata` are best-effort: if the matching env var is set the call uses that provider; otherwise the call auto-falls back to `mpc` `cop-dem-glo-30` (or `cop-dem-glo-90` for `--resolution >= 90`). The `plan` JSON includes a `credential_fallback` block, and `download` emits a `credential_fallback` event so the substitution is always visible.
- `mpc`、`aws`、`usgs` 开箱即用。
- `opentopography` 和 `earthdata` 是可选增强：设了环境变量就用原数据源，否则自动降级到 `mpc` 的 `cop-dem-glo-30`（`--resolution >= 90` 时降级到 `cop-dem-glo-90`）；`plan` 的 JSON 里有 `credential_fallback` 块，`download` 会发出 `credential_fallback` 事件，透明可见。

## Output Modes / 输出模式

- `auto`: Select `mosaic` only when both area and pixel limits pass; otherwise select `tiles`. 仅当面积和像元数均通过限制时选择 `mosaic`，否则选择 `tiles`。
- `mosaic`: Stream COG ranges when possible, write the mosaic in bounded-memory windows, and mask vector AOIs block by block. If streaming fails, stage assets with resumable downloads and retry locally. 尽量流式读取 COG，并以受控内存窗口写入拼接结果和分块掩膜；流式读取失败时，续传下载资产后在本地重试。
- `tiles`: Download provider assets concurrently into `<output>_tiles/<source>/` (a sibling directory next to `<output>`, not a subdirectory of it), retain `manifest.json`, preserve `.part` files after interruption, and do not mosaic or apply an exact AOI mask. Raw assets may extend beyond the AOI. 并发下载到 `<output>_tiles/<source>/`（与 `<output>` 同级的兄弟目录，不是其子目录），保留 `manifest.json` 和中断后的 `.part` 文件，不拼接也不执行精确 AOI 掩膜；原始瓦片可能超出 AOI。

Use `--mosaic-max-area-km2` to change the 10,000 km2 threshold and `--max-pixels` for the bbox-grid limit. Require `--allow-large` for an explicitly oversized mosaic. Both `plan` and `download` accept `--allow-large`; `plan` only acknowledges it and adds a warning, while `download` actually bypasses the size check. Do not use `--allow-large` merely to bypass planning.

使用 `--mosaic-max-area-km2` 调整 10,000 km2 阈值，使用 `--max-pixels` 调整边界框像元上限。超限拼接必须显式指定 `--allow-large`；`plan` 与 `download` 都接受此参数，`plan` 只确认并添加警告，`download` 才会真正跳过尺寸检查。不要仅为绕过规划而使用 `--allow-large`。

### Where staged assets go / 暂存资产位置

When `--stage-assets` is used in mosaic mode (with or without `--keep-cache`), the cached native tiles are written to a sibling hidden directory `.<output>.parts/<source>/` next to `<output>`, not into `<output>` itself. Without `--keep-cache` the cache is removed on success; with `--keep-cache` it is retained for inspection and reuse. Tile mode never uses a hidden cache: it writes to `<output>_tiles/<source>/` directly.

在 mosaic 模式下使用 `--stage-assets`（无论是否带 `--keep-cache`）时，缓存的原始瓦片会写入 `<output>` 同级的隐藏目录 `.<output>.parts/<source>/`，不会写入 `<output>` 本身。不加 `--keep-cache` 时缓存会在成功后被删除；加 `--keep-cache` 时会保留以便检查和复用。tiles 模式不使用隐藏缓存，直接写入 `<output>_tiles/<source>/`。

## Resume And Failure Rules / 续传与故障规则

- Re-run the identical command and output path to resume. Completed assets are skipped; partial HTTP downloads use Range requests when supported. 使用完全相同的命令和输出路径续传；跳过已完成资产，并在服务端支持时使用 HTTP Range 续传。
- Use `--verify-existing` when storage corruption is a concern. Use `validate <tile-dir> --verify-checksums` for a full manifest checksum audit. 怀疑存储损坏时使用 `--verify-existing`；完整清单校验使用 `validate <tile-dir> --verify-checksums`。
- Keep provider, dataset, bbox, and mode identical to the manifest. Use a new output directory for a different job. 数据源、数据集、边界框和模式必须与清单一致；不同任务使用新目录。
- Use `--no-resume` only to restart transfers. Use `--stage-assets` when reproducible local inputs matter more than COG range-read efficiency. Use `--keep-cache` to retain staged mosaic inputs. 仅在需要重新传输时使用 `--no-resume`；重视本地输入可复现性时使用 `--stage-assets`；需要保留拼接缓存时使用 `--keep-cache`。
- Never persist signed MPC query strings, OpenTopography keys, Earthdata tokens, or Authorization headers. Sidecars and manifests store sanitized URLs only. 不得持久化 MPC 签名参数、OpenTopography 密钥、Earthdata 令牌或 Authorization 请求头；边车和清单仅保存脱敏 URL。
- Do not mix partial tiles from different providers. Auto-fallback from MPC to AWS is allowed only before resumable MPC assets have completed. 不要混用不同数据源的部分瓦片；仅在尚无已完成 MPC 续传资产时允许自动回退到 AWS。

## Provider Rules / 数据源规则

- `mpc` is the default for global Copernicus GLO-30 / GLO-90. `aws` is a direct anonymous mirror you can use instead. 默认用 `mpc` 获取全球 Copernicus GLO-30/GLO-90；`aws` 是匿名的直接镜像。
- `usgs` is public for US 10 m / 1 m products. Product archives remain raw in tile mode and are securely extracted only for mosaicking. 美国 `10m` 或 `1m` 产品使用 USGS 3DEP（公开）；瓦片模式保留原始压缩包，仅在拼接时安全解压。
- `opentopography` (SRTM, NASADEM, AW3D30, COP30/COP90, EU-DEM) is **optional and enhanced**: if `OPENTOPOGRAPHY_API_KEY` is set the call uses OpenTopography; if not, the call falls back to `mpc` `cop-dem-glo-30` (or `cop-dem-glo-90` for ≥90 m). The adapter splits requests into geographic chunks and limits concurrency to two.
- `earthdata` (`aster-gdem-v3`) is **optional and enhanced**: if `EARTHDATA_TOKEN` is set, discovery uses the official CMR collection `ASTGTM.003` and selects only `_dem.tif`, not the quality-count band. If not, the call falls back to `mpc` `cop-dem-glo-30`. ASTER GDEM V3 coverage is limited to roughly 83°S–83°N.
- A `credential_fallback` block always tells the caller which substitution (if any) was applied. Always include it in the final report.
- `opentopography`（SRTM、NASADEM、AW3D30、COP30/COP90、EU-DEM）**可选增强**：设了 `OPENTOPOGRAPHY_API_KEY` 就用原数据源；没设则降级到 `mpc` 的 `cop-dem-glo-30`（≥90 m 时降级到 `cop-dem-glo-90`）。适配器按地理范围分块并将并发限制为 2。
- `earthdata`（`aster-gdem-v3`）**可选增强**：设了 `EARTHDATA_TOKEN` 就走官方 CMR `ASTGTM.003` 且只取 `_dem.tif`；没设则降级到 `mpc` 的 `cop-dem-glo-30`。ASTER GDEM V3 覆盖范围约 83°S–83°N。
- `credential_fallback` 字段如实回报所有替换，便于最终交付。
- Do not silently substitute resolution, DSM/DTM class, vertical datum, or geographic coverage. 不得静默替换分辨率、DSM/DTM 类型、垂直基准或覆盖范围。

## Data Integrity / 数据完整性

- Treat Copernicus DEM and ASTER GDEM as DSMs. Buildings and vegetation may remain. 将 Copernicus DEM 和 ASTER GDEM 视为 DSM，其中可能保留建筑物和植被。
- Do not infer accuracy from pixel spacing. Do not merge different vertical datums without a documented vertical transformation. 不要根据像元间距推断精度；没有记录明确的垂直转换时，不要合并不同垂直基准。
- Interpret bbox input as EPSG:4326. Split antimeridian-crossing AOIs before download. 将 bbox 输入解释为 EPSG:4326；跨越反子午线的 AOI 在下载前拆分。
- Preserve native values. Do not fill voids, smooth, resample, or derive terrain products unless requested and recorded. 保留原始值；除非用户要求并记录，否则不填洞、不平滑、不重采样，也不派生地形产品。
- Verify current license and attribution before publication, redistribution, or commercial use. 发布、再分发或商业使用前核验最新许可和署名要求。

## Dependencies / 依赖

Require Python 3.10+ and `rasterio`; vector AOIs require `fiona`; MPC requires `pystac-client` and `planetary-computer`. Other providers use the Python standard library. Do not install missing packages without user authorization.

需要 Python 3.10+ 和 `rasterio`；矢量 AOI 需要 `fiona`；MPC 需要 `pystac-client` 和 `planetary-computer`。其他数据源使用 Python 标准库。未经用户授权不要安装缺失依赖。

## Output Contract / 输出约定

Return the effective output path, mode, source, dataset, requested source (when fallback fired), `credential_fallback` (when applicable), access time, AOI area, bbox, dimensions or tile count, CRS, resolution, surface type, vertical datum, validation status, NoData/sample statistics when mosaicked, manifest/checksum information when tiled, official source URLs, and any fallback or limitation.

返回实际输出路径、模式、数据源、数据集、requested_source（发生降级时）、`credential_fallback`（若发生）、访问时间、AOI 面积、边界框、尺寸或瓦片数、CRS、分辨率、表面类型、垂直基准、验证状态；拼接模式还需返回 NoData/样本统计，瓦片模式需返回清单/校验和，并始终附官方来源 URL、回退或限制说明。
