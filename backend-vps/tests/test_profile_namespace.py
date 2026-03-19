from study_agents.profile_namespace import (
    build_profile_output_dir,
    compose_group_id,
    infer_profile_id_from_group_id,
    normalize_profile_id,
)


def test_normalize_profile_id():
    assert normalize_profile_id(" Texas Homeowners ") == "texas-homeowners"
    assert normalize_profile_id("abc_123") == "abc_123"


def test_compose_group_id():
    gid = compose_group_id("tx-homeowners", "web_research", "source-doc")
    assert gid == "profile:tx-homeowners:web_research:source-doc"


def test_infer_profile_id_from_group_id():
    assert infer_profile_id_from_group_id("profile:tx-homeowners:web_research:x") == "tx-homeowners"
    assert infer_profile_id_from_group_id("scenario:abc") == "scenario"


def test_build_profile_output_dir(tmp_path):
    out = build_profile_output_dir(tmp_path, "tx-homeowners", "rag_build", "run01")
    assert out == tmp_path / "profiles" / "tx-homeowners" / "rag_build" / "run01"
