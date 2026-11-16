"""La décision du fog jumeau : faut-il reprendre la parcelle du voisin ?

Fonction pure (testée seule) : on lui donne ce que le fog voit, elle répond un état et sa raison.

    normal          le voisin parle (statut domaine/fog/<voisin>/status frais)
    voisin-inconnu  jamais vu le voisin depuis le démarrage : on ne reprend pas un inconnu
    partition       le voisin s'est tu chez nous MAIS parle encore au témoin cloud :
                    c'est le lien entre fogs qui est coupé, pas le voisin -> surtout pas de reprise
                    (sinon deux fogs piloteraient la même parcelle : « split-brain »)
    isole           le voisin s'est tu ET nous ne joignons plus le témoin : c'est peut-être nous
                    qui sommes coupés du monde -> pas de reprise
    reprise         le voisin s'est tu chez nous ET au témoin : il est en panne, on reprend
                    (ou il nous l'a demandé : maintenance)
"""

from dataclasses import dataclass


@dataclass
class View:
    now: float                              # heure locale (unix)
    neighbor: str
    neighbor_last: float | None = None      # dernier statut "vivant" du voisin reçu (heure locale)
    neighbor_alive: bool = True             # dernier statut reçu : alive (False = dernière volonté)
    witness_enabled: bool = True
    witness_ok: bool = False                # nous joignons le témoin (notre propre message nous revient)
    neighbor_witness_last: float | None = None   # dernier message du voisin au témoin (heure locale)
    maintenance: bool = False               # le voisin nous a demandé de reprendre sa parcelle
    dead_after: float = 30.0                # secondes de silence = voisin muet


def assess(v: View) -> tuple[str, str]:
    n = v.neighbor
    if v.maintenance:
        return "reprise", f"{n} nous a demandé de reprendre sa parcelle (maintenance)"
    if v.neighbor_last is None and v.neighbor_witness_last is None:
        return "voisin-inconnu", f"{n} jamais vu depuis le démarrage"
    silent_for = None if v.neighbor_last is None else v.now - v.neighbor_last
    if v.neighbor_alive and silent_for is not None and silent_for <= v.dead_after:
        return "normal", f"{n} en ligne (statut il y a {silent_for:.0f} s)"
    why = "a annoncé son arrêt" if not v.neighbor_alive else (
        f"muet depuis {silent_for:.0f} s" if silent_for is not None else "jamais vu chez nous")
    if v.witness_enabled:
        if not v.witness_ok:
            return "isole", f"{n} {why} et le témoin cloud ne répond pas : c'est peut-être nous qui sommes coupés"
        if v.neighbor_witness_last is not None and v.now - v.neighbor_witness_last <= v.dead_after:
            return "partition", (f"{n} {why} chez nous mais parle encore au témoin cloud : "
                                 f"lien entre fogs coupé, {n} garde sa parcelle")
        return "reprise", f"{n} {why}, le témoin cloud confirme : reprise de sa parcelle"
    return "reprise", f"{n} {why} (pas de témoin configuré) : reprise de sa parcelle"
