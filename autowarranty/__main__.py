import argparse,json,tempfile
from pathlib import Path
from . import diagnosis,repair,warranty,adas,adas_calibration
from .report import report,audit
COMMANDS={'vehicle-import':diagnosis.vehicle,'diagnosis-import':diagnosis.diagnose,'authorization-import':diagnosis.authorize,
          'part-import':repair.part,'repair-import':repair.execute,'inspection-import':repair.inspect,
          'warranty-import':warranty.grant,'return-import':warranty.return_case,
          'camera-import':adas.camera,'target-board-import':adas.board,
          'image-point-import':adas.point,'road-pose-import':adas.pose}
def main():
    p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
    for name in COMMANDS:
        x=s.add_parser(name);x.add_argument('file');x.add_argument('--db',required=True)
    d=s.add_parser('warranty-decide');d.add_argument('--db',required=True);d.add_argument('--case',required=True);d.add_argument('--at',required=True)
    r=s.add_parser('part-recall');r.add_argument('--db',required=True);r.add_argument('--part',required=True)
    for name in ('report','audit'):
        x=s.add_parser(name);x.add_argument('--db',required=True)
    x=s.add_parser('adas-observations');x.add_argument('--db',required=True)
    c=s.add_parser('adas-calibrate');c.add_argument('--db',required=True);c.add_argument('--camera',required=True);c.add_argument('--at',required=True);c.add_argument('--id')
    rl=s.add_parser('adas-release');rl.add_argument('--db',required=True);rl.add_argument('--camera',required=True)
    rl.add_argument('--profile',required=True,choices=('parking_assist','lane_keeping'));rl.add_argument('--at',required=True);rl.add_argument('--id');rl.add_argument('--calibration')
    w=s.add_parser('adas-withdraw');w.add_argument('--db',required=True);w.add_argument('--calibration',required=True);w.add_argument('--reason',required=True);w.add_argument('--at',required=True)
    m=s.add_parser('adas-mount-change');m.add_argument('--db',required=True);m.add_argument('--camera',required=True);m.add_argument('--serial',required=True);m.add_argument('--lens',required=True);m.add_argument('--at',required=True)
    we=s.add_parser('adas-withdrawal-execute');we.add_argument('--db',required=True);we.add_argument('--task',required=True);we.add_argument('--at',required=True)
    for name in ('adas-calibrations','adas-activations','adas-withdrawal-tasks'):
        x=s.add_parser(name);x.add_argument('--db',required=True);x.add_argument('--camera')
    ms=s.add_parser('adas-mount-state');ms.add_argument('--db',required=True);ms.add_argument('--camera',required=True)
    x=s.add_parser('demo');x.add_argument('--db')
    a=p.parse_args()
    if a.cmd in COMMANDS:out=COMMANDS[a.cmd](a.db,a.file)
    elif a.cmd=='warranty-decide':out=warranty.decide(a.db,a.case,a.at)
    elif a.cmd=='part-recall':out=warranty.recall_part(a.db,a.part)
    elif a.cmd=='report':out=report(a.db)
    elif a.cmd=='audit':out=audit(a.db)
    elif a.cmd=='adas-observations':out=adas.observations(a.db)
    elif a.cmd=='adas-calibrate':out=adas_calibration.calibrate(a.db,a.camera,a.at,cal_id=a.id)
    elif a.cmd=='adas-release':out=adas_calibration.release(a.db,a.camera,a.profile,a.at,activation_id=a.id,calibration_id=a.calibration)
    elif a.cmd=='adas-withdraw':out=adas_calibration.withdraw(a.db,a.calibration,a.reason,a.at)
    elif a.cmd=='adas-mount-change':out=adas_calibration.register_mount_change(a.db,a.camera,a.serial,a.lens,a.at)
    elif a.cmd=='adas-withdrawal-execute':out=adas_calibration.execute_withdrawal(a.db,a.task,a.at)
    elif a.cmd=='adas-calibrations':out=adas_calibration.calibrations(a.db,a.camera)
    elif a.cmd=='adas-activations':out=adas_calibration.activations(a.db,a.camera)
    elif a.cmd=='adas-withdrawal-tasks':out=adas_calibration.withdrawal_tasks(a.db,a.camera)
    elif a.cmd=='adas-mount-state':out=adas_calibration.mount_state(a.db,a.camera)
    else:
        db=a.db or str(Path(tempfile.mkdtemp())/'demo.sqlite');f=Path(__file__).resolve().parent.parent/'fixtures'
        for cmd,name in [('vehicle-import','vehicle-ok.json'),('diagnosis-import','diagnosis-ok.json'),('authorization-import','authorization-ok.json'),('part-import','part-ok.json'),('repair-import','repair-ok.json'),('inspection-import','inspection-ok.json'),('warranty-import','warranty-ok.json'),('return-import','return-ok.json')]:COMMANDS[cmd](db,f/name)
        out={'db':db,'liability':warranty.decide(db,'RET1','2026-06-20T00:00:00Z'),'audit':audit(db)}
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
