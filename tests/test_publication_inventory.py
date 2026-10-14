import hashlib
import json
import sqlite3
import zipfile
from network_fmri.records.publication import add_publication_records


def test_restored_current_anatomy_rejoins_exact_sha256_receipt(tmp_path):
    from network_fmri.records.publication import refresh_restored_checksums
    from network_fmri.records.lineage import artifact_id
    root = tmp_path / 'raw'
    root.mkdir()
    path = 'sub-s03_T1w.nii.gz'
    payload = b'defaced anatomy'
    (root / path).write_bytes(payload)
    md5 = 'md5:' + hashlib.md5(payload).hexdigest()
    sha = 'sha256:' + hashlib.sha256(payload).hexdigest()
    old, current = artifact_id('raw', path, md5), artifact_id('raw', path, sha)
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE artifact_versions(id PRIMARY KEY,dataset_id,path,content_id);
    CREATE TABLE artifact_observations(artifact_id,commit_hash,availability);
    ''')
    db.executemany('INSERT INTO artifact_versions VALUES (?,?,?,?)',
                   [(old,'raw',path,md5), (current,'raw',path,sha), ('wrong','raw',path,'sha256:'+'0'*64)])
    db.executemany('INSERT INTO artifact_observations VALUES (?,?,?)',
                   [(old,'commit','unavailable'), (current,None,'historical'), ('wrong',None,'historical')])
    refresh_restored_checksums(db, {'raw':root})
    assert db.execute("SELECT artifact_id FROM artifact_observations WHERE commit_hash='commit' AND availability='available'").fetchall() == [(current,)]
    assert db.execute('SELECT * FROM artifact_checksum_aliases').fetchall() == [(old,current)]
    assert db.execute("SELECT availability FROM artifact_observations WHERE artifact_id=?", (old,)).fetchone() == ('checksum_alias',)
    assert db.execute("SELECT commit_hash FROM artifact_observations WHERE artifact_id='wrong'").fetchone() == (None,)
    refresh_restored_checksums(db, {'raw':root})  # Re-publication is idempotent.
    assert db.execute("SELECT COUNT(*) FROM artifact_observations WHERE artifact_id=? AND commit_hash='commit'", (current,)).fetchone() == (1,)


def test_restored_checksums_reject_changed_bytes(tmp_path):
    import pytest
    from network_fmri.records.publication import refresh_restored_checksums
    path = tmp_path / 'image.nii.gz'
    path.write_bytes(b'changed')
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE artifact_versions(id PRIMARY KEY,dataset_id,path,content_id);
    CREATE TABLE artifact_observations(artifact_id,commit_hash,availability);
    ''')
    db.executemany('INSERT INTO artifact_versions VALUES (?,?,?,?)', [
        ('old','raw',path.name,'md5:'+'0'*32),
        ('known','raw',path.name,'sha256:'+hashlib.sha256(b'changed').hexdigest())])
    db.execute("INSERT INTO artifact_observations VALUES ('old','commit','unavailable')")
    with pytest.raises(ValueError, match='differs'):
        refresh_restored_checksums(db, {'raw':tmp_path})
    assert db.execute('SELECT * FROM artifact_checksum_aliases').fetchall() == []


def test_matching_name_or_other_dataset_cannot_supply_a_checksum_alias(tmp_path):
    from network_fmri.records.publication import refresh_restored_checksums
    path = tmp_path / 'image.nii.gz'
    path.write_bytes(b'current')
    db = sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE artifact_versions(id PRIMARY KEY,dataset_id,path,content_id);
    CREATE TABLE artifact_observations(artifact_id,commit_hash,availability);
    ''')
    db.executemany('INSERT INTO artifact_versions VALUES (?,?,?,?)', [
        ('current','raw',path.name,'md5:'+hashlib.md5(b'current').hexdigest()),
        ('same-name','raw',path.name,'sha256:'+hashlib.sha256(b'previous').hexdigest()),
        ('other-dataset','other',path.name,'sha256:'+hashlib.sha256(b'current').hexdigest())])
    db.execute("INSERT INTO artifact_observations VALUES ('current','commit','unavailable')")
    refresh_restored_checksums(db, {'raw':tmp_path})
    assert db.execute('SELECT * FROM artifact_checksum_aliases').fetchall() == []
    assert db.execute('SELECT * FROM artifact_observations').fetchall() == [('current','commit','unavailable')]


def test_publication_audits_target_and_indexes_pinned_archive_members(tmp_path):
    study=tmp_path/'study';source=study/'derivatives/fMRIPrep+full';source.mkdir(parents=True)
    archive=source/'sub-s03_results.zip'
    path='sub-s03/func/sub-s03_task-rest_space-T1w_desc-preproc_bold.nii.gz'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('fMRIPrep/'+path,b'bold')
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE metadata(key PRIMARY KEY,value);INSERT INTO metadata VALUES ('study_id','study');
    CREATE TABLE artifacts(path,kind);INSERT INTO artifacts VALUES ('derivatives/fMRIPrep+full','dataset:deriv');
    CREATE TABLE artifact_versions(id PRIMARY KEY,dataset_id,path,content_id);
    CREATE TABLE artifact_observations(artifact_id,commit_hash,availability);
    CREATE TABLE findings(evidence_path,evidence_json,finding_type);
    ''')
    digest='sha256:'+hashlib.sha256(archive.read_bytes()).hexdigest()
    db.executemany('INSERT INTO artifact_versions VALUES (?,?,?,?)',[('zip','deriv',archive.name,digest),('missing','study','gone.txt','sha256:missing')])
    db.executemany('INSERT INTO artifact_observations VALUES (?,?,?)',[('zip','abc','available'),('missing','abc','available')])
    evidence={'source_commit':'abc','runs':[{'run':'sub-s03_task-rest','outputs':[{'path':path,'kind':'BOLD','volumes':90,'tr':1.49}]}]}
    db.execute('INSERT INTO findings VALUES (?,?,?)',('derivatives/fMRIPrep+full+review/code/network_fmri/fmriprep-evidence.json',json.dumps(evidence),'fmriprep-output-check'))
    db.execute('INSERT INTO findings SELECT * FROM findings')
    add_publication_records(db,study)
    assert db.execute("SELECT availability FROM artifact_locations WHERE artifact_id='missing'").fetchone()==('unavailable',)
    row=db.execute('SELECT archive_id,member,details FROM archived_inputs').fetchone()
    assert row[:2]==('zip','fMRIPrep/'+path)
    assert json.loads(row[2])['archive_content_id']==digest
    assert db.execute("SELECT value FROM metadata WHERE key='publication_audited_at'").fetchone()
    # A changed archive must never be labeled with the earlier archive identity.
    archive.write_bytes(b'changed')
    import pytest
    with pytest.raises(ValueError,match='differs'):
        add_publication_records(db,study)
    archive.unlink()
    add_publication_records(db,study)
    missing=json.loads(db.execute('SELECT details FROM archived_inputs').fetchone()[0])
    assert missing['availability']=='archived_unavailable'
