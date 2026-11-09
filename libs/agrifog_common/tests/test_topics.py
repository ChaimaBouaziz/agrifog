from agrifog_common import load_config, topics


def test_local_topics_stay_under_fog():
    assert topics.telemetry("fog-a", "z1") == "fog-a/zone/z1/telemetry"
    assert topics.cmd("fog-b", "z2").startswith("fog-b/")


def test_shared_topics_cross_bridge():
    assert topics.quota_request("fog-a").startswith("domaine/")
    assert topics.PUMP_TOKEN.startswith("domaine/")


def test_both_fog_configs_load():
    for fog in ("fog-a", "fog-b"):
        cfg = load_config(fog)
        assert cfg["fog_id"] == fog
        assert cfg["zones"]
