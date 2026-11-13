from agrifog_common import load_config


def test_mqtt_host_override(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "mosquitto")
    assert load_config("fog-b")["mqtt"]["host"] == "mosquitto"


def test_fallback_override_can_empty_the_list(monkeypatch):
    assert load_config("fog-b")["mqtt"]["fallback_brokers"] == ["fog-a"]
    monkeypatch.setenv("MQTT_FALLBACK", "")
    assert load_config("fog-b")["mqtt"]["fallback_brokers"] == []
