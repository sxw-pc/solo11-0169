from pathlib import Path
from tempfile import TemporaryDirectory
from .store import connect,snapshot

def report(path):
    with connect(path) as db:return {'state':snapshot(db),'sources':[{'kind':x['kind'],'sha':x['sha']} for x in db.execute('SELECT kind,sha FROM source ORDER BY id')]}

def audit(path):
    from . import diagnosis,repair,warranty,ota,trial,ota_trial
    funcs={'vehicle':diagnosis.vehicle,'diagnosis':diagnosis.diagnose,'authorization':diagnosis.authorize,
           'part':repair.part,'repair':repair.execute,'inspection':repair.inspect,
           'warranty':warranty.grant,'return_case':warranty.return_case,
           'ecu':ota.ecu,'software_package':ota.package,
           'install_receipt':ota.receipt,'feature_entitlement':ota.entitlement,
           'trial_assignment':trial.assignment,'drive_session':trial.session,'safety_alert':trial.alert,
           'trial_wave':ota_trial.wave,'release_criterion':ota_trial.criterion,
           'version_attestation':ota_trial.attestation,'hazard_map':ota_trial.hazard}
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
            if kind=='trial_decision':
                import json
                doc=json.loads(raw);ota_trial.evaluate(db,doc['criterion_id'],doc['at'])
                continue
            file=Path(tmp)/f'{i}.json';file.write_bytes(raw);funcs[kind](db,file)
        rebuilt=report(db)
    return {'replay_ok':rebuilt==original,'events':len(events)}
