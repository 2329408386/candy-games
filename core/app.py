import csv, functools, hashlib, io, json, os, secrets, sqlite3, time
from pathlib import Path
from flask import Flask, request, session, jsonify, render_template, redirect, url_for, abort, Response, g, flash, current_app, send_from_directory
from jinja2 import ChoiceLoader, FileSystemLoader
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
from itsdangerous import URLSafeSerializer, BadSignature
from . import schema

ROOT=Path(__file__).resolve().parent.parent
SHARED_STATIC=ROOT/'shared-static'
GRADE_NAMES=['一','二','三','四','五','六']
GRADES={f'g{year}-t{term}':f'{GRADE_NAMES[year-1]}年级'+('上册' if term==1 else '下册') for year in range(1,7) for term in (1,2)}
KEY_ALPHABET='ABCDEFGHJKLMNPQRSTUVWXYZ23456789'

def data_dir():return Path(os.environ.get('GAME_DATA','/var/lib/candy-games'))
def site():return current_app.site
def digest(s):return hashlib.sha256(s.encode()).hexdigest()

# ---- database ----
def db():
    if 'db' not in g:
        g.db=sqlite3.connect(current_app.config['DATABASE'],timeout=20); g.db.row_factory=sqlite3.Row; g.db.execute('PRAGMA foreign_keys=ON')
    return g.db
def close_db(error):
    if 'db' in g:g.db.close()
def audit(action,lid=None,details=''):
    db().execute('INSERT INTO audit(site,created_at,action,license_id,details) VALUES(?,?,?,?,?)',(site().key,int(time.time()),action,lid,details))
def device_count(lid):
    return db().execute('SELECT count(*) FROM devices WHERE site=? AND license_id=?',(site().key,lid)).fetchone()[0]

# ---- game groups (cached per request; group_admin drops the cache after writing) ----
def group_map():
    if 'group_map' not in g:
        g.group_map={r['id']:dict(r) for r in db().execute('SELECT id,grade,name,position FROM game_groups WHERE site=? ORDER BY grade,position,id',(site().key,))}
    return g.group_map
def groups_for_grade(grade):return [v for v in group_map().values() if v['grade']==grade]
def ordered_games(found):return [game for game in site().games if game in found]
def group_games(gid):
    return ordered_games({r[0] for r in db().execute('SELECT game FROM game_memberships WHERE site=? AND group_id=?',(site().key,gid))})
def game_group_id(game):
    row=db().execute('SELECT group_id FROM game_memberships WHERE site=? AND game=?',(site().key,game)).fetchone();return row[0] if row else None
def _scoped_groups(table,column,value):
    return [r[0] for r in db().execute(f'SELECT gg.id FROM {table} t JOIN game_groups gg ON gg.site=t.site AND gg.id=t.group_id WHERE t.site=? AND t.{column}=? ORDER BY gg.grade,gg.position,gg.id',(site().key,value))]
def license_group_ids(lid):return _scoped_groups('license_groups','license_id',lid)
def batch_group_ids(bid):return _scoped_groups('batch_groups','batch_id',bid)
def all_license_group_ids():
    result={}
    for r in db().execute('SELECT lg.license_id,gg.id FROM license_groups lg JOIN game_groups gg ON gg.site=lg.site AND gg.id=lg.group_id WHERE lg.site=? ORDER BY gg.grade,gg.position,gg.id',(site().key,)):
        result.setdefault(r[0],[]).append(r[1])
    return result
def group_labels(ids):
    catalog=group_map();return [GRADES[catalog[gid]['grade']]+'／'+catalog[gid]['name'] for gid in ids if gid in catalog]
def games_for_grades(scopes):return [game for game in site().games if site().game_grades[game] in scopes]
def allowed_games(row):
    return ordered_games({r[0] for r in db().execute('SELECT m.game FROM license_groups lg JOIN game_memberships m ON m.site=lg.site AND m.group_id=lg.group_id WHERE lg.site=? AND lg.license_id=?',(site().key,row['id']))})
def group_scopes_payload(lid):
    catalog=group_map();return [{k:catalog[gid][k] for k in ('id','grade','name')} for gid in license_group_ids(lid)]

# ---- sessions and licenses ----
def csrf():
    if 'csrf' not in session:session['csrf']=secrets.token_urlsafe(32)
    if 'device' not in session:session['device']=secrets.token_urlsafe(32)
    session.permanent=True
    return session['csrf']
