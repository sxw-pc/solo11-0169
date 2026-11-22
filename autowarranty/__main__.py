import argparse,json,tempfile
from pathlib import Path
from . import diagnosis,repair,warranty,title,title_transfer
from .report import report,audit
COMMANDS={'vehicle-import':diagnosis.vehicle,'diagnosis-import':diagnosis.diagnose,'authorization-import':diagnosis.authorize,
          'part-import':repair.part,'repair-import':repair.execute,'inspection-import':repair.inspect,
          'warranty-import':warranty.grant,'return-import':warranty.return_case,
          'title-import':title.record,'lien-import':title.lien,'frame-scan-import':title.scan,
          'factory-archive-import':title_transfer.archive,'registry-doc-import':title_transfer.registry}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('warranty-decide');d.add_argument('--db',required=True);d.add_argument('--case',required=True);d.add_argument('--at',required=True)
    r=s.add_parser('part-recall');r.add_argument('--db',required=True);r.add_argument('--part',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('title-records');x.add_argument('--db',required=True)
    g=s.add_parser('title-graph');g.add_argument('--db',required=True);g.add_argument('--vin',required=True)
    q=s.add_parser('title-prepare');q.add_argument('--db',required=True);q.add_argument('--vin',required=True);q.add_argument('--route',required=True,choices=title_transfer.ROUTES);q.add_argument('--at',required=True)
    t=s.add_parser('title-statement');t.add_argument('--db',required=True);t.add_argument('--vin',required=True)
    g2=s.add_parser('delivery-sign');g2.add_argument('--db',required=True);g2.add_argument('--listing',required=True);g2.add_argument('--buyer',required=True);g2.add_argument('--price-cents',required=True,type=int);g2.add_argument('--at',required=True);g2.add_argument('--assumption-doc')
    v=s.add_parser('delivery-recover');v.add_argument('--db',required=True);v.add_argument('--receipt',required=True)
    x=s.add_parser('demo');x.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='warranty-decide':out=warranty.decide(a.db,a.case,a.at)
    elif a.cmd=='part-recall':out=warranty.recall_part(a.db,a.part)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='title-records':out=title.records(a.db)
    elif a.cmd=='title-graph':out=title_transfer.graph(a.db,a.vin)
    elif a.cmd=='title-prepare':out=title_transfer.prepare(a.db,a.vin,a.route,a.at)
    elif a.cmd=='title-statement':out=title_transfer.statement(a.db,a.vin)
    elif a.cmd=='delivery-sign':out=title_transfer.sign(a.db,a.listing,a.buyer,a.price_cents,a.at,a.assumption_doc)
    elif a.cmd=='delivery-recover':out=title_transfer.recover(a.db,a.receipt)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('vehicle-import','vehicle-ok.json'),('diagnosis-import','diagnosis-ok.json'),('authorization-import','authorization-ok.json'),('part-import','part-ok.json'),('repair-import','repair-ok.json'),('inspection-import','inspection-ok.json'),('warranty-import','warranty-ok.json'),('return-import','return-ok.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'liability':warranty.decide(db,'RET1','2026-06-20T00:00:00Z'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
