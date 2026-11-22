import argparse,json,tempfile
from pathlib import Path
from . import diagnosis,repair,warranty,ota,trial,ota_trial
from .report import report,audit
COMMANDS={'vehicle-import':diagnosis.vehicle,'diagnosis-import':diagnosis.diagnose,'authorization-import':diagnosis.authorize,
          'part-import':repair.part,'repair-import':repair.execute,'inspection-import':repair.inspect,
          'warranty-import':warranty.grant,'return-import':warranty.return_case,
          'ecu-import':ota.ecu,'software-package-import':ota.package,
          'install-receipt-import':ota.receipt,'feature-entitlement-import':ota.entitlement,
          'trial-assignment-import':trial.assignment,'drive-session-import':trial.session,'safety-alert-import':trial.alert,
          'trial-wave-import':ota_trial.wave,'release-criterion-import':ota_trial.criterion,
          'version-attestation-import':ota_trial.attestation,'hazard-map-import':ota_trial.hazard}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('warranty-decide');d.add_argument('--db',required=True);d.add_argument('--case',required=True);d.add_argument('--at',required=True)
    r=s.add_parser('part-recall');r.add_argument('--db',required=True);r.add_argument('--part',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('ota-captures');x.add_argument('--db',required=True)
    x=s.add_parser('trial-captures');x.add_argument('--db',required=True)
    e=s.add_parser('ota-trial-evaluate');e.add_argument('--db',required=True);e.add_argument('--criterion',required=True);e.add_argument('--at',required=True)
    x=s.add_parser('ota-trial-exposure');x.add_argument('--db',required=True)
    x=s.add_parser('ota-trial-captures');x.add_argument('--db',required=True)
    x=s.add_parser('demo');x.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='warranty-decide':out=warranty.decide(a.db,a.case,a.at)
    elif a.cmd=='part-recall':out=warranty.recall_part(a.db,a.part)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='ota-captures':out=ota.captures(a.db)
    elif a.cmd=='trial-captures':out=trial.captures(a.db)
    elif a.cmd=='ota-trial-evaluate':out=ota_trial.evaluate(a.db,a.criterion,a.at)
    elif a.cmd=='ota-trial-exposure':out=ota_trial.exposure(a.db)
    elif a.cmd=='ota-trial-captures':out=ota_trial.captures(a.db)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('vehicle-import','vehicle-ok.json'),('diagnosis-import','diagnosis-ok.json'),('authorization-import','authorization-ok.json'),('part-import','part-ok.json'),('repair-import','repair-ok.json'),('inspection-import','inspection-ok.json'),('warranty-import','warranty-ok.json'),('return-import','return-ok.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'liability':warranty.decide(db,'RET1','2026-06-20T00:00:00Z'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
