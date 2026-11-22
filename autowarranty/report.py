from pathlib import Path
from tempfile import TemporaryDirectory
from .store import connect,snapshot

def report(path):
    with connect(path) as db:return {'state':snapshot(db),'sources':[{'kind':x['kind'],'sha':x['sha']} for x in db.execute('SELECT kind,sha FROM source ORDER BY id')]}

def audit(path):
    from . import diagnosis,repair,warranty,adas,adas_calibration
    funcs={'vehicle':diagnosis.vehicle,'diagnosis':diagnosis.diagnose,'authorization':diagnosis.authorize,
           'part':repair.part,'repair':repair.execute,'inspection':repair.inspect,
           'warranty':warranty.grant,'return_case':warranty.return_case,
           'camera_unit':adas.camera,'target_board':adas.board,
           'image_point':adas.point,'road_pose':adas.pose}
    original=report(path)
    with connect(path) as db:events=[(x['kind'],x['raw']) for x in db.execute('SELECT kind,raw FROM source ORDER BY id')]
    with TemporaryDirectory() as tmp:
        db=Path(tmp)/'replay.sqlite'
        for i,(kind,raw) in enumerate(events):
            if kind=='recall':
                import json
                warranty.recall_part(db,json.loads(raw)['part_id'])
                continue
            if kind=='liability':
                import json
                doc=json.loads(raw);warranty.decide(db,doc['case_id'],doc['at'])
                continue
            if kind=='calibration':
                import json
                doc=json.loads(raw);adas_calibration.calibrate(db,doc['camera_id'],doc['at'],cal_id=doc['id'])
                continue
            if kind=='activation':
                import json
                doc=json.loads(raw)
                adas_calibration.release(db,doc['camera_id'],doc['profile'],doc['at'],
                                         activation_id=doc['id'],calibration_id=doc['calibration_id'])
                continue
            if kind=='withdrawal':
                import json
                doc=json.loads(raw);adas_calibration.withdraw(db,doc['calibration_id'],doc['reason'],doc['at'])
                continue
            if kind=='mount_change':
                import json
                doc=json.loads(raw)
                adas_calibration.register_mount_change(db,doc['camera_id'],doc['serial'],doc['lens_model'],doc['at'])
                continue
            if kind=='withdrawal_execution':
                import json
                doc=json.loads(raw);adas_calibration.execute_withdrawal(db,doc['task_id'],doc['at'])
                continue
            file=Path(tmp)/f'{i}.json';file.write_bytes(raw);funcs[kind](db,file)
        rebuilt=report(db)
    return {'replay_ok':rebuilt==original,'events':len(events)}
