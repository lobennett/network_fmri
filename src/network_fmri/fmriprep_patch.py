"""Build-time correction for the BOLD atlas initializer in fMRIPrep 25.2.5.

Run this file inside the pinned base image. The workflow itself remains upstream;
only its initial transform changes. Existing affine refinement is preserved.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path

BUILD = "25.2.5-rigidinit1"
ORIGINAL_SHA256 = "183077163f8334e8a7fab2e198a4435337949afce819865a8ff6531ec407df56"


def patch_initializer(source: str) -> str:
    """Change exactly the BOLD workflow's AI transform; reject upstream drift."""
    functions = [node for node in ast.parse(source).body
                 if isinstance(node, ast.FunctionDef)
                 and node.name == 'init_enhance_and_skullstrip_bold_wf']
    if len(functions) != 1:
        raise ValueError('expected one BOLD enhancement workflow')
    transforms = [keyword.value for node in ast.walk(functions[0])
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == 'AI' for keyword in node.keywords
                  if keyword.arg == 'transform']
    if len(transforms) != 1 or ast.literal_eval(transforms[0]) != ('Affine', 0.1):
        raise ValueError('expected the original affine BOLD initializer')
    value = transforms[0]
    lines = source.splitlines(keepends=True)
    start = sum(len(line.encode()) for line in lines[:value.lineno - 1]) + value.col_offset
    end = sum(len(line.encode()) for line in lines[:value.end_lineno - 1]) + value.end_col_offset
    encoded = source.encode()
    return (encoded[:start] + b"('Rigid', 0.1)" + encoded[end:]).decode()


def install_patch() -> None:
    """Apply to the exact pinned source and retain its build provenance."""
    import niworkflows.func.util

    versions = {name: importlib.metadata.version(name) for name in ('fmriprep', 'niworkflows')}
    if versions != {'fmriprep': '25.2.5', 'niworkflows': '1.14.4'}:
        raise ValueError(f'unexpected base package versions: {versions}')
    module = Path(niworkflows.func.util.__file__)
    source = module.read_text()
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest != ORIGINAL_SHA256:
        raise ValueError('base niworkflows source differs from the validated image')
    patched = patch_initializer(source)
    root = Path('/opt/network')
    root.mkdir(parents=True, exist_ok=True)
    (root / 'niworkflows-util-original.py').write_text(source)
    module.write_text(patched)
    receipt = {
        'schema_version': 1, 'build': BUILD, 'versions': versions,
        'module': str(module), 'original_sha256': digest,
        'patched_sha256': hashlib.sha256(patched.encode()).hexdigest(),
        'change': {'node': 'init_aff', 'before': ['Affine', 0.1], 'after': ['Rigid', 0.1]},
        'retained': ['affine refinement', 'N4 correction', 'skull stripping'],
        'validation_scan': 'sub-s03_ses-09_task-goNogo_run-1',
        'validation_job': '46199943',
    }
    (root / 'fmriprep-build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    install_patch()
