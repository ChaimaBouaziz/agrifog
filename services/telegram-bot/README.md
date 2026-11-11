# telegram-bot

Responsable : Chaima · Semaine 6 — alertes et commandes depuis le téléphone du fermier

Un bot **par parcelle** (Telegram n'accepte qu'un programme par bot) : Chaima crée celui de fog-a,
Ines celui de fog-b, avec le même code.

## Créer son bot (5 min, une seule fois)
1. Dans Telegram, ouvrir **@BotFather** → `/newbot` → un nom (ex. *AgriFog Tomates*) et un
   identifiant finissant par `bot` (ex. `agrifog_fog_a_bot`).
2. BotFather donne un **jeton** `123456789:AA…` : c'est le mot de passe du bot, **ne jamais le
   mettre dans Git** (ni dans un fichier du dépôt).

## Lancer
```powershell
$env:TELEGRAM_TOKEN="123456789:AA..."     # le jeton de BotFather
$env:TELEGRAM_PIN="2580"                  # un code à choisir
$env:FOG_ID="fog-a"
python -m app.main                        # depuis services\telegram-bot
```
Dans Telegram, ouvrir le bot et envoyer `/start 2580` : ce téléphone est autorisé (mémorisé dans
`data/telegram_fog-a.json`) et reçoit désormais les alertes.

## Ce qu'il fait
| Sens | Contenu |
|---|---|
| fog → téléphone | alertes du moteur (fuite, pompe, arrosage sans effet, capteur, nœud muet) et leur résolution ; quota du jour ; eau cédée par le voisin |
| téléphone → fog | `/etat`, `/alertes`, `/arroser z1 600` |
| démo | `/fuite z2 6`, `/pompe z1`, `/capteur esp-a-02 35`, `/biais esp-a-05 -8`, `/muet esp-a-09`, `/pluie 20`, `/reparer` |

Hors ligne : sans internet, les messages attendent et partent au retour de la connexion ; le reste
du fog continue de fonctionner normalement.
