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
    """Ordre de mise à jour (ota-manager -> ESP32) : id, version, url, sha256, size."""
    return f"{fog}/device/{device}/ota"


def ota_status(fog: str, device: str) -> str:
    """Avancement de la mise à jour (ESP32 -> ota-manager) : downloading, installed, failed…"""
    return f"{fog}/device/{device}/ota/status"


def alert(fog: str) -> str:
    return f"{fog}/alert"


def valve(fog: str, zone: str) -> str:
    """Valve device acknowledges a command: actual open time and until."""
    return f"{fog}/zone/{zone}/valve"


def weather_forecast(fog: str) -> str:
    return f"{fog}/weather/forecast"


def sim_fault(fog: str) -> str:
    """Fault injection (simulator only): leak, pump_fail, sensor_stuck..."""
    return f"{fog}/sim/fault"


# --- shared topics (cross the bridge) ---
def fog_status(fog: str) -> str:
    return f"{SHARED_ROOT}/fog/{fog}/status"


def air(fog: str) -> str:
    return f"{SHARED_ROOT}/air/{fog}"


def quota_request(fog: str) -> str:
    return f"{SHARED_ROOT}/quota/request/{fog}"


def quota_allocation(fog: str) -> str:
    """Allocation computed by `fog` (both fogs compute and publish the same one)."""
    return f"{SHARED_ROOT}/quota/allocation/{fog}"


def quota_release(fog: str) -> str:
    """Liters given back by `fog` to its neighbour for today."""
    return f"{SHARED_ROOT}/quota/release/{fog}"


# --- local quota topics ---
def quota_budget(fog: str) -> str:
    return f"{fog}/quota/budget"


def quota_usage(fog: str) -> str:
    return f"{fog}/quota/usage"


PUMP_TOKEN = f"{SHARED_ROOT}/pump/token"


# --- federated learning (shared) ---
FL_GLOBAL = f"{SHARED_ROOT}/fl/global"    # retained : modèle global du tour en cours


def fl_update(fog: str) -> str:
    return f"{SHARED_ROOT}/fl/update/{fog}"
