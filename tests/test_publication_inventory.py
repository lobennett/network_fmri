import hashlib
import json
import sqlite3
import zipfile
from network_fmri.records.publication import add_publication_records


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
