from pathlib import Path

from network_fmri.qa.mriqc import default_campaign


def test_campaign_path_prefers_explicit_configuration():
    environment = {
        "NETWORK_FMRI_CAMPAIGN": "/site/campaign",
        "SCRATCH": "/site/scratch",
    }
    assert default_campaign(environment) == Path("/site/campaign")


def test_campaign_path_defaults_to_portable_scratch_location():
    assert default_campaign({"SCRATCH": "/site/scratch"}) == Path(
        "/site/scratch/mechababs_campaigns/r01network"
    )
