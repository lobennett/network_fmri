"""Normalize existing producer receipts into file-level provenance links."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from network_fmri.records.lineage import artifact_id


def file_record(dataset: str, item: dict, *, availability="historical", source_ids=None) -> dict:
    relative, content = item["path"], "sha256:" + item["sha256"]
    return {"id": artifact_id(dataset, relative, content), "dataset_id": dataset,
            "path": relative, "content_id": content, "commit": None,
            "source_ids": source_ids or {}, "availability": availability}


def transformation(stage: str, scope: str, inputs: list[dict], outputs: list[dict],
                   *, software=None, parameters=None) -> dict:
    ids = sorted({row["id"] for row in inputs + outputs})
    identity = hashlib.sha256(json.dumps([stage, scope, ids, software, parameters], sort_keys=True).encode()).hexdigest()
    return {"schema_version": 1,
            "artifacts": list({row["id"]: row for row in inputs + outputs}.values()),
            "attempts": [{"id": identity, "stage": stage, "scope": scope, "status": "success",
                          "software": software, "parameters": parameters}],
            "links": [{"input": src["id"], "output": dst["id"], "attempt": identity, "relation": stage}
                      for src in inputs for dst in outputs if src["id"] != dst["id"]]}


def collect_native_lineage(raw: Path, dataset_id: str) -> tuple[dict, ...]:
    results = []
    for path in sorted(raw.glob("sub-*/ses-*/func/*_bold.json")):
        value = json.loads(path.read_text()).get("NetworkFMRITrim")
        if value is None:
            continue
        if value.get("schema_version") != 1:
            raise ValueError("unsupported trim provenance schema")
        relative = path.relative_to(raw).as_posix().removesuffix(".json") + ".nii.gz"
        before = file_record(dataset_id, {"path": relative, "sha256": value["input_sha256"]})
        after = file_record(dataset_id, {"path": relative, "sha256": value["output_sha256"]})
        results.append(transformation("trim_dummy", relative, [before], [after],
                                      parameters={"discarded_volumes": value["discarded_volumes"]}))
    for path in sorted(raw.glob("code/network_fw2bids/conversion/*.json")):
        value = json.loads(path.read_text())
        if value.get("schema_version") != 1:
            raise ValueError("unsupported conversion provenance schema")
        for archive in value["archives"]:
            if archive.get('method') == 'cni-spiral-import':
                sources = [file_record('flywheel', {'path': 'reconstructed/' + row['file_id'] + '/' + row['name'],
                            'sha256': row['sha256']}, availability='remote',
                            source_ids={'acquisition_id': archive['acquisition_id'], 'file_id': row['file_id']})
                           for row in archive['sources']]
                outputs = [file_record(dataset_id, row) for row in archive['outputs']]
                results.append(transformation('conversion', 'sub-' + value['subject'], sources, outputs,
                                              software=archive['software'], parameters={'pfile': archive['pfile']}))
                continue
            source = file_record("flywheel", {"path": "archives/" + archive["archive_sha256"] + ".zip",
                "sha256": archive["archive_sha256"]}, availability="remote",
                source_ids={k: archive.get(k) for k in ("acquisition_id", "file_id")})
            outputs = [file_record(dataset_id, row) for row in archive["outputs"]]
            results.append(transformation("conversion", "sub-" + value["subject"], [source], outputs,
                                          software={"dcm2niix": archive.get("dcm2niix_version")}))
    for path in sorted(raw.glob("code/network_fw2bids/defacing/*.json")):
        value = json.loads(path.read_text())
        for row in value.get("images", []):
            before = file_record(dataset_id, {"path": row["path"], "sha256": row["input_sha256"]}, availability="temporary")
            after = file_record(dataset_id, {"path": row["path"], "sha256": row["output_sha256"]})
            results.append(transformation("defacing", "sub-" + value["subject"], [before], [after], software=value.get("software")))
    for path in sorted(raw.glob("sourcedata/events_qc/**/*_desc-truncation.json")):
        value = json.loads(path.read_text()).get("Provenance")
        if value is None:
            continue
        if value.get("SchemaVersion") != 1:
            raise ValueError("unsupported events provenance schema")
        inputs = [file_record("external-behavior" if row.get("external") else dataset_id, row)
                  for row in [value["Behavior"], *value["BOLDInputs"], *value["TimingSidecars"]]]
        output = file_record(dataset_id, value["Events"])
        results.append(transformation("events", value["Events"]["path"], inputs, [output]))
    return tuple(results)


def collect_surface_lineage(root: Path, dataset_id: str, raw_id: str) -> tuple[dict, ...]:
    """Link extracted surfaces only when the adapter recorded exact anatomical inputs."""
    path = root / 'code/network_fmri/surface-evidence.json'
    if not path.is_file():
        return ()
    value = json.loads(path.read_text())
    results = []
    for subject, reconstruction in value.get('reconstructions', {}).items():
        inputs = [file_record(raw_id, row) for row in reconstruction['inputs']]
        outputs = [file_record(dataset_id, {'path': f'subjects/sub-{subject}/{relative}', 'sha256': digest})
                   for relative, digest in value['inventories'][subject].items()]
        results.append(transformation('freesurfer', 'sub-' + subject, inputs, outputs,
                                      software={'FreeSurfer': reconstruction['build']},
                                      parameters={'input_datalad_commit': value.get('input_datalad_commit')}))
    return tuple(results)
