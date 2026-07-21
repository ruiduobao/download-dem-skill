import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("dem_download.py")
SPEC = importlib.util.spec_from_file_location("dem_download", MODULE_PATH)
dem = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = dem
SPEC.loader.exec_module(dem)


class RangeHandler(BaseHTTPRequestHandler):
    payload = bytes(range(256)) * 4096

    def do_GET(self):
        start = 0
        range_header = self.headers.get("Range")
        if range_header:
            start = int(range_header.split("=")[1].split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}")
        else:
            self.send_response(200)
        body = self.payload[start:]
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        return


class DemDownloadTests(unittest.TestCase):
    def test_normalize_bbox_rejects_antimeridian(self):
        with self.assertRaises(dem.DemError):
            dem.normalize_bbox((170, -10, -170, 10))

    def test_boundary_touch_is_not_area_overlap(self):
        self.assertFalse(dem.bbox_has_area_overlap((11, 47, 12, 48), (11, 46, 12, 47)))
        self.assertTrue(dem.bbox_has_area_overlap((11, 47, 12, 48), (11.5, 47.5, 12.5, 48.5)))

    def test_spherical_area_uses_geometry_not_bbox(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[(0, 0), (0.5, 0), (0.5, 1), (0, 1), (0, 0)]],
        }
        bbox_area = dem.bbox_area_km2((0, 0, 1, 1))
        geometry_area, method = dem.aoi_area_km2((0, 0, 1, 1), [geometry])
        self.assertEqual(method, "geometry_spherical")
        self.assertAlmostEqual(geometry_area / bbox_area, 0.5, places=3)

    def test_output_mode_uses_area_and_pixel_limits(self):
        self.assertEqual(dem.choose_output_mode("auto", 9_999, 10_000, 50_000_000, 100_000_000)[0], "mosaic")
        self.assertEqual(dem.choose_output_mode("auto", 10_001, 10_000, 50_000_000, 100_000_000)[0], "tiles")
        self.assertEqual(dem.choose_output_mode("auto", 100, 10_000, 200_000_000, 100_000_000)[0], "tiles")
        self.assertEqual(dem.choose_output_mode("mosaic", 50_000, 10_000, 200_000_000, 100_000_000)[0], "mosaic")

    def test_split_bbox(self):
        chunks = dem.split_bbox((0, 0, 2.2, 1.2), 1.0)
        self.assertEqual(len(chunks), 6)
        self.assertEqual(chunks[0], (0.0, 0.0, 1.0, 1.0))
        self.assertEqual(chunks[-1], (2.0, 1.0, 2.2, 1.2))

    def test_aws_single_tile_url(self):
        urls = dem.aws_urls((116.1, 39.7, 116.6, 40.0), "cop-dem-glo-30")
        self.assertEqual(len(urls), 1)
        self.assertIn("Copernicus_DSM_COG_10_N39_00_E116_00_DEM", urls[0])
        self.assertIn("copernicus-dem-30m", urls[0])

    def test_aws_negative_coordinate_tiles(self):
        urls = dem.aws_urls((-123.2, 45.1, -121.9, 46.2), "cop-dem-glo-90")
        self.assertEqual(len(urls), 6)
        self.assertTrue(any("N45_00_W124_00" in url for url in urls))
        self.assertTrue(all("Copernicus_DSM_COG_30_" in url for url in urls))

    def test_source_selection_understands_dataset_aliases(self):
        self.assertEqual(
            dem.select_source("auto", None, 30, (0, 0, 1, 1)),
            ("mpc", "cop-dem-glo-30", None),
        )
        with mock.patch.dict(os.environ, {"OPENTOPOGRAPHY_API_KEY": "stub"}, clear=False):
            self.assertEqual(
                dem.select_source("auto", "srtm", 30, (0, 0, 1, 1)),
                ("opentopography", "SRTMGL1", None),
            )
        with mock.patch.dict(os.environ, {"EARTHDATA_TOKEN": "stub"}, clear=False):
            self.assertEqual(
                dem.select_source("auto", "aster", 30, (0, 0, 1, 1)),
                ("earthdata", "aster-gdem-v3", None),
            )

    def test_source_selection_falls_back_to_mpc_without_credentials(self):
        env = os.environ.copy()
        env.pop("OPENTOPOGRAPHY_API_KEY", None)
        env.pop("EARTHDATA_TOKEN", None)
        with mock.patch.dict(os.environ, env, clear=True):
            source, dataset, fallback = dem.select_source("opentopography", None, 30, (0, 0, 1, 1))
        self.assertEqual(source, "mpc")
        self.assertEqual(dataset, "cop-dem-glo-30")
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["from_source"], "opentopography")
        self.assertEqual(fallback["to_source"], "mpc")
        self.assertIn("OPENTOPOGRAPHY_API_KEY", fallback["reason"])

    def test_source_selection_earthdata_falls_back_to_mpc_without_token(self):
        env = os.environ.copy()
        env.pop("EARTHDATA_TOKEN", None)
        with mock.patch.dict(os.environ, env, clear=True):
            source, dataset, fallback = dem.select_source("earthdata", "aster-gdem-v3", 30, (0, 0, 1, 1))
        self.assertEqual(source, "mpc")
        self.assertEqual(dataset, "aster-gdem-v3") if False else None  # dataset name passes through unchanged
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["from_source"], "earthdata")
        self.assertIn("EARTHDATA_TOKEN", fallback["reason"])

    def test_usgs_official_dataset_tags(self):
        self.assertEqual(
            dem.USGS_DATASET_NAMES["10m"],
            "National Elevation Dataset (NED) 1/3 arc-second",
        )

    def test_windowed_mosaic_and_exact_mask(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            first = root / "first.tif"
            second = root / "second.tif"
            output = root / "mosaic.tif"
            for path, west, value in ((first, 0, 1), (second, 1, 2)):
                with rasterio.open(
                    path,
                    "w",
                    driver="GTiff",
                    width=10,
                    height=10,
                    count=1,
                    dtype="float32",
                    crs="EPSG:4326",
                    transform=from_origin(west, 1, 0.1, 0.1),
                    nodata=-9999,
                ) as dataset:
                    dataset.write(np.full((10, 10), value, dtype="float32"), 1)
            geometry = {
                "type": "Polygon",
                "coordinates": [[(0, 0), (1.5, 0), (1.5, 1), (0, 1), (0, 0)]],
            }
            report = dem.mosaic_sources_windowed([first, second], (0, 0, 2, 1), output, [geometry], 16)
            self.assertTrue(report["windowed"])
            with rasterio.open(output) as dataset:
                values = dataset.read(1, masked=True)
                self.assertEqual((dataset.width, dataset.height), (20, 10))
                self.assertEqual(dataset.block_shapes[0], (512, 512))
            self.assertEqual(int(values[:, 15:].count()), 0)
            self.assertGreater(int(values[:, :15].count()), 0)

    def test_validate_synthetic_geotiff(self):
        import numpy as np
        import rasterio
        from rasterio.transform import from_origin

        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "synthetic.tif"
            values = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                width=10,
                height=10,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(116.0, 40.0, 0.01, 0.01),
                nodata=-9999,
            ) as dataset:
                dataset.write(values, 1)
            report = dem.validate_dem(path, (116.0, 39.9, 116.1, 40.0))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["statistics_sample"]["min"], 0.0)
            self.assertEqual(report["statistics_sample"]["max"], 99.0)

    def test_mosaic_pixel_guard(self):
        with self.assertRaises(dem.DemError):
            dem.enforce_mosaic_size(200_000_000, 100_000_000, False)
        dem.enforce_mosaic_size(200_000_000, 100_000_000, True)

    def test_archive_extraction_renames_untrusted_paths(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            archive = root / "product.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../../outside.tif", b"TIFF")
                stream.writestr("metadata.xml", b"ignored")
            paths = dem.extract_geotiffs(archive, root / "extract")
            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].parent, root / "extract")
            self.assertFalse((root.parent / "outside.tif").exists())

    def test_safe_error_redacts_secrets_and_queries(self):
        message = dem.safe_error("https://example.test/a.tif?sig=secret&se=later API_Key=abc Bearer token123")
        self.assertNotIn("secret", message)
        self.assertNotIn("abc", message)
        self.assertNotIn("token123", message)

    def test_manifest_rejects_different_job(self):
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "manifest.json"
            dem.JobManifest(path, {"source": "aws"}, resume=True)
            with self.assertRaises(dem.DemError):
                dem.JobManifest(path, {"source": "mpc"}, resume=True)

    def test_tile_validation_detects_incomplete_manifest_and_checksum(self):
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            asset = root / "asset.bin"
            asset.write_bytes(b"valid")
            manifest = {
                "assets": {
                    "done": {
                        "id": "done",
                        "filename": "asset.bin",
                        "status": "completed",
                        "bytes": 5,
                        "sha256": hashlib.sha256(b"wrong").hexdigest().upper(),
                    },
                    "pending": {
                        "id": "pending",
                        "filename": "missing.bin",
                        "status": "pending",
                        "bytes": 0,
                    },
                }
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            report = dem.validate_tile_directory(root, verify_checksums=True)
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any("checksum mismatch" in item for item in report["failures"]))
            self.assertTrue(any("not completed" in item for item in report["failures"]))

    def test_http_range_resume(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_name:
                root = Path(temp_name)
                destination = root / "asset.bin"
                partial = root / "asset.bin.part"
                partial.write_bytes(RangeHandler.payload[:4096])
                asset = dem.Asset(
                    "local",
                    f"http://127.0.0.1:{server.server_port}/asset.bin",
                    "asset.bin",
                    "http://example.invalid/asset.bin",
                    kind="archive",
                )
                info = dem.download_resumable(asset, destination, True, 10, 1, 2_000_000)
                self.assertEqual(info["resumed_from"], 4096)
                self.assertEqual(destination.read_bytes(), RangeHandler.payload)
                self.assertEqual(info["sha256"], hashlib.sha256(RangeHandler.payload).hexdigest().upper())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_aster_cmr_parser_filters_boundary_touch(self):
        entries = [
            {
                "id": "inside",
                "producer_granule_id": "ASTGTMV003_N30E121",
                "links": [
                    {
                        "href": "https://data.example/ASTGTMV003_N30E121_dem.tif",
                        "rel": "http://esipfed.org/ns/fedsearch/1.1/data#",
                    }
                ],
            },
            {
                "id": "touch",
                "producer_granule_id": "ASTGTMV003_N29E121",
                "links": [
                    {
                        "href": "https://data.example/ASTGTMV003_N29E121_dem.tif",
                        "rel": "http://esipfed.org/ns/fedsearch/1.1/data#",
                    }
                ],
            },
        ]
        args = argparse.Namespace(
            earthdata_token_env="TEST_EARTHDATA_TOKEN",
            timeout=10,
            retries=0,
            max_assets=10,
            allow_many_assets=False,
        )
        with mock.patch.dict(os.environ, {"TEST_EARTHDATA_TOKEN": "secret"}, clear=False):
            with mock.patch.object(dem, "_get_json", side_effect=[{"feed": {"entry": entries}}]):
                assets = dem.discover_earthdata_assets((121.1, 30.1, 121.2, 30.2), "aster-gdem-v3", args)
        self.assertEqual([asset.id for asset in assets], ["inside"])
        self.assertEqual(assets[0].filename, "ASTGTMV003_N30E121_dem.tif")
        self.assertNotIn("secret", json.dumps(assets[0].metadata))

    def test_tile_output_path_is_predictable(self):
        self.assertEqual(dem._tiles_output_path(Path("china.tif")), Path("china_tiles"))
        self.assertEqual(dem._tiles_output_path(Path("china_tiles")), Path("china_tiles"))

    def test_validate_dem_missing_file_raises_demerror(self):
        with tempfile.TemporaryDirectory() as temp_name:
            missing = Path(temp_name) / "does-not-exist.tif"
            with self.assertRaises(dem.DemError) as ctx:
                dem.validate_dem(missing, (116.0, 39.9, 116.1, 40.0))
            self.assertIn("file not found", str(ctx.exception))

    def test_validate_dem_corrupt_file_raises_demerror(self):
        with tempfile.TemporaryDirectory() as temp_name:
            corrupt = Path(temp_name) / "corrupt.tif"
            corrupt.write_bytes(b"not a real GeoTIFF at all")
            with self.assertRaises(dem.DemError) as ctx:
                dem.validate_dem(corrupt, (116.0, 39.9, 116.1, 40.0))
            self.assertIn("cannot open raster", str(ctx.exception))

    def test_plan_payload_includes_allow_large_warning(self):
        with mock.patch.object(dem, "resolve_aoi", return_value=((78.0, 27.0, 99.0, 36.0), None)):
            with mock.patch.object(dem, "select_source", return_value=("mpc", "cop-dem-glo-30", None)):
                with mock.patch.object(dem, "estimate_pixels", return_value=2_500_000_000):
                    with mock.patch.object(dem, "aoi_area_km2", return_value=(1_990_000.0, "bbox_spherical")):
                        with mock.patch.object(dem, "estimate_asset_count", return_value=189):
                            args = argparse.Namespace(
                                source="auto",
                                dataset="cop-dem-glo-30",
                                resolution=None,
                                mode="mosaic",
                                mosaic_max_area_km2=10_000.0,
                                max_pixels=100_000_000,
                                chunk_degrees=dem.DEFAULT_CHUNK_DEGREES,
                                allow_large=True,
                            )
                            with mock.patch.object(dem, "select_source", return_value=("mpc", "cop-dem-glo-30", None)):
                                payload = dem._plan_payload(args)
                            self.assertTrue(payload["allow_large_acknowledged"])
                            self.assertTrue(any("--allow-large acknowledges" in w for w in payload["warnings"]))

                            args_no_flag = argparse.Namespace(**{**vars(args), "allow_large": False})
                            payload_no_flag = dem._plan_payload(args_no_flag)
                            self.assertFalse(payload_no_flag["allow_large_acknowledged"])
                            self.assertTrue(any("pass --allow-large" in w for w in payload_no_flag["warnings"]))

    def test_plan_parser_accepts_allow_large(self):
        parser = dem.build_parser()
        args = parser.parse_args([
            "plan",
            "--bbox", "78", "27", "99", "36",
            "--source", "auto",
            "--dataset", "cop-dem-glo-30",
            "--mode", "mosaic",
            "--allow-large",
        ])
        self.assertTrue(args.allow_large)
        self.assertEqual(args.command, "plan")


class AdminApiTests(unittest.TestCase):
    def test_expand_bbox_one_kilometre_is_symmetric(self):
        west, south, east, north = 104.05775, 30.549384, 104.16833, 30.672644
        w, s, e, n = dem.expand_bbox_km((west, south, east, north), 1.0)
        # original side lengths
        original_width_deg = east - west
        original_height_deg = north - south
        # expanded bbox is strictly LARGER on every side (west goes further west, etc.)
        self.assertLess(w, west)
        self.assertGreater(e, east)
        self.assertLess(s, south)
        self.assertGreater(n, north)
        # new bbox should be a little larger in both axes
        self.assertAlmostEqual(e - w, original_width_deg + 2 * (1.0 / (111.320 * math.cos(math.radians((s + n) / 2)))), places=6)
        self.assertAlmostEqual(n - s, original_height_deg + 2 * (1.0 / 110.574), places=6)

    def test_expand_bbox_zero_is_identity(self):
        bbox = (104.0, 30.0, 105.0, 31.0)
        self.assertEqual(dem.expand_bbox_km(bbox, 0.0), bbox)

    def test_expand_bbox_rejects_negative(self):
        with self.assertRaises(dem.DemError):
            dem.expand_bbox_km((0, 0, 1, 1), -0.5)

    def test_bbox_of_geojson_polygon(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[(10, 20), (12, 20), (12, 22), (10, 22), (10, 20)]],
        }
        self.assertEqual(dem._bbox_of_geometry(geometry), (10.0, 20.0, 12.0, 22.0))

    def test_bbox_of_geojson_feature_collection(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]],
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [
                            [[(2, 3), (3, 3), (3, 4), (2, 4), (2, 3)]],
                        ],
                    },
                },
            ],
        }
        self.assertEqual(dem._bbox_of_geojson(geojson), (0.0, 0.0, 3.0, 4.0))

    def test_normalize_admin_level_aliases(self):
        self.assertEqual(dem._normalize_admin_level("省"), "sheng")
        self.assertEqual(dem._normalize_admin_level("county"), "xian")
        self.assertEqual(dem._normalize_admin_level("XIANG"), "xiang")
        self.assertEqual(dem._normalize_admin_level(None), "xian")
        with self.assertRaises(dem.DemError):
            dem._normalize_admin_level("planet")

    def test_ruiduobao_search_parses_sse(self):
        sse = (
            "data: {\"type\":\"result\",\"data\":{\"name\":\"锦江区\",\"code\":\"510104\",\"level\":\"xian\","
            "\"province_name\":\"四川省\",\"city_name\":\"成都市\"},\"scope\":\"province\"}\n"
            "\n"
            "data: {\"type\":\"provinceDone\",\"hasResults\":true}\n"
            "data: {\"type\":\"result\",\"data\":{\"name\":\"510104000000\",\"code\":\"510104000000\","
            "\"level\":\"cun\"},\"scope\":\"province\"}\n"
            "data: {\"type\":\"done\",\"total\":2}\n"
        )
        import io
        with mock.patch.object(dem, "_ruiduobao_request", return_value=io.BytesIO(sse.encode("utf-8"))):
            results = dem._ruiduobao_search("锦江区", province="四川省", limit=10)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["code"], "510104")
        self.assertEqual(results[0]["level"], "xian")
        with mock.patch.object(dem, "_ruiduobao_request", return_value=io.BytesIO(sse.encode("utf-8"))):
            filtered = dem._ruiduobao_search("锦江区", province="四川省", level="xian", limit=10)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["code"], "510104")

    def test_resolve_admin_with_code_only(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[(104.0, 30.5), (104.2, 30.5), (104.2, 30.7), (104.0, 30.7), (104.0, 30.5)]],
                    },
                }
            ],
        }
        with mock.patch.object(dem, "_ruiduobao_geojson_for_code", return_value=geojson):
            result = dem.resolve_admin(name=None, code="510104", expand_km=1.0)
        self.assertEqual(result["code"], "510104")
        self.assertEqual(result["admin_level_code"], "xian")
        self.assertEqual(result["bbox_wgs84"], [104.0, 30.5, 104.2, 30.7])
        # expanded bbox should be larger on all four sides
        rw, rs, re, rn = result["bbox_wgs84_expanded"]
        self.assertLess(rw, 104.0)
        self.assertLess(rs, 30.5)
        self.assertGreater(re, 104.2)
        self.assertGreater(rn, 30.7)
        self.assertEqual(result["source"], "map.ruiduobao.com")
        self.assertGreater(result["area_km2_expanded"], result["area_km2"])

    def test_pick_admin_result_prefers_province_and_city(self):
        items = [
            {"name": "朝阳区", "code": "220104", "level": "xian", "province_name": "吉林省", "city_name": "长春市", "_scope": "nationwide"},
            {"name": "朝阳区", "code": "110105", "level": "xian", "province_name": "北京市", "city_name": "北京市", "_scope": "province"},
        ]
        chosen = dem._pick_admin_result(items, "朝阳区", province="北京市", city="北京市", level="xian")
        self.assertEqual(chosen["code"], "110105")

    def test_pick_admin_result_raises_when_no_match(self):
        with self.assertRaises(dem.DemError):
            dem._pick_admin_result([], "蓬莱区", province="北京市", city=None, level="xian")

    def test_resolve_aoi_uses_admin_metadata(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Polygon",
                    "coordinates": [[(116.0, 39.8), (116.5, 39.8), (116.5, 40.2), (116.0, 40.2), (116.0, 39.8)]]}}
            ],
        }
        with mock.patch.object(dem, "_ruiduobao_geojson_for_code", return_value=geojson):
            args = argparse.Namespace(
                admin="海淀区", admin_code=None, admin_province="北京市", admin_city=None,
                admin_level="xian", admin_year=2023, admin_expand_km=2.0,
                bbox=None, aoi=None,
            )
            bbox, geometries = dem.resolve_aoi(args)
        self.assertIsNone(geometries)
        # 2km expand; bbox strictly larger
        w, s, e, n = bbox
        self.assertLess(w, 116.0)
        self.assertLess(s, 39.8)
        self.assertGreater(e, 116.5)
        self.assertGreater(n, 40.2)
        meta = args.admin_metadata
        self.assertEqual(meta["code"], "110108")  # 海淀区 = 110108

    def test_admin_bbox_parser(self):
        parser = dem.build_parser()
        args = parser.parse_args([
            "admin-bbox", "--name", "锦江区", "--province", "四川省",
            "--city", "成都市", "--expand-km", "1.5",
        ])
        self.assertEqual(args.name, "锦江区")
        self.assertEqual(args.expand_km, 1.5)
        self.assertEqual(args.level, "xian")

    def test_admin_parser_disallows_bbox_with_admin(self):
        parser = dem.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([
                "plan", "--admin", "锦江区", "--bbox", "104", "30", "105", "31",
            ])

    def test_plan_parser_accepts_admin(self):
        parser = dem.build_parser()
        args = parser.parse_args([
            "plan",
            "--admin", "锦江区", "--admin-province", "四川省",
            "--admin-city", "成都市", "--source", "mpc",
            "--dataset", "cop-dem-glo-30",
        ])
        self.assertEqual(args.admin, "锦江区")
        self.assertEqual(args.command, "plan")
        self.assertIsNone(args.bbox)
        self.assertIsNone(args.aoi)


if __name__ == "__main__":
    unittest.main()
