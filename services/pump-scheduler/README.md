# pump-scheduler

Responsable : Ines · Semaine 6

**Un seul forage pour les deux parcelles** : une seule zone (de fog-a ou de fog-b) peut être arrosée à la fois.
Exclusion mutuelle entre les deux fogs, sans coordinateur, par messages MQTT retained qui traversent le pont.

```
irrigation-engine ──{fog}/pump/request──▶ pump-scheduler ──{fog}/zone/<z>/cmd──▶ vanne (ESP32)
                                               │  ▲                                  │
                         domaine/pump/claim/<fog>  │  (l'autre fog, via le pont)      │
                                               ▼  │                                  ▼
                                       pump-scheduler voisin          {fog}/zone/<z>/valve (accusé)
```

Règles (appliquées à l'identique par les deux fogs, chacun ne publie que SA demande) :
1. l'autre fog pompe (`using`, bail non expiré) → on attend ;
2. les deux attendent → le premier entré dans la file passe (`since`), égalité → ordre des noms ;
3. après un arrosage, s'il reste des zones, on repasse **en fin de file** → les fogs alternent ;
4. une demande muette depuis 20 s = fog mort → ignorée (le bail expire seul) ;
5. on attend 1,5 s après sa demande avant de pomper : la demande de l'autre a le temps d'arriver.

La pompe reste réservée jusqu'à la **fin réelle** de l'arrosage : la vanne n'exécute l'ordre qu'à son
réveil (deep sleep), puis confirme `opened`/`until`. Sans confirmation, la pompe est libérée après
1,5 × la période de réveil + la durée prévue.

La zone Wokwi (`zw`, vrai firmware en temps réel) a sa propre petite pompe : ses ordres ne passent pas ici.

## Lancer (PowerShell, depuis `services\pump-scheduler`)

```powershell
$env:FOG_ID="fog-b"; uvicorn app.main:app --port 8004
```
`http://localhost:8004/pump` : qui pompe, file d'attente, demandes du voisin, 30 derniers arrosages.

`pump.shared: false` dans `config/<fog>.yaml` (ou `PUMP_SHARED=0` pour irrigation-engine) : sans pompe commune.
