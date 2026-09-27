"""The index must retain the configured cohort even before subjects are ingested."""
import json
import sqlite3
from types import SimpleNamespace
from tests.records.test_database import records


def test_index_keeps_expected_subjects_not_just_ingested_entities(tmp_path,monkeypatch):
    import network_fmri.records as module
    study=tmp_path/'study';raw=study/'sourcedata/raw';raw.mkdir(parents=True)
    config=SimpleNamespace(subjects=('s03','s10'),mechababs=SimpleNamespace(study_dir=study,raw_slot='raw'))
    monkeypatch.setattr(module,'collect_study',lambda *a,**kw: records())
    monkeypatch.setattr(module,'study_datasets',lambda *a:[('raw',raw)])
    monkeypatch.setattr(module,'inventory_dataset',lambda *a:{'artifacts':[],'attempts':[],'links':[]})
    monkeypatch.setattr(module,'collect_surface_lineage',lambda *a:[])
    monkeypatch.setattr(module,'ProcessingManager',lambda *a:SimpleNamespace(status=lambda:SimpleNamespace(stages=[])))
    monkeypatch.setattr(module,'collect_attempts',lambda *a:())
    monkeypatch.setattr(module,'read_history',lambda *a:((),{'artifacts':[],'attempts':[],'links':[]}))
    output=tmp_path/'records.sqlite';module.build_index(config,output)
    with sqlite3.connect(output) as db:
        value=db.execute("SELECT value FROM metadata WHERE key='expected_subjects'").fetchone()
        assert value is not None
        assert json.loads(value[0])==['s03','s10']
