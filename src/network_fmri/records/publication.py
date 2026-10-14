"""Index Oak availability and verified archive inventories without extracting them."""
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path, PurePosixPath
import zipfile


def refresh_restored_checksums(db, roots):
    """Join restored annex MD5 identities to SHA256 receipts using verified bytes."""
    db.execute('''CREATE TABLE IF NOT EXISTS artifact_checksum_aliases (
        original_id TEXT PRIMARY KEY REFERENCES artifact_versions(id),
        canonical_id TEXT NOT NULL REFERENCES artifact_versions(id)
    )''')
    restored = db.execute("""SELECT DISTINCT a.id,a.dataset_id,a.path,a.content_id
        FROM artifact_versions a JOIN artifact_observations o ON o.artifact_id=a.id
        WHERE a.content_id LIKE 'md5:%' AND o.commit_hash IS NOT NULL
          AND o.availability IN ('available','unavailable')
          AND EXISTS (SELECT 1 FROM artifact_versions s WHERE s.dataset_id=a.dataset_id
                      AND s.path=a.path AND s.content_id LIKE 'sha256:%')""").fetchall()
    for identity, dataset, relative, recorded in restored:
        root = roots.get(dataset)
        path = PurePosixPath(relative)
        if path.is_absolute() or '..' in path.parts or '\\' in relative:
            raise ValueError('Invalid restored file path')
        if root is None or not (root / path).is_file():
            continue
        md5, sha = hashlib.md5(), hashlib.sha256()
        with (root / path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                md5.update(chunk)
                sha.update(chunk)
        if recorded != 'md5:' + md5.hexdigest():
            raise ValueError(f'Restored file differs from recorded annex identity: {relative}')
        match = db.execute('SELECT id FROM artifact_versions WHERE dataset_id=? AND path=? AND content_id=?',
                           (dataset, relative, 'sha256:' + sha.hexdigest())).fetchone()
        if match is None:
            continue  # Filename similarity alone cannot establish equivalence.
        canonical = match[0]
        commits = db.execute("SELECT DISTINCT commit_hash FROM artifact_observations WHERE artifact_id=? AND commit_hash IS NOT NULL AND availability IN ('available','unavailable')", (identity,)).fetchall()
        for (commit,) in commits:
            db.execute("INSERT INTO artifact_observations SELECT ?,?,'available' WHERE NOT EXISTS (SELECT 1 FROM artifact_observations WHERE artifact_id=? AND commit_hash=? AND availability='available')",
                       (canonical, commit, canonical, commit))
        # Preserve the original observation and both hashes; only the exact SHA
        # version appears in current-file lists and can match its privacy receipt.
        for (commit,) in commits:
            db.execute("INSERT INTO artifact_observations SELECT ?,?,'checksum_alias' WHERE NOT EXISTS (SELECT 1 FROM artifact_observations WHERE artifact_id=? AND commit_hash=? AND availability='checksum_alias')",
                       (identity, commit, identity, commit))
        db.execute("DELETE FROM artifact_observations WHERE artifact_id=? AND commit_hash IS NOT NULL AND availability IN ('available','unavailable')", (identity,))
        db.execute('INSERT OR REPLACE INTO artifact_checksum_aliases VALUES (?,?)', (identity, canonical))


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
    refresh_restored_checksums(db, roots)
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
    inventoried = {}
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
        identity = (archive_id, evidence['source_commit'])
        if identity in inventoried:
            if inventoried[identity] != outputs:
                raise ValueError(f'Conflicting output inventories for {subject}')
            continue
        inventoried[identity] = outputs
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
    db.execute("INSERT OR REPLACE INTO metadata VALUES ('publication_code_sha256',?)", (hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),))
