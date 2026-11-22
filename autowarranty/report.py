from pathlib import Path
from tempfile import TemporaryDirectory
from .store import connect,snapshot

def report(path):
    with connect(path) as db:return {'state':snapshot(db),'sources':[{'kind':x['kind'],'sha':x['sha']} for x in db.execute('SELECT kind,sha FROM source ORDER BY id')]}

def audit(path):
    from . import diagnosis,repair,warranty,loaner
    from . import loaner_tolls
    funcs={'vehicle':diagnosis.vehicle,'diagnosis':diagnosis.diagnose,'authorization':diagnosis.authorize,
           'part':repair.part,'repair':repair.execute,'inspection':repair.inspect,
           'warranty':warranty.grant,'return_case':warranty.return_case,
           'loaner':loaner.asset,'loan_agreement':loaner.agreement,
           'trip_fix':loaner.fix,'toll_receipt':loaner.toll,
           'fix_batch':loaner_tolls.fix_batch,'road_network':loaner_tolls.road_network,
           'tag_handover':loaner_tolls.tag_handover,
           'gate_signature':loaner_tolls.gate_signature,'ocr_read':loaner_tolls.ocr_read,
           'plate_appeal':loaner_tolls.plate_appeal,'loan_return':loaner_tolls.loan_return,
           'customer_payment':loaner_tolls.customer_payment,
           'toll_payment':loaner_tolls.toll_payment}
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
            if kind=='toll_settlement':
                import json
                loaner_tolls.settle(db,json.loads(raw)['at'])
                continue
            file=Path(tmp)/f'{i}.json';file.write_bytes(raw);funcs[kind](db,file)
        rebuilt=report(db)
    return {'replay_ok':rebuilt==original,'events':len(events)}
