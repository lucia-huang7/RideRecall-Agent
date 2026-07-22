"""Snapshots preserve replay/binary state and refuse live, corrupt or unsafe copies."""
import sqlite3
import pytest
from mobility.backup import snapshot, verify, restore
from mobility.runtime_lock import embedded_writer_lock
from mobility.store import Store


def state(path):
    store=Store(path)
    case=dict(id='KU-SNAPSHOT',owner='rider',trip_id='trip-river',revision=0,updated_at='2026-10-22',messages=[],events=[],evidence=[])
    def operation(db):
        store.save(db,case)
        store.put_attachment(db,case['id'],'receipt','application/pdf',b'%PDF-recoverable')
        return case
    store.write('rider','saved-command','fingerprint',operation)
    path.with_suffix('.vectors').mkdir()
    (path.with_suffix('.vectors')/'index.bin').write_bytes(b'derived-memory-index')
    path.with_suffix('.skills').mkdir()
    (path.with_suffix('.skills')/'SKILL.md').write_text('# Recovery Skill\nPreserve context.\n')
    path.with_suffix('.baseline.json').write_text('{"quality":"mechanical"}')
    return store


def test_snapshot_restore_preserves_database_receipt_blob_and_sidecars(tmp_path):
    source=tmp_path/'cases.sqlite3';store=state(source)
    target=tmp_path/'backup';report=snapshot(source,target)
    assert report['database']=={'schema_version':4,'cases':1,'receipts':1}
    assert verify(target)==report
    restored=restore(target,tmp_path/'restored');recovered=Store(restored)
    assert recovered.get('KU-SNAPSHOT')==store.get('KU-SNAPSHOT')
    assert recovered.replay('rider','saved-command','fingerprint')==store.replay('rider','saved-command','fingerprint')
    assert recovered.attachment('KU-SNAPSHOT','receipt')['data']==b'%PDF-recoverable'
    assert (restored.with_suffix('.vectors')/'index.bin').read_bytes()==b'derived-memory-index'
    assert restored.with_suffix('.baseline.json').read_text()=='{"quality":"mechanical"}'
    with pytest.raises(FileExistsError):restore(target,tmp_path/'restored')
    with pytest.raises(FileExistsError):snapshot(source,target)


def test_active_writer_cannot_be_snapshotted(tmp_path):
    source=tmp_path/'cases.sqlite3';state(source)
    with embedded_writer_lock(source),pytest.raises(RuntimeError,match='one worker'):snapshot(source,tmp_path/'backup')
    assert not (tmp_path/'backup').exists()


@pytest.mark.parametrize('corruption',['modify','extra','missing','symlink'])
def test_snapshot_integrity_blocks_corruption_before_restore(tmp_path,corruption):
    source=tmp_path/'cases.sqlite3';state(source)
    target=tmp_path/'backup';snapshot(source,target);item=target/'vectors'/'index.bin'
    if corruption=='modify':item.write_bytes(b'bad')
    elif corruption=='extra':(target/'unexpected').write_text('untracked')
    elif corruption=='missing':item.unlink()
    else:item.unlink();item.symlink_to(source)
    with pytest.raises(ValueError):restore(target,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_backup_failure_removes_partial_output_but_preserves_source(tmp_path):
    source=tmp_path/'cases.sqlite3';state(source)
    (source.with_suffix('.skills')/'outside').symlink_to(source)
    with pytest.raises(ValueError,match='symbolic'):snapshot(source,tmp_path/'backup')
    assert not (tmp_path/'backup').exists()
    assert Store(source).get('KU-SNAPSHOT')['id']=='KU-SNAPSHOT'


def test_schema_one_snapshot_is_read_only_and_preserves_version(tmp_path):
    source=tmp_path/'legacy.sqlite3'
    with sqlite3.connect(source) as db:
        db.executescript('CREATE TABLE cases(id TEXT PRIMARY KEY,owner TEXT,data TEXT); CREATE TABLE receipts(owner TEXT,key TEXT,fingerprint TEXT,response TEXT,PRIMARY KEY(owner,key)); PRAGMA user_version=1;')
    report=snapshot(source,tmp_path/'backup')
    assert report['database']['schema_version']==1
    with sqlite3.connect(source) as db:assert db.execute('PRAGMA user_version').fetchone()[0]==1
