# Expériences de la plateforme (semaine 7, Ines)

Le vrai code de pump-scheduler, fog-twin et ota-manager, avec horloge et réseau simulés (≈ 3 s en tout,
sans Docker ni broker) :

```powershell
python exp_pump.py           # 4. pompe commune : sans coordination vs pump-scheduler, latence du pont
python exp_twin.py           # 5. fog jumeau : décisions face aux pannes, reprises à tort avec / sans témoin
python exp_ota.py            # 6. OTA : tout d'un coup vs canary + rollback, firmwares sain / crash / sensor
python charts_platform.py    # graphiques -> results/fig4_pompe.png, fig5_jumeau.png, fig6_ota.png
```

| Expérience | Ce qu'on compare | Indicateurs |
|---|---|---|
| 4. Pompe commune | sans coordination, pump-scheduler (pont de 0,1 à 3 s), sans l'attente de 1,5 s, vannes réveillées plus souvent | minutes où deux fogs pompent ensemble, durée du tour, attente, pompe réservée à vide |
| 5. Fog jumeau | panne, lien coupé, fog isolé × avec / sans témoin ; 24 h de coupures brèves du pont × seuil de silence | bonne décision, temps de détection, reprises à tort (split-brain) |
| 6. OTA | tout d'un coup vs canary sur 10 ESP32, firmware sain / qui plante / qui ne mesure plus | ESP32 exposés, heures de parcelle sans mesure, délai pour une flotte saine |

Ce que ces expériences montrent (et ont fait découvrir) :
1. l'attente de 1,5 s avant de pomper est indispensable : sans elle, ou si le pont met plus de 1,5 s,
   les deux fogs démarrent ensemble certains matins ;
2. la pompe reste réservée pendant que la vanne dort (deep sleep) : ~45 % du tour à vide ; réveiller
   les vannes plus souvent aux heures fraîches ramène ce temps à ~24 % (piste d'amélioration) ;
3. sans témoin cloud, chaque coupure du pont plus longue que le seuil déclenche une reprise à tort ;
   avec le témoin : 0, quel que soit le seuil -> on peut détecter une vraie panne vite sans risque ;
4. le canary coûte une minute d'observation sur un firmware sain, mais limite un mauvais firmware à
   un seul ESP32 ; un firmware « qui ne mesure plus » n'est vu que par le canary (le bootloader ne voit rien).