def rotate_admin_session():
    # Admin sign-in/out must not consume a new game device slot.
    saved={key:session[key] for key in ('device','license') if key in session}
    session.clear();session.update(saved);session.permanent=True
def current(game=None):
    key=(session.get('license'),session.get('device'))
    if g.get('current_key')!=key:
        row=None
        if all(key):
            row=db().execute('SELECT l.* FROM licenses l JOIN devices d ON d.site=l.site AND d.license_id=l.id WHERE l.site=? AND l.id=? AND d.token_hash=? AND l.enabled=1 AND l.archived_at IS NULL',(site().key,key[0],digest(key[1]))).fetchone()
            if row and not license_group_ids(row['id']):row=None
        g.current_key,g.current_row=key,row
    row=g.current_row
    if row and (not game or game in allowed_games(row)):return row
    return None
def limited(kind,maximum,window):
    d=db(); now=int(time.time()); bucket=site().key+':'+kind+':'+request.remote_addr
    d.execute('BEGIN IMMEDIATE'); d.execute('DELETE FROM limits WHERE until<?',(now,))
    d.execute('INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1',(bucket,now+window))
    count=d.execute('SELECT count FROM limits WHERE bucket=?',(bucket,)).fetchone()[0];d.commit()
    return count>maximum
def license_row(lid):
    return db().execute('SELECT * FROM licenses WHERE site=? AND id=?',(site().key,lid)).fetchone()
def license_codes(lids=None):
    sql='SELECT l.key_hash,k.code FROM licenses l JOIN license_keys k ON k.site=l.site AND k.license_id=l.id WHERE l.site=?';params=[site().key]
    if lids is not None:
        lids=list(lids)
        if not lids:return {}
        sql+=' AND l.id IN ('+','.join('?'*len(lids))+')';params+=lids
    return {r['key_hash']:r['code'] for r in db().execute(sql,params)}
def admin_row():return db().execute('SELECT * FROM admin WHERE site=?',(site().key,)).fetchone()
def admin_only(fn):
    @functools.wraps(fn)
    def wrapper(*a,**kw):
        row=admin_row()
        if not row or session.get('admin_version')!=row['version'] or session.get('admin_until',0)<time.time():return redirect(url_for('login'))
        if row['must_change'] and request.endpoint!='password':return redirect(url_for('password'))
        return fn(*a,**kw)
    return wrapper
def integer_field(name,minimum,maximum):
    value=request.form.get(name,'')
    if not value.isascii() or not value.isdigit() or not minimum<=int(value)<=maximum:raise ValueError(f'请输入{minimum}至{maximum}之间的整数')
    return int(value)
def filtered():
    where=['site=?','archived_at IS NOT NULL' if request.args.get('archived')=='1' else 'archived_at IS NULL'];params=[site().key]; q=request.args.get('q','').strip(); grade=request.args.get('grade','');state=request.args.get('state','');batch=request.args.get('batch','')
    if batch:where.append('batch_id=?');params.append(batch)
    if q:where.append('(buyer LIKE ? OR key_hash=? OR CAST(id AS TEXT)=?)');params.extend(['%'+q+'%',digest(q.upper().replace('-','').replace(' ','')),q])
    if grade:
        if grade not in GRADES:abort(400,'无效年级')
        where.append('EXISTS (SELECT 1 FROM json_each(licenses.grade_scopes) WHERE value=?)');params.append(grade)
    if request.args.get('group'):
        if request.args['group'] not in group_map():abort(400,'无效游戏组')
        where.append('EXISTS (SELECT 1 FROM license_groups lg WHERE lg.site=licenses.site AND lg.license_id=licenses.id AND lg.group_id=?)');params.append(request.args['group'])
    if state=='assigned':where.append('assigned_at IS NOT NULL')
    elif state=='unassigned':where.append('assigned_at IS NULL')
    elif state=='disabled':where.append('enabled=0')
    return ' WHERE '+' AND '.join(where),params
