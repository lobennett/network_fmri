"""Run MRIQC to the scan-review boundary, preserving all human decisions."""
from __future__ import annotations

from collections import Counter
import csv
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile
import time

from network_fmri.mriqc import prepare_mriqc_review
from network_fmri.pipeline import save_stage_result
from network_fmri.processing import ProcessingManager
from network_fmri.stages.decisions import generate_decisions


def run_mriqc(config, *, interval=300, manager=None, sleep=time.sleep):
    """Poll upstream through merge, prepare review, then stop without approving.

    Run in a Slurm controller job for unattended operation. Restarting this command
    re-reads campaign state; neither failed jobs nor existing reviews are reset.
    """
    if interval < 1:
        raise ValueError('poll interval must be at least one second')
    root = config.mechababs.study_dir
    lock = _lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('another MRIQC handoff controller is running') from error
        manager = manager or ProcessingManager(config)
        while True:
            stage = next(s for s in manager.plan() if s.stage == 'mriqc')
            print(f'mriqc: {stage.state}', flush=True)
            if stage.state == 'complete':
                break
            if stage.state not in {'ready', 'active'}:
                raise RuntimeError(f'MRIQC is {stage.state}; intervention required')
            manager.advance('mriqc')
            sleep(interval)
        evidence = prepare_mriqc_review(config)
        manifest = root / 'code/network_fmri/scan_decisions.tsv'
        created = _prepare_decisions(config, evidence.evidence_dir, manifest)
        with manifest.open(newline='') as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        flags = Counter(flag for row in rows for flag in row['flags'].split(',') if flag)
        return {
            'state': 'evidence-error' if flags.get('fd_thres_mismatch') else 'awaiting-scan-review',
            'manifest': str(manifest), 'evidence_dir': str(evidence.evidence_dir),
            'created': created, 'rows': len(rows), 'flags': dict(flags),
        }


def _prepare_decisions(config, evidence: Path, manifest: Path) -> bool:
    metadata = manifest.with_suffix('.meta.json')
    pair = (manifest, metadata)
    if any(path.is_symlink() for path in pair):
        raise RuntimeError('review files must not be symlinks')
    if manifest.exists() != metadata.exists():
        raise RuntimeError('incomplete review pair; recover it before restarting')
    if not manifest.exists():
        result = generate_decisions(config.paths.bids_dir, mriqc_dir=evidence, output=manifest)
        save_stage_result(config.mechababs.study_dir, result)
        return True

    # Generate a fresh baseline elsewhere. Compare provenance, not reviewer edits.
    # A changed input requires explicit regeneration/migration, never overwriting.
    with tempfile.TemporaryDirectory(prefix='network-review-check-') as directory:
        baseline = Path(directory) / 'scan_decisions.tsv'
        generate_decisions(config.paths.bids_dir, mriqc_dir=evidence, output=baseline)
        stored = json.loads(metadata.read_text())
        current = json.loads(baseline.with_suffix('.meta.json').read_text())
        for value in (stored, current):
            for field in ('approved_manifest_sha256', 'approved_metadata_sha256'):
                value.pop(field, None)
        if stored != current:
            raise RuntimeError('review evidence changed; existing decisions were preserved')
        review_fields = {'decision', 'approved', 'reason_code', 'reason_detail', 'reviewer', 'reviewed_at', 'notes'}
        def immutable_rows(path):
            with path.open(newline='') as stream:
                rows = csv.DictReader(stream, delimiter='\t')
                return sorted(json.dumps({k: v for k, v in row.items() if k not in review_fields},
                                         sort_keys=True) for row in rows)
        if immutable_rows(manifest) != immutable_rows(baseline):
            raise RuntimeError('review evidence changed; existing decisions were preserved')
    return False


def _lock_path(root: Path) -> Path:
    directory = subprocess.run(
        ('git', 'rev-parse', '--absolute-git-dir'), cwd=root,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return Path(directory) / 'network-fmri-handoff.lock'
