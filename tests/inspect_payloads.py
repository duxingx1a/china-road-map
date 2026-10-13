import gzip,json,sqlite3
from pathlib import Path
from mapbox_vector_tile.Mapbox import vector_tile_pb2

root=Path(__file__).resolve().parents[1]
db=sqlite3.connect(root/'data/basemap/china.mbtiles')
for z,x,y in [(5,26,13),(7,99,53),(8,201,105)]:
    row=db.execute('SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?',(z,x,2**z-1-y)).fetchone()
    blob=row[0]; tile=vector_tile_pb2.tile(); tile.ParseFromString(gzip.decompress(blob))
    print(json.dumps({'tile':[z,x,y],'gzip':len(blob),'layers':{l.name:[len(l.features),len(l.SerializeToString())] for l in tile.layers}},indent=2))
print('Counts',db.execute('SELECT zoom_level,count(*) FROM tiles WHERE zoom_level<=11 GROUP BY zoom_level').fetchall())