def csv_response(rows):
    keys=license_codes();groups=all_license_group_ids();out=io.StringIO();w=csv.writer(out);w.writerow(['编号','授权码','授权年级／学期／组','最多设备','状态','买家备注','历史游戏权限'])
    for r in rows:
        note=r['buyer'];note="'"+note if note.startswith(('=','+','-','@','\t','\r')) else note
        w.writerow([r['id'],keys[r['key_hash']],'、'.join(group_labels(groups.get(r['id'],[]))),r['device_limit'],'已归档（不可使用）' if r['archived_at'] is not None else ('启用' if r['enabled'] else '停用'),note,'、'.join(site().games[g] for g in json.loads(r['games'])) if r['archived_at'] is not None else ''])
    return '﻿'+out.getvalue()

# ---- static assets ----
_asset_versions={}
def asset(name):
    path=Path(current_app.static_folder)/name;mtime=path.stat().st_mtime
    cached=_asset_versions.get(path)
    if not cached or cached[0]!=mtime:cached=_asset_versions[path]=(mtime,hashlib.sha256(path.read_bytes()).hexdigest()[:8])
    return url_for('static',filename=name,v=cached[1])
_pages={}
def game_html(game):
    path=site().game_root/site().files[game];mtime=path.stat().st_mtime
    cached=_pages.get(path)
    if not cached or cached[0]!=mtime:cached=_pages[path]=(mtime,path.read_text())
    return cached[1]
GUARD='''<script>(function(){async function check(){const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);try{let r=await fetch('SESSION_URL',{cache:'no-store',signal:controller.signal});if(!r.ok)location.replace('HOME_URL');}catch(e){location.replace('HOME_URL');}finally{clearTimeout(timeout);}}setInterval(check,30000);addEventListener('pageshow',check);document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});})();</script>'''

