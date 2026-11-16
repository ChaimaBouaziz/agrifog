import os

os.environ.setdefault("ENGINE_MQTT", "0")  # pas de connexion MQTT pendant les tests
os.environ.setdefault("PUMP_SHARED", "0")  # les tests du moteur parlent directement aux vannes
