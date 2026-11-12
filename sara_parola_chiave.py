"""Parola chiave SARA nel cloud: gira su GitHub Actions ogni 5 minuti, anche a PC e portale spenti.

Chi scrive SARA (o GIOCO) sotto un post o un Reel della Pagina riceve in privato la formula preimpostata
(regalo + link del libro) e sotto il commento la risposta «Ti ho scritto in privato! 🦕».

Niente database: un commento è «già fatto» se sotto c'è già una risposta della Pagina. Così cloud e portale
non si pestano i piedi e nessuno riceve due messaggi. Solo libreria standard di Python, niente pacchetti.

Variabili d'ambiente (su GitHub sono «secrets», mai scritte nel codice):
    META_PAGE_TOKEN   codice della Pagina (lo stesso di dati/meta.json)
    META_PAGE_ID      id della Pagina Facebook
    META_IG_USER_ID   id del profilo Instagram (facoltativo)
    PROVA=1           mostra quanti commenti tratterebbe, senza inviare niente

I log di un archivio pubblico li vede chiunque: qui si stampano solo numeri, mai nomi o testi.
I testi devono restare uguali a stampa/portale/parola_chiave.py (un test lo controlla).
"""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

GRAPH = "https://graph.facebook.com"
PAROLE = ("sara", "gioco")
MESSAGGIO = ("Grazie per aver giocato con Sara! 🦕 Ecco il tuo attestato Amico di Sara: "
             "https://linktr.ee/consaragiocaeimpara. Se vuoi altri giochi da fare insieme, il libro è qui: "
             "https://www.amazon.it/dp/B0HN9BKTJ7")
RISPOSTA_PUBBLICA = "Ti ho scritto in privato! 🦕"
FINESTRA = timedelta(days=7)   # Meta: risposta privata entro 7 giorni dal commento
GIORNI_POST = 30


class ErroreMeta(RuntimeError):
    pass


def _chiama(metodo: str, percorso: str, token: str, params: dict | None = None, corpo: dict | None = None) -> dict:
    query = urllib.parse.urlencode({**(params or {}), "access_token": token})
    dati = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(f"{GRAPH}/{percorso}?{query}", data=dati, method=metodo,
                                 headers={"Content-Type": "application/json"} if dati else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {})
        except ValueError:
            err = {}
        raise ErroreMeta(f"{err.get('code', e.code)}/{err.get('error_subcode', '')}: {err.get('message', '')[:120]}")


def ha_parola(testo: str) -> bool:
    """SARA, sara, «Sara!», «sara 🦕» sì; «Sarah», «saranno», «giocone» no."""
    t = unicodedata.normalize("NFKD", testo or "").encode("ascii", "ignore").decode().lower()
    return any(re.search(rf"(?<![a-z0-9]){p}(?![a-z0-9])", t) for p in PAROLE)


def _data(testo: str) -> datetime:
    return datetime.strptime(testo, "%Y-%m-%dT%H:%M:%S%z")


def commenti_da_fare(page_id: str, ig_id: str, leggi, adesso: datetime) -> list[dict]:
    """Commenti con la parola chiave, degli ultimi 7 giorni, senza ancora una risposta della Pagina."""
    da = adesso - timedelta(days=GIORNI_POST)
    trovati = []
    post = leggi(f"{page_id}/posts", {"since": str(int(da.timestamp())), "limit": "50",
                 "fields": "id,comments.limit(100){id,message,created_time,from,comments.limit(25){from}}"})
    for p in post.get("data", []):
        for c in (p.get("comments") or {}).get("data", []):
            if (c.get("from") or {}).get("id") == page_id:
                continue
            risposto = any((r.get("from") or {}).get("id") == page_id for r in (c.get("comments") or {}).get("data", []))
            trovati.append({"id": c["id"], "piattaforma": "facebook", "testo": c.get("message", ""),
                            "quando": _data(c["created_time"]), "risposto": risposto})
    if ig_id:
        mio = leggi(ig_id, {"fields": "username"}).get("username", "")
        media = leggi(f"{ig_id}/media", {"limit": "50",
                      "fields": "id,timestamp,comments.limit(100){id,text,timestamp,username,replies{username}}"})
        for m in media.get("data", []):
            if _data(m["timestamp"]) < da:
                continue
            for c in (m.get("comments") or {}).get("data", []):
                if c.get("username") == mio:
                    continue
                risposto = any(r.get("username") == mio for r in (c.get("replies") or {}).get("data", []))
                trovati.append({"id": c["id"], "piattaforma": "instagram", "testo": c.get("text", ""),
                                "quando": _data(c["timestamp"]), "risposto": risposto})
    return [c for c in trovati
            if adesso - c["quando"] <= FINESTRA and not c["risposto"] and ha_parola(c["testo"])]


def rispondi(page_id: str, c: dict, scrivi) -> None:
    """Prima il messaggio privato, poi la risposta pubblica (che segna il commento come fatto)."""
    try:
        scrivi(f"{page_id}/messages", {"recipient": {"comment_id": c["id"]}, "message": {"text": MESSAGGIO}})
    except ErroreMeta as e:
        # 10900 / «already replied»: il privato era già partito (es. dal portale), manca solo la risposta pubblica
        if "10900" not in str(e) and "already" not in str(e).lower():
            raise
    sotto = "comments" if c["piattaforma"] == "facebook" else "replies"
    scrivi(f"{c['id']}/{sotto}", {"message": RISPOSTA_PUBBLICA})


def giro(page_id: str, ig_id: str, leggi, scrivi, invia: bool = True, adesso: datetime | None = None) -> dict:
    adesso = adesso or datetime.now(timezone.utc)
    nuovi = commenti_da_fare(page_id, ig_id, leggi, adesso)
    esito = {"trovati": len(nuovi), "inviati": 0, "errori": []}
    if not invia:
        return esito
    for c in nuovi:
        try:
            rispondi(page_id, c, scrivi)
            esito["inviati"] += 1
        except ErroreMeta as e:
            esito["errori"].append(f"{c['piattaforma']}: {e}")
    return esito


def main() -> int:
    token = os.environ.get("META_PAGE_TOKEN", "").strip()
    page_id = os.environ.get("META_PAGE_ID", "").strip()
    ig_id = os.environ.get("META_IG_USER_ID", "").strip()
    if not token or not page_id:
        print("Mancano META_PAGE_TOKEN o META_PAGE_ID nei secrets: non faccio niente.")
        return 1
    leggi = lambda percorso, params: _chiama("GET", percorso, token, params)          # noqa: E731
    scrivi = lambda percorso, corpo: _chiama("POST", percorso, token, corpo=corpo)    # noqa: E731
    prova = os.environ.get("PROVA") == "1"
    try:
        esito = giro(page_id, ig_id, leggi, scrivi, invia=not prova)
    except ErroreMeta as e:
        print(f"Errore di Meta in lettura: {e}")
        return 1
    print(f"{'PROVA - ' if prova else ''}commenti con SARA da servire: {esito['trovati']}, inviati: {esito['inviati']}, "
          f"errori: {len(esito['errori'])}")
    for e in esito["errori"]:
        print(f"  errore {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