def create_app(site_config):
    app=Flask(__name__,static_folder=str(site_config.static_root),static_url_path='/static',template_folder=None)
    app.site=site_config
    secret=os.environ.get(site_config.secret_env,'')
    if not secret:raise RuntimeError(site_config.secret_env+' is not set')
    app.config.update(SECRET_KEY=secret,DATABASE=str(data_dir()/schema.DB_NAME),MAX_CONTENT_LENGTH=8192,SESSION_COOKIE_NAME=site_config.cookie_name,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',PERMANENT_SESSION_LIFETIME=86400*30)
    app.jinja_loader=ChoiceLoader([FileSystemLoader(str(site_config.template_root)),FileSystemLoader(str(ROOT/'core'/'templates'))])
    app.teardown_appcontext(close_db)
    app.jinja_env.globals.update(csrf=csrf,site=site_config,games=site_config.games,grades=GRADES,grade_names=GRADE_NAMES,game_grades=site_config.game_grades,games_for_grades=games_for_grades,decode=json.loads,asset=asset,
        game_group_id=game_group_id,group_map=group_map,groups_for_grade=groups_for_grade,group_games=group_games,license_group_ids=license_group_ids,batch_group_ids=batch_group_ids,group_labels=group_labels)
    app.jinja_env.filters['datetime']=lambda value: time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(value))
    app.jinja_env.filters['grade_names']=lambda value: '、'.join(GRADES[g] for g in json.loads(value))
    app.jinja_env.filters['game_names']=lambda value: '、'.join(site_config.games[g] for g in json.loads(value))

    @app.before_request
    def protect():
        if request.method=='POST':
            supplied=request.headers.get('X-CSRF-Token') or request.form.get('csrf','')
            if not session.get('csrf') or not secrets.compare_digest(supplied,session['csrf']):
                if request.path.startswith('/api/'):
                    return jsonify(error='页面验证已过期，请重新点击激活；若仍失败，请允许本站使用Cookie后刷新页面',code='csrf_expired'),400
                abort(400,'页面已过期，请刷新后重试')
    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            messages={400:'请求格式不正确，请刷新页面后重试',404:'接口不存在，请刷新页面后重试',405:'请求方式不正确',413:'提交内容过长',500:'服务器处理失败，请稍后重试',503:'服务器繁忙，请稍后重试'}
            return jsonify(error=messages.get(error.code,'请求失败，请稍后重试'),code='http_error'),error.code
        return error
    @app.errorhandler(sqlite3.Error)
    def database_error(error):
        app.logger.error('Database operation failed: %s',type(error).__name__)
        if request.path.startswith('/api/'):
            return jsonify(error='服务器繁忙，请稍后重试',code='database_unavailable'),503
        return '服务器繁忙，请稍后重试',503
    @app.after_request
    def headers(r):
        # Static files are fingerprinted, so only dynamic responses must bypass caches.
        if request.endpoint not in ('static','shared_static'):r.headers['Cache-Control']='no-store'
        r.headers['X-Content-Type-Options']='nosniff';r.headers['X-Frame-Options']='SAMEORIGIN';r.headers['Referrer-Policy']='same-origin'
        if request.path in ('/api/activate','/api/session') and r.is_json and r.status_code==200:
            payload=r.get_json();row=current()
            if row:payload['group_scopes']=group_scopes_payload(row['id']);r.set_data(app.json.dumps(payload))
        return r

    @app.get('/static/game-audio-v3/<path:filename>')
    def shared_static(filename):return send_from_directory(SHARED_STATIC/'game-audio-v3',filename)
    @app.get('/api/csrf')
    def csrf_token():return jsonify(csrf=csrf())
    @app.get('/')
    def home():
        row=current()
        year=request.args.get('year',3,type=int);year=year if year in range(1,7) else 3
        term=request.args.get('term',1,type=int);term=term if term in (1,2) else 1
        grade=f'g{year}-t{term}'
        return render_template('home.html',license=row,device_count=device_count(row['id']) if row else 0,selected_year=year,selected_term=term,selected_grade=grade,grade_games=games_for_grades([grade]),unlocked=allowed_games(row) if row else [])
    @app.post('/api/activate')
    def activate():
        if limited('activate',30,300):return jsonify(error='请求过于频繁，请5分钟后再试'),429
        payload=request.get_json(silent=True)
        if not isinstance(payload,dict):return jsonify(error='请求格式不正确'),400
        key=payload.get('key','')
        if not isinstance(key,str):return jsonify(error='授权码格式不正确'),400
        key=key.strip().upper().replace('-','').replace(' ','')
        token=session.get('device') or secrets.token_urlsafe(32); now=int(time.time());d=db();d.execute('BEGIN IMMEDIATE');s=site().key
        row=d.execute('SELECT * FROM licenses WHERE site=? AND key_hash=?',(s,digest(key))).fetchone()
        if not row or not row['enabled'] or row['archived_at'] is not None or not license_group_ids(row['id']):d.rollback();return jsonify(error='授权码无效或已停用'),403
        bound=d.execute('SELECT 1 FROM devices WHERE site=? AND license_id=? AND token_hash=?',(s,row['id'],digest(token))).fetchone()
        count=device_count(row['id'])
        if not bound and count>=row['device_limit']:d.rollback();return jsonify(error=f"此授权码已绑定{row['device_limit']}台设备，请联系管理员解绑",device_count=count,device_limit=row['device_limit']),403
        if not bound:d.execute('INSERT INTO devices(site,license_id,token_hash,label,created_at,last_seen) VALUES(?,?,?,?,?,?)',(s,row['id'],digest(token),request.user_agent.string[:200],now,now))
        else:d.execute('UPDATE devices SET last_seen=? WHERE site=? AND license_id=? AND token_hash=?',(now,s,row['id'],digest(token)))
        d.commit();session.update(license=row['id'],device=token);session.permanent=True
        return jsonify(ok=True,games=allowed_games(row),grade_scopes=json.loads(row['grade_scopes']),device_count=count+(not bound),device_limit=row['device_limit'])
    @app.get('/api/session')
    def status():
        row=current(request.args.get('game'))
        if not row:return jsonify(error='授权已失效，请返回首页重新激活或联系管理员'),403
        return jsonify(games=allowed_games(row),grade_scopes=json.loads(row['grade_scopes']),device_limit=row['device_limit'],device_count=device_count(row['id']))
    @app.post('/api/logout')
    def logout():session.pop('license',None);return redirect(url_for('home'))
    @app.get('/games/<game>')
    def game_page(game):
        if game not in site().games:abort(404)
        if not current(game):return render_template('denied.html'),403
        guard=GUARD.replace('SESSION_URL',url_for('status',game=game)).replace('HOME_URL',url_for('home'))
        return Response(game_html(game).replace('</body>',guard+'</body>'),mimetype='text/html')
    if site_config.legacy_html_routes:
        @app.get('/<name>.html')
        def legacy(name):
            if name=='游戏入口':return redirect(url_for('home'))
            for game,file in site().files.items():
                if file==name+'.html':return game_page(game)
            abort(404)

    @app.route('/admin/login',methods=['GET','POST'])
    def login():
        error=''
        if request.method=='POST':
            if limited('admin',10,900):return render_template('login.html',error='尝试过多，请15分钟后重试'),429
            row=admin_row()
            if request.form.get('username')=='admin' and row and check_password_hash(row['password'],request.form.get('password','')):
                rotate_admin_session();session.update(admin_version=row['version'],admin_until=int(time.time())+28800);audit('login');db().commit();return redirect(url_for('dashboard'))
            error='用户名或密码错误'
        return render_template('login.html',error=error)
    @app.route('/admin/password',methods=['GET','POST'])
    @admin_only
    def password():
        error=''
        if request.method=='POST':
            value=request.form.get('password',''); old=request.form.get('old','');row=admin_row()
            if not check_password_hash(row['password'],old):error='当前密码错误'
            elif len(value)<12 or value!=request.form.get('confirm'):error='新密码至少12位，两次输入必须一致'
            else:
                db().execute('UPDATE admin SET password=?,must_change=0,version=version+1 WHERE site=?',(generate_password_hash(value),site().key));audit('password_change');db().commit();rotate_admin_session();return redirect(url_for('login'))
        return render_template('password.html',error=error)
    @app.post('/admin/logout')
    def admin_logout():rotate_admin_session();return redirect(url_for('login'))
    @app.get('/admin')
    @admin_only
    def dashboard():
        where,params=filtered();d=db();s=site().key
        total=d.execute('SELECT count(*) FROM licenses'+where,params).fetchone()[0]
        pages=max(1,(total+49)//50);page=max(1,min(request.args.get('page',1,type=int),pages))
        rows=d.execute('SELECT *, (SELECT count(*) FROM devices WHERE site=licenses.site AND license_id=licenses.id) AS devices FROM licenses'+where+' ORDER BY id LIMIT 50 OFFSET ?',params+[(page-1)*50]).fetchall()
        stats=d.execute("SELECT count(*) total,coalesce(sum(assigned_at IS NOT NULL),0) assigned,coalesce(sum(assigned_at IS NULL AND enabled=1),0) available,coalesce(sum(enabled=0),0) disabled FROM licenses WHERE site=? AND archived_at IS NULL",(s,)).fetchone()
        batch=d.execute('SELECT * FROM generation_batches WHERE site=? AND id=?',(s,request.args.get('batch',''))).fetchone()
        query=request.args.to_dict();query.pop('page',None)
        return render_template('admin.html',rows=rows,archived=request.args.get('archived')=='1',archive_count=d.execute('SELECT count(*) FROM licenses WHERE site=? AND archived_at IS NOT NULL',(s,)).fetchone()[0],keys=license_codes(r['id'] for r in rows),decode=json.loads,stats=stats,total=total,page=page,pages=pages,batch=batch,previous=url_for('dashboard',**dict(query,page=page-1)),next=url_for('dashboard',**dict(query,page=page+1)),export_url=url_for('export',**query))
    @app.route('/admin/licenses/new',methods=['GET','POST'])
    @admin_only
    def new_licenses():
        signer=URLSafeSerializer(current_app.secret_key,salt='group-license-generation-v1')
        nonce=request.form.get('nonce','') if request.method=='POST' else signer.dumps(secrets.token_hex(16))
        selected=request.form.getlist('groups') if request.method=='POST' else []
        error=''
        if request.method=='POST':
            try:
                batch_id=signer.loads(nonce)
                if not isinstance(batch_id,str) or len(batch_id)!=32:raise ValueError('表单已失效，请重新打开新增页面')
                if not selected or any(gid not in group_map() for gid in selected):raise ValueError('请至少选择一个有效游戏组')
                chosen=[gid for gid in group_map() if gid in selected]
                try:quantity=integer_field('quantity',1,1000)
                except ValueError:raise ValueError('生成数量必须为1至1000的整数')
                try:limit=integer_field('device_limit',1,100)
                except ValueError:raise ValueError('设备上限必须为1至100的整数')
                grades_json=json.dumps(list(dict.fromkeys(group_map()[gid]['grade'] for gid in chosen)));d=db();d.execute('BEGIN IMMEDIATE');s=site().key
                existing=d.execute('SELECT * FROM generation_batches WHERE site=? AND id=?',(s,batch_id)).fetchone()
                if existing:
                    if (batch_group_ids(batch_id),existing['quantity'],existing['device_limit'])!=(chosen,quantity,limit):raise ValueError('该表单已生成过授权码，请重新打开新增页面')
                else:
                    now=int(time.time());d.execute('INSERT INTO generation_batches(site,id,games,quantity,device_limit,created_at,grade_scopes) VALUES(?,?,?,?,?,?,?)',(s,batch_id,'[]',quantity,limit,now,grades_json))
                    d.executemany('INSERT INTO batch_groups(site,batch_id,group_id) VALUES(?,?,?)',[(s,batch_id,gid) for gid in chosen])
                    next_id=d.execute('SELECT coalesce(max(id),0)+1 FROM licenses WHERE site=?',(s,)).fetchone()[0]
                    for lid in range(next_id,next_id+quantity):
                        while True:
                            key=''.join(secrets.choice(KEY_ALPHABET) for _ in range(16))
                            cursor=d.execute('INSERT OR IGNORE INTO licenses(site,id,key_hash,games,created_at,device_limit,batch_id,grade_scopes) VALUES(?,?,?,?,?,?,?,?)',(s,lid,digest(key),'[]',now,limit,batch_id,grades_json))
                            if cursor.rowcount:break
                        d.execute('INSERT INTO license_keys(site,license_id,code) VALUES(?,?,?)',(s,lid,key))
                        d.executemany('INSERT INTO license_groups(site,license_id,group_id) VALUES(?,?,?)',[(s,lid,gid) for gid in chosen])
                    audit('generate',details=json.dumps({'batch':batch_id,'quantity':quantity,'group_scopes':chosen,'device_limit':limit},ensure_ascii=False))
                d.commit();flash(f'已生成 {quantity} 个授权码，每码最多 {limit} 个浏览器。','success')
                return redirect(url_for('dashboard',batch=batch_id))
            except BadSignature:error='表单已失效，请重新打开新增页面'
            except ValueError as exc:error=str(exc)
            if 'db' in g:db().rollback()
        return render_template('new.html',nonce=nonce,selected=selected,error=error),400 if error else 200
    @app.get('/admin/licenses/<int:lid>')
    @admin_only
    def detail(lid):
        row=license_row(lid)
        if not row:abort(404)
        s=site().key
        return render_template('detail.html',row=row,key=license_codes([lid])[row['key_hash']],devices=db().execute('SELECT * FROM devices WHERE site=? AND license_id=?',(s,lid)).fetchall(),events=db().execute('SELECT * FROM audit WHERE site=? AND license_id=? ORDER BY id DESC LIMIT 30',(s,lid)).fetchall())
    @app.post('/admin/licenses/<int:lid>/<action>')
    @admin_only
    def edit(lid,action):
        d=db();d.execute('BEGIN IMMEDIATE');s=site().key
        record=license_row(lid)
        if not record:abort(404)
        if record['archived_at'] is not None:d.rollback();abort(403,'归档授权只可查看，不能修改或重新启用')
        if action=='assign':
            buyer=request.form.get('buyer','').strip()[:500]
            if not buyer:abort(400,'请填写买家或订单备注')
            d.execute('UPDATE licenses SET buyer=?,assigned_at=coalesce(assigned_at,?) WHERE site=? AND id=?',(buyer,int(time.time()),s,lid));details=buyer
        elif action in ('enable','disable'):d.execute('UPDATE licenses SET enabled=? WHERE site=? AND id=?',(int(action=='enable'),s,lid));details=''
        elif action=='grades':
            d.rollback();abort(400,'请使用游戏组授权表单')
        elif action=='groups':
            selected=list(dict.fromkeys(request.form.getlist('groups')))
            if not selected or any(gid not in group_map() for gid in selected):
                d.rollback();flash('请至少选择一个有效游戏组','error');return redirect(url_for('detail',lid=lid))
            details=json.dumps({'before':group_labels(license_group_ids(lid)),'after':group_labels(selected)},ensure_ascii=False)
            d.execute('DELETE FROM license_groups WHERE site=? AND license_id=?',(s,lid))
            d.executemany('INSERT INTO license_groups(site,license_id,group_id) VALUES(?,?,?)',[(s,lid,gid) for gid in selected])
            d.execute('UPDATE licenses SET grade_scopes=? WHERE site=? AND id=?',(json.dumps(list(dict.fromkeys(group_map()[gid]['grade'] for gid in selected))),s,lid))
        elif action=='limit':
            try:limit=integer_field('device_limit',1,100)
            except ValueError:
                d.rollback();flash('设备上限必须为1至100的整数','error');return redirect(url_for('detail',lid=lid))
            count=device_count(lid)
            if limit<count:
                d.rollback();flash(f'当前已绑定{count}个浏览器，请先解绑后再降低设备上限','error');return redirect(url_for('detail',lid=lid))
            d.execute('UPDATE licenses SET device_limit=? WHERE site=? AND id=?',(limit,s,lid));details=f"{record['device_limit']} → {limit}"
        elif action=='unbind':
            details=request.form.get('device','');d.execute('DELETE FROM devices WHERE site=? AND license_id=? AND token_hash=?',(s,lid,details))
        else:abort(404)
        audit(action,lid,details);d.commit();flash('已保存更改','success');return redirect(url_for('detail',lid=lid))
    @app.get('/admin/export')
    @admin_only
    def export():
        where,params=filtered();rows=db().execute('SELECT * FROM licenses'+where+' ORDER BY id',params).fetchall();audit('export',details=str(len(rows)));db().commit()
        return Response(csv_response(rows),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="licenses.csv"'})
    @app.get('/health')
    def health():db().execute('SELECT count(*) FROM licenses WHERE site=?',(site().key,)).fetchone();return jsonify(status='ok')
    @app.get('/admin/games')
    @admin_only
    def game_directory():return render_template('directory.html')
    @app.route('/admin/groups',methods=['GET','POST'])
    @admin_only
    def group_admin():
        if request.method=='POST':
            d=db();d.execute('BEGIN IMMEDIATE');action=request.form.get('action');grade=request.form.get('grade');s=site().key
            try:
                if grade not in GRADES:raise ValueError('请选择有效学期')
                catalog=group_map();gid=request.form.get('group')
                if action in ('create','rename'):
                    name=request.form.get('name','').strip()
                    if not 1<=len(name)<=30 or any(c in name for c in '/\\\x00'):raise ValueError('组名为1至30字，不能包含斜杠')
                    position=integer_field('position',0,999)
                    if action=='create':
                        gid=grade+'-'+secrets.token_hex(6);d.execute('INSERT INTO game_groups(site,id,grade,name,position) VALUES(?,?,?,?,?)',(s,gid,grade,name,position))
                    else:
                        if gid not in catalog or catalog[gid]['grade']!=grade:raise ValueError('无效游戏组')
                        d.execute('UPDATE game_groups SET name=?,position=? WHERE site=? AND id=?',(name,position,s,gid))
                elif action=='move':
                    game=request.form.get('game')
                    if game not in site().games or gid not in catalog or catalog[gid]['grade']!=site().game_grades[game] or grade!=site().game_grades[game]:raise ValueError('游戏只能移动到同学期的组')
                    before=game_group_id(game)
                    d.execute('INSERT INTO game_memberships(site,game,group_id) VALUES(?,?,?) ON CONFLICT(site,game) DO UPDATE SET group_id=excluded.group_id',(s,game,gid));audit('game_move',details=json.dumps({'game':game,'before':before,'after':gid},ensure_ascii=False))
                elif action=='delete':
                    if gid not in catalog or catalog[gid]['grade']!=grade:raise ValueError('无效游戏组')
                    for table in ('game_memberships','license_groups','batch_groups'):
                        if d.execute('SELECT 1 FROM '+table+' WHERE site=? AND group_id=?',(s,gid)).fetchone():raise ValueError('该组仍有游戏、授权或历史批次引用，不能删除')
                    d.execute('DELETE FROM game_groups WHERE site=? AND id=?',(s,gid))
                else:raise ValueError('无效操作')
                audit('group_'+action,details=json.dumps(dict(request.form),ensure_ascii=False).replace(request.form.get('csrf','__none__'),'[redacted]'));d.commit();flash('分组已保存，权限已同步生效','success')
            except (ValueError,sqlite3.IntegrityError) as exc:
                d.rollback();flash('组名不能重复' if isinstance(exc,sqlite3.IntegrityError) else str(exc),'error')
            g.pop('group_map',None)
            return redirect(url_for('group_admin',grade=grade))
        grade=request.args.get('grade','g3-t1');grade=grade if grade in GRADES else 'g3-t1'
        return render_template('groups.html',selected_grade=grade)
    return app
