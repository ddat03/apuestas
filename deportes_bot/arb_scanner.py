# ============================================================
#  arb_scanner.py — Escáner de arbitraje / surebets entre
#  1xbet y Ecuabet (fútbol, mercado 1X2)
#
#  Creado por Diego Aleman.
#
#  Es una estrategia DISTINTA a sharp_ev.py / combo_builder.py: esos
#  módulos buscan +EV comparando contra un precio "justo" (el no-vig
#  de Pinnacle) — acá no importa si el precio es justo o no, solo que
#  la MISMA apuesta tenga cuotas distintas en dos casas donde Diego
#  realmente puede jugar. Si la suma de 1/mejor_cuota de cada resultado
#  (tomando la mejor entre las dos casas) da menos de 1, hay ganancia
#  garantizada repartiendo el stake entre ambas — sin importar quién gane.
#
#  Ambos feeds son públicos, sin API key ni login (confirmados a mano
#  el 2026-09-05 con requests plano, sin necesidad de navegador):
#    - 1xbet:   service-api/main-line-feed/v3/games1x2 (mercado 1X2,
#               groupId=1 dentro de eventGroups; type 1/2/3 = local/
#               empate/visitante)
#    - Ecuabet: widget Altenar GetTopEvents/GetUpcoming — devuelve
#               eventos + markets + odds + competitors por separado,
#               hay que cruzarlos por id.
#
#  OJO — dos límites reales, no técnicos:
#   1. Estas cuotas pueden tener minutos de retraso frente a la web en
#      vivo. Este script es una ALERTA para ir a confirmar el precio a
#      mano en cada casa antes de cargar cualquier apuesta — nunca se
#      apuesta solo con lo que imprime acá.
#   2. Las casas (sobre todo las blandas como estas dos) limitan o
#      banean cuentas que ven patrón de arbitraje sostenido. Es un
#      riesgo operativo real del negocio, no un bug de este script.
#
#  Por eso este script SOLO reporta — no ejecuta ni automatiza ninguna
#  apuesta, igual que el resto del sistema.
# ============================================================

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

import requests

import config

HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

VENTANA_SEGUNDOS = 36 * 3600   # tolerancia de kickoff entre los dos feeds
MARGEN_MINIMO    = 0.0          # 0.0 = cualquier margen positivo cuenta; subir para filtrar ruido


@dataclass
class EventoOdds:
    book: str
    home: str
    away: str
    liga: str
    start_ts: int
    local: float
    empate: float
    visitante: float
    event_id: str


@dataclass
class Surebet:
    home: str
    away: str
    liga: str
    start_ts: int
    mejor_local: tuple[str, float]
    mejor_empate: tuple[str, float]
    mejor_visitante: tuple[str, float]
    prob_implicita: float   # suma de 1/mejor_cuota — <1 = arbitraje
    margen: float            # 1 - prob_implicita


def _normalizar(nombre: str) -> str:
    n = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    for suf in (" fc", " cf", " sc", " afc", " ac", "fc ", "cf "):
        n = n.replace(suf, " ")
    return re.sub(r"\s+", " ", n).strip()


