# ============================================================
#  probe_sofascore_ecuabet.py — Sondeo puntual, NO parte del
#  pipeline. Confirma si Sofascore y el widget Altenar de
#  Ecuabet responden igual desde requests plano (sin sesión
#  de navegador) como respondieron desde chrome-mcp-server.
#
#  Creado por Diego Aleman.
# ============================================================

import json

import requests

HEADERS_SOFASCORE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-EC,es;q=0.9,en;q=0.8",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "sec-ch-ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

HEADERS_ECUABET = {
    "User-Agent": HEADERS_SOFASCORE["User-Agent"],
    "Accept": "application/json",
    "Referer": "https://ecuabet.com/",
    "Origin": "https://ecuabet.com",
}


def probar(nombre: str, url: str, headers: dict) -> None:
    print(f"\n=== {nombre} ===\n{url}")
    try:
        r = requests.get(url, headers=headers, timeout=15)
        print(f"status={r.status_code}  content-type={r.headers.get('content-type')}")
        if r.status_code == 200:
            data = r.json()
            texto = json.dumps(data, ensure_ascii=False)
            print(texto[:600] + ("..." if len(texto) > 600 else ""))
        else:
            print(r.text[:300])
    except requests.RequestException as e:
        print(f"ERROR: {e}")


if __name__ == "__main__":
    # Sofascore: partidos programados hoy (categoría fútbol) — endpoint
    # confirmado por captura de red real navegando sofascore.com.
    probar(
        "Sofascore — torneos de fútbol programados hoy",
        "https://www.sofascore.com/api/v1/sport/football/scheduled-tournaments/2026-09-05",
        HEADERS_SOFASCORE,
    )

    # Ecuabet (Altenar): próximos eventos, mismo endpoint visto en la
    # captura de red del widget de deportes.
    probar(
        "Ecuabet / Altenar — próximos eventos (GetUpcoming)",
        "https://sb2frontend-altenar2.biahosted.com/api/widget/GetUpcoming"
        "?culture=es-ES&timezoneOffset=300&integration=ecuabet&deviceType=1"
        "&numFormat=en-GB&countryCode=EC&eventCount=5&sportId=0",
        HEADERS_ECUABET,
    )
