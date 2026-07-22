"""Run retained Java checks with isolated Redis and optional loopback Chroma.

No model key is needed: HTTP integration tests stub model responses while
exercising actual Redis, Spring HTTP and MCP transport. State is disposable.
"""
import argparse
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]


def available_port():
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));return probe.getsockname()[1]


def wait(process,port):
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError('An isolated test dependency exited during startup')
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.2):return
        except OSError:time.sleep(.1)
    raise TimeoutError('Isolated test dependency did not become ready')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-chroma',action='store_true',help='Requires the locked Python runtime dependencies in this interpreter')
    args=parser.parse_args()
    redis=shutil.which('redis-server')
    if not redis:parser.error('Install redis-server to run the full retained Java integration checks')
    processes=[]
    with TemporaryDirectory(prefix='kinder-java-check-') as directory:
        try:
            port=available_port()
            process=subprocess.Popen([redis,'--bind','127.0.0.1','--port',str(port),'--save','','--appendonly','no','--dir',directory],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            processes.append(process);wait(process,port)
            arguments=['./mvnw','clean','test','-q','-Dtest.redis.port='+str(port)]
            if args.with_chroma:
                chroma_port=available_port()
                environment={**os.environ,'PYTHONPATH':str(ROOT/'backend'),'ANONYMIZED_TELEMETRY':'False',
                    'CHROMA_PRODUCT_TELEMETRY_IMPL':'mobility.telemetry.LocalTelemetry','CHROMA_TELEMETRY_IMPL':'mobility.telemetry.LocalTelemetry'}
                process=subprocess.Popen([sys.executable,'-c','from chromadb.cli.cli import app; app()', 'run','--host','127.0.0.1','--port',str(chroma_port),'--path',str(Path(directory)/'chroma')],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                processes.append(process);wait(process,chroma_port)
                arguments.append('-Dtest.chroma.url=http://127.0.0.1:'+str(chroma_port))
            result=subprocess.run(arguments,cwd=ROOT/'legacy-java',timeout=180)
            if result.returncode:raise SystemExit(result.returncode)
            suites=[ET.parse(p).getroot().attrib for p in (ROOT/'legacy-java/target/surefire-reports').glob('TEST-*.xml')]
            print({key:sum(int(row[key]) for row in suites) for key in ('tests','failures','errors','skipped')})
        finally:
            for process in reversed(processes):
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':main()
