import os
import tempfile

os.environ.setdefault("TWIN_MQTT", "0")      # pas de connexion MQTT pendant les tests
os.environ.setdefault("TWIN_DATA_DIR", tempfile.mkdtemp(prefix="twin-"))
