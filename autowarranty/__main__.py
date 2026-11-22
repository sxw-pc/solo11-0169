import argparse, json, tempfile
from pathlib import Path
from . import diagnosis, repair, warranty, loaner, loaner_tolls
from .report import report, audit
COMMANDS={'vehicle-import':diagnosis.vehicle,'diagnosis-import':diagnosis.diagnose,'authorization-import':diagnosis.authorize,
          'part-import':repair.part,'repair-import':repair.execute,'inspection-import':repair.inspect,
          'warranty-import':warranty.grant,'return-import':warranty.return_case,
          'loaner-import':loaner.asset,'loan-agreement-import':loaner.agreement,
          'trip-fix-import':loaner.fix,'toll-receipt-import':loaner.toll,
          'toll-fix-batch-import':loaner_tolls.fix_batch,
          'road-network-import':loaner_tolls.road_network,
          'tag-handover-import':loaner_tolls.tag_handover,
          'gate-signature-import':loaner_tolls.gate_signature,
          'ocr-read-import':loaner_tolls.ocr_read,
          'plate-appeal-import':loaner_tolls.plate_appeal,
          'loan-return-import':loaner_tolls.loan_return,
          'customer-payment-import':loaner_tolls.customer_payment,
          'toll-payment-import':loaner_tolls.toll_payment}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('warranty-decide');d.add_argument('--db',required=True);d.add_argument('--case',required=True);d.add_argument('--at',required=True)
    r=s.add_parser('part-recall');r.add_argument('--db',required=True);r.add_argument('--part',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('loaner-trips');x.add_argument('--db',required=True)
    x=s.add_parser('toll-reconcile');x.add_argument('--db',required=True);x.add_argument('--at')
    x=s.add_parser('toll-settle');x.add_argument('--db',required=True);x.add_argument('--at',required=True)
    x=s.add_parser('toll-ledger');x.add_argument('--db',required=True)
    x=s.add_parser('toll-explain');x.add_argument('--db',required=True);x.add_argument('--subject')
    x=s.add_parser('demo');x.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='warranty-decide':out=warranty.decide(a.db,a.case,a.at)
    elif a.cmd=='part-recall':out=warranty.recall_part(a.db,a.part)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='loaner-trips':out=loaner.trips(a.db)
    elif a.cmd=='toll-reconcile':out=loaner_tolls.reconcile(a.db,a.at)
    elif a.cmd=='toll-settle':out=loaner_tolls.settle(a.db,a.at)
    elif a.cmd=='toll-ledger':out=loaner_tolls.ledger(a.db)
    elif a.cmd=='toll-explain':out=loaner_tolls.explain(a.db,a.subject)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('vehicle-import','vehicle-ok.json'),('diagnosis-import','diagnosis-ok.json'),('authorization-import','authorization-ok.json'),('part-import','part-ok.json'),('repair-import','repair-ok.json'),('inspection-import','inspection-ok.json'),('warranty-import','warranty-ok.json'),('return-import','return-ok.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'liability':warranty.decide(db,'RET1','2026-06-20T00:00:00Z'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
