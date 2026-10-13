"""Separate administrative land from evidenced domestic transport corridors."""
import json
from pathlib import Path
from shapely.geometry import shape
from shapely.ops import unary_union


def coverage(data):
    data = Path(data)
    geometries = [shape(feature['geometry']) for feature in json.loads((data/'china.geojson').read_bytes())['features']]
    corridors = data/'transport-corridors.geojson'
    if corridors.exists():
        geometries.extend(shape(feature['geometry']) for feature in json.loads(corridors.read_bytes())['features'])
    return unary_union(geometries)
