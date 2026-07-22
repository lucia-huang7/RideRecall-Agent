"""launchd-owned process supervisor for the local no-key demo."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path(__file__).resolve().parents[1]
STATE = HOME/'state.json'
STOPPING = False


def read_json(url, headers=None, data=None):
    request=urllib.request.Request(url,headers=headers or {},data=json.dumps(data).encode() if data is not None else None)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request,timeout=3) as response:
        return json.load(response)


def stop_process(process):
    if process is None:
        return
    try:
        os.killpg(process.pid,signal.SIGTERM)
        process.wait(timeout=8)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        process.wait(timeout=5)


def save_state(data):
    temporary=STATE.with_suffix('.pending')
    temporary.write_text(json.dumps(data,indent=2))
    temporary.replace(STATE)


def main():
    global STOPPING
    HOME.mkdir(parents=True,exist_ok=True)
    lock=(HOME/'supervisor.lock').open('w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        return
    # A hard-killed supervisor cannot reap its child sessions. Recover only
    # processes whose command belongs to this exact installation.
    try:
        previous=json.loads(STATE.read_text())
    except (OSError,ValueError):
        previous={}
    for name in ('backend','frontend'):
        pid=previous.get(name+'_pid')
        if not isinstance(pid,int):
            continue
        command=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout.strip()
        if command.startswith(str(HOME/'venv'/'bin'/'python')+' '):
            try:
                os.killpg(pid,signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                command=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout.strip()
                if not command.startswith(str(HOME/'venv'/'bin'/'python')+' '):
                    break
                time.sleep(.2)
            else:
                os.killpg(pid,signal.SIGKILL)
    config=json.loads((HOME/'config.json').read_text())
    logs=HOME/'logs'
    logs.mkdir(exist_ok=True)
    for name in ('backend','frontend'):
        file=logs/(name+'.log')
        if file.exists() and file.stat().st_size>10*1024*1024:
            file.replace(file.with_suffix('.previous.log'))
    env=os.environ.copy()
    env.update(KINDER_UBER_ENV='development',KINDER_UBER_RIDE_MODEL_MODE='demo',KINDER_UBER_RIDE_DB=str(HOME/'data'/'rides.sqlite3'),KINDER_UBER_INSTANCE_ID=config['instance'],PYTHONUNBUFFERED='1')
    commands={
        'backend':[sys.executable,'-m','uvicorn','mobility.app:app','--host','127.0.0.1','--port','8010'],
        'frontend':[sys.executable,str(HOME/'runtime'/'frontend.py')],
    }
    processes={}
    failures={'backend':0,'frontend':0,'mcp':0}
    restarts={'backend':0,'frontend':0,'mcp':0}
    handles={name:(logs/(name+'.log')).open('a') for name in commands}
    status={'supervisor_pid':os.getpid(),'instance':config['instance'],'status':'starting','restarts':restarts}

    def signal_stop(*args):
        global STOPPING
        STOPPING=True
    signal.signal(signal.SIGTERM,signal_stop)
    signal.signal(signal.SIGINT,signal_stop)

    def start(name):
        processes[name]=subprocess.Popen(commands[name],cwd=HOME/'app'/'backend',env=env,stdin=subprocess.DEVNULL,stdout=handles[name],stderr=subprocess.STDOUT,start_new_session=True)
        status[name+'_pid']=processes[name].pid
        status[name+'_started']=time.time()
        failures[name]=0
        save_state(status)

    try:
        for name in commands:
            start(name)
        while not STOPPING:
            for name,process in list(processes.items()):
                if process.poll() is not None:
                    restarts[name]+=1
                    time.sleep(min(5,restarts[name]))
                    if not STOPPING:
                        start(name)
            if STOPPING:
                break
            healthy={}
            for name,url in [('backend','http://127.0.0.1:8010/health'),('frontend','http://127.0.0.1:5175/__kinderuber_health')]:
                try:
                    data=read_json(url)
                    healthy[name]=data.get('status')=='ok' if name=='backend' else data.get('instance')==config['instance']
                except Exception:
                    healthy[name]=False
                failures[name]=0 if healthy[name] else failures[name]+1
                # Allow cold Chroma initialization; then recover failed health checks.
                if failures[name]>=4 and time.time()-status[name+'_started']>35:
                    stop_process(processes[name])
                    restarts[name]+=1
                    start(name)
            if healthy.get('backend'):
                try:
                    result=read_json('http://127.0.0.1:8010/api/agent/operations',{'X-Demo-Agent':'local-demo-agent'})
                    heartbeat=read_json('http://127.0.0.1:8010/api/agent/mcp/probe', {'X-Demo-Agent':'local-demo-agent','Content-Type':'application/json'}, {'trip_id':'trip-river'})
                    healthy['mcp']=heartbeat.get('success') is True and bool(result['mcp']['servers'])
                except Exception:
                    healthy['mcp']=False
                failures['mcp']=0 if healthy['mcp'] else failures['mcp']+1
                if failures['mcp']>=6:
                    # The original client retries first; restart its owning backend if stuck.
                    stop_process(processes['backend'])
                    restarts['backend']+=1
                    restarts['mcp']+=1
                    start('backend')
                    failures['mcp']=0
            else:
                healthy['mcp']=False
            status.update(status='ready' if all(healthy.values()) else 'recovering',healthy=healthy,checked_at=time.time())
            save_state(status)
            for _ in range(20):
                if STOPPING:
                    break
                time.sleep(.25)
    finally:
        for process in processes.values():
            stop_process(process)
        status.update(status='stopped',checked_at=time.time())
        save_state(status)
        for handle in handles.values():
            handle.close()


if __name__=='__main__':
    main()
