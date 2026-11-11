# Expériences (semaine 7)

Les simulations réutilisent **le vrai code du projet** (modèle de sol du simulateur, règles du
moteur d'irrigation, répartition du quota-negotiator) : elles testent ce qui tourne sur les fogs.

```powershell
cd experiments
pip install -r requirements.txt
python exp_irrigation.py      # 1. arrosage à heure fixe vs AgriFog, toute la saison (≈ 5 s)
python exp_quota.py           # 2. quota partagé : 50/50 vs négociation (≈ 5 s)
python charts.py              # graphiques -> results/fig1..3.png
# 3. résilience : sur le système qui tourne (Docker + simulateur + moteur lancés)
$env:FOG_ID="fog-a"; python exp_resilience.py --runs 3
```

| Expérience | Ce qu'on compare | Indicateurs |
|---|---|---|
| 1. Irrigation | AgriFog contre une dose fixe chaque jour à 7 h, dont la **plus petite dose fixe sans aucun stress** (comparaison équitable) | eau (m³, mm), drainage, jours de stress, ET relative |
| 2. Quota | même quota journalier, partagé 50/50 ou négocié (03 h demandes, 04 h répartition, 10 h cession) | ET relative et jours de stress de chaque culture, eau réellement utilisée |
| 3. Résilience | panne injectée → alerte du moteur | temps de détection et de retour à la normale (s) |

**ET relative** = transpiration réelle / transpiration sans stress : indicateur du rendement (FAO-33).

Hypothèses : zones de 200 m², vanne de 20 L/min, capteurs bruités (σ = 0,2 %), prévision de pluie
parfaite, saison avril–septembre avec la météo réelle de Sfax (`data/meteo_sfax_*.csv`).

Ce que les expériences ont fait corriger dans le système :
1. répartition du quota : chaque fog reçoit d'abord son besoin dans la limite de sa part égale
   (personne ne perd par rapport au 50/50) ;
2. avant la négociation de 4 h, le moteur se limite à sa part égale (sinon le quota était dépassé la nuit) ;
3. le « besoin » = l'eau pour finir la journée au-dessus du seuil de stress (pas de remplir le sol) ;
4. le moteur suit aussi les arrosages manuels (Telegram) : sinon fausse fuite / pompe en panne non vue ;
5. une alerte de pompe se résout dès que l'eau coule de nouveau.
