import json
import os
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
    """
    Scarica la pagina delle call EP PerMed.
    """

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
    Converte un URL relativo in un URL assoluto.

    Rimuove inoltre:
    - frammenti come #main;
    - parametri di paginazione;
    - spazi iniziali o finali.
    """

    indirizzo_assoluto = urljoin(
        EP_PERMED_URL,
        indirizzo.strip(),
    )

    elementi = urlparse(indirizzo_assoluto)

    percorso = elementi.path

    if not percorso.endswith("/"):
        percorso = f"{percorso}/"

    return urlunparse(
        (
            elementi.scheme.lower(),
            elementi.netloc.lower(),
            percorso,
            "",
            "",
            "",
        )
    )


def pulisci_testo(testo):
    """
    Elimina spazi, tabulazioni e ritorni a capo ripetuti.
    """

    return " ".join(testo.split())


def trova_titolo_della_call(link):
    """
    Ricava il titolo della call.

    Se il collegamento è denominato 'Read more', cerca il titolo
    nelle intestazioni HTML del contenitore circostante.
    """

    titolo = pulisci_testo(
        link.get_text(" ", strip=True)
    )

    testi_generici = {
        "read more",
        "learn more",
        "more information",
    }

    if titolo.lower() not in testi_generici:
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

            if (
                possibile_titolo
                and possibile_titolo.lower() not in testi_generici
            ):
                return possibile_titolo

    return titolo


def collegamento_valido(titolo, indirizzo):
    """
    Esclude menu, ancore interne, paginazione e collegamenti
    che non rappresentano pagine relative alle call.
    """

    elementi = urlparse(indirizzo)
    percorso = elementi.path

    if elementi.netloc not in {
        "eppermed.eu",
        "www.eppermed.eu",
    }:
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
    """
    Estrae le call dalla pagina EP PerMed e rimuove i duplicati.
    """

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
        key=lambda elemento: (
            elemento["titolo"].lower(),
            elemento["url"],
        )
    )

    return risultati


def carica_calls_precedenti():
    """
    Legge il file JSON già presente nel repository.

    Se il file non esiste o non è valido, restituisce
    un elenco vuoto.
    """

    if not OUTPUT_FILE.exists():
        print("Nessun archivio precedente trovato.")
        return []

    try:
        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            dati = json.load(file)

        calls = dati.get("calls", [])

        if not isinstance(calls, list):
            print(
                "Il campo 'calls' dell'archivio precedente "
                "non è un elenco."
            )
            return []

        print(
            "Risultati presenti nell'archivio precedente: "
            f"{len(calls)}"
        )

        return calls

    except (OSError, json.JSONDecodeError) as errore:
        print(
            "Impossibile leggere l'archivio precedente: "
            f"{errore}"
        )

        return []


def identifica_nuove_calls(calls_precedenti, calls_correnti):
    """
    Confronta gli URL correnti con quelli già archiviati.
    """

    url_precedenti = {
        call.get("url")
        for call in calls_precedenti
        if call.get("url")
    }

    return [
        call
        for call in calls_correnti
        if call["url"] not in url_precedenti
    ]


def identifica_calls_rimosse(calls_precedenti, calls_correnti):
    """
    Identifica le call non più presenti nella pagina principale.
    Non le elimina dallo storico, ma le segnala nel registro.
    """

    url_correnti = {
        call.get("url")
        for call in calls_correnti
        if call.get("url")
    }

    return [
        call
        for call in calls_precedenti
        if call.get("url")
        and call["url"] not in url_correnti
    ]


def salva_risultati(calls):
    """
    Salva un JSON stabile.

    Non viene inserita la data di esecuzione perché una data
    variabile produrrebbe un nuovo commit a ogni controllo.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": "EP PerMed",
        "pagina_monitorata": EP_PERMED_URL,
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
            sort_keys=False,
        )

        file.write("\n")


def aggiungi_riepilogo_github(
    calls_correnti,
    nuove_calls,
    calls_rimosse,
):
    """
    Inserisce un riepilogo nella pagina dell'esecuzione
    di GitHub Actions.
    """

    percorso_riepilogo = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if not percorso_riepilogo:
        return

    righe = [
        "# Monitoraggio EP PerMed",
        "",
        f"Call rilevate: **{len(calls_correnti)}**",
        "",
        f"Nuove call: **{len(nuove_calls)}**",
        "",
        f"Call non più presenti nella pagina: "
        f"**{len(calls_rimosse)}**",
        "",
    ]

    if nuove_calls:
        righe.extend(
            [
                "## Nuove call",
                "",
            ]
        )

        for call in nuove_calls:
            righe.append(
                f"- {call['url']}"
            )

        righe.append("")

    if calls_rimosse:
        righe.extend(
            [
                "## Call non più presenti nella pagina",
                "",
            ]
        )

        for call in calls_rimosse:
            righe.append(
                f"- {call['url']}"
            )

        righe.append("")

    if not nuove_calls and not calls_rimosse:
        righe.extend(
            [
                "Nessuna variazione rispetto "
                "all'esecuzione precedente.",
                "",
            ]
        )

    with open(
        percorso_riepilogo,
        "a",
        encoding="utf-8",
    ) as file:
        file.write("\n".join(righe))


def stampa_elenco(titolo_sezione, calls):
    """
    Stampa nel log un elenco numerato di call.
    """

    print()
    print(titolo_sezione)
    print("-" * len(titolo_sezione))

    if not calls:
        print("Nessun risultato.")
        return

    for numero, call in enumerate(calls, start=1):
        print(f"{numero}. {call['titolo']}")
        print(f"   {call['url']}")


def main():
    print("=" * 60)
    print("MONITORAGGIO EP PERMED")
    print("=" * 60)
    print(f"Controllo della pagina: {EP_PERMED_URL}")

    calls_precedenti = carica_calls_precedenti()

    try:
        html = scarica_pagina(EP_PERMED_URL)
        calls_correnti = estrai_calls_ep_permed(html)

    except requests.RequestException as errore:
        print(f"Errore durante il download: {errore}")
        raise SystemExit(1)

    except Exception as errore:
        print(f"Errore durante il monitoraggio: {errore}")
        raise SystemExit(1)

    if not calls_correnti:
        print(
            "Errore: non è stata individuata alcuna call. "
            "Il file precedente non verrà sovrascritto."
        )
        raise SystemExit(1)

    nuove_calls = identifica_nuove_calls(
        calls_precedenti,
        calls_correnti,
    )

    calls_rimosse = identifica_calls_rimosse(
        calls_precedenti,
        calls_correnti,
    )

    salva_risultati(calls_correnti)

    print()
    print(f"Risultati validi trovati: {len(calls_correnti)}")
    print(f"Nuove call rilevate: {len(nuove_calls)}")
    print(
        "Call non più presenti nella pagina: "
        f"{len(calls_rimosse)}"
    )
    print(f"File aggiornato: {OUTPUT_FILE}")

    stampa_elenco(
        "ELENCO CORRENTE",
        calls_correnti,
    )

    stampa_elenco(
        "NUOVE CALL",
        nuove_calls,
    )

    stampa_elenco(
        "CALL NON PIÙ PRESENTI",
        calls_rimosse,
    )

    aggiungi_riepilogo_github(
        calls_correnti,
        nuove_calls,
        calls_rimosse,
    )

    print()
    print("Monitoraggio completato correttamente.")


if __name__ == "__main__":
    main()
