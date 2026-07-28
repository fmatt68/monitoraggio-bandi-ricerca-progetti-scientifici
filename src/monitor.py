import json
import os
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup


FONTE = "EP PerMed"

EP_PERMED_URL = (
    "https://www.eppermed.eu/funding-projects/calls/"
)

OUTPUT_FILE = Path("data/ep_permed_calls.json")

REQUEST_DELAY_SECONDS = 0.4


# Termini che indicano una pertinenza oncologica diretta.
TERMINI_ONCOLOGICI = {
    # Inglese
    "cancer",
    "cancers",
    "oncology",
    "oncological",
    "oncologic",
    "onco-haematology",
    "onco-hematology",
    "oncohaematology",
    "oncohematology",
    "tumor",
    "tumors",
    "tumour",
    "tumours",
    "neoplasm",
    "neoplasms",
    "neoplastic",
    "malignancy",
    "malignancies",
    "malignant",
    "carcinoma",
    "carcinomas",
    "carcinogenesis",
    "oncogenesis",
    "sarcoma",
    "sarcomas",
    "leukemia",
    "leukaemia",
    "lymphoma",
    "lymphomas",
    "myeloma",
    "metastasis",
    "metastases",
    "metastatic",
    "melanoma",
    "melanomas",
    "glioma",
    "gliomas",
    "glioblastoma",
    "mesothelioma",
    "neuroblastoma",
    "retinoblastoma",
    "medulloblastoma",

    # Italiano
    "cancro",
    "tumore",
    "tumori",
    "oncologia",
    "oncologico",
    "oncologica",
    "oncologici",
    "oncologiche",
    "neoplasia",
    "neoplasie",
    "neoplastico",
    "neoplastica",
    "neoplastici",
    "neoplastiche",
    "carcinomi",
    "sarcomi",
    "leucemia",
    "linfoma",
    "linfomi",
    "mieloma",
    "metastasi",
}


# Termini trasversali che possono indicare una possibile
# pertinenza oncologica.
#
# Una call viene classificata "da_verificare" soltanto se
# contiene almeno due termini distinti di questo gruppo.
TERMINI_DA_VERIFICARE = {
    "precision medicine",
    "personalised medicine",
    "personalized medicine",
    "precision oncology",
    "immunotherapy",
    "immunotherapies",
    "cell therapy",
    "cell therapies",
    "gene therapy",
    "gene therapies",
    "advanced therapy",
    "advanced therapies",
    "biomarker",
    "biomarkers",
    "liquid biopsy",
    "liquid biopsies",
    "circulating tumour dna",
    "circulating tumor dna",
    "genomic profiling",
    "molecular profiling",
    "early detection",
    "screening",
}


TESTI_DA_IGNORARE = {
    "",
    "read more",
    "learn more",
    "more information",
    "direkt zum inhalt",
    "direkt zur hauptnavigation",
    "direkt zur fussleiste",
    "joint transnational calls",
}


PERCORSI_DA_IGNORARE = {
    "/funding-projects/calls/",
    "/funding-projects/calls/joint-transnational-calls/",
}


def crea_sessione():
    """
    Crea una sessione HTTP riutilizzabile.
    """

    sessione = requests.Session()

    sessione.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 "
                "(compatible; ResearchCallsMonitor/1.0; "
                "+https://github.com/fmatt68/"
                "monitoraggio-bandi-ricerca-"
                "progetti-scientifici)"
            ),
            "Accept-Language": (
                "en-US,en;q=0.9,it;q=0.8"
            ),
        }
    )

    return sessione


def scarica_pagina(sessione, url):
    """
    Scarica una pagina e interrompe il programma
    in caso di errore HTTP.
    """

    risposta = sessione.get(
        url,
        timeout=30,
    )

    risposta.raise_for_status()

    print(
        f"Pagina scaricata: "
        f"HTTP {risposta.status_code} - {url}"
    )

    return risposta.text


def pulisci_testo(testo):
    """
    Elimina spazi, tabulazioni e ritorni a capo ripetuti.
    """

    return " ".join(testo.split())


def normalizza_testo(testo):
    """
    Trasforma il testo in una forma adatta al confronto:

    - converte in minuscolo;
    - elimina gli accenti;
    - normalizza i trattini;
    - elimina spazi ripetuti.
    """

    testo = unicodedata.normalize(
        "NFKD",
        testo,
    )

    testo = "".join(
        carattere
        for carattere in testo
        if not unicodedata.combining(carattere)
    )

    testo = testo.lower()

    testo = testo.replace("–", "-")
    testo = testo.replace("—", "-")

    return pulisci_testo(testo)


