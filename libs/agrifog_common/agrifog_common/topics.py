"""MQTT topic contract — keep in sync with docs/contracts.md."""

SHARED_ROOT = "domaine"


# --- local topics (stay on the fog's own broker) ---
def telemetry(fog: str, zone: str) -> str:
    return f"{fog}/zone/{zone}/telemetry"


def cmd(fog: str, zone: str) -> str:
    return f"{fog}/zone/{zone}/cmd"


def decision(fog: str, zone: str) -> str:
    return f"{fog}/zone/{zone}/decision"


def heartbeat(fog: str, device: str) -> str:
    return f"{fog}/device/{device}/heartbeat"


def ota(fog: str, device: str) -> str:
    return f"{fog}/device/{device}/ota"


def alert(fog: str) -> str:
    return f"{fog}/alert"


# --- shared topics (cross the bridge) ---
def fog_status(fog: str) -> str:
    return f"{SHARED_ROOT}/fog/{fog}/status"


def air(fog: str) -> str:
    return f"{SHARED_ROOT}/air/{fog}"


def quota_request(fog: str) -> str:
    return f"{SHARED_ROOT}/quota/request/{fog}"


QUOTA_ALLOCATION = f"{SHARED_ROOT}/quota/allocation"
PUMP_TOKEN = f"{SHARED_ROOT}/pump/token"
