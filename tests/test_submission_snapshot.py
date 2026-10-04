"""A later BABS submission must not change subjects of an already queued array."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def check(wrapper):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'code').mkdir()
        (root / 'bin').mkdir()
        fake = root / 'bin/sbatch'
        fake.write_text('#!'+sys.executable+'\nimport json,os,pathlib,sys\n'
            'pathlib.Path(os.environ["CAPTURE"]).write_text(json.dumps(sys.argv[1:]))\n'
            'print("Submitted batch job 123")\n')
        fake.chmod(0o755)
        capture = root / 'capture.json'
        env = dict(os.environ, PATH=str(root/'bin')+os.pathsep+os.environ['PATH'], CAPTURE=str(capture))
        csv = root / 'code/job_submit.csv'
        snapshots = []
        for subject in ('sub-s10', 'sub-s29'):
            csv.write_text('sub_id,task_id\n'+subject+',1\n')
            subprocess.run(['bash', str(Path(wrapper).resolve()), '--array=1-1', 'worker.sh', str(csv)],
                           env=env, check=True, capture_output=True, text=True)
            snapshots.append(Path(json.loads(capture.read_text())[-1]))
        assert snapshots[0] != snapshots[1], 'Queued arrays share a mutable subject list'
        assert snapshots[0].read_text() == 'sub_id,task_id\nsub-s10,1\n'
        assert snapshots[1].read_text() == 'sub_id,task_id\nsub-s29,1\n'
        assert snapshots[0].stat().st_mode & 0o222 == 0
    print('PASS: queued arrays retain immutable subject lists across retries')


def test_queued_subject_lists_are_immutable():
    check(Path(__file__).parents[1] / "scripts/submit_with_snapshot.sh")
