"""Fault-check only this repository's macOS loopback no-key installation.

Requires an already started demo. Kills its owned frontend/backend/MCP processes
one at a time and verifies recovery without changing source business records.
Never run this against a real production service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
INSTANCE=hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
INSTALL=Path.home()/'Library'/'Application Support'/'LLM-Agent-System'/INSTANCE


def read(url):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url,timeout=4) as response:return json.load(response)


def state():return json.loads((INSTALL/'state.json').read_text())


def snapshot():
    digest=hashlib.sha256();counts={}
    with sqlite3.connect((INSTALL/'data/rides.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        for table in ('cases','case_index','receipts','attachment_blobs','attachments','policy_sources','runtime_settings'):
            count=0
            for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid'):
                for value in row:
                    encoded=value if isinstance(value,bytes) else json.dumps(value,separators=(',',':')).encode()
                    digest.update(str(len(encoded)).encode()+b':'+encoded)
                count+=1
            counts[table]=count
    return dict(sha256=digest.hexdigest(),table_counts=counts)


def owned_pid(component,current):
    if component in ('backend','frontend'):
        pid=current[component+'_pid']
        command=subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
        expected=str(INSTALL/'venv/bin/python')+' '
        if not command.startswith(expected):raise RuntimeError('Refusing to signal an unrelated process')
        if component=='backend' and '--port 8010' not in command:raise RuntimeError('Backend port ownership mismatch')
        if component=='frontend' and str(INSTALL/'runtime/frontend.py') not in command:raise RuntimeError('Frontend command ownership mismatch')
        return pid
    rows=subprocess.check_output(['ps','-Ao','pid=,ppid=,command='],text=True).splitlines()
    matches=[int(parts[0]) for row in rows if len(parts:=row.strip().split(None,2))==3 and parts[1]==str(current['backend_pid']) and str(INSTALL/'app/backend/mobility/mcp_demo_server.py') in parts[2]]
    if len(matches)!=1:raise RuntimeError('Expected one owned MCP child')
    return matches[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if sys.platform!='darwin':parser.error('This verifier targets the macOS background launcher')
    if args.output.exists():parser.error('Refusing to overwrite recovery evidence')
    configuration=read('http://127.0.0.1:8010/runtime-config')
    if configuration.get('auth_mode')!='demo' or configuration.get('model_mode')!='demo':parser.error('Refusing to fault a non-demo profile')
    if json.loads((INSTALL/'config.json').read_text())['source']!=str(ROOT):parser.error('Installation does not belong to this repository')
    baseline=snapshot();results=[]
    for component in ('backend','frontend','mcp'):
        before=state()
        if before.get('status')!='ready':raise RuntimeError('Start from a healthy installation before fault testing')
        pid=owned_pid(component,before);began=time.monotonic();os.kill(pid,signal.SIGKILL)
        deadline=began+65;recovered=None
        while time.monotonic()<deadline:
            try:
                current=state()
                replacement=owned_pid(component,current)
                if replacement!=pid and current.get('status')=='ready' and all(current['healthy'].values()):
                    if read('http://127.0.0.1:8010/readyz')['status']=='ok' and read('http://127.0.0.1:5175/__llmagentsystem_health')['instance']==INSTANCE:
                        recovered=current;break
            except (OSError,ValueError,KeyError,RuntimeError,subprocess.CalledProcessError):pass
            time.sleep(.5)
        item=dict(component=component,old_pid=pid,recovered=bool(recovered),elapsed_seconds=round(time.monotonic()-began,3),source_state_preserved=snapshot()==baseline)
        results.append(item)
        if not item['recovered'] or not item['source_state_preserved']:
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(dict(status='failed',results=results),indent=2)+'\n')
            raise RuntimeError('Recovery failed; partial evidence is preserved')
    modes={name:oct((INSTALL/'logs'/name).stat().st_mode & 0o777) for name in ('backend.log','frontend.log')}
    report=dict(status='passed',scope='Owned local no-key demo only',results=results,source_state=baseline,log_modes=modes)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
