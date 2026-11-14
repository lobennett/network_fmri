"""The Slurm-spooled job must bind the explicitly selected source checkout."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize('check_only', [False, True])
def test_spooled_audit_uses_explicit_source_and_checks_import(tmp_path, check_only):
    root = Path(__file__).resolve().parents[1]
    spool = tmp_path / 'slurm-spool'
    spool.mkdir()
    script = spool / 'slurm_script'
    shutil.copyfile(root / 'scripts/run_bold_reference_audit.sh', script)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = bin_dir / 'apptainer'
    fake.write_text(f'#!{sys.executable}\n' + '''
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
assert os.environ['NETWORK_FMRI_CODE']+':/code:ro' in args, args
assert any('PYTHONPATH=/code/src' in arg for arg in args)
with Path(os.environ['CALL_LOG']).open('a') as stream:
    stream.write(json.dumps(args)+'\\n')
if '-c' in args:
    assert 'import network_fmri.bold_reference_qc' in args[args.index('-c')+1]
    print('s29')
''')
    fake.chmod(0o755)
    checksum = bin_dir / 'sha256sum'
    checksum.write_text('#!/bin/sh\ncat >/dev/null\n')
    checksum.chmod(0o755)
    manifest = tmp_path / 'inventory.json'
    manifest.write_text('{"subjects":["s29"]}')
    output = tmp_path / 'results'
    output.mkdir()
    image = tmp_path / 'image.sif'
    image.write_bytes(b'image')
    log = tmp_path / 'calls.jsonl'
    env = dict(os.environ, NETWORK_FMRI_CODE=str(root), SLURM_JOB_ID='test',
               SLURM_ARRAY_TASK_ID='0', L_SCRATCH_JOB=str(tmp_path), OAK=str(tmp_path),
               TEMPLATEFLOW_HOME=str(tmp_path), FMRIPREP_IMAGE=str(image),
               FMRIPREP_IMAGE_SHA256=hashlib.sha256(b'image').hexdigest(),
               CALL_LOG=str(log), PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
    command = ['bash', str(script), str(manifest), str(output)]
    if check_only:
        command.append('--check-only')
    result = subprocess.run(command, cwd=spool, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == (1 if check_only else 2)
    if check_only:
        assert 'AUDIT_PREFLIGHT_PASSED' in result.stdout
    else:
        assert calls[-1][calls[-1].index('-m')+1:][:2] == ['network_fmri.bold_reference_qc', 'audit']