def normalizza_url(indirizzo):
    """
    Converte gli URL relativi in URL assoluti.

    Rimuove inoltre:
    - parametri;
    - frammenti;
    - ancore interne.
    """

    assoluto = urljoin(
        EP_PERMED_URL,
        indirizzo.strip(),
    )

    elementi = urlparse(assoluto)

    percorso = elementi.path or "/"

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


def trova_titolo(link):
    """
    Ricava il titolo associato a un collegamento.

    Se il testo del collegamento è generico, per esempio
    'Read more', cerca il titolo nelle intestazioni HTML
    del contenitore circostante.
    """

    titolo = pulisci_testo(
        link.get_text(
            " ",
            strip=True,
        )
    )

    testi_generici = {
        "read more",
        "learn more",
        "more information",
    }

    if normalizza_testo(titolo) not in testi_generici:
        return titolo

    contenitore = link

    for _ in range(8):
        contenitore = contenitore.parent

        if contenitore is None:
            break

        intestazione = contenitore.find(
            [
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
            ]
        )

        if intestazione:
            possibile_titolo = pulisci_testo(
                intestazione.get_text(
                    " ",
                    strip=True,
                )
            )

            if possibile_titolo:
                return possibile_titolo

    return titolo


def collegamento_valido(titolo, indirizzo):
    """
    Verifica che il collegamento rappresenti una pagina
    EP PerMed potenzialmente relativa a una call.
    """

    elementi = urlparse(indirizzo)
    percorso = elementi.path

    domini_validi = {
        "eppermed.eu",
        "www.eppermed.eu",
    }

    if elementi.netloc not in domini_validi:
        return False

    if not percorso.startswith(
        "/funding-projects/calls/"
    ):
        return False

    if percorso in PERCORSI_DA_IGNORARE:
        return False

    if normalizza_testo(titolo) in TESTI_DA_IGNORARE:
        return False

    if titolo.isdigit():
        return False

    return True


def estrai_link_calls(html):
    """
    Estrae dalla pagina principale i collegamenti
    alle possibili call EP PerMed.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    risultati = []
    url_visti = set()

    for link in soup.find_all(
        "a",
        href=True,
    ):
        indirizzo_originale = link.get(
            "href",
            "",
        )

        if not indirizzo_originale:
            continue

        indirizzo = normalizza_url(
            indirizzo_originale
        )

        titolo = trova_titolo(link)

        if not collegamento_valido(
            titolo,
            indirizzo,
        ):
            continue

        if indirizzo in url_visti:
            continue

        url_visti.add(indirizzo)

        risultati.append(
            {
                "fonte": FONTE,
                "titolo": titolo,
                "url": indirizzo,
            }
        )

    return risultati


def estrai_testo_principale(html):
    """
    Estrae il testo principale della pagina.

    Menu, intestazioni, footer, script e altri elementi
    non informativi vengono eliminati per ridurre i
    falsi positivi.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    elementi_da_eliminare = [
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "header",
    ]

    for elemento in soup(
        elementi_da_eliminare
    ):
        elemento.decompose()

    area = (
        soup.find("main")
        or soup.find("article")
        or soup.body
        or soup
    )

    return pulisci_testo(
        area.get_text(
            " ",
            strip=True,
        )
    )


def termine_presente(
    testo_normalizzato,
    termine,
):
    """
    Cerca un termine rispettando i confini delle parole.

    Evita, per esempio, che una sequenza contenuta
    accidentalmente in un'altra parola venga considerata
    una corrispondenza valida.
    """

    termine_normalizzato = normalizza_testo(
        termine
    )

    schema = (
        r"(?<![a-z0-9])"
        + re.escape(termine_normalizzato)
        + r"(?![a-z0-9])"
    )

    return (
        re.search(
            schema,
            testo_normalizzato,
        )
        is not None
    )


def trova_corrispondenze(
    testo,
    termini,
):
    """
    Restituisce l'elenco ordinato dei termini trovati
    nel testo.
    """

    testo_normalizzato = normalizza_testo(
        testo
    )

    corrispondenze = [
        termine
        for termine in termini
        if termine_presente(
            testo_normalizzato,
            termine,
        )
    ]

    return sorted(corrispondenze)


