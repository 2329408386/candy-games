"""One-off: merge the two pre-merge site databases into the single candy-games database.

Usage: python -m tools.merge_databases --chinese OLD.sqlite3 --math OLD.sqlite3 --out NEW.sqlite3
Sources are opened read-only inside one read transaction (a consistent snapshot); the output must not exist.
Every table is verified row-for-row after copying.
"""
import argparse, sqlite3, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from core import schema

# table -> columns copied verbatim (site is added); audit ids are re-assigned but keep their per-site order.
TABLES={
    'licenses':['id','key_hash','games','enabled','buyer','assigned_at','created_at','device_limit','batch_id','grade_scopes','archived_at'],
    'license_keys':['license_id','code'],
    'devices':['license_id','token_hash','label','created_at','last_seen'],
    'generation_batches':['id','games','quantity','device_limit','created_at','grade_scopes'],
    'game_groups':['id','grade','name','position'],
    'game_memberships':['game','group_id'],
    'license_groups':['license_id','group_id'],
    'batch_groups':['batch_id','group_id'],
    'audit':['created_at','action','license_id','details'],
}

def copy_site(src,out,site):
    for table,cols in TABLES.items():
        order='id' if table=='audit' else ','.join(cols)
        rows=src.execute(f'SELECT {",".join(cols)} FROM {table} ORDER BY {order}').fetchall()
        out.executemany(f'INSERT INTO {table}(site,{",".join(cols)}) VALUES(?{",?"*len(cols)})',[(site,*r) for r in rows])
    for r in src.execute('SELECT password,must_change,version FROM admin WHERE id=1'):
        out.execute('INSERT INTO admin(site,password,must_change,version) VALUES(?,?,?,?)',(site,*r))
    out.executemany('INSERT INTO limits VALUES(?,?,?)',[(site+':'+b,c,u) for b,c,u in src.execute('SELECT bucket,count,until FROM limits')])

def verify(src,out,site):
    report={}
    for table,cols in TABLES.items():
        order='id' if table=='audit' else ','.join(cols)
        old=src.execute(f'SELECT {",".join(cols)} FROM {table} ORDER BY {order}').fetchall()
        new=out.execute(f'SELECT {",".join(cols)} FROM {table} WHERE site=? ORDER BY {order}',(site,)).fetchall()
        if [tuple(r) for r in old]!=[tuple(r) for r in new]:raise SystemExit(f'校验失败：{site}.{table} 内容不一致')
        report[table]=len(new)
    old=src.execute('SELECT password,must_change,version FROM admin WHERE id=1').fetchall()
    new=out.execute('SELECT password,must_change,version FROM admin WHERE site=?',(site,)).fetchall()
    if [tuple(r) for r in old]!=[tuple(r) for r in new]:raise SystemExit(f'校验失败：{site}.admin 内容不一致')
    report['admin']=len(new)
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--chinese',required=True);p.add_argument('--math',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    out_path=Path(a.out)
    if out_path.exists():raise SystemExit('输出文件已存在，拒绝覆盖：'+str(out_path))
    schema.initialize(out_path)
    sources={site:sqlite3.connect(f'file:{path}?mode=ro',uri=True) for site,path in (('chinese',a.chinese),('math',a.math))}
    out=sqlite3.connect(out_path);out.execute('PRAGMA foreign_keys=ON')
    try:
        for src in sources.values():src.execute('BEGIN')  # snapshot
        with out:
            for site,src in sources.items():copy_site(src,out,site)
        for site,src in sources.items():print(site,verify(src,out,site))
        if out.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('integrity_check 失败')
        if out.execute('PRAGMA foreign_key_check').fetchall():raise SystemExit('foreign_key_check 失败')
    except BaseException:
        out.close();out_path.unlink(missing_ok=True);raise
    out.close();print('合并完成：'+str(out_path))

if __name__=='__main__':main()
