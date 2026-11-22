from pathlib import Path
from tempfile import TemporaryDirectory
from .store import connect,snapshot

def report(path):
    with connect(path) as db:return {'state':snapshot(db),'sources':[{'kind':x['kind'],'sha':x['sha']} for x in db.execute('SELECT kind,sha FROM source ORDER BY id')]}

def audit(path):
    from . import diagnosis,repair,warranty,battery,battery_grade
    funcs={'vehicle':diagnosis.vehicle,'diagnosis':diagnosis.diagnose,'authorization':diagnosis.authorize,
           'part':repair.part,'repair':repair.execute,'inspection':repair.inspect,
           'warranty':warranty.grant,'return_case':warranty.return_case,
           'battery_pack':battery.pack,'charge_test':battery.charge,'resistance_test':battery.resistance,
           'pack_composition':battery_grade.composition,'use_agreement':battery_grade.agreement,
           'window_evidence':battery_grade.window_evidence,'full_cycle_test':battery_grade.full_cycle,
           'use_certificate':battery_grade.certify,'use_order':battery_grade.order,'shipment':battery_grade.ship}
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
            if kind=='grade_decision':
                import json
                doc=json.loads(raw);battery_grade.decide(db,doc['pack_id'],doc['at'])
                continue
            file=Path(tmp)/f'{i}.json';file.write_bytes(raw);funcs[kind](db,file)
        rebuilt=report(db)
    return {'replay_ok':rebuilt==original,'events':len(events)}
