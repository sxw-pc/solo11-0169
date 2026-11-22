import json
from .store import connect,source,at,repeated

def camera(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'camera_unit',file)
        if repeated(db,'camera_unit',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM vehicle WHERE vin=?',(d['vin'],)).fetchone():raise ValueError('unknown_vehicle')
        db.execute('INSERT INTO camera_unit VALUES(?,?,?,?,?,?,?)',(d['id'],d['vin'],d['serial'],d['lens_model'],d['width_px'],d['height_px'],sid))
        return {'id':d['id'],'replayed':False}
def board(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'target_board',file)
        if repeated(db,'target_board',d['id'],sid):return {'id':d['id'],'replayed':True}
        db.execute('INSERT INTO target_board VALUES(?,?,?,?,?)',(d['id'],d['board_serial'],json.dumps(d['points']),at(d['survey_at']),sid))
        return {'id':d['id'],'replayed':False}
def point(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'image_point',file)
        if repeated(db,'image_point',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM camera_unit WHERE id=?',(d['camera_id'],)).fetchone():raise ValueError('unknown_camera')
        if not db.execute('SELECT 1 FROM target_board WHERE id=?',(d['board_id'],)).fetchone():raise ValueError('unknown_board')
        db.execute('INSERT INTO image_point VALUES(?,?,?,?,?,?,?,?)',(d['id'],d['camera_id'],d['board_id'],d['corner_no'],d['u_px'],d['v_px'],at(d['captured_at']),sid))
        return {'id':d['id'],'replayed':False}
def pose(path,file):
    with connect(path) as db,db:
        sid,d,_=source(db,'road_pose',file)
        if repeated(db,'road_pose',d['id'],sid):return {'id':d['id'],'replayed':True}
        if not db.execute('SELECT 1 FROM camera_unit WHERE id=?',(d['camera_id'],)).fetchone():raise ValueError('unknown_camera')
        db.execute('INSERT INTO road_pose VALUES(?,?,?,?,?,?,?)',(d['id'],d['camera_id'],d['speed_kph'],d['pitch_mdeg'],d['load_kg'],at(d['observed_at']),sid))
        return {'id':d['id'],'replayed':False}
def observations(path):
    with connect(path) as db:return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('camera_unit','target_board','image_point','road_pose')}
