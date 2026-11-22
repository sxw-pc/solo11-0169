"""代步车租约、轨迹与收费回执的采录入口。

`loaner_tolls` 负责同一批证据的逐次通行核销；这里只保留现有采录与并排查询接口，
避免把维修/保修账与代步车通行账混在一起。
"""
from . import loaner_tolls
from .store import connect


def asset(path, file):
    from .store import source, repeated
    with connect(path) as db, db:
        sid, d, _ = source(db, 'loaner', file)
        if repeated(db, 'loaner', d['id'], sid):
            return {'id': d['id'], 'replayed': True}
        if not d.get('id') or not d.get('plate'):
            raise ValueError('loaner_identity_missing')
        db.execute('INSERT INTO loaner VALUES(?,?,?,?)',
                   (d['id'], d['plate'], d['model'], sid))
        return {'id': d['id'], 'replayed': False}


def agreement(path, file):
    return loaner_tolls.agreement(path, file)


def fix(path, file):
    return loaner_tolls.fix(path, file)


def toll(path, file):
    return loaner_tolls.toll(path, file)


def trips(path):
    with connect(path) as db:
        fixes = [dict(x) for x in db.execute(
            'SELECT id,agreement_id,observed_at,x_m,y_m,accuracy_m FROM trip_fix')]
        fixes += [dict(x) for x in db.execute(
            'SELECT id,agreement_id,observed_at,x_m,y_m,accuracy_m FROM fix_batch_item')]
        fixes.sort(key=lambda x: (x['observed_at'], x['id']))
        return {'fixes': fixes,
                'tolls': [dict(x) for x in db.execute(
                    'SELECT * FROM toll_receipt ORDER BY id')]}
