"""Index Oak availability and verified archive inventories without extracting them."""
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path, PurePosixPath
import zipfile


def add_publication_records(db, study):
    study = Path(study)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS artifact_locations (
      artifact_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
      location TEXT, availability TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS archived_inputs (
      archive_id TEXT NOT NULL REFERENCES artifact_versions(id),
      member TEXT NOT NULL, path TEXT NOT NULL, details TEXT NOT NULL,
      PRIMARY KEY(archive_id,member)
    );
    ''')
    roots = {identity.removeprefix('dataset:'): study / path for path,identity in
             db.execute("SELECT path,kind FROM artifacts WHERE kind LIKE 'dataset:%'")}
    roots[db.execute("SELECT value FROM metadata WHERE key='study_id'").fetchone()[0]] = study
    # Availability is audited at the publication target, not inferred from the working clone.
    db.execute('DELETE FROM artifact_locations')
    for identity,dataset,path in db.execute("SELECT DISTINCT a.id,a.dataset_id,a.path FROM artifact_versions a JOIN artifact_observations o ON a.id=o.artifact_id WHERE o.commit_hash IS NOT NULL AND o.availability IN ('available','unavailable')"):
        root = roots.get(dataset)
        present = root is not None and (root/path).is_file()
        db.execute('INSERT INTO artifact_locations VALUES (?,?,?)', (identity,None,'available' if present else 'unavailable'))
    manifest = study/'code/network_fmri/publication/file-locations.json'
    if manifest.is_file():
        for identity,record in json.loads(manifest.read_text()).items():
            original = db.execute('SELECT dataset_id,path,content_id FROM artifact_versions WHERE id=?',(identity,)).fetchone()
            if original is None or tuple(original) != tuple(record[k] for k in ('dataset_id','path','content_id')):
                raise ValueError(f'Historical identity differs: {identity}')
            location = PurePosixPath(record['location'])
            if location.is_absolute() or '..' in location.parts or '\\' in str(location):
                raise ValueError('Invalid historical location')
            db.execute('INSERT OR REPLACE INTO artifact_locations VALUES (?,?,?)',
                       (identity,str(location),'available' if (study/location).is_file() else 'unavailable'))
    db.execute('DELETE FROM archived_inputs')
    for evidence_path,evidence_json in db.execute("SELECT evidence_path,evidence_json FROM findings WHERE finding_type='fmriprep-output-check'"):
        evidence = json.loads(evidence_json)
        review = evidence_path.split('/code/',1)[0]
        source = study / review.removesuffix('+review')
        dataset = next((key for key,root in roots.items() if root==source),None)
        if dataset is None:
            continue
        runs = evidence['runs']
        if not runs:
            continue
        subject = runs[0]['run'].split('_')[0]
        archives = db.execute("SELECT DISTINCT a.id,a.path,a.content_id FROM artifact_versions a JOIN artifact_observations o ON a.id=o.artifact_id WHERE a.dataset_id=? AND o.commit_hash=? AND o.availability IN ('available','unavailable')", (dataset,evidence['source_commit'])).fetchall()
        archives = [row for row in archives if row[1].startswith(subject+'_') and row[1].endswith('.zip') and '/' not in row[1]]
        if len(archives)!=1:
            raise ValueError(f'Expected one pinned fMRIPrep archive for {subject}')
        archive_id,path,content_id = archives[0]
        outputs = {output['path']:output for run in runs for output in run['outputs']}
        outputs.update({path:{'path':path,'kind':'confounds','rows':run['confound_rows']} for run in runs for path in run.get('confounds',[])})
        if not (source/path).is_file():
            for relative,output in outputs.items():
                details = {**output,'archive_content_id':content_id,'dataset_id':dataset,
                           'archive_path':path,'commit_hash':evidence['source_commit'],
                           'availability':'archived_unavailable'}
                db.execute('INSERT INTO archived_inputs VALUES (?,?,?,?)', (archive_id,'unavailable:'+relative,relative,json.dumps(details,sort_keys=True)))
            continue
        algorithm,expected = content_id.split(':',1)
        if algorithm not in {'sha256','md5'}:
            raise ValueError('Archive has no verifiable checksum')
        print(f'Verifying published archive: {subject}',flush=True)
        with (source/path).open('rb') as stream:
            if hashlib.file_digest(stream,algorithm).hexdigest()!=expected:
                raise ValueError(f'Published archive differs from recorded version: {path}')
        with zipfile.ZipFile(source/path) as archive:
            matched = set()
            for info in archive.infolist():
                member = PurePosixPath(info.filename)
                # fMRIPrep evidence validates one top-level directory and exact subject membership.
                relative = str(PurePosixPath(*member.parts[1:]))
                if relative not in outputs:
                    continue
                if member.is_absolute() or '..' in member.parts or '\\' in info.filename or relative in matched:
                    raise ValueError('Unsafe or ambiguous fMRIPrep archive member')
                matched.add(relative)
                details = {**outputs[relative], 'size':info.file_size, 'crc32':f'{info.CRC:08x}', 'archive_content_id':content_id,
                           'dataset_id':dataset, 'archive_path':path, 'commit_hash':evidence['source_commit'],
                           'availability':'archived'}
                db.execute('INSERT INTO archived_inputs VALUES (?,?,?,?)', (archive_id,info.filename,relative,json.dumps(details,sort_keys=True)))
            if set(outputs) != matched:
                raise ValueError(f'Archive no longer contains the recorded outputs for {subject}')
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('publication_audited_at',?)", (datetime.now(timezone.utc).isoformat(),))
