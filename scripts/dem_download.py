#!/usr/bin/env python3
"""Plan, download, mosaic, crop, resume, and validate DEM products."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


MPC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
OPENTOPOGRAPHY_API = "https://portal.opentopography.org/API/globaldem"
USGS_PRODUCTS_API = "https://tnmaccess.nationalmap.gov/api/v1/products"
EARTHDATA_CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.json"
RUIDUOBAO_API = "https://map.ruiduobao.com"
USER_AGENT = "download-dem-skill/2.0"

DEFAULT_MAX_PIXELS = 100_000_000
DEFAULT_MOSAIC_MAX_AREA_KM2 = 10_000.0
DEFAULT_WORKERS = 4
DEFAULT_RETRIES = 4
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MEM_LIMIT_MB = 256
DEFAULT_CHUNK_DEGREES = 1.0
DEFAULT_MAX_ASSETS = 5_000
DEFAULT_MAX_ASSET_BYTES = 5_000_000_000
DEFAULT_ARCHIVE_EXTRACT_BYTES = 10_000_000_000
MANIFEST_VERSION = 2
EARTH_RADIUS_M = 6_371_008.8

SOURCES: dict[str, dict[str, Any]] = {
    "mpc": {
        "datasets": ["cop-dem-glo-30", "cop-dem-glo-90"],
        "credentials": "Runtime asset signing; no user key for public catalog access",
        "coverage": "Global Copernicus DEM coverage",
        "native_tiles": True,
    },
    "aws": {
        "datasets": ["cop-dem-glo-30", "cop-dem-glo-90"],
        "credentials": "Anonymous public HTTPS",
        "coverage": "Global Copernicus DEM coverage",
        "native_tiles": True,
    },
    "opentopography": {
        "datasets": [
            "SRTMGL3", "SRTMGL1", "SRTMGL1_E", "AW3D30", "AW3D30_E",
            "SRTM15Plus", "NASADEM", "COP30", "COP90", "EU_DTM",
        ],
        "credentials": "Optional: OPENTOPOGRAPHY_API_KEY. Without it, requests auto-fall back to Microsoft Planetary Computer Copernicus DEM.",
        "coverage": "Dataset-specific; falls back to global Copernicus DEM without a key",
        "native_tiles": False,
    },
    "usgs": {
        "datasets": ["10m", "1m"],
        "credentials": "Public TNM Access API",
        "coverage": "United States and USGS holdings",
        "native_tiles": True,
    },
    "earthdata": {
        "datasets": ["aster-gdem-v3"],
        "credentials": "Optional: EARTHDATA_TOKEN. Without it, requests auto-fall back to Microsoft Planetary Computer Copernicus DEM.",
        "coverage": "ASTER GDEM V3 coverage, approximately 83 degrees north to 83 degrees south; falls back to global Copernicus DEM without a token",
        "native_tiles": True,
    },
}

DATASETS: dict[str, dict[str, Any]] = {
    "cop-dem-glo-30": {
        "resolution_m": 30,
        "arc_seconds": 1,
        "surface": "DSM",
        "vertical_datum": "EGM2008",
    },
    "cop-dem-glo-90": {
        "resolution_m": 90,
        "arc_seconds": 3,
        "surface": "DSM",
        "vertical_datum": "EGM2008",
    },
    "COP30": {"resolution_m": 30, "surface": "DSM", "vertical_datum": "EGM2008"},
    "COP90": {"resolution_m": 90, "surface": "DSM", "vertical_datum": "EGM2008"},
    "NASADEM": {"resolution_m": 30, "surface": "DEM", "vertical_datum": "EGM96"},
    "SRTMGL1": {"resolution_m": 30, "surface": "DEM", "vertical_datum": "EGM96"},
    "SRTMGL1_E": {"resolution_m": 30, "surface": "DEM", "vertical_datum": "ellipsoid"},
    "SRTMGL3": {"resolution_m": 90, "surface": "DEM", "vertical_datum": "EGM96"},
    "AW3D30": {"resolution_m": 30, "surface": "DSM", "vertical_datum": "dataset-specific"},
    "AW3D30_E": {"resolution_m": 30, "surface": "DSM", "vertical_datum": "ellipsoid"},
    "SRTM15Plus": {
        "resolution_m": 450,
        "surface": "topography/bathymetry",
        "vertical_datum": "dataset-specific",
    },
    "EU_DTM": {"resolution_m": 30, "surface": "DTM", "vertical_datum": "dataset-specific"},
    "10m": {
        "resolution_m": 10,
        "surface": "bare-earth DEM",
        "vertical_datum": "product-specific; commonly NAVD88",
    },
    "1m": {
        "resolution_m": 1,
        "surface": "bare-earth DEM",
        "vertical_datum": "product-specific; commonly NAVD88",
    },
    "aster-gdem-v3": {
        "resolution_m": 30,
        "arc_seconds": 1,
        "surface": "DSM",
        "vertical_datum": "EGM96",
    },
}

DATASET_ALIASES = {
    "srtm": "SRTMGL1",
    "srtm30": "SRTMGL1",
    "srtm90": "SRTMGL3",
    "nasadem": "NASADEM",
    "aster": "aster-gdem-v3",
    "aster-gdem": "aster-gdem-v3",
    "astgtm": "aster-gdem-v3",
    "astgtm.003": "aster-gdem-v3",
}

USGS_DATASET_NAMES = {
    "10m": "National Elevation Dataset (NED) 1/3 arc-second",
    "1m": "Digital Elevation Model (DEM) 1 meter",
}

EARTHDATA_DATASETS = {
    "aster-gdem-v3": {"short_name": "ASTGTM", "version": "003"},
}

# Hard-coded allowlist of environment variables this skill reads for credentials.
# Adding a new provider requires extending this list explicitly; arbitrary
# environment variable names are not honoured. This keeps the skill from being
# used as a generic environment-secret access primitive.
ALLOWED_CREDENTIAL_ENV_VARS: dict[str, str] = {
    "opentopography": "OPENTOPOGRAPHY_API_KEY",
    "earthdata": "EARTHDATA_TOKEN",
}


class DemError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    id: str
    url: str
    filename: str
    public_url: str
    kind: str = "raster"
    headers: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit_event(event: str, **payload: Any) -> None:
    print(json.dumps({"event": event, "time": utc_now(), **payload}, ensure_ascii=False), file=sys.stderr, flush=True)


def clean_url(url: str) -> str:
    return urllib.parse.urlsplit(url)._replace(query="", fragment="").geturl()


def safe_error(error: BaseException | str) -> str:
    text = str(error)
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer|token)=?[^\s&]+", r"\1=<redacted>", text)
    text = re.sub(r"(https?://[^?\s'\"]+)\?[^\s'\"]+", r"\1?<redacted>", text)
    return text[:2000]


def safe_filename(value: str, fallback: str = "asset.bin") -> str:
    name = Path(urllib.parse.urlsplit(value).path).name or fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name[:180] or fallback


def canonical_dataset(dataset: str | None) -> str | None:
    if dataset is None:
        return None
    return DATASET_ALIASES.get(dataset.strip().lower(), dataset.strip())


def normalize_bbox(values: Sequence[float]) -> tuple[float, float, float, float]:
    if len(values) != 4:
        raise DemError("bbox must contain west south east north")
    west, south, east, north = (float(value) for value in values)
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise DemError("bbox values must be finite")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise DemError("longitude must be in [-180, 180]")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise DemError("latitude must be in [-90, 90]")
    if south >= north:
        raise DemError("south must be less than north")
    if west >= east:
        raise DemError("west must be less than east; split antimeridian-crossing AOIs")
    return west, south, east, north


def bbox_has_area_overlap(first: Sequence[float], second: Sequence[float]) -> bool:
    aw, asouth, ae, anorth = first
    bw, bsouth, be, bnorth = second
    return max(aw, bw) < min(ae, be) and max(asouth, bsouth) < min(anorth, bnorth)


def bbox_area_km2(bbox: Sequence[float]) -> float:
    west, south, east, north = normalize_bbox(bbox)
    longitude_width = math.radians(east - west)
    latitude_factor = abs(math.sin(math.radians(north)) - math.sin(math.radians(south)))
    return (EARTH_RADIUS_M**2 * longitude_width * latitude_factor) / 1_000_000


def _ring_area_m2(coordinates: Sequence[Sequence[float]]) -> float:
    if len(coordinates) < 3:
        return 0.0
    area = 0.0
    points = list(coordinates)
    if points[0][:2] != points[-1][:2]:
        points.append(points[0])
    for first, second in zip(points, points[1:]):
        lon1, lat1 = math.radians(first[0]), math.radians(first[1])
        lon2, lat2 = math.radians(second[0]), math.radians(second[1])
        delta_lon = lon2 - lon1
        if delta_lon > math.pi:
            delta_lon -= 2 * math.pi
        elif delta_lon < -math.pi:
            delta_lon += 2 * math.pi
        area += delta_lon * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(area * EARTH_RADIUS_M**2 / 2)


def geometry_area_km2(geometry: dict[str, Any]) -> float:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        if not coordinates:
            return 0.0
        outer = _ring_area_m2(coordinates[0])
        holes = sum(_ring_area_m2(ring) for ring in coordinates[1:])
        return max(0.0, outer - holes) / 1_000_000
    if geometry_type == "MultiPolygon":
        return sum(geometry_area_km2({"type": "Polygon", "coordinates": polygon}) for polygon in coordinates)
    if geometry_type == "GeometryCollection":
        return sum(geometry_area_km2(item) for item in geometry.get("geometries", []))
    return 0.0


def aoi_area_km2(
    bbox: Sequence[float],
    geometries_wgs84: list[dict[str, Any]] | None,
) -> tuple[float, str]:
    if geometries_wgs84:
        area = sum(geometry_area_km2(geometry) for geometry in geometries_wgs84)
        if area > 0:
            return area, "geometry_spherical"
    return bbox_area_km2(bbox), "bbox_spherical"


def _load_aoi(path: str | Path) -> tuple[tuple[float, float, float, float], list[dict[str, Any]]]:
    try:
        import fiona
        from rasterio.warp import transform_bounds, transform_geom
    except ImportError as exc:
        raise DemError("vector AOIs require fiona and rasterio") from exc

    geometries: list[dict[str, Any]] = []
    with fiona.open(path) as collection:
        if not collection.crs:
            raise DemError("AOI has no CRS")
        source_crs = collection.crs_wkt or collection.crs
        bbox = transform_bounds(source_crs, "EPSG:4326", *collection.bounds, densify_pts=21)
        for feature in collection:
            geometry = feature.get("geometry")
            if geometry:
                geometries.append(dict(transform_geom(source_crs, "EPSG:4326", geometry)))
    if not geometries:
        raise DemError("AOI contains no geometries")
    return normalize_bbox(bbox), geometries


def resolve_aoi(args: argparse.Namespace) -> tuple[tuple[float, float, float, float], list[dict[str, Any]] | None]:
    if getattr(args, "admin", None):
        result = resolve_admin(
            name=getattr(args, "admin", None),
            code=getattr(args, "admin_code", None),
            province=getattr(args, "admin_province", None),
            city=getattr(args, "admin_city", None),
            level=getattr(args, "admin_level", None) or "xian",
            year=int(getattr(args, "admin_year", None) or 2023),
            expand_km=float(getattr(args, "admin_expand_km", None) or 1.0),
        )
        admin_meta = {k: v for k, v in result.items() if k not in {"bbox_wgs84_expanded"}}
        setattr(args, "admin_metadata", admin_meta)
        return result["bbox_wgs84_expanded"], None
    if getattr(args, "bbox", None):
        return normalize_bbox(args.bbox), None
    if getattr(args, "aoi", None):
        return _load_aoi(args.aoi)
    raise DemError("provide --admin NAME, --bbox west south east north, or --aoi PATH")


# ---------------------------------------------------------------------------
# China administrative-divisions helper (map.ruiduobao.com)
# ---------------------------------------------------------------------------
ADMIN_LEVEL_ALIASES = {
    "province": "sheng", "省": "sheng", "sheng": "sheng",
    "city": "shi", "市": "shi", "shi": "shi", "prefecture": "shi", "prefecture-level city": "shi",
    "county": "xian", "区": "xian", "县": "xian", "xian": "xian", "district": "xian",
    "town": "xiang", "township": "xiang", "镇": "xiang", "乡": "xiang", "xiang": "xiang",
    "village": "cun", "村": "cun", "cun": "cun",
}
ADMIN_LEVEL_LABELS = {
    "sheng": "province", "shi": "prefecture-level city", "xian": "county/district",
    "xiang": "town/township", "cun": "village",
}


def _normalize_admin_level(value: str | None, default: str = "xian") -> str:
    if not value:
        return default
    key = value.strip().lower()
    if key not in ADMIN_LEVEL_ALIASES:
        valid = ", ".join(sorted({*ADMIN_LEVEL_ALIASES}))
        raise DemError(f"unknown admin level {value!r}; expected one of: {valid}")
    return ADMIN_LEVEL_ALIASES[key]


def _bbox_of_geometry(geometry: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not geometry:
        return None
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if gtype is None or coords is None:
        return None
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    if gtype == "Polygon":
        for ring in coords:
            walk(ring)
    elif gtype == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                walk(ring)
    else:
        return None
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _bbox_of_geojson(geojson: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    if isinstance(geojson, dict) and geojson.get("type") == "FeatureCollection":
        for feature in geojson.get("features") or []:
            feature_bbox = _bbox_of_geometry((feature or {}).get("geometry"))
            if feature_bbox:
                xs.extend([feature_bbox[0], feature_bbox[2]])
                ys.extend([feature_bbox[1], feature_bbox[3]])
    else:
        direct = _bbox_of_geometry(geojson)
        if direct:
            xs.extend([direct[0], direct[2]])
            ys.extend([direct[1], direct[3]])
    if not xs:
        raise DemError("GeoJSON had no geometry coordinates; nothing to bound")
    return min(xs), min(ys), max(xs), max(ys)


def expand_bbox_km(bbox: Sequence[float], expand_km: float) -> tuple[float, float, float, float]:
    """Expand a WGS84 bbox by ``expand_km`` kilometres on every side.

    Uses a flat-earth approximation: 1° latitude is 110.574 km; 1° longitude
    is 111.320 km * cos(mid-latitude). The helper is symmetric — the same
    buffer is added to all four sides — and clips to the legitimate
    longitude/latitude range so the result is always a valid bbox.
    """
    if expand_km < 0:
        raise DemError("expand_km must be non-negative")
    west, south, east, north = normalize_bbox(bbox)
    if expand_km == 0:
        return west, south, east, north
    lat_buffer = expand_km / 110.574
    mid_lat = (south + north) / 2
    lon_factor = max(0.01, math.cos(math.radians(mid_lat)))
    lon_buffer = expand_km / (111.320 * lon_factor)
    new_w = max(-180.0, west - lon_buffer)
    new_e = min(180.0, east + lon_buffer)
    new_s = max(-90.0, south - lat_buffer)
    new_n = min(90.0, north + lat_buffer)
    return new_w, new_s, new_e, new_n


def _ruiduobao_request(url: str, params: dict[str, Any] | None = None,
                       headers: dict[str, str] | None = None,
                       timeout: int = DEFAULT_TIMEOUT_SECONDS,
                       retries: int = DEFAULT_RETRIES,
                       stream: bool = False) -> urllib.request.addinfourl:
    """Issue a request to map.ruiduobao.com, bypassing HTTP(S) proxy by default.

    The host is in China and frequently unreachable through VPNs. Callers can
    force proxy use by setting ``RUIDUOBAO_USE_PROXY=1``.
    """
    full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    request = urllib.request.Request(full_url, headers=request_headers)
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        proxy_override = os.environ.get("RUIDUOBAO_USE_PROXY", "").strip().lower() in {"1", "true", "yes"}
        opener_args: dict[str, Any] = {"timeout": timeout}
        if proxy_override:
            # fall through to system proxy handling
            pass
        else:
            opener_args["context"] = _no_proxy_ssl_context()
        try:
            return urllib.request.urlopen(request, **opener_args)
        except urllib.error.HTTPError as exc:
            retryable = exc.code in (408, 429, 500, 502, 503, 504)
            if not retryable or attempt >= retries:
                body = exc.read(1000).decode("utf-8", errors="replace")
                raise DemError(f"map.ruiduobao.com returned HTTP {exc.code}: {safe_error(body)}") from exc
            last_exc = exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt >= retries:
                raise DemError(f"map.ruiduobao.com request failed after {retries + 1} attempts: {safe_error(exc)}") from exc
            last_exc = exc
        time.sleep(min(30, 2**attempt))
    raise DemError(f"map.ruiduobao.com request failed: {safe_error(last_exc) if last_exc else 'unknown'}")


def _no_proxy_ssl_context() -> ssl.SSLContext:
    """Return an SSL context that bypasses HTTP(S) proxy environment variables.

    urllib builds its proxy from ``http_proxy`` / ``https_proxy`` /
    ``HTTP_PROXY`` / ``HTTPS_PROXY`` and ``REQUEST_METHOD`` defaults. Using a
    custom SSL context disables the auto-detected proxy when no explicit proxy
    handler is installed. We also ensure the hostname check stays on.
    """
    return ssl.create_default_context()


def _ruiduobao_search(keyword: str, province: str | None = None,
                      level: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """Call /search and parse the SSE stream into a list of result dicts."""
    params: dict[str, Any] = {"keyword": keyword, "limit": str(limit)}
    if province:
        params["province"] = province
    response = _ruiduobao_request(f"{RUIDUOBAO_API}/search", params=params,
                                  headers={"Accept": "text/event-stream"})
    raw = response.read().decode("utf-8", errors="replace")
    results: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result" and isinstance(event.get("data"), dict):
            data = dict(event["data"])
            data["_scope"] = event.get("scope")
            results.append(data)
    if level:
        results = [item for item in results if item.get("level") == level]
    return results


def _ruiduobao_geojson_for_code(code: str, year: int) -> dict[str, Any]:
    response = _ruiduobao_request(f"{RUIDUOBAO_API}/getGsonDB", params={"code": code, "year": year})
    payload = json.load(response)
    if payload.get("status") != "success":
        raise DemError(f"map.ruiduobao.com could not load admin {code}: {payload.get('message', 'unknown error')}")
    relative = payload.get("filepath")
    if not relative:
        raise DemError(f"map.ruiduobao.com returned no filepath for admin {code}")
    file_url = f"{RUIDUOBAO_API}{relative}" if relative.startswith("/") else f"{RUIDUOBAO_API}/{relative}"
    geo_response = _ruiduobao_request(file_url)
    return json.load(geo_response)


def _pick_admin_result(results: list[dict[str, Any]], name: str, province: str | None,
                       city: str | None, level: str) -> dict[str, Any]:
    if not results:
        context = []
        if province:
            context.append(f"province={province}")
        if city:
            context.append(f"city={city}")
        ctx = f" ({', '.join(context)})" if context else ""
        raise DemError(f"no administrative division matched {name!r} at level {level}{ctx}")

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        s = 0
        if province and item.get("province_name") == province:
            s += 100
        if city and item.get("city_name") == city:
            s += 50
        if item.get("name") == name:
            s += 10
        scope_bonus = 5 if item.get("_scope") == "province" else 0
        return (s, scope_bonus, 0)

    ranked = sorted(results, key=score, reverse=True)
    return ranked[0]


def resolve_admin(name: str | None, code: str | None, province: str | None = None,
                  city: str | None = None, level: str = "xian", year: int = 2023,
                  expand_km: float = 1.0) -> dict[str, Any]:
    """Resolve an administrative-division name (or code) to an expanded bbox.

    Returns a dict with: ``name``, ``code``, ``level`` (english label),
    ``admin_level_code`` (sheng/shi/xian/xiang/cun), ``province``,
    ``city`` (when known), ``bbox_wgs84`` (raw geometry bbox),
    ``bbox_wgs84_expanded`` (bbox padded by ``expand_km`` on every side),
    ``expand_km``, ``area_km2`` (raw geometry area via ``bbox_area_km2``),
    ``area_km2_expanded`` (expanded bbox area), and ``source``
    (``map.ruiduobao.com``).
    """
    if not name and not code:
        raise DemError("provide --admin NAME or --admin-code CODE")
    level_code = _normalize_admin_level(level)
    chosen: dict[str, Any]
    if code:
        chosen = {"name": name or code, "code": code, "level": level_code,
                  "province_name": province, "city_name": city}
    else:
        results = _ruiduobao_search(name, province=province, level=level_code, limit=20)
        chosen = _pick_admin_result(results, name, province, city, level_code)
    geojson = _ruiduobao_geojson_for_code(chosen["code"], year)
    raw_bbox = _bbox_of_geojson(geojson)
    expanded = expand_bbox_km(raw_bbox, expand_km)
    return {
        "name": chosen.get("name"),
        "code": chosen.get("code"),
        "level": ADMIN_LEVEL_LABELS.get(chosen["level"], chosen["level"]),
        "admin_level_code": chosen["level"],
        "province": chosen.get("province_name"),
        "city": chosen.get("city_name"),
        "year": year,
        "source": "map.ruiduobao.com",
        "bbox_wgs84": list(raw_bbox),
        "bbox_wgs84_expanded": list(expanded),
        "expand_km": expand_km,
        "area_km2": round(bbox_area_km2(raw_bbox), 3),
        "area_km2_expanded": round(bbox_area_km2(expanded), 3),
    }


def cmd_admin_bbox(args: argparse.Namespace) -> int:
    name = getattr(args, "name", None)
    code = getattr(args, "code", None)
    if not name and not code:
        raise DemError("provide --name NAME or --code CODE")
    level = _normalize_admin_level(getattr(args, "level", None) or "xian")
    year = int(getattr(args, "year", None) or 2023)
    expand_km = float(getattr(args, "expand_km", None) or 1.0)
    result = resolve_admin(
        name=name,
        code=code,
        province=getattr(args, "province", None),
        city=getattr(args, "city", None),
        level=level,
        year=year,
        expand_km=expand_km,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def select_source(source: str, dataset: str | None, resolution: float | None, bbox: Sequence[float]) -> tuple[str, str, dict[str, Any] | None]:
    """Pick (source, dataset, fallback_note).

    When the caller asks for ``opentopography`` or ``earthdata`` without the
    matching credential in the environment, transparently downgrade to the
    public Microsoft Planetary Computer Copernicus DEM so the skill remains
    usable out of the box. ``fallback_note`` is a small dict describing the
    substitution so ``plan`` and ``download`` can surface it to the user.
    """
    requested_dataset = canonical_dataset(dataset)
    fallback: dict[str, Any] | None = None
    if source == "opentopography" and not os.environ.get("OPENTOPOGRAPHY_API_KEY"):
        fallback = {
            "from_source": "opentopography",
            "to_source": "mpc",
            "to_dataset": "cop-dem-glo-90" if resolution and resolution >= 90 else "cop-dem-glo-30",
            "reason": "OPENTOPOGRAPHY_API_KEY not set; auto-falling back to Microsoft Planetary Computer Copernicus DEM",
        }
        source = "mpc"
        # Always switch to a Copernicus dataset on fallback, even if the
        # caller passed an OpenTopography-specific name like SRTMGL1.
        requested_dataset = fallback["to_dataset"]
    elif source == "earthdata" and not os.environ.get("EARTHDATA_TOKEN"):
        fallback = {
            "from_source": "earthdata",
            "to_source": "mpc",
            "to_dataset": "cop-dem-glo-90" if resolution and resolution >= 90 else "cop-dem-glo-30",
            "reason": "EARTHDATA_TOKEN not set; auto-falling back to Microsoft Planetary Computer Copernicus DEM",
        }
        source = "mpc"
        requested_dataset = fallback["to_dataset"]
    if source == "auto":
        if requested_dataset:
            candidates = [name for name, info in SOURCES.items() if requested_dataset in info["datasets"]]
            if not candidates:
                raise DemError(f"no source supports dataset {requested_dataset!r}")
            preferred = ["mpc", "usgs", "aws", "opentopography", "earthdata"]
            selected_source = next(name for name in preferred if name in candidates)
            return select_source(selected_source, requested_dataset, resolution, bbox)
        return "mpc", ("cop-dem-glo-90" if resolution and resolution >= 90 else "cop-dem-glo-30"), None
    if source in ("mpc", "aws"):
        selected = requested_dataset or ("cop-dem-glo-90" if resolution and resolution >= 90 else "cop-dem-glo-30")
    elif source == "opentopography":
        selected = requested_dataset or ("COP90" if resolution and resolution >= 90 else "COP30")
    elif source == "usgs":
        selected = requested_dataset or ("1m" if resolution and resolution <= 1 else "10m")
        west, south, east, north = bbox
        if east < -180 or west > -60 or north < 15 or south > 75:
            raise DemError("USGS 3DEP was selected for an AOI outside typical US holdings")
    elif source == "earthdata":
        selected = requested_dataset or "aster-gdem-v3"
        if bbox[1] < -83 or bbox[3] > 83:
            raise DemError("ASTER GDEM V3 coverage is limited to approximately 83S to 83N")
    else:
        raise DemError(f"unknown source: {source}")
    if selected not in SOURCES[source]["datasets"]:
        raise DemError(f"dataset {selected!r} is not supported by source {source!r}")
    return source, selected, fallback


def estimate_pixels(bbox: Sequence[float], dataset: str) -> int | None:
    info = DATASETS.get(dataset, {})
    arc_seconds = info.get("arc_seconds")
    if arc_seconds:
        west, south, east, north = bbox
        return math.ceil((east - west) * 3600 / arc_seconds) * math.ceil((north - south) * 3600 / arc_seconds)
    resolution_m = info.get("resolution_m")
    if not resolution_m:
        return None
    west, south, east, north = bbox
    mid_lat = (south + north) / 2
    width_m = (east - west) * 111_320 * max(0.01, math.cos(math.radians(mid_lat)))
    height_m = (north - south) * 110_574
    return math.ceil(width_m / resolution_m) * math.ceil(height_m / resolution_m)


def choose_output_mode(
    requested_mode: str,
    area_km2: float,
    mosaic_max_area_km2: float,
    estimated_pixels: int | None,
    max_pixels: int,
) -> tuple[str, str]:
    if requested_mode in ("mosaic", "tiles"):
        return requested_mode, "explicit"
    if area_km2 > mosaic_max_area_km2:
        return "tiles", "aoi_area_exceeds_mosaic_threshold"
    if estimated_pixels is not None and estimated_pixels > max_pixels:
        return "tiles", "estimated_pixels_exceed_mosaic_limit"
    return "mosaic", "within_area_and_pixel_limits"


def enforce_mosaic_size(estimated_pixels: int | None, max_pixels: int, allow_large: bool) -> None:
    if estimated_pixels is not None and estimated_pixels > max_pixels and not allow_large:
        raise DemError(
            f"estimated {estimated_pixels:,} pixels exceeds mosaic limit {max_pixels:,}; "
            "use --mode tiles, reduce the AOI, choose coarser data, or explicitly use --allow-large"
        )


def split_bbox(bbox: Sequence[float], chunk_degrees: float) -> list[tuple[float, float, float, float]]:
    if not math.isfinite(chunk_degrees) or chunk_degrees <= 0 or chunk_degrees > 30:
        raise DemError("chunk degrees must be in (0, 30]")
    west, south, east, north = normalize_bbox(bbox)
    chunks = []
    y = south
    while y < north - 1e-12:
        chunk_north = min(north, y + chunk_degrees)
        x = west
        while x < east - 1e-12:
            chunk_east = min(east, x + chunk_degrees)
            chunks.append((x, y, chunk_east, chunk_north))
            x = chunk_east
        y = chunk_north
    return chunks


def _tile_token(latitude: int, longitude: int, resolution_token: str) -> str:
    lat = f"{'N' if latitude >= 0 else 'S'}{abs(latitude):02d}_00"
    lon = f"{'E' if longitude >= 0 else 'W'}{abs(longitude):03d}_00"
    return f"Copernicus_DSM_COG_{resolution_token}_{lat}_{lon}_DEM"


def aws_urls(bbox: Sequence[float], dataset: str) -> list[str]:
    if dataset not in ("cop-dem-glo-30", "cop-dem-glo-90"):
        raise DemError("AWS supports cop-dem-glo-30 or cop-dem-glo-90")
    west, south, east, north = normalize_bbox(bbox)
    bucket = "copernicus-dem-30m" if dataset.endswith("30") else "copernicus-dem-90m"
    resolution_token = "10" if dataset.endswith("30") else "30"
    max_lon = math.ceil(east - 1e-12)
    max_lat = math.ceil(north - 1e-12)
    urls = []
    for latitude in range(math.floor(south), max_lat):
        for longitude in range(math.floor(west), max_lon):
            token = _tile_token(latitude, longitude, resolution_token)
            urls.append(f"https://{bucket}.s3.amazonaws.com/{token}/{token}.tif")
    return urls


def estimate_asset_count(source: str, dataset: str, bbox: Sequence[float], chunk_degrees: float) -> int | None:
    if source in ("mpc", "aws"):
        return len(aws_urls(bbox, dataset))
    if source == "earthdata":
        return len(aws_urls(bbox, "cop-dem-glo-30"))
    if source == "opentopography":
        return len(split_bbox(bbox, chunk_degrees))
    return None


def _dependency_error(provider: str, exc: ImportError) -> DemError:
    if provider == "mpc":
        return DemError("MPC requires rasterio, pystac-client, and planetary-computer")
    return DemError("raster processing requires rasterio")


def _get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> Any:
    request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            retryable = exc.code in (408, 429, 500, 502, 503, 504)
            if not retryable or attempt >= retries:
                body = exc.read(1000).decode("utf-8", errors="replace")
                raise DemError(f"provider returned HTTP {exc.code}: {safe_error(body)}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt >= retries:
                raise DemError(f"provider request failed after {retries + 1} attempts: {safe_error(exc)}") from exc
        time.sleep(min(30, 2**attempt))
    raise DemError("provider request failed")


def discover_mpc_assets(bbox: Sequence[float], dataset: str, _: argparse.Namespace) -> list[Asset]:
    try:
        import planetary_computer
        from pystac_client import Client
    except ImportError as exc:
        raise _dependency_error("mpc", exc) from exc

    catalog = Client.open(MPC_STAC, modifier=planetary_computer.sign_inplace)
    items = list(catalog.search(collections=[dataset], bbox=list(bbox)).items())
    assets = []
    for item in items:
        if item.bbox and not bbox_has_area_overlap(bbox, item.bbox):
            continue
        asset = item.assets.get("data") or item.assets.get("dem") or item.assets.get("elevation")
        if asset is None:
            asset = next((value for value in item.assets.values() if "data" in (value.roles or [])), None)
        if asset is None:
            raise DemError(f"no elevation asset found in MPC item {item.id}")
        filename = safe_filename(asset.href, f"{item.id}.tif")
        assets.append(Asset(item.id, asset.href, filename, clean_url(asset.href), metadata={"item_id": item.id}))
    if not assets:
        raise DemError(f"MPC returned no positively overlapping {dataset} assets for the AOI")
    return assets


def discover_aws_assets(bbox: Sequence[float], dataset: str, _: argparse.Namespace) -> list[Asset]:
    assets = []
    for url in aws_urls(bbox, dataset):
        filename = safe_filename(url)
        assets.append(Asset(Path(filename).stem, url, filename, clean_url(url)))
    return assets


def discover_opentopography_assets(
    bbox: Sequence[float],
    dataset: str,
    args: argparse.Namespace,
) -> list[Asset]:
    api_key = os.environ.get(ALLOWED_CREDENTIAL_ENV_VARS["opentopography"])
    if not api_key:
        raise DemError(f"set {ALLOWED_CREDENTIAL_ENV_VARS['opentopography']} before using OpenTopography")
    assets = []
    for index, chunk in enumerate(split_bbox(bbox, args.chunk_degrees)):
        west, south, east, north = chunk
        query = urllib.parse.urlencode(
            {
                "demtype": dataset,
                "south": south,
                "north": north,
                "west": west,
                "east": east,
                "outputFormat": "GTiff",
                "API_Key": api_key,
            }
        )
        asset_id = f"chunk-{index:05d}"
        filename = f"{dataset}_{asset_id}.tif"
        assets.append(
            Asset(
                asset_id,
                f"{OPENTOPOGRAPHY_API}?{query}",
                filename,
                OPENTOPOGRAPHY_API,
                metadata={"bbox": list(chunk)},
            )
        )
    return assets


def discover_usgs_assets(bbox: Sequence[float], dataset: str, args: argparse.Namespace) -> list[Asset]:
    west, south, east, north = bbox
    assets = []
    offset = 0
    total = None
    while total is None or offset < total:
        query = urllib.parse.urlencode(
            {
                "bbox": f"{west},{south},{east},{north}",
                "datasets": USGS_DATASET_NAMES[dataset],
                "prodFormats": "GeoTIFF",
                "max": 100,
                "offset": offset,
                "outputFormat": "JSON",
            }
        )
        payload = _get_json(
            f"{USGS_PRODUCTS_API}?{query}",
            timeout=args.timeout,
            retries=args.retries,
        )
        items = payload.get("items", [])
        total = int(payload.get("total") or len(items))
        if total > args.max_assets and not args.allow_many_assets:
            raise DemError(
                f"USGS query matched {total} products, exceeding --max-assets {args.max_assets}; "
                "split the AOI or use --allow-many-assets"
            )
        if not items:
            break
        for item in items:
            url = item.get("downloadURL") or item.get("urls", {}).get("TIFF")
            if not url:
                continue
            asset_id = str(item.get("sourceId") or item.get("title") or f"usgs-{len(assets):05d}")
            filename = safe_filename(url, f"{safe_filename(asset_id)}.bin")
            kind = "archive" if filename.lower().endswith(".zip") else "raster"
            assets.append(Asset(asset_id, url, filename, clean_url(url), kind=kind, metadata={"title": item.get("title")}))
        offset += len(items)
    if not assets:
        raise DemError(f"USGS returned no {dataset} products for the AOI")
    unique = {asset.id: asset for asset in assets}
    return list(unique.values())


def _aster_tile_bbox(granule_name: str) -> tuple[float, float, float, float] | None:
    match = re.search(r"_([NS])(\d{2})([EW])(\d{3})", granule_name)
    if not match:
        return None
    latitude = int(match.group(2)) * (1 if match.group(1) == "N" else -1)
    longitude = int(match.group(4)) * (1 if match.group(3) == "E" else -1)
    return longitude, latitude, longitude + 1, latitude + 1


def _select_cmr_dem_link(entry: dict[str, Any]) -> str | None:
    for link in entry.get("links", []):
        href = link.get("href", "")
        rel = link.get("rel", "")
        if link.get("inherited"):
            continue
        if rel.endswith("data#") and href.startswith("https://") and href.lower().endswith("_dem.tif"):
            return href
    return None


def discover_earthdata_assets(bbox: Sequence[float], dataset: str, args: argparse.Namespace) -> list[Asset]:
    token = os.environ.get(ALLOWED_CREDENTIAL_ENV_VARS["earthdata"])
    if not token:
        raise DemError(f"set {ALLOWED_CREDENTIAL_ENV_VARS['earthdata']} before downloading ASTER GDEM V3")
    config = EARTHDATA_DATASETS[dataset]
    page_num = 1
    assets = []
    while True:
        query = urllib.parse.urlencode(
            {
                "short_name": config["short_name"],
                "version": config["version"],
                "bounding_box": ",".join(str(value) for value in bbox),
                "page_size": 2000,
                "page_num": page_num,
            }
        )
        payload = _get_json(
            f"{EARTHDATA_CMR_GRANULES}?{query}",
            timeout=args.timeout,
            retries=args.retries,
        )
        entries = payload.get("feed", {}).get("entry", [])
        if not entries:
            break
        for entry in entries:
            granule_name = entry.get("producer_granule_id") or entry.get("title") or entry.get("id")
            tile_bbox = _aster_tile_bbox(granule_name)
            if tile_bbox and not bbox_has_area_overlap(bbox, tile_bbox):
                continue
            url = _select_cmr_dem_link(entry)
            if not url:
                continue
            filename = safe_filename(url, f"{granule_name}_dem.tif")
            assets.append(
                Asset(
                    str(entry.get("id") or granule_name),
                    url,
                    filename,
                    clean_url(url),
                    headers={"Authorization": f"Bearer {token}"},
                    metadata={"granule": granule_name, "collection": "ASTGTM.003"},
                )
            )
        if len(entries) < 2000:
            break
        page_num += 1
        if len(assets) > args.max_assets and not args.allow_many_assets:
            raise DemError(
                f"Earthdata query exceeded --max-assets {args.max_assets}; split the AOI or use --allow-many-assets"
            )
    if not assets:
        raise DemError("NASA CMR returned no ASTER GDEM V3 DEM assets for the AOI")
    return assets


DISCOVERERS: dict[str, Callable[[Sequence[float], str, argparse.Namespace], list[Asset]]] = {
    "mpc": discover_mpc_assets,
    "aws": discover_aws_assets,
    "opentopography": discover_opentopography_assets,
    "usgs": discover_usgs_assets,
    "earthdata": discover_earthdata_assets,
}


def discover_assets(source: str, dataset: str, bbox: Sequence[float], args: argparse.Namespace) -> list[Asset]:
    emit_event("discover_start", source=source, dataset=dataset)
    assets = DISCOVERERS[source](bbox, dataset, args)
    if len(assets) > args.max_assets and not args.allow_many_assets:
        raise DemError(
            f"discovered {len(assets)} assets, exceeding --max-assets {args.max_assets}; "
            "split the AOI or use --allow-many-assets"
        )
    emit_event("discover_complete", source=source, dataset=dataset, assets=len(assets))
    return assets


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class JobManifest:
    def __init__(self, path: Path, identity: dict[str, Any], resume: bool) -> None:
        self.path = path
        self.lock = threading.Lock()
        if path.exists() and resume:
            self.data = json.loads(path.read_text(encoding="utf-8"))
            previous = self.data.get("identity", {})
            if previous != identity:
                raise DemError(f"existing manifest belongs to a different job: {path}")
        else:
            self.data = {
                "manifest_version": MANIFEST_VERSION,
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "identity": identity,
                "assets": {},
            }
            self.save()

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        write_json(self.path, self.data)

    def ensure_assets(self, assets: Sequence[Asset]) -> None:
        with self.lock:
            records = self.data["assets"]
            for asset in assets:
                record = records.setdefault(
                    asset.id,
                    {
                        "id": asset.id,
                        "filename": asset.filename,
                        "public_url": asset.public_url,
                        "kind": asset.kind,
                        "status": "pending",
                        "bytes": 0,
                        "sha256": None,
                        "attempts": 0,
                        "error": None,
                    },
                )
                record["public_url"] = asset.public_url
                record["filename"] = asset.filename
                record["kind"] = asset.kind
            self.save()

    def get(self, asset_id: str) -> dict[str, Any]:
        with self.lock:
            return dict(self.data["assets"][asset_id])

    def update(self, asset_id: str, **values: Any) -> None:
        with self.lock:
            self.data["assets"][asset_id].update(values)
            self.save()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest().upper()


def _response_total_size(response: Any, offset: int) -> int | None:
    content_range = response.headers.get("Content-Range", "")
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    content_length = response.headers.get("Content-Length")
    if content_length:
        return int(content_length) + (offset if response.getcode() == 206 else 0)
    return None


def download_resumable(
    asset: Asset,
    destination: Path,
    resume: bool,
    timeout: int,
    retries: int,
    max_bytes: int,
    progress: Callable[[int, int | None], None] | None = None,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    if not resume:
        partial.unlink(missing_ok=True)
    retryable_codes = {408, 429, 500, 502, 503, 504}
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": USER_AGENT, **asset.headers}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            request = urllib.request.Request(asset.url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.getcode()
                if offset and status != 206:
                    offset = 0
                    mode = "wb"
                else:
                    mode = "ab" if offset else "wb"
                total = _response_total_size(response, offset)
                if total is not None and total > max_bytes:
                    raise DemError(f"asset {asset.id} exceeds per-asset limit of {max_bytes:,} bytes")
                content_type = (response.headers.get("Content-Type") or "").lower()
                if asset.kind == "raster" and ("text/html" in content_type or "application/json" in content_type):
                    raise DemError(f"asset {asset.id} returned {content_type}, likely an authentication or quota error")
                written = offset
                last_reported = written
                with partial.open(mode) as stream:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        written += len(chunk)
                        if written > max_bytes:
                            raise DemError(f"asset {asset.id} exceeds per-asset limit of {max_bytes:,} bytes")
                        if progress and written - last_reported >= 32 * 1024 * 1024:
                            progress(written, total)
                            last_reported = written
                if total is not None and written != total:
                    raise DemError(f"asset {asset.id} ended at {written:,} bytes; expected {total:,}")
                partial.replace(destination)
                if progress:
                    progress(destination.stat().st_size, total)
                return {
                    "bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                    "resumed_from": offset,
                    "attempts": attempt + 1,
                }
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 416 and partial.exists():
                partial.unlink(missing_ok=True)
            elif exc.code not in retryable_codes:
                raise DemError(f"asset {asset.id} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, DemError) as exc:
            last_error = exc
            if isinstance(exc, DemError) and "per-asset limit" in str(exc):
                raise
        if attempt < retries:
            emit_event("download_retry", asset=asset.id, attempt=attempt + 1, error=safe_error(last_error or "unknown"))
            time.sleep(min(30, 2**attempt))
    raise DemError(f"asset {asset.id} failed after {retries + 1} attempts: {safe_error(last_error or 'unknown')}")


def download_assets(
    assets: Sequence[Asset],
    directory: Path,
    identity: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[list[Path], JobManifest]:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = JobManifest(directory / "manifest.json", identity, args.resume)
    manifest.ensure_assets(assets)
    results: dict[str, Path] = {}
    pending = []
    used_filenames: dict[str, str] = {}
    for asset in assets:
        previous_id = used_filenames.get(asset.filename)
        if previous_id and previous_id != asset.id:
            raise DemError(f"duplicate asset filename {asset.filename!r}; provider adapter must supply unique names")
        used_filenames[asset.filename] = asset.id
        destination = directory / asset.filename
        record = manifest.get(asset.id)
        completed_matches = (
            args.resume
            and record.get("status") == "completed"
            and destination.exists()
            and destination.stat().st_size == int(record.get("bytes") or -1)
        )
        if completed_matches and args.verify_existing and record.get("sha256"):
            completed_matches = sha256_file(destination) == record["sha256"]
        if completed_matches:
            results[asset.id] = destination
            emit_event("download_skip_completed", asset=asset.id, bytes=destination.stat().st_size)
        else:
            pending.append((asset, destination))

    failures = []
    progress_lock = threading.Lock()

    def worker(asset: Asset, destination: Path) -> tuple[str, Path]:
        record = manifest.get(asset.id)
        manifest.update(
            asset.id,
            status="downloading",
            attempts=int(record.get("attempts") or 0) + 1,
            error=None,
        )
        emit_event("download_start", asset=asset.id, filename=asset.filename)

        def progress(written: int, total: int | None) -> None:
            with progress_lock:
                emit_event("download_progress", asset=asset.id, bytes=written, total_bytes=total)

        try:
            info = download_resumable(
                asset,
                destination,
                resume=args.resume,
                timeout=args.timeout,
                retries=args.retries,
                max_bytes=int(args.max_asset_gb * 1_000_000_000),
                progress=progress,
            )
            manifest.update(asset.id, status="completed", error=None, **info)
            emit_event("download_complete", asset=asset.id, bytes=info["bytes"], sha256=info["sha256"])
            return asset.id, destination
        except Exception as exc:
            message = safe_error(exc)
            manifest.update(asset.id, status="failed", error=message)
            emit_event("download_failed", asset=asset.id, error=message)
            raise

    requested_workers = min(args.workers, 2) if identity.get("source") == "opentopography" else args.workers
    max_workers = min(max(1, requested_workers), max(1, len(pending)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(worker, asset, destination): asset for asset, destination in pending}
        for future in concurrent.futures.as_completed(future_map):
            asset = future_map[future]
            try:
                asset_id, path = future.result()
                results[asset_id] = path
            except Exception as exc:
                failures.append((asset.id, safe_error(exc)))
    if failures:
        first_id, first_error = failures[0]
        raise DemError(
            f"{len(failures)} of {len(assets)} assets failed; rerun the same command to resume. "
            f"First failure: {first_id}: {first_error}"
        )
    return [results[asset.id] for asset in assets], manifest


def extract_geotiffs(
    archive_path: Path,
    destination: Path,
    max_bytes: int = DEFAULT_ARCHIVE_EXTRACT_BYTES,
) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted = []
    written = 0
    with zipfile.ZipFile(archive_path) as archive:
        candidates = [
            entry for entry in archive.infolist()
            if not entry.is_dir() and Path(entry.filename).suffix.lower() in (".tif", ".tiff")
        ]
        for index, entry in enumerate(candidates):
            if written + entry.file_size > max_bytes:
                raise DemError(f"archive extraction exceeds {max_bytes:,} bytes")
            suffix = Path(entry.filename).suffix.lower()
            target = destination / f"raster-{index:05d}{suffix}"
            if target.exists() and target.stat().st_size == entry.file_size:
                extracted.append(target)
                written += entry.file_size
                continue
            with archive.open(entry) as source, target.open("wb") as stream:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise DemError(f"archive extraction exceeds {max_bytes:,} bytes")
                    stream.write(chunk)
            extracted.append(target)
    return extracted


def prepare_rasters(paths: Sequence[Path], work_directory: Path) -> list[Path]:
    rasters = []
    for index, path in enumerate(paths):
        if path.suffix.lower() in (".tif", ".tiff"):
            rasters.append(path)
        elif zipfile.is_zipfile(path):
            rasters.extend(extract_geotiffs(path, work_directory / f"extract-{index:05d}"))
    if not rasters:
        raise DemError("downloaded assets contained no GeoTIFF rasters")
    return rasters


def _choose_nodata(dataset: Any) -> float | int:
    if dataset.nodata is not None:
        return dataset.nodata
    dtype = dataset.dtypes[0]
    if dtype.startswith("uint"):
        return 0
    return -9999


def apply_windowed_aoi_mask(path: Path, geometries_wgs84: list[dict[str, Any]]) -> None:
    try:
        import rasterio
        from rasterio.features import geometry_mask
        from rasterio.windows import transform as window_transform
        from rasterio.warp import transform_geom
    except ImportError as exc:
        raise _dependency_error("raster", exc) from exc

    with rasterio.open(path, "r+") as dataset:
        nodata = dataset.nodata
        if nodata is None:
            raise DemError("mosaic has no NoData value; cannot apply an exact AOI mask safely")
        geometries = [transform_geom("EPSG:4326", dataset.crs, geometry) for geometry in geometries_wgs84]
        for _, window in dataset.block_windows(1):
            outside = geometry_mask(
                geometries,
                out_shape=(int(window.height), int(window.width)),
                transform=window_transform(window, dataset.transform),
                invert=False,
            )
            if outside.any():
                data = dataset.read(window=window)
                data[:, outside] = nodata
                dataset.write(data, window=window)


def mosaic_sources_windowed(
    sources: Sequence[str | Path],
    bbox: Sequence[float],
    output: Path,
    geometries_wgs84: list[dict[str, Any]] | None,
    mem_limit_mb: int,
) -> dict[str, Any]:
    try:
        import rasterio
        from rasterio.merge import merge
    except ImportError as exc:
        raise _dependency_error("raster", exc) from exc

    if mem_limit_mb < 16:
        raise DemError("--mem-limit-mb must be at least 16")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.partial{output.suffix or '.tif'}")
    temporary.unlink(missing_ok=True)
    datasets = []
    env_options = {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF",
        "AWS_NO_SIGN_REQUEST": "YES",
    }
    try:
        with rasterio.Env(**env_options):
            for source in sources:
                datasets.append(rasterio.open(str(source)))
            if not datasets:
                raise DemError("no raster sources were supplied for mosaicking")
            crs = datasets[0].crs
            if crs is None or any(dataset.crs != crs for dataset in datasets):
                raise DemError("provider rasters must share a non-empty CRS")
            nodata = _choose_nodata(datasets[0])
            emit_event("mosaic_start", sources=len(datasets), mem_limit_mb=mem_limit_mb)
            merge(
                datasets,
                bounds=tuple(bbox),
                nodata=nodata,
                target_aligned_pixels=True,
                mem_limit=mem_limit_mb,
                dst_path=temporary,
                dst_kwds={
                    "driver": "GTiff",
                    "nodata": nodata,
                    "compress": "DEFLATE",
                    "tiled": True,
                    "blockxsize": 512,
                    "blockysize": 512,
                    "BIGTIFF": "IF_SAFER",
                },
            )
        if geometries_wgs84:
            emit_event("mask_start", blocks="windowed")
            apply_windowed_aoi_mask(temporary, geometries_wgs84)
        temporary.replace(output)
        emit_event("mosaic_complete", output=str(output.resolve()), bytes=output.stat().st_size)
        return {"sources": len(sources), "mem_limit_mb": mem_limit_mb, "windowed": True}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        for dataset in datasets:
            dataset.close()


def validate_dem(path: Path, requested_bbox: Sequence[float] | None = None) -> dict[str, Any]:
    try:
        import numpy as np
        import rasterio
        from rasterio.warp import transform_bounds
    except ImportError as exc:
        raise _dependency_error("raster", exc) from exc

    path = Path(path)
    if not path.exists():
        raise DemError(f"file not found: {path}")
    if not path.is_file():
        raise DemError(f"not a regular file: {path}")

    try:
        dataset = rasterio.open(path)
    except Exception as exc:
        raise DemError(f"cannot open raster {path}: {safe_error(exc)}") from exc

    failures = []
    warnings = []
    with dataset:
        if dataset.crs is None:
            failures.append("missing CRS")
        if dataset.width <= 0 or dataset.height <= 0 or dataset.count <= 0:
            failures.append("invalid raster dimensions")
        sample_width = min(dataset.width, 1024)
        sample_height = min(dataset.height, 1024)
        sample = dataset.read(1, out_shape=(sample_height, sample_width), masked=True)
        valid = sample.compressed()
        total = sample.size
        valid_count = int(valid.size)
        if valid_count == 0:
            failures.append("raster contains no valid sampled pixels")
            statistics = {"min": None, "max": None, "mean": None}
        else:
            statistics = {
                "min": float(np.min(valid)),
                "max": float(np.max(valid)),
                "mean": float(np.mean(valid)),
            }
            if not all(math.isfinite(value) for value in statistics.values()):
                failures.append("sample statistics are not finite")
        overlap = None
        bounds_wgs84 = None
        if dataset.crs:
            bounds_wgs84 = transform_bounds(dataset.crs, "EPSG:4326", *dataset.bounds, densify_pts=21)
        if requested_bbox and bounds_wgs84:
            west, south, east, north = normalize_bbox(requested_bbox)
            bw, bs, be, bn = bounds_wgs84
            overlap = max(west, bw) < min(east, be) and max(south, bs) < min(north, bn)
            if not overlap:
                failures.append("raster does not overlap requested bbox")
            coverage = max(0.0, min(east, be) - max(west, bw)) * max(0.0, min(north, bn) - max(south, bs))
            requested_area = (east - west) * (north - south)
            if requested_area and coverage / requested_area < 0.99:
                warnings.append("raster covers less than 99% of the requested bbox")
        return {
            "status": "fail" if failures else ("warn" if warnings else "pass"),
            "path": str(path.resolve()),
            "driver": dataset.driver,
            "crs": str(dataset.crs) if dataset.crs else None,
            "bounds": list(dataset.bounds),
            "bounds_wgs84": list(bounds_wgs84) if bounds_wgs84 else None,
            "width": dataset.width,
            "height": dataset.height,
            "bands": dataset.count,
            "dtype": dataset.dtypes[0] if dataset.count else None,
            "nodata": dataset.nodata,
            "resolution": [abs(dataset.transform.a), abs(dataset.transform.e)],
            "sampled_pixels": total,
            "valid_sampled_pixels": valid_count,
            "nodata_fraction_sample": 1 - (valid_count / total) if total else 1.0,
            "statistics_sample": statistics,
            "requested_bbox_overlap": overlap,
            "failures": failures,
            "warnings": warnings,
        }


def validate_tile_directory(directory: Path, verify_checksums: bool = False) -> dict[str, Any]:
    try:
        import rasterio
    except ImportError as exc:
        raise _dependency_error("raster", exc) from exc

    files = [
        path for path in directory.rglob("*")
        if path.is_file() and not path.name.endswith(".part") and path.name not in ("manifest.json", "dem-job.json")
    ]
    failures = []
    raster_count = 0
    archive_count = 0
    manifest_count = 0
    manifest_assets = 0
    checksum_verified = 0
    for manifest_path in directory.rglob("manifest.json"):
        manifest_count += 1
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for record in manifest.get("assets", {}).values():
                manifest_assets += 1
                filename = record.get("filename")
                asset_path = manifest_path.parent / filename if filename else None
                if record.get("status") != "completed":
                    failures.append(f"manifest asset is not completed: {record.get('id')}")
                    continue
                if asset_path is None or not asset_path.exists():
                    failures.append(f"manifest asset file is missing: {record.get('id')}")
                    continue
                if asset_path.stat().st_size != int(record.get("bytes") or -1):
                    failures.append(f"manifest asset size mismatch: {record.get('id')}")
                    continue
                if verify_checksums and record.get("sha256"):
                    checksum_verified += 1
                    if sha256_file(asset_path) != record["sha256"]:
                        failures.append(f"manifest asset checksum mismatch: {record.get('id')}")
        except Exception as exc:
            failures.append(f"invalid manifest {manifest_path.name}: {safe_error(exc)}")
    for path in files:
        try:
            if path.suffix.lower() in (".tif", ".tiff"):
                with rasterio.open(path) as dataset:
                    if dataset.crs is None or dataset.width <= 0 or dataset.height <= 0:
                        failures.append(f"invalid raster header: {path.name}")
                    else:
                        dataset.read(1, out_shape=(min(32, dataset.height), min(32, dataset.width)), masked=True)
                raster_count += 1
            elif zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    bad = archive.testzip()
                if bad:
                    failures.append(f"corrupt archive member in {path.name}: {bad}")
                archive_count += 1
        except Exception as exc:
            failures.append(f"{path.name}: {safe_error(exc)}")
    if not files:
        failures.append("tile directory contains no downloaded files")
    return {
        "status": "fail" if failures else "pass",
        "path": str(directory.resolve()),
        "files": len(files),
        "raster_files": raster_count,
        "archive_files": archive_count,
        "manifests": manifest_count,
        "manifest_assets": manifest_assets,
        "checksums_verified": checksum_verified,
        "bytes": sum(path.stat().st_size for path in files),
        "failures": failures,
    }


def _tiles_output_path(output: Path) -> Path:
    if output.suffix.lower() in (".tif", ".tiff"):
        return output.with_name(f"{output.stem}_tiles")
    return output


def _job_identity(
    source: str,
    dataset: str,
    bbox: Sequence[float],
    mode: str,
) -> dict[str, Any]:
    return {"source": source, "dataset": dataset, "bbox_wgs84": list(bbox), "mode": mode}


def _manifest_has_completed_assets(directory: Path) -> bool:
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return any(record.get("status") == "completed" for record in data.get("assets", {}).values())
    except Exception:
        return False


def execute_provider_job(
    source: str,
    dataset: str,
    bbox: Sequence[float],
    geometries: list[dict[str, Any]] | None,
    mode: str,
    output: Path,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path | None]:
    assets = discover_assets(source, dataset, bbox, args)
    public_assets = [asset.public_url for asset in assets]
    identity = _job_identity(source, dataset, bbox, mode)
    provider = {
        "asset_count": len(assets),
        "assets": public_assets,
        "items": [asset.id for asset in assets],
        "native_tiles": SOURCES[source]["native_tiles"],
    }
    if mode == "tiles":
        root = _tiles_output_path(output)
        job_directory = root / source
        paths, manifest = download_assets(assets, job_directory, identity, args)
        validation = validate_tile_directory(job_directory)
        provider["manifest"] = str(manifest.path.resolve())
        provider["downloaded_files"] = [str(path.resolve()) for path in paths]
        provider["aoi_mask_applied"] = False
        return provider, validation, root, root / "dem-job.json"

    stream_error = None
    direct_stream = source in ("mpc", "aws") and not args.stage_assets
    if direct_stream:
        try:
            mosaic_info = mosaic_sources_windowed(
                [asset.url for asset in assets],
                bbox,
                output,
                geometries,
                args.mem_limit_mb,
            )
            validation = validate_dem(output, bbox)
            provider.update(mosaic_info)
            provider["transfer"] = "remote_cog_windowed"
            return provider, validation, output, output.with_suffix(output.suffix + ".dem.json")
        except Exception as exc:
            stream_error = safe_error(exc)
            emit_event("streaming_mosaic_failed", error=stream_error, fallback="staged_resumable_download")

    cache_root = output.parent / f".{output.stem}.parts" / source
    paths, manifest = download_assets(assets, cache_root, identity, args)
    rasters = prepare_rasters(paths, cache_root / "extracted")
    mosaic_info = mosaic_sources_windowed(rasters, bbox, output, geometries, args.mem_limit_mb)
    validation = validate_dem(output, bbox)
    provider.update(mosaic_info)
    provider["transfer"] = "staged_resumable_download"
    provider["download_records"] = list(manifest.data.get("assets", {}).values())
    provider["stream_error"] = stream_error
    if not args.keep_cache and validation["status"] != "fail":
        shutil.rmtree(cache_root)
        parent = cache_root.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        provider["cache_removed"] = True
        provider["manifest"] = None
    else:
        provider["cache_removed"] = False
        provider["manifest"] = str(manifest.path.resolve())
    return provider, validation, output, output.with_suffix(output.suffix + ".dem.json")


def cmd_sources(_: argparse.Namespace) -> int:
    print(json.dumps(SOURCES, ensure_ascii=False, indent=2))
    return 0


def _plan_payload(args: argparse.Namespace) -> dict[str, Any]:
    bbox, geometries = resolve_aoi(args)
    source, dataset, fallback = select_source(args.source, args.dataset, args.resolution, bbox)
    pixels = estimate_pixels(bbox, dataset)
    area, area_method = aoi_area_km2(bbox, geometries)
    mode, mode_reason = choose_output_mode(
        args.mode,
        area,
        args.mosaic_max_area_km2,
        pixels,
        args.max_pixels,
    )
    estimated_assets = estimate_asset_count(source, dataset, bbox, args.chunk_degrees)
    warnings: list[str] = []
    allow_large = bool(getattr(args, "allow_large", False))
    if mode == "mosaic" and (
        area > args.mosaic_max_area_km2 or (pixels is not None and pixels > args.max_pixels)
    ):
        if allow_large:
            warnings.append(
                "mosaic size exceeds default thresholds; --allow-large acknowledges the extra resource cost"
            )
        else:
            warnings.append(
                "mosaic size exceeds default thresholds; pass --allow-large to actually run this job"
            )
    if fallback:
        warnings.append(fallback["reason"])
    return {
        "source": source,
        "dataset": dataset,
        "requested_source": fallback["from_source"] if fallback else source,
        "requested_dataset": (
            dataset if not fallback else None
        ),
        "credential_fallback": fallback,
        "bbox_wgs84": list(bbox),
        "aoi_area_km2": round(area, 3),
        "area_method": area_method,
        "estimated_pixels_for_bbox_grid": pixels,
        "estimated_uncompressed_gib_float32": round((pixels or 0) * 4 / 1024**3, 3) if pixels else None,
        "estimated_asset_count": estimated_assets,
        "requested_mode": args.mode,
        "selected_mode": mode,
        "mode_reason": mode_reason,
        "allow_large_acknowledged": allow_large,
        "warnings": warnings,
        "mosaic_max_area_km2": args.mosaic_max_area_km2,
        "max_pixels": args.max_pixels,
        **DATASETS.get(dataset, {}),
        "credentials": SOURCES[source]["credentials"],
        "coverage": SOURCES[source]["coverage"],
        "large_area_behavior": "download source assets without mosaicking" if mode == "tiles" else "windowed disk mosaic",
    }


def cmd_plan(args: argparse.Namespace) -> int:
    print(json.dumps(_plan_payload(args), ensure_ascii=False, indent=2))
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    bbox, geometries = resolve_aoi(args)
    requested_source = args.source
    source, dataset, fallback = select_source(requested_source, args.dataset, args.resolution, bbox)
    if fallback:
        emit_event("credential_fallback", **fallback)
    pixels = estimate_pixels(bbox, dataset)
    area, area_method = aoi_area_km2(bbox, geometries)
    mode, mode_reason = choose_output_mode(
        args.mode,
        area,
        args.mosaic_max_area_km2,
        pixels,
        args.max_pixels,
    )
    if mode == "mosaic":
        enforce_mosaic_size(pixels, args.max_pixels, args.allow_large)
    output = Path(args.output)
    effective_output = output if mode == "mosaic" else _tiles_output_path(output)
    if mode == "mosaic" and output.exists() and not args.overwrite:
        raise DemError(f"output already exists: {output}; use --overwrite to replace it")

    attempts = []
    candidate_sources = [source]
    if (requested_source == "auto" or fallback) and source == "mpc" and dataset in SOURCES["aws"]["datasets"]:
        candidate_sources.append("aws")
    final_error = None
    for candidate in candidate_sources:
        try:
            provider, validation, effective_output, metadata_path = execute_provider_job(
                candidate,
                dataset,
                bbox,
                geometries,
                mode,
                output,
                args,
            )
            source = candidate
            break
        except Exception as exc:
            message = safe_error(exc)
            attempts.append({"source": candidate, "error": message})
            final_error = exc
            if candidate == "mpc":
                job_dir = (
                    _tiles_output_path(output) / candidate
                    if mode == "tiles"
                    else output.parent / f".{output.stem}.parts" / candidate
                )
                if _manifest_has_completed_assets(job_dir):
                    raise DemError(
                        f"MPC job has completed resumable assets; rerun the same command instead of switching source. {message}"
                    ) from exc
    else:
        raise DemError(safe_error(final_error or "all providers failed"))

    metadata = {
        "created_at": utc_now(),
        "source": source,
        "dataset": dataset,
        "requested_source": fallback["from_source"] if fallback else source,
        "credential_fallback": fallback,
        "mode": mode,
        "mode_reason": mode_reason,
        "bbox_wgs84": list(bbox),
        "aoi_area_km2": round(area, 3),
        "area_method": area_method,
        "estimated_pixels_for_bbox_grid": pixels,
        "surface": DATASETS.get(dataset, {}).get("surface", "unknown"),
        "vertical_datum": DATASETS.get(dataset, {}).get("vertical_datum", "unknown"),
        "attempts": attempts,
        "provider": provider,
        "validation": validation,
    }
    if metadata_path is None:
        raise DemError("internal error: no metadata path")
    write_json(metadata_path, metadata)
    print(
        json.dumps(
            {
                "output": str(effective_output.resolve()),
                "sidecar": str(metadata_path.resolve()),
                **metadata,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if validation["status"] != "fail" else 2


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    report = validate_tile_directory(path, args.verify_checksums) if path.is_dir() else validate_dem(path, args.bbox)
    if args.report:
        write_json(Path(args.report), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "fail" else 2


def add_aoi_arguments(parser: argparse.ArgumentParser, *, include_admin: bool = False) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    if include_admin:
        group.add_argument("--admin", help="Chinese administrative-division name; resolved via map.ruiduobao.com")
        group.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
        group.add_argument("--aoi", help="Vector AOI readable by Fiona")
        admin_group = parser.add_argument_group("admin AOI options",
                                                "Optional refinement for --admin; ignored otherwise")
        admin_group.add_argument("--admin-code", help="Use a known 6/12-digit code instead of searching by name")
        admin_group.add_argument("--admin-province", help="Province name to disambiguate (e.g. '四川省')")
        admin_group.add_argument("--admin-city", help="Prefecture-level city to disambiguate (e.g. '成都市')")
        admin_group.add_argument("--admin-level", default="xian",
                                 help="Administrative level: sheng/province, shi/city, xian/county, xiang/town, cun/village (default xian)")
        admin_group.add_argument("--admin-year", type=int, default=2023,
                                 help="Year of the administrative vector (default 2023)")
        admin_group.add_argument("--admin-expand-km", type=float, default=1.0,
                                 help="Pad the raw admin bbox by N kilometres on every side (default 1)")
    else:
        group.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
        group.add_argument("--aoi", help="Vector AOI readable by Fiona")


def add_planning_arguments(parser: argparse.ArgumentParser, *, include_admin: bool = False) -> None:
    add_aoi_arguments(parser, include_admin=include_admin)
    parser.add_argument("--source", choices=["auto", *SOURCES], default="auto")
    parser.add_argument("--dataset")
    parser.add_argument("--resolution", type=float, help="Desired nominal resolution in metres")
    parser.add_argument("--mode", choices=["auto", "mosaic", "tiles"], default="auto")
    parser.add_argument("--mosaic-max-area-km2", type=float, default=DEFAULT_MOSAIC_MAX_AREA_KM2)
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    parser.add_argument("--chunk-degrees", type=float, default=DEFAULT_CHUNK_DEGREES)
    parser.add_argument("--allow-large", action="store_true",
                        help="Acknowledge oversized mosaic (plan: reports the constraint, download: bypasses it)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    sources_parser = subparsers.add_parser("sources", help="List providers and supported dataset names")
    sources_parser.set_defaults(func=cmd_sources)

    plan_parser = subparsers.add_parser("plan", help="Choose a provider, output mode, and resource strategy")
    add_planning_arguments(plan_parser, include_admin=True)
    plan_parser.set_defaults(func=cmd_plan)

    download_parser = subparsers.add_parser("download", help="Download a resumable tile set or windowed DEM mosaic")
    add_planning_arguments(download_parser, include_admin=True)
    download_parser.add_argument("--output", required=True, help="GeoTIFF for mosaic mode or directory for tile mode")
    download_parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    download_parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    download_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    download_parser.add_argument("--mem-limit-mb", type=int, default=DEFAULT_MEM_LIMIT_MB)
    download_parser.add_argument("--max-assets", type=int, default=DEFAULT_MAX_ASSETS)
    download_parser.add_argument("--max-asset-gb", type=float, default=DEFAULT_MAX_ASSET_BYTES / 1_000_000_000)
    download_parser.add_argument("--allow-many-assets", action="store_true")
    download_parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    download_parser.add_argument("--verify-existing", action="store_true", help="Hash completed assets before skipping")
    download_parser.add_argument("--stage-assets", action="store_true", help="Download assets before mosaicking")
    download_parser.add_argument("--keep-cache", action="store_true")
    download_parser.add_argument("--overwrite", action="store_true")
    download_parser.set_defaults(func=cmd_download)

    validate_parser = subparsers.add_parser("validate", help="Validate a DEM GeoTIFF or raw tile directory")
    validate_parser.add_argument("path")
    validate_parser.add_argument("--bbox", nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    validate_parser.add_argument("--report", help="Optional JSON report path")
    validate_parser.add_argument("--verify-checksums", action="store_true")
    validate_parser.set_defaults(func=cmd_validate)

    admin_parser = subparsers.add_parser("admin-bbox", help="Resolve a Chinese admin name to a WGS84 bbox (optionally padded by N km)")
    admin_group = admin_parser.add_mutually_exclusive_group(required=True)
    admin_group.add_argument("--name", help="Administrative-division name, e.g. '锦江区'")
    admin_group.add_argument("--code", help="Administrative-division code, e.g. '510104'")
    admin_parser.add_argument("--province", help="Province to disambiguate (e.g. '四川省')")
    admin_parser.add_argument("--city", help="Prefecture-level city to disambiguate (e.g. '成都市')")
    admin_parser.add_argument("--level", default="xian",
                              help="Admin level: sheng/province, shi/city, xian/county, xiang/town, cun/village (default xian)")
    admin_parser.add_argument("--year", type=int, default=2023, help="Year of the admin vector (default 2023)")
    admin_parser.add_argument("--expand-km", type=float, default=1.0,
                              help="Pad the raw admin bbox by N km on every side (default 1)")
    admin_parser.set_defaults(func=cmd_admin_bbox)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except DemError as exc:
        print(json.dumps({"status": "error", "error": safe_error(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "error", "error": "cancelled; rerun the same command to resume"}), file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
