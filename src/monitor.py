import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


EP_PERMED_URL = "https://www.eppermed.eu/funding-projects/calls/"
OUTPUT_FILE = Path("data/ep_permed_calls.json")

TITOLI_DA_IGNORARE = {
    "",
    "read more",
    "learn more",
    "more information",
    "direkt zum inhalt",
    "direkt zur hauptnavigation",
    "direkt zur fußleiste",
    "joint transnational calls",
}

PERCORSI_DA_IGNORARE = {
    "/funding-projects/calls/",
    "/funding-projects/calls/joint-transnational-calls/",
}


def scarica_pagina(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; ResearchCallsMonitor/1.0; "
            "+https://github.com/fmatt68/"
            "monitoraggio-bandi-ricerca-progetti-scientifici)"
        ),
        "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
    }

    risposta = requests.get(
        url,
        headers=headers,
        timeout=30,
    )

    risposta.raise_for_status()

    print(
        "Pagina scaricata correttamente: "
        f"HTTP {risposta.status_code}"
    )

    return risposta.text


def normalizza_url(indirizzo):
    """
    Converte gli URL relativi in assoluti e rimuove:
    - frammenti come #main
    - parametri di paginazione
    - spazi indesiderati
    """

    indirizzo_assoluto = urljoin(
        EP_PERMED_URL,
        indirizzo.strip(),
    )

    elementi = urlparse(indirizzo_assoluto)

    return urlunparse(
        (
            elementi.scheme,
            elementi.netloc,
            elementi.path,
            "",
            "",
            "",
        )
    )


def pulisci_testo(testo):
    """
    Elimina spazi, ritorni a capo e tabulazioni ripetute.
    """

    return " ".join(testo.split())


def trova_titolo_della_call(link):
    """
    Ricava il titolo dal testo del collegamento.

    Se il collegamento è denominato 'Read more', cerca il titolo
    nell'articolo o nel contenitore HTML circostante.
    """

    titolo = pulisci_testo(
        link.get_text(" ", strip=True)
    )

    if titolo.lower() not in {
        "read more",
        "learn more",
        "more information",
    }:
        return titolo

    contenitore = link

    for _ in range(8):
        contenitore = contenitore.parent

        if contenitore is None:
            break

        intestazione = contenitore.find(
            ["h1", "h2", "h3", "h4", "h5", "h6"]
        )

        if intestazione:
            possibile_titolo = pulisci_testo(
                intestazione.get_text(" ", strip=True)
            )

            if possibile_titolo:
                return possibile_titolo

    return titolo


def collegamento_valido(titolo, indirizzo):
    """
    Controlla che il collegamento rappresenti una possibile call
    e non un elemento di navigazione del sito.
    """

    elementi = urlparse(indirizzo)
    percorso = elementi.path

    if elementi.netloc != "www.eppermed.eu":
        return False

    if not percorso.startswith("/funding-projects/calls/"):
        return False

    if percorso in PERCORSI_DA_IGNORARE:
        return False

    if titolo.lower() in TITOLI_DA_IGNORARE:
        return False

    if titolo.isdigit():
        return False

    if indirizzo.rstrip("/") == EP_PERMED_URL.rstrip("/"):
        return False

    return True


def estrai_calls_ep_permed(html):
    soup = BeautifulSoup(html, "html.parser")

    risultati = []
    indirizzi_visti = set()

    for link in soup.find_all("a", href=True):
        indirizzo_originale = link.get("href", "")

        if not indirizzo_originale:
            continue

        indirizzo = normalizza_url(indirizzo_originale)
        titolo = trova_titolo_della_call(link)

        if not collegamento_valido(titolo, indirizzo):
            continue

        if indirizzo in indirizzi_visti:
            continue

        indirizzi_visti.add(indirizzo)

        risultati.append(
            {
                "fonte": "EP PerMed",
                "titolo": titolo,
                "url": indirizzo,
            }
        )

    risultati.sort(
        key=lambda elemento: elemento["titolo"].lower()
    )

    return risultati


def salva_risultati(calls):
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": "EP PerMed",
        "pagina_monitorata": EP_PERMED_URL,
        "data_controllo_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "numero_risultati": len(calls),
        "calls": calls,
    }

    with OUTPUT_FILE.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            dati,
            file,
            ensure_ascii=False,
            indent=2,
        )

        file.write("\n")


def main():
    print("=" * 60)
    print("MONITORAGGIO EP PERMED")
    print("=" * 60)
    print(f"Controllo della pagina: {EP_PERMED_URL}")

    try:
        html = scarica_pagina(EP_PERMED_URL)
        calls = estrai_calls_ep_permed(html)
        salva_risultati(calls)

    except requests.RequestException as errore:
        print(f"Errore durante il download: {errore}")
        raise SystemExit(1)

    except Exception as errore:
        print(f"Errore durante il monitoraggio: {errore}")
        raise SystemExit(1)

    print(f"Risultati validi trovati: {len(calls)}")
    print(f"File creato: {OUTPUT_FILE}")
    print()

    if not calls:
        print("Nessuna call individuata.")
        return

    for numero, call in enumerate(calls, start=1):
        print(f"{numero}. {call['titolo']}")
        print(f"   {call['url']}")

    print()
    print("Monitoraggio completato correttamente.")


if __name__ == "__main__":
    main()
