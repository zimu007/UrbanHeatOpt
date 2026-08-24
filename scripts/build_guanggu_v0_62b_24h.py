"""Build the isolated all-62-building Guanggu V0.0b integration case."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd

from build_guanggu_v0_6b_24h import DELIVERY, ROOT, build_case


if __name__ == "__main__":
    source = gpd.read_file(DELIVERY / "03_buildings.geojson")
    building_ids = tuple(sorted(source["building_id"].astype(str)))
    if len(building_ids) != 62 or len(set(building_ids)) != 62:
        raise ValueError(f"Expected 62 unique delivery buildings, got {len(building_ids)}")
    print(
        build_case(
            ROOT / "cases" / "v0_guanggu_62b_24h",
            building_ids=building_ids,
            case_id="v0_guanggu_62b_24h",
            scale_synthetic_capacity_to_load=True,
        )
    )
