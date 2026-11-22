from pathlib import Path
from tempfile import TemporaryDirectory
from .store import connect,snapshot

def report(path):
    with connect(path) as db:return {'state':snapshot(db),'sources':[{'kind':x['kind'],'sha':x['sha']} for x in db.execute('SELECT kind,sha FROM source ORDER BY id')]}

def audit(path):
    from . import diagnosis,repair,warranty,title,title_transfer
    funcs={'vehicle':diagnosis.vehicle,'diagnosis':diagnosis.diagnose,'authorization':diagnosis.authorize,
           'part':repair.part,'repair':repair.execute,'inspection':repair.inspect,
           'warranty':warranty.grant,'return_case':warranty.return_case,
           'title_record':title.record,'lien_record':title.lien,'frame_scan':title.scan,
           'factory_archive':title_transfer.archive,'registry_doc':title_transfer.registry}
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
            if kind=='listing':
                import json
                doc=json.loads(raw);title_transfer.prepare(db,doc['vin'],doc['route'],doc['at'])
                continue
            if kind=='delivery_sign':
                import json
                doc=json.loads(raw);title_transfer.sign(db,doc['listing_id'],doc['buyer'],doc['price_cents'],doc['at'],doc.get('assumption_doc'))
                continue
            if kind=='delivery_close':
                import json
                title_transfer.recover(db,json.loads(raw)['receipt_no'])
                continue
            file=Path(tmp)/f'{i}.json';file.write_bytes(raw);funcs[kind](db,file)
        rebuilt=report(db)
    return {'replay_ok':rebuilt==original,'events':len(events)}
