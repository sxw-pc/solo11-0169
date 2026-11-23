import argparse,json,tempfile
from pathlib import Path
from . import diagnosis,repair,warranty,paint,paint_match
from .report import report,audit
COMMANDS={'vehicle-import':diagnosis.vehicle,'diagnosis-import':diagnosis.diagnose,'authorization-import':diagnosis.authorize,
          'part-import':repair.part,'repair-import':repair.execute,'inspection-import':repair.inspect,
          'warranty-import':warranty.grant,'return-import':warranty.return_case,
          'paint-sample-import':paint.sample,'pigment-import':paint.pigment,'spray-panel-import':paint.panel,
          'paint-order-import':paint_match.order,'band-measure-import':paint_match.measure,
          'paint-plan':paint_match.plan,'paint-draw':paint_match.draw,
          'paint-spray-import':paint_match.spray,'paint-rework-import':paint_match.rework}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('warranty-decide');d.add_argument('--db',required=True);d.add_argument('--case',required=True);d.add_argument('--at',required=True)
    r=s.add_parser('part-recall');r.add_argument('--db',required=True);r.add_argument('--part',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('paint-captures');x.add_argument('--db',required=True)
    x=s.add_parser('paint-stock');x.add_argument('--db',required=True)
    for name in ('paint-order-status','paint-candidates'):
        x=s.add_parser(name);x.add_argument('--db',required=True);x.add_argument('--order',required=True)
    x=s.add_parser('demo');x.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='warranty-decide':out=warranty.decide(a.db,a.case,a.at)
    elif a.cmd=='part-recall':out=warranty.recall_part(a.db,a.part)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='paint-captures':out=paint.captures(a.db)
    elif a.cmd=='paint-stock':out=paint_match.stock(a.db)
    elif a.cmd=='paint-order-status':out=paint_match.order_status(a.db,a.order)
    elif a.cmd=='paint-candidates':out=paint_match.candidates(a.db,a.order)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('vehicle-import','vehicle-ok.json'),('diagnosis-import','diagnosis-ok.json'),('authorization-import','authorization-ok.json'),('part-import','part-ok.json'),('repair-import','repair-ok.json'),('inspection-import','inspection-ok.json'),('warranty-import','warranty-ok.json'),('return-import','return-ok.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'liability':warranty.decide(db,'RET1','2026-06-20T00:00:00Z'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