def _mismo_equipo(a: str, b: str) -> bool:
    na, nb = _normalizar(a), _normalizar(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def obtener_1xbet(count: int = 40) -> list[EventoOdds]:
    """Mercado 1X2 prepartido de fútbol, feed público de 1xbet.ec."""
    url = "https://1xbet.ec/service-api/main-line-feed/v3/games1x2"
    params = {"cfView": 3, "count": count, "country": 209, "fcountry": 209,
              "gr": 285, "grMode": 4, "lng": "es", "ref": 1}
    headers = {**HEADERS_BASE, "Referer": "https://1xbet.ec/es/line/football"}
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()

    eventos = []
    for g in r.json():
        grupo_1x2 = next((eg for eg in g.get("eventGroups", []) if eg.get("groupId") == 1), None)
        if not grupo_1x2:
            continue
        cuotas = {oc["type"]: oc["cf"] for slot in grupo_1x2["events"] for oc in slot}
        if not all(t in cuotas for t in (1, 2, 3)):
            continue
        eventos.append(EventoOdds(
            book="1xbet",
            home=g.get("opponent1", {}).get("fullName", ""),
            away=g.get("opponent2", {}).get("fullName", ""),
            liga=g.get("liga", {}).get("name", ""),
            start_ts=g.get("startTs", 0),
            local=cuotas[1], empate=cuotas[2], visitante=cuotas[3],
            event_id=str(g.get("id", "")),
        ))
    return eventos


def obtener_ecuabet(paginas: int = 10, eventos_por_pagina: int = 50) -> list[EventoOdds]:
    """Mercado 1x2 prepartido de fútbol, widget público Altenar de Ecuabet.

    Usa GetUpcoming (orden cronológico, ~1850 partidos de fútbol en
    total) en vez de GetTopEvents — ese último es una lista curada de
    solo ~7 partidos "destacados" y casi nunca cruza con el top-50 de
    1xbet. GetUpcoming paginado sí trae los mismos partidos grandes
    (Premier League, Liga MX, etc.) que 1xbet expone en su feed."""
    competidores: dict[int, str] = {}
    odds_por_id: dict[int, dict] = {}
    markets_por_id: dict[int, dict] = {}
    eventos_raw: list[dict] = []

    headers = {**HEADERS_BASE, "Referer": "https://ecuabet.com/"}
    for page in range(1, paginas + 1):
        url = "https://sb2frontend-altenar2.biahosted.com/api/widget/GetUpcoming"
        params = {
            "culture": "es-ES", "timezoneOffset": 300, "integration": "ecuabet",
            "deviceType": 1, "numFormat": "en-GB", "countryCode": "EC",
            "eventCount": eventos_por_pagina, "sportId": 66, "page": page,
        }
        r = requests.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        competidores.update({c["id"]: c["name"] for c in data.get("competitors", [])})
        odds_por_id.update({o["id"]: o for o in data.get("odds", [])})
        markets_por_id.update({m["id"]: m for m in data.get("markets", [])})
        eventos_raw.extend(data.get("events", []))
        if page >= data.get("pageCount", page):
            break

    eventos = []
    for ev in eventos_raw:
        market = next((markets_por_id[mid] for mid in ev.get("marketIds", [])
                       if mid in markets_por_id and markets_por_id[mid].get("name") == "1x2"), None)
        if not market:
            continue
        cuotas = {}
        for oid in market["oddIds"]:
            o = odds_por_id.get(oid)
            if o:
                cuotas[o["typeId"]] = o["price"]
        if not all(t in cuotas for t in (1, 2, 3)):
            continue

        comp_ids = ev.get("competitorIds", [])
        if len(comp_ids) != 2:
            continue

        try:
            start_ts = int(datetime.fromisoformat(ev["startDate"].replace("Z", "+00:00")).timestamp())
        except (KeyError, ValueError):
            start_ts = 0

        eventos.append(EventoOdds(
            book="ecuabet",
            home=competidores.get(comp_ids[0], ""),
            away=competidores.get(comp_ids[1], ""),
            liga="",
            start_ts=start_ts,
            local=cuotas[1], empate=cuotas[2], visitante=cuotas[3],
            event_id=str(ev.get("id", "")),
        ))
    return eventos


def emparejar(eventos_a: list[EventoOdds], eventos_b: list[EventoOdds]) -> list[tuple[EventoOdds, EventoOdds]]:
    """Cruza el mismo partido entre los dos feeds por nombre de equipo +
    cercanía de kickoff. Un partido de cada feed entra a lo sumo una vez."""
    pares = []
    usados_b: set[int] = set()
    for ea in eventos_a:
        for i, eb in enumerate(eventos_b):
            if i in usados_b:
                continue
            if ea.start_ts and eb.start_ts and abs(ea.start_ts - eb.start_ts) > VENTANA_SEGUNDOS:
                continue
            if _mismo_equipo(ea.home, eb.home) and _mismo_equipo(ea.away, eb.away):
                pares.append((ea, eb))
                usados_b.add(i)
                break
    return pares


def detectar_surebets(pares: list[tuple[EventoOdds, EventoOdds]],
                      margen_min: float = MARGEN_MINIMO) -> list[Surebet]:
    resultados = []
    for ea, eb in pares:
        opciones = {
            "local":      [(ea.book, ea.local),      (eb.book, eb.local)],
            "empate":     [(ea.book, ea.empate),     (eb.book, eb.empate)],
            "visitante":  [(ea.book, ea.visitante),  (eb.book, eb.visitante)],
        }
        mejores = {k: max(v, key=lambda t: t[1]) for k, v in opciones.items()}
        prob = sum(1.0 / cuota for _, cuota in mejores.values())
        margen = 1.0 - prob

        # Si las tres mejores cuotas vienen del mismo book, no hay nada
        # que combinar entre casas — no cuenta como arbitraje real.
        if margen > margen_min and len({b for b, _ in mejores.values()}) > 1:
            resultados.append(Surebet(
                home=ea.home, away=ea.away, liga=ea.liga or eb.liga,
                start_ts=ea.start_ts or eb.start_ts,
                mejor_local=mejores["local"], mejor_empate=mejores["empate"],
                mejor_visitante=mejores["visitante"],
                prob_implicita=round(prob, 4), margen=round(margen, 4),
            ))
    return sorted(resultados, key=lambda s: s.margen, reverse=True)


def reparto_stakes(s: Surebet, total: float = 100.0) -> dict[str, float]:
    """Cuánto poner en cada resultado para que la ganancia sea IGUAL
    gane quien gane — proporcional a 1/cuota, escalado a `total`."""
    partes = {"local": s.mejor_local, "empate": s.mejor_empate, "visitante": s.mejor_visitante}
    return {
        resultado: round(total * (1.0 / cuota) / s.prob_implicita, 2)
        for resultado, (_, cuota) in partes.items()
    }


def _fmt(s: Surebet) -> str:
    stakes = reparto_stakes(s)
    return (
        f"{s.home} vs {s.away}"
        + (f" ({s.liga})" if s.liga else "")
        + f"\n  Margen: {s.margen:+.2%}  (prob. implícita combinada: {s.prob_implicita:.2%})\n"
        + f"  Local     {s.mejor_local[1]:.3f}  [{s.mejor_local[0]}]   stake ${stakes['local']}\n"
        + f"  Empate    {s.mejor_empate[1]:.3f}  [{s.mejor_empate[0]}]   stake ${stakes['empate']}\n"
        + f"  Visitante {s.mejor_visitante[1]:.3f}  [{s.mejor_visitante[0]}]   stake ${stakes['visitante']}"
    )


def _telegram(texto: str) -> None:
    if not (config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto},
            timeout=10,
        )
    except requests.RequestException:
        pass


def main() -> None:
    eventos_1xbet  = obtener_1xbet()
    eventos_ecuabet = obtener_ecuabet()
    print(f"1xbet: {len(eventos_1xbet)} eventos con 1X2 | Ecuabet: {len(eventos_ecuabet)} eventos con 1X2")

    pares = emparejar(eventos_1xbet, eventos_ecuabet)
    print(f"Partidos cruzados entre ambas casas: {len(pares)}")

    surebets = detectar_surebets(pares)
    if not surebets:
        print("Sin surebets ahora mismo (normal — no siempre hay).")
        return

    print(f"\n{len(surebets)} posible(s) surebet(s):\n")
    aviso = [f"🎯 {len(surebets)} surebet(s) 1xbet↔Ecuabet — verificar en vivo antes de apostar\n"]
    for s in surebets:
        texto = _fmt(s)
        print(texto, "\n")
        aviso.append(texto)
    _telegram("arb_scanner · " + datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\n" + "\n\n".join(aviso[1:]))


if __name__ == "__main__":
    main()
