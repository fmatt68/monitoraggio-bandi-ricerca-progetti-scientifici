import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


EP_PERMED_URL = "https://www.eppermed.eu/funding-projects/calls/"
OUTPUT_FILE = Path("data/ep_permed_calls.json")


def scarica_pagina(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 research-calls-monitor/1.0 "
            "(GitHub Actions; monitoraggio bandi di ricerca)"
        )
    }

    risposta = requests.get(
        url,
        headers=headers,
        timeout=30,
    )

    risposta.raise_for_status()
    return risposta.text


def estrai_calls_ep_permed(html):
    soup = BeautifulSoup(html, "html.parser")
    risultati = []
    collegamenti_visti = set()

    for link in soup.find_all("a", href=True):
        titolo = " ".join(link.get_text(" ", strip=True).split())
        indirizzo = urljoin(EP_PERMED_URL, link["href"])

        if not titolo:
            continue

        if "/funding-projects/calls/" not in indirizzo:
            continue

        if indirizzo.rstrip("/") == EP_PERMED_URL.rstrip("/"):
            continue

        if indirizzo in collegamenti_visti:
            continue

        collegamenti_visti.add(indirizzo)

        risultati.append(
            {
                "fonte": "EP PerMed",
                "titolo": titolo,
                "url": indirizzo,
            }
        )

    return risultati


def salva_risultati(calls):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    dati = {
        "fonte": "EP PerMed",
        "pagina_monitorata": EP_PERMED_URL,
        "data_controllo_utc": datetime.now(timezone.utc).isoformat(),
        "numero_risultati": len(calls),
        "calls": calls,
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            dati,
            file,
            ensure_ascii=False,
            indent=2,
        )


def main():
    print(f"Controllo della pagina: {EP_PERMED_URL}")

    html = scarica_pagina(EP_PERMED_URL)
    calls = estrai_calls_ep_permed(html)
    salva_risultati(calls)

    print(f"Risultati trovati: {len(calls)}")
    print(f"File creato: {OUTPUT_FILE}")

    for call in calls:
        print(f"- {call['titolo']}")
        print(f"  {call['url']}")


if __name__ == "__main__":
    main()
