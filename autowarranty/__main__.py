import argparse,json,tempfile
from pathlib import Path
from . import diagnosis,repair,warranty,battery,battery_grade
from .report import report,audit
COMMANDS={'vehicle-import':diagnosis.vehicle,'diagnosis-import':diagnosis.diagnose,'authorization-import':diagnosis.authorize,
          'part-import':repair.part,'repair-import':repair.execute,'inspection-import':repair.inspect,
          'warranty-import':warranty.grant,'return-import':warranty.return_case,
          'battery-pack-import':battery.pack,'charge-test-import':battery.charge,'resistance-test-import':battery.resistance,
          'battery-composition-import':battery_grade.composition,'use-agreement-import':battery_grade.agreement,
          'window-evidence-import':battery_grade.window_evidence,'full-cycle-import':battery_grade.full_cycle,
          'use-order-import':battery_grade.order,'certificate-import':battery_grade.certify,'shipment-import':battery_grade.ship}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('warranty-decide');d.add_argument('--db',required=True);d.add_argument('--case',required=True);d.add_argument('--at',required=True)
    r=s.add_parser('part-recall');r.add_argument('--db',required=True);r.add_argument('--part',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('battery-tests');x.add_argument('--db',required=True)
    g=s.add_parser('battery-grade');g.add_argument('--db',required=True);g.add_argument('--pack',required=True)
    gd=s.add_parser('battery-grade-decide');gd.add_argument('--db',required=True);gd.add_argument('--pack',required=True);gd.add_argument('--at',required=True)
    c=s.add_parser('battery-certificates');c.add_argument('--db',required=True);c.add_argument('--pack')
    x=s.add_parser('demo');x.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='warranty-decide':out=warranty.decide(a.db,a.case,a.at)
    elif a.cmd=='part-recall':out=warranty.recall_part(a.db,a.part)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='battery-tests':out=battery.tests(a.db)
    elif a.cmd=='battery-grade':out=battery_grade.evaluate(a.db,a.pack)
    elif a.cmd=='battery-grade-decide':out=battery_grade.decide(a.db,a.pack,a.at)
    elif a.cmd=='battery-certificates':out=battery_grade.certificates(a.db,a.pack)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('vehicle-import','vehicle-ok.json'),('diagnosis-import','diagnosis-ok.json'),('authorization-import','authorization-ok.json'),('part-import','part-ok.json'),('repair-import','repair-ok.json'),('inspection-import','inspection-ok.json'),('warranty-import','warranty-ok.json'),('return-import','return-ok.json')]:COMMANDS[cmd](db,f/name)
        for cmd,name in [('battery-pack-import','battery-pack-ok.json'),('charge-test-import','charge-test-ok.json'),('resistance-test-import','resistance-test-ok.json'),
                         ('battery-composition-import','battery-composition-ok.json'),('use-agreement-import','use-agreement-window.json'),('use-agreement-import','use-agreement-mobile.json'),
                         ('window-evidence-import','window-evidence-ok.json'),('use-order-import','use-order-window.json'),('use-order-import','use-order-mobile.json'),
                         ('certificate-import','certificate-window.json'),('shipment-import','shipment-window.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'liability':warranty.decide(db,'RET1','2026-06-20T00:00:00Z'),'battery_grade':battery_grade.evaluate(db,'BAT1'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
