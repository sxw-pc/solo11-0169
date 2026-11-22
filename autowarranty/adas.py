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
        ground=1 if d.get('ground_plane') else 0
        verify=1 if d.get('verification') else 0
        pitch=float(d.get('pitch_mrad') or 0.0)
        db.execute('INSERT INTO target_board VALUES(?,?,?,?,?,?,?,?)',
                   (d['id'],d['board_serial'],json.dumps(d['points']),ground,verify,pitch,at(d['survey_at']),sid))
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
        at(d['observed_at'])
        db.execute('INSERT INTO road_pose VALUES(?,?,?,?,?,?,?)',(d['id'],d['camera_id'],d['speed_kph'],d['pitch_mdeg'],d['load_kg'],at(d['observed_at']),sid))
        return {'id':d['id'],'replayed':False}
def observations(path):
    with connect(path) as db:return {t:[dict(x) for x in db.execute(f'SELECT * FROM {t} ORDER BY id')] for t in ('camera_unit','target_board','image_point','road_pose')}

def _load_calibration_evidence(db,camera_id):
    """Read-only assembly of every original record relevant to a calibration solve."""
    cam=db.execute('SELECT * FROM camera_unit WHERE id=?',(camera_id,)).fetchone()
    if not cam:raise ValueError('unknown_camera')
    boards={r['id']:dict(r) for r in db.execute('SELECT * FROM target_board ORDER BY id')}
    points=[dict(r) for r in db.execute(
        'SELECT * FROM image_point WHERE camera_id=? ORDER BY board_id,corner_no,captured_at',(camera_id,))]
    poses=[dict(r) for r in db.execute(
        'SELECT * FROM road_pose WHERE camera_id=? ORDER BY observed_at,id',(camera_id,))]
    groups={}
    for p in points:
        b=boards.get(p['board_id'])
        if not b:raise ValueError('unknown_board')
        key=(p['board_id'],p['captured_at'])
        groups.setdefault(key,[]).append(p)
    view_groups=[]
    for key in sorted(groups):
        board_id,captured_at=key
        b=boards[board_id]
        world=json.loads(b['points_json'])
        pts=sorted(groups[key],key=lambda r:r['corner_no'])
        if len(pts)!=len(world):raise ValueError('board_point_count_mismatch')
        view_groups.append({
            'board_id':board_id,'captured_at':captured_at,
            'ground_plane':bool(b['ground_plane']),'verification':bool(b['verification']),
            'pitch_mrad':float(dict(b).get('pitch_mrad') or 0.0),
            'world_mm':[[float(x),float(y)] for x,y in world],
            'pixels':[(float(r['u_px']),float(r['v_px'])) for r in pts],
        })
    return {
        'camera':dict(cam),
        'boards':boards,
        'view_groups':view_groups,
        'road_poses':poses,
        'mount':db.execute('SELECT * FROM camera_mount WHERE camera_id=?',(camera_id,)).fetchone(),
    }
