"""Download railway source data on the preparation machine, never on app startup."""
from download_data import download_pbf, RAW

if __name__ == '__main__':
    download_pbf('https://download.geofabrik.de/asia/china-latest.osm.pbf', RAW/'railways-source.osm.pbf')