def classifica_call(
    titolo,
    testo_pagina,
):
    """
    Classifica la call in una delle categorie:

    - oncologica;
    - da_verificare;
    - non_pertinente.
    """

    testo_completo = (
        f"{titolo} {testo_pagina}"
    )

    parole_oncologiche = trova_corrispondenze(
        testo_completo,
        TERMINI_ONCOLOGICI,
    )

    parole_secondarie = trova_corrispondenze(
        testo_completo,
        TERMINI_DA_VERIFICARE,
    )

    if parole_oncologiche:
        rilevanza = "oncologica"

    elif len(parole_secondarie) >= 2:
        rilevanza = "da_verificare"

    else:
        rilevanza = "non_pertinente"

    return (
        rilevanza,
        parole_oncologiche,
        parole_secondarie,
    )


def analizza_calls(
    sessione,
    candidati,
):
    """
    Visita ogni pagina candidata e applica
    il filtro oncologico.
    """

    selezionate = []
    non_pertinenti = 0

    totale = len(candidati)

    for numero, call in enumerate(
        candidati,
        start=1,
    ):
        print()
        print(
            f"Analisi {numero}/{totale}: "
            f"{call['titolo']}"
        )

        html = scarica_pagina(
            sessione,
            call["url"],
        )

        testo_pagina = estrai_testo_principale(
            html
        )

        (
            rilevanza,
            parole_oncologiche,
            parole_secondarie,
        ) = classifica_call(
            call["titolo"],
            testo_pagina,
        )

        print(
            f"  Classificazione: {rilevanza}"
        )

        if parole_oncologiche:
            print(
                "  Termini oncologici: "
                + ", ".join(parole_oncologiche)
            )

        if parole_secondarie:
            print(
                "  Termini secondari: "
                + ", ".join(parole_secondarie)
            )

        if rilevanza == "non_pertinente":
            non_pertinenti += 1

        else:
            selezionate.append(
                {
                    **call,
                    "rilevanza": rilevanza,
                    "parole_chiave_oncologiche": (
                        parole_oncologiche
                    ),
                    "parole_chiave_secondarie": (
                        parole_secondarie
                    ),
                }
            )

        if numero < totale:
            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    selezionate.sort(
        key=lambda elemento: (
            0
            if elemento["rilevanza"]
            == "oncologica"
            else 1,
            elemento["titolo"].lower(),
            elemento["url"],
        )
    )

    return (
        selezionate,
        non_pertinenti,
    )


def carica_calls_precedenti():
    """
    Legge il JSON già presente nel repository.

    Se il file non esiste o non è valido,
    restituisce un elenco vuoto.
    """

    if not OUTPUT_FILE.exists():
        print(
            "Nessun archivio precedente trovato."
        )

        return []

    try:
        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            dati = json.load(file)

        calls = dati.get(
            "calls",
            [],
        )

        if not isinstance(calls, list):
            print(
                "Il campo 'calls' non è un elenco."
            )

            return []

        print(
            "Risultati nell'archivio precedente: "
            f"{len(calls)}"
        )

        return calls

    except (
        OSError,
        json.JSONDecodeError,
    ) as errore:
        print(
            "Impossibile leggere "
            "l'archivio precedente: "
            f"{errore}"
        )

        return []


def identifica_nuove_calls(
    calls_precedenti,
    calls_correnti,
):
    """
    Identifica le call selezionate che non erano
    presenti nell'archivio precedente.
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


def identifica_calls_rimosse(
    calls_precedenti,
    calls_correnti,
):
    """
    Identifica le call precedentemente selezionate
    che non sono più presenti fra i risultati correnti.
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


