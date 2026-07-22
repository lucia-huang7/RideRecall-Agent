"""Transactional lightweight case summaries and account-bound keyset cursors."""
import base64
import hashlib
import hmac
import json
import secrets
import sqlite3

from fastapi import HTTPException

FIELDS=('id','trip_id','title','status','updated_at','revision','handoff','assigned_to','has_adjustment')
COLUMNS='case_id,trip_id,title,status,updated_at,revision,handed_off,assigned_to,has_adjustment'


def index_case(db,case,owner=None):
    db.execute('''INSERT INTO case_index(case_id,owner,trip_id,title,status,updated_at,revision,handed_off,assigned_to,has_adjustment)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET owner=excluded.owner,trip_id=excluded.trip_id,
        title=excluded.title,status=excluded.status,updated_at=excluded.updated_at,revision=excluded.revision,
        handed_off=excluded.handed_off,assigned_to=excluded.assigned_to,has_adjustment=excluded.has_adjustment''',
        (case['id'],owner if owner is not None else case['owner'],case.get('trip_id',''),case.get('title','Support case'),
         case.get('status',''),case.get('updated_at',''),case.get('revision',0),int(bool(case.get('handoff'))),
         case.get('assigned_to'),int(bool(case.get('adjustment')))))


def migrate_index(db):
    db.execute('''CREATE TABLE case_index(case_id TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
        owner TEXT NOT NULL,trip_id TEXT NOT NULL,title TEXT NOT NULL,status TEXT NOT NULL,updated_at TEXT NOT NULL,
        revision INTEGER NOT NULL,handed_off INTEGER NOT NULL,assigned_to TEXT,has_adjustment INTEGER NOT NULL)''')
    db.execute('CREATE INDEX case_owner_activity ON case_index(owner,updated_at DESC,case_id DESC)')
    db.execute('CREATE INDEX case_handoff_activity ON case_index(handed_off,updated_at DESC,case_id DESC)')
    db.execute('CREATE INDEX case_owner_trip ON case_index(owner,trip_id,updated_at DESC,case_id DESC)')
    db.execute('CREATE INDEX case_owner_status ON case_index(owner,status)')
    for identity,owner,raw in db.execute('SELECT id,owner,data FROM cases'):
        case=json.loads(raw)
        if case['id']!=identity:raise RuntimeError('Case identity mismatch; summary migration refused without changing source state')
        index_case(db,case,owner)
    db.execute("INSERT INTO runtime_settings(key,value) VALUES('case_cursor_secret',?)",(secrets.token_hex(32),))


def summary(row):
    result=dict(zip(FIELDS,row,strict=True))
    result['handoff']=bool(result['handoff']);result['has_adjustment']=bool(result['has_adjustment'])
    return result


def scope(owner,handed_off,trip_id):
    return hashlib.sha256(json.dumps([owner,handed_off,trip_id],separators=(',',':')).encode()).hexdigest()


def cursor_encode(key,position,scope_id):
    payload=base64.urlsafe_b64encode(json.dumps(dict(v=1,after=position,scope=scope_id),separators=(',',':')).encode()).decode().rstrip('=')
    return payload+'.'+hmac.new(key,payload.encode(),hashlib.sha256).hexdigest()


def cursor_decode(key,value,scope_id):
    try:
        if len(value)>512:raise ValueError
        payload,signature=value.split('.')
        if not hmac.compare_digest(hmac.new(key,payload.encode(),hashlib.sha256).hexdigest(),signature):raise ValueError
        record=json.loads(base64.b64decode(payload+'='*(-len(payload)%4),altchars=b'-_',validate=True))
        if not isinstance(record,dict):raise ValueError
        if record.get('v')!=1 or record.get('scope')!=scope_id:raise ValueError
        after=record['after']
        if not isinstance(after,list) or len(after)!=2 or not all(isinstance(x,str) and len(x)<=100 for x in after):raise ValueError
        return after
    except (ValueError,KeyError,TypeError,UnicodeError):
        raise HTTPException(400,'This page cursor is invalid for the current account or filter. Refresh the list.') from None


def page(store,owner=None,handed_off=False,trip_id=None,limit=25,cursor=None,full=False):
    if type(limit) is not int or not 1<=limit<=100:raise HTTPException(422,'Page size must be between 1 and 100.')
    clauses=[];parameters=[]
    if owner is not None:clauses.append('owner=?');parameters.append(owner)
    if handed_off:clauses.append('handed_off=1')
    if trip_id is not None:clauses.append('trip_id=?');parameters.append(trip_id)
    base=' AND '.join(clauses) if clauses else '1=1'
    scope_id=scope(owner,handed_off,trip_id)
    with store.connect() as db:
        # A single read snapshot keeps counts, summaries and full representations coherent.
        db.execute('BEGIN')
        secret=db.execute("SELECT value FROM runtime_settings WHERE key='case_cursor_secret'").fetchone()
        if secret is None:raise sqlite3.DatabaseError('Case cursor configuration is missing')
        try:
            key=bytes.fromhex(secret[0])
            if len(key)!=32:raise ValueError
        except (ValueError,TypeError):raise sqlite3.DatabaseError('Invalid case cursor configuration') from None
        total,active=db.execute('SELECT COUNT(*),COALESCE(SUM(status!=?),0) FROM case_index WHERE '+base,('resolved',*parameters)).fetchone()
        where=base;arguments=list(parameters)
        if cursor:
            position=cursor_decode(key,cursor,scope_id)
            where+=' AND (updated_at,case_id)<(?,?)';arguments.extend(position)
        rows=db.execute('SELECT '+COLUMNS+' FROM case_index WHERE '+where+' ORDER BY updated_at DESC,case_id DESC LIMIT ?',(*arguments,limit+1)).fetchall()
        next_cursor=cursor_encode(key,[rows[limit-1][4],rows[limit-1][0]],scope_id) if len(rows)>limit else None
        items=[summary(row) for row in rows[:limit]]
        featured=db.execute('SELECT '+COLUMNS+' FROM case_index WHERE '+base+' AND status!=? ORDER BY updated_at DESC,case_id DESC LIMIT 1',(*parameters,'resolved')).fetchone()
        if full:
            result=[]
            for item in items:
                raw=db.execute('SELECT data FROM cases WHERE id=?',(item['id'],)).fetchone()
                if raw is None:raise sqlite3.DatabaseError('Case summary references missing source state')
                record=json.loads(raw[0]);record.setdefault('revision',0);result.append(record)
            items=result
    return dict(items=items,next_cursor=next_cursor,total=total,active_total=active,featured_active=summary(featured) if featured else None,limit=limit)


def find_trip(db,owner,trip_id):
    row=db.execute('''SELECT c.data FROM case_index i JOIN cases c ON c.id=i.case_id
        WHERE i.owner=? AND i.trip_id=? AND (i.status!='resolved' OR i.has_adjustment=1)
        ORDER BY i.updated_at DESC,i.case_id DESC LIMIT 1''',(owner,trip_id)).fetchone()
    if row is None:return None
    record=json.loads(row[0]);record.setdefault('revision',0)
    return record