def salva_risultati(
    calls,
    totale_candidati,
    totale_non_pertinenti,
):
    """
    Salva esclusivamente le call oncologiche
    e quelle da verificare.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": FONTE,
        "pagina_monitorata": EP_PERMED_URL,
        "criterio": "oncologia",
        "totale_pagine_candidate": (
            totale_candidati
        ),
        "totale_non_pertinenti": (
            totale_non_pertinenti
        ),
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


def aggiungi_riepilogo_github(
    calls_correnti,
    nuove_calls,
    calls_rimosse,
    non_pertinenti,
):
    """
    Aggiunge un riepilogo leggibile alla pagina
    dell'esecuzione di GitHub Actions.
    """

    percorso = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if not percorso:
        return

    oncologiche = sum(
        call["rilevanza"] == "oncologica"
        for call in calls_correnti
    )

    da_verificare = sum(
        call["rilevanza"] == "da_verificare"
        for call in calls_correnti
    )

    righe = [
        "# Monitoraggio oncologico EP PerMed",
        "",
        f"Call oncologiche: **{oncologiche}**",
        "",
        (
            "Call da verificare: "
            f"**{da_verificare}**"
        ),
        "",
        (
            "Pagine escluse come non pertinenti: "
            f"**{non_pertinenti}**"
        ),
        "",
        (
            "Nuove call selezionate: "
            f"**{len(nuove_calls)}**"
        ),
        "",
        (
            "Call selezionate non più presenti: "
            f"**{len(calls_rimosse)}**"
        ),
        "",
    ]

    if calls_correnti:
        righe.extend(
            [
                "## Risultati correnti",
                "",
            ]
        )

        for call in calls_correnti:
            parole = (
                call[
                    "parole_chiave_oncologiche"
                ]
                or call[
                    "parole_chiave_secondarie"
                ]
            )

            righe.append(
                f"- **{call['rilevanza']}**: "
                f"[{call['titolo']}]"
                f"({call['url']}) "
                f"- parole: {', '.join(parole)}"
            )

        righe.append("")

    else:
        righe.extend(
            [
                "Nessuna call pertinente rilevata.",
                "",
            ]
        )

    if nuove_calls:
        righe.extend(
            [
                "## Nuove call",
                "",
            ]
        )

        for call in nuove_calls:
            righe.append(
                f"- [{call['titolo']}]"
                f"({call['url']})"
            )

        righe.append("")

    with open(
        percorso,
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            "\n".join(righe)
        )


def stampa_elenco(
    titolo,
    calls,
):
    """
    Stampa nel registro di GitHub Actions
    un elenco leggibile di call.
    """

    print()
    print(titolo)
    print("-" * len(titolo))

    if not calls:
        print("Nessun risultato.")
        return

    for numero, call in enumerate(
        calls,
        start=1,
    ):
        rilevanza = call.get(
            "rilevanza",
            "archivio_precedente",
        )

        parole = (
            call.get(
                "parole_chiave_oncologiche",
                [],
            )
            or call.get(
                "parole_chiave_secondarie",
                [],
            )
        )

        titolo_call = call.get(
            "titolo",
            "Senza titolo",
        )

        url_call = call.get(
            "url",
            "URL non disponibile",
        )

        print(
            f"{numero}. "
            f"[{rilevanza}] "
            f"{titolo_call}"
        )

        print(
            f"   {url_call}"
        )

        if parole:
            print(
                "   Parole rilevate: "
                + ", ".join(parole)
            )


def main():
    """
    Funzione principale del programma.
    """

    print("=" * 60)
    print(
        "MONITORAGGIO ONCOLOGICO EP PERMED"
    )
    print("=" * 60)

    calls_precedenti = (
        carica_calls_precedenti()
    )

    sessione = crea_sessione()

    try:
        print(
            "Controllo dell'indice: "
            f"{EP_PERMED_URL}"
        )

        html_indice = scarica_pagina(
            sessione,
            EP_PERMED_URL,
        )

        candidati = estrai_link_calls(
            html_indice
        )

        if not candidati:
            raise RuntimeError(
                "Nessuna pagina candidata "
                "individuata nell'indice."
            )

        print(
            "Pagine candidate trovate: "
            f"{len(candidati)}"
        )

        (
            calls_correnti,
            non_pertinenti,
        ) = analizza_calls(
            sessione,
            candidati,
        )

    except (
        requests.RequestException,
        RuntimeError,
    ) as errore:
        print(
            "Errore durante il monitoraggio: "
            f"{errore}"
        )

        print(
            "Il file precedente non verrà "
            "sovrascritto."
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

    salva_risultati(
        calls_correnti,
        len(candidati),
        non_pertinenti,
    )

    aggiungi_riepilogo_github(
        calls_correnti,
        nuove_calls,
        calls_rimosse,
        non_pertinenti,
    )

    print()
    print(
        "Pagine candidate analizzate: "
        f"{len(candidati)}"
    )

    print(
        "Call selezionate: "
        f"{len(calls_correnti)}"
    )

    print(
        "Pagine non pertinenti: "
        f"{non_pertinenti}"
    )

    print(
        "Nuove call selezionate: "
        f"{len(nuove_calls)}"
    )

    print(
        "Call selezionate non più presenti: "
        f"{len(calls_rimosse)}"
    )

    print(
        f"File aggiornato: {OUTPUT_FILE}"
    )

    stampa_elenco(
        "RISULTATI ONCOLOGICI E DA VERIFICARE",
        calls_correnti,
    )

    stampa_elenco(
        "NUOVE CALL SELEZIONATE",
        nuove_calls,
    )

    stampa_elenco(
        "CALL SELEZIONATE NON PIÙ PRESENTI",
        calls_rimosse,
    )

    print()
    print(
        "Monitoraggio oncologico "
        "completato correttamente."
    )


if __name__ == "__main__":
    main()
