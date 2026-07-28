import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import dateparser
import requests
from bs4 import BeautifulSoup


FONTE = "EP PerMed"

EP_PERMED_URL = (
    "https://www.eppermed.eu/funding-projects/calls/"
)

OUTPUT_FILE = Path(
    "data/ep_permed_calls.json"
)

REQUEST_DELAY_SECONDS = 0.4

FUSO_ORARIO_EUROPA = ZoneInfo(
    "Europe/Rome"
)


TERMINI_ONCOLOGICI = {
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


MESI_INGLESE = (
    "January|February|March|April|May|June|"
    "July|August|September|October|November|December"
)

MESI_ITALIANO = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|"
    "luglio|agosto|settembre|ottobre|novembre|dicembre"
)

NOMI_MESI = (
    f"(?:{MESI_INGLESE}|{MESI_ITALIANO})"
)


SCHEMA_DATA_GIORNO_MESE = (
    rf"\b\d{{1,2}}\s+{NOMI_MESI}\s+\d{{4}}"
    r"(?:\s*(?:at|alle|,)\s*"
    r"\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?"
)

SCHEMA_DATA_MESE_GIORNO = (
    rf"\b{NOMI_MESI}\s+\d{{1,2}},?\s+\d{{4}}"
    r"(?:\s*(?:at|alle|,)\s*"
    r"\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?"
)

SCHEMA_DATA_ISO = (
    r"\b\d{4}-\d{2}-\d{2}"
    r"(?:[T\s]\d{1,2}:\d{2})?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?"
)

SCHEMA_DATA_COMPLETO = (
    rf"(?:"
    rf"{SCHEMA_DATA_GIORNO_MESE}|"
    rf"{SCHEMA_DATA_MESE_GIORNO}|"
    rf"{SCHEMA_DATA_ISO}"
    rf")"
)

SCHEMA_PAROLE_DEADLINE = (
    r"(?:"
    r"deadline(?:\s+date)?|"
    r"submission\s+deadline|"
    r"proposal\s+submission|"
    r"proposals?\s+submissions?|"
    r"application\s+deadline|"
    r"applications?\s+submissions?|"
    r"submit(?:ting)?\s+(?:the\s+)?"
    r"(?:proposal|application)"
    r")"
)


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
    Scarica una pagina HTML.
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

    return " ".join(
        testo.split()
    )


def normalizza_testo(testo):
    """
    Normalizza il testo per il confronto con le parole chiave.
    """

    testo = unicodedata.normalize(
        "NFKD",
        testo,
    )

    testo = "".join(
        carattere
        for carattere in testo
        if not unicodedata.combining(
            carattere
        )
    )

    testo = testo.lower()
    testo = testo.replace("–", "-")
    testo = testo.replace("—", "-")

    return pulisci_testo(
        testo
    )


def normalizza_url(indirizzo):
    """
    Converte un URL relativo in URL assoluto
    ed elimina parametri e frammenti.
    """

    assoluto = urljoin(
        EP_PERMED_URL,
        indirizzo.strip(),
    )

    elementi = urlparse(
        assoluto
    )

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
            titolo_trovato = pulisci_testo(
                intestazione.get_text(
                    " ",
                    strip=True,
                )
            )

            if titolo_trovato:
                return titolo_trovato

    return titolo


def collegamento_valido(titolo, indirizzo):
    """
    Esclude menu, paginazione, sezioni generiche
    e collegamenti esterni.
    """

    elementi = urlparse(
        indirizzo
    )

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
    Estrae dalla pagina principale le pagine candidate.
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

        titolo = trova_titolo(
            link
        )

        if not collegamento_valido(
            titolo,
            indirizzo,
        ):
            continue

        if indirizzo in url_visti:
            continue

        url_visti.add(
            indirizzo
        )

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
    Estrae il testo principale della pagina,
    eliminando gli elementi non informativi.
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
        "form",
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


def termine_presente(testo_normalizzato, termine):
    """
    Cerca un termine rispettando i confini delle parole.
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


def trova_corrispondenze(testo, termini):
    """
    Restituisce i termini trovati nel testo.
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

    return sorted(
        corrispondenze
    )


def classifica_call(titolo, testo_pagina):
    """
    Classifica una call come oncologica,
    da verificare oppure non pertinente.
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


def calcola_punteggio_deadline(contesto):
    """
    Assegna un punteggio di affidabilità
    al contesto della possibile deadline.
    """

    testo = normalizza_testo(
        contesto
    )

    punteggio = 0

    criteri_positivi = {
        "deadline for proposals submissions": 40,
        "deadline for proposal submissions": 40,
        "deadline for proposals submission": 40,
        "deadline for proposal submission": 40,
        "proposal submission deadline": 40,
        "submission deadline": 35,
        "deadline for proposals": 30,
        "deadline for applications": 30,
        "application deadline": 30,
        "proposal submission": 20,
        "proposals submissions": 20,
        "deadline": 15,
        "submission": 8,
        "proposal": 5,
        "application": 5,
    }

    criteri_negativi = {
        "opening": -20,
        "webinar": -25,
        "matchmaking": -20,
        "notification": -20,
        "eligibility check": -20,
        "final results": -25,
        "kick off": -20,
        "kick-off": -20,
        "contracting": -15,
        "project start": -15,
        "project end": -15,
        "stand still": -15,
    }

    for criterio, valore in criteri_positivi.items():
        if criterio in testo:
            punteggio += valore

    for criterio, valore in criteri_negativi.items():
        if criterio in testo:
            punteggio += valore

    return punteggio


def estrai_candidati_deadline(testo_pagina):
    """
    Cerca costruzioni in cui una data è vicina
    a parole come deadline, submission o proposal.

    Gestisce sia:

    26 February 2026 at 16:00 CET
    Deadline for proposals submissions

    sia:

    Submission deadline:
    26 February 2026 at 16:00 CET
    """

    testo = pulisci_testo(
        testo_pagina
    )

    candidati = []

    schema_data_prima = re.compile(
        rf"(?P<data>{SCHEMA_DATA_COMPLETO})"
        rf"(?P<separatore>.{{0,140}}?)"
        rf"(?P<parola>{SCHEMA_PAROLE_DEADLINE})",
        flags=re.IGNORECASE,
    )

    schema_parola_prima = re.compile(
        rf"(?P<parola>{SCHEMA_PAROLE_DEADLINE})"
        rf"(?P<separatore>.{{0,140}}?)"
        rf"(?P<data>{SCHEMA_DATA_COMPLETO})",
        flags=re.IGNORECASE,
    )

    for schema in [
        schema_data_prima,
        schema_parola_prima,
    ]:
        for corrispondenza in schema.finditer(
            testo
        ):
            stringa_data = pulisci_testo(
                corrispondenza.group("data")
            )

            inizio = max(
                0,
                corrispondenza.start() - 120,
            )

            fine = min(
                len(testo),
                corrispondenza.end() + 120,
            )

            contesto = pulisci_testo(
                testo[inizio:fine]
            )

            candidati.append(
                {
                    "stringa_data": stringa_data,
                    "contesto": contesto,
                    "punteggio": (
                        calcola_punteggio_deadline(
                            contesto
                        )
                    ),
                }
            )

    candidati_unici = []
    chiavi_viste = set()

    for candidato in candidati:
        chiave = (
            candidato["stringa_data"],
            candidato["contesto"],
        )

        if chiave in chiavi_viste:
            continue

        chiavi_viste.add(
            chiave
        )

        candidati_unici.append(
            candidato
        )

    return candidati_unici


def interpreta_data(stringa_data):
    """
    Converte una data testuale in datetime.

    Se la data non specifica l'orario,
    viene impostato prudenzialmente 23:59:59.
    """

    contiene_orario = bool(
        re.search(
            r"\b\d{1,2}[:.]\d{2}\b",
            stringa_data,
        )
    )

    data = dateparser.parse(
        stringa_data,
        languages=[
            "en",
            "it",
        ],
        settings={
            "DATE_ORDER": "DMY",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Europe/Rome",
            "TO_TIMEZONE": "Europe/Rome",
            "STRICT_PARSING": False,
        },
    )

    if data is None:
        return None

    if data.tzinfo is None:
        data = data.replace(
            tzinfo=FUSO_ORARIO_EUROPA
        )

    if not contiene_orario:
        data = data.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )

    return data


def estrai_deadline_submission(testo_pagina):
    """
    Individua la deadline di submission più affidabile.
    """

    candidati_testuali = estrai_candidati_deadline(
        testo_pagina
    )

    candidati_validi = []

    for candidato in candidati_testuali:
        data = interpreta_data(
            candidato["stringa_data"]
        )

        if data is None:
            continue

        candidati_validi.append(
            {
                "data": data,
                "stringa_data": (
                    candidato["stringa_data"]
                ),
                "contesto": (
                    candidato["contesto"]
                ),
                "punteggio": (
                    candidato["punteggio"]
                ),
            }
        )

    if not candidati_validi:
        return None

    candidati_validi.sort(
        key=lambda elemento: (
            elemento["punteggio"],
            elemento["data"],
        ),
        reverse=True,
    )

    migliore = candidati_validi[0]

    if migliore["punteggio"] < 20:
        return None

    return {
        "deadline": (
            migliore["data"].isoformat()
        ),
        "deadline_testo": (
            migliore["stringa_data"]
        ),
        "deadline_contesto": (
            migliore["contesto"]
        ),
        "deadline_affidabilita": (
            migliore["punteggio"]
        ),
    }


def valuta_deadline(deadline_iso):
    """
    Verifica se la deadline è futura
    rispetto al momento di esecuzione.
    """

    if not deadline_iso:
        return {
            "submission_aperta": False,
            "stato_submission": (
                "deadline_non_rilevata"
            ),
            "giorni_residui": None,
        }

    try:
        deadline = datetime.fromisoformat(
            deadline_iso
        )

    except ValueError:
        return {
            "submission_aperta": False,
            "stato_submission": (
                "deadline_non_valida"
            ),
            "giorni_residui": None,
        }

    if deadline.tzinfo is None:
        deadline = deadline.replace(
            tzinfo=FUSO_ORARIO_EUROPA
        )

    adesso = datetime.now(
        timezone.utc
    )

    differenza = (
        deadline.astimezone(timezone.utc)
        - adesso
    )

    secondi_residui = (
        differenza.total_seconds()
    )

    if secondi_residui <= 0:
        return {
            "submission_aperta": False,
            "stato_submission": "scaduta",
            "giorni_residui": 0,
        }

    giorni_residui = int(
        secondi_residui // 86400
    )

    if secondi_residui % 86400:
        giorni_residui += 1

    return {
        "submission_aperta": True,
        "stato_submission": "aperta",
        "giorni_residui": giorni_residui,
    }


def analizza_calls(sessione, candidati):
    """
    Analizza ogni pagina candidata.

    Vengono selezionate soltanto call:
    - oncologiche o da verificare;
    - con deadline rilevata;
    - con submission ancora aperta.
    """

    selezionate = []

    statistiche = {
        "non_pertinenti": 0,
        "scadute": 0,
        "deadline_non_rilevate": 0,
        "deadline_non_valide": 0,
    }

    totale = len(
        candidati
    )

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
                + ", ".join(
                    parole_oncologiche
                )
            )

        if parole_secondarie:
            print(
                "  Termini secondari: "
                + ", ".join(
                    parole_secondarie
                )
            )

        if rilevanza == "non_pertinente":
            statistiche[
                "non_pertinenti"
            ] += 1

            print(
                "  Esclusa: non pertinente "
                "all'oncologia."
            )

            if numero < totale:
                time.sleep(
                    REQUEST_DELAY_SECONDS
                )

            continue

        informazioni_deadline = (
            estrai_deadline_submission(
                testo_pagina
            )
        )

        if informazioni_deadline is None:
            statistiche[
                "deadline_non_rilevate"
            ] += 1

            print(
                "  Deadline di submission "
                "non rilevata."
            )

            print(
                "  Esclusa prudenzialmente."
            )

            if numero < totale:
                time.sleep(
                    REQUEST_DELAY_SECONDS
                )

            continue

        valutazione = valuta_deadline(
            informazioni_deadline[
                "deadline"
            ]
        )

        print(
            "  Deadline rilevata: "
            f"{informazioni_deadline['deadline']}"
        )

        print(
            "  Deadline originale: "
            f"{informazioni_deadline['deadline_testo']}"
        )

        print(
            "  Affidabilità estrazione: "
            f"{informazioni_deadline['deadline_affidabilita']}"
        )

        print(
            "  Stato submission: "
            f"{valutazione['stato_submission']}"
        )

        stato = valutazione[
            "stato_submission"
        ]

        if stato == "scaduta":
            statistiche[
                "scadute"
            ] += 1

            print(
                "  Esclusa: submission scaduta."
            )

        elif stato == "deadline_non_valida":
            statistiche[
                "deadline_non_valide"
            ] += 1

            print(
                "  Esclusa: deadline non valida."
            )

        elif valutazione["submission_aperta"]:
            risultato = {
                **call,
                "rilevanza": rilevanza,
                "parole_chiave_oncologiche": (
                    parole_oncologiche
                ),
                "parole_chiave_secondarie": (
                    parole_secondarie
                ),
                "submission_aperta": True,
                "stato_submission": "aperta",
                "deadline": (
                    informazioni_deadline[
                        "deadline"
                    ]
                ),
                "deadline_testo": (
                    informazioni_deadline[
                        "deadline_testo"
                    ]
                ),
                "deadline_affidabilita": (
                    informazioni_deadline[
                        "deadline_affidabilita"
                    ]
                ),
                "giorni_residui": (
                    valutazione[
                        "giorni_residui"
                    ]
                ),
            }

            selezionate.append(
                risultato
            )

            print(
                "  Inclusa: submission aperta."
            )

            print(
                "  Giorni residui: "
                f"{valutazione['giorni_residui']}"
            )

        if numero < totale:
            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    selezionate.sort(
        key=lambda elemento: (
            elemento["deadline"],
            0
            if elemento["rilevanza"]
            == "oncologica"
            else 1,
            elemento["titolo"].lower(),
        )
    )

    return (
        selezionate,
        statistiche,
    )


def carica_calls_precedenti():
    """
    Legge il JSON già presente nel repository.
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
            dati = json.load(
                file
            )

        calls = dati.get(
            "calls",
            [],
        )

        if not isinstance(
            calls,
            list,
        ):
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
    Identifica le call nuove sulla base dell'URL.
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
    Identifica call che non risultano più attive.
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


def identifica_calls_modificate(
    calls_precedenti,
    calls_correnti,
):
    """
    Identifica modifiche a titolo, deadline,
    stato o classificazione.
    """

    precedenti_per_url = {
        call.get("url"): call
        for call in calls_precedenti
        if call.get("url")
    }

    campi_da_confrontare = [
        "titolo",
        "rilevanza",
        "deadline",
        "stato_submission",
    ]

    modificate = []

    for call_corrente in calls_correnti:
        url = call_corrente.get(
            "url"
        )

        call_precedente = (
            precedenti_per_url.get(url)
        )

        if not call_precedente:
            continue

        campi_modificati = [
            campo
            for campo in campi_da_confrontare
            if call_precedente.get(campo)
            != call_corrente.get(campo)
        ]

        if campi_modificati:
            modificate.append(
                {
                    "titolo": (
                        call_corrente["titolo"]
                    ),
                    "url": url,
                    "campi_modificati": (
                        campi_modificati
                    ),
                }
            )

    return modificate


def salva_risultati(
    calls,
    totale_candidati,
    statistiche,
):
    """
    Salva esclusivamente le call pertinenti
    con submission ancora aperta.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": FONTE,
        "pagina_monitorata": EP_PERMED_URL,
        "criterio": (
            "oncologia e submission non scaduta"
        ),
        "totale_pagine_candidate": (
            totale_candidati
        ),
        "totale_non_pertinenti": (
            statistiche["non_pertinenti"]
        ),
        "totale_call_scadute": (
            statistiche["scadute"]
        ),
        "totale_deadline_non_rilevate": (
            statistiche[
                "deadline_non_rilevate"
            ]
        ),
        "totale_deadline_non_valide": (
            statistiche[
                "deadline_non_valide"
            ]
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


def formatta_deadline(deadline_iso):
    """
    Converte una deadline ISO in formato leggibile.
    """

    try:
        deadline = datetime.fromisoformat(
            deadline_iso
        )

    except (
        TypeError,
        ValueError,
    ):
        return deadline_iso

    if deadline.tzinfo is None:
        deadline = deadline.replace(
            tzinfo=FUSO_ORARIO_EUROPA
        )

    deadline_locale = deadline.astimezone(
        FUSO_ORARIO_EUROPA
    )

    return deadline_locale.strftime(
        "%d/%m/%Y alle %H:%M %Z"
    )


def aggiungi_riepilogo_github(
    calls_correnti,
    nuove_calls,
    calls_rimosse,
    calls_modificate,
    statistiche,
):
    """
    Inserisce il riepilogo nella pagina
    dell'esecuzione GitHub Actions.
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
        (
            "Call oncologiche con submission "
            f"aperta: **{oncologiche}**"
        ),
        "",
        (
            "Call da verificare con submission "
            f"aperta: **{da_verificare}**"
        ),
        "",
        (
            "Call escluse perché scadute: "
            f"**{statistiche['scadute']}**"
        ),
        "",
        (
            "Call escluse perché la deadline "
            "non è stata rilevata: "
            f"**{statistiche['deadline_non_rilevate']}**"
        ),
        "",
        (
            "Pagine non pertinenti: "
            f"**{statistiche['non_pertinenti']}**"
        ),
        "",
        (
            "Nuove call attive: "
            f"**{len(nuove_calls)}**"
        ),
        "",
        (
            "Call modificate: "
            f"**{len(calls_modificate)}**"
        ),
        "",
        (
            "Call non più attive: "
            f"**{len(calls_rimosse)}**"
        ),
        "",
    ]

    if calls_correnti:
        righe.extend(
            [
                "## Call attive",
                "",
            ]
        )

        for call in calls_correnti:
            deadline_visualizzata = (
                formatta_deadline(
                    call["deadline"]
                )
            )

            righe.append(
                f"- **{call['rilevanza']}**: "
                f"[{call['titolo']}]"
                f"({call['url']})"
            )

            righe.append(
                f"  - Deadline: "
                f"**{deadline_visualizzata}**"
            )

            righe.append(
                f"  - Giorni residui: "
                f"**{call['giorni_residui']}**"
            )

        righe.append("")

    else:
        righe.extend(
            [
                "Nessuna call oncologica "
                "con submission aperta.",
                "",
            ]
        )

    if nuove_calls:
        righe.extend(
            [
                "## Nuove call attive",
                "",
            ]
        )

        for call in nuove_calls:
            righe.append(
                f"- [{call['titolo']}]"
                f"({call['url']})"
            )

        righe.append("")

    if calls_modificate:
        righe.extend(
            [
                "## Call modificate",
                "",
            ]
        )

        for call in calls_modificate:
            campi = ", ".join(
                call["campi_modificati"]
            )

            righe.append(
                f"- [{call['titolo']}]"
                f"({call['url']}): {campi}"
            )

        righe.append("")

    if calls_rimosse:
        righe.extend(
            [
                "## Call non più attive",
                "",
            ]
        )

        for call in calls_rimosse:
            righe.append(
                f"- [{call.get('titolo', 'Senza titolo')}]"
                f"({call.get('url', '')})"
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


def stampa_elenco(titolo, calls):
    """
    Stampa un elenco leggibile nel registro.
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

        titolo_call = call.get(
            "titolo",
            "Titolo non disponibile",
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

        deadline = call.get(
            "deadline"
        )

        if deadline:
            print(
                "   Deadline: "
                f"{formatta_deadline(deadline)}"
            )

            print(
                f"   Deadline ISO: {deadline}"
            )

        deadline_testo = call.get(
            "deadline_testo"
        )

        if deadline_testo:
            print(
                "   Deadline riportata "
                f"nella pagina: {deadline_testo}"
            )

        giorni_residui = call.get(
            "giorni_residui"
        )

        if giorni_residui is not None:
            print(
                "   Giorni residui: "
                f"{giorni_residui}"
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

        if parole:
            print(
                "   Parole rilevate: "
                + ", ".join(parole)
            )


def main():
    """
    Funzione principale.
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
            statistiche,
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

    calls_modificate = (
        identifica_calls_modificate(
            calls_precedenti,
            calls_correnti,
        )
    )

    salva_risultati(
        calls_correnti,
        len(candidati),
        statistiche,
    )

    aggiungi_riepilogo_github(
        calls_correnti,
        nuove_calls,
        calls_rimosse,
        calls_modificate,
        statistiche,
    )

    print()
    print(
        "Pagine candidate analizzate: "
        f"{len(candidati)}"
    )

    print(
        "Call attive selezionate: "
        f"{len(calls_correnti)}"
    )

    print(
        "Pagine non pertinenti: "
        f"{statistiche['non_pertinenti']}"
    )

    print(
        "Call escluse perché scadute: "
        f"{statistiche['scadute']}"
    )

    print(
        "Call escluse perché la deadline "
        "non è stata rilevata: "
        f"{statistiche['deadline_non_rilevate']}"
    )

    print(
        "Deadline non valide: "
        f"{statistiche['deadline_non_valide']}"
    )

    print(
        "Nuove call attive: "
        f"{len(nuove_calls)}"
    )

    print(
        "Call modificate: "
        f"{len(calls_modificate)}"
    )

    print(
        "Call non più attive: "
        f"{len(calls_rimosse)}"
    )

    print(
        f"File aggiornato: {OUTPUT_FILE}"
    )

    stampa_elenco(
        "CALL ONCOLOGICHE CON SUBMISSION APERTA",
        calls_correnti,
    )

    stampa_elenco(
        "NUOVE CALL ATTIVE",
        nuove_calls,
    )

    stampa_elenco(
        "CALL NON PIÙ ATTIVE",
        calls_rimosse,
    )

    print()
    print(
        "Monitoraggio oncologico e controllo "
        "delle deadline completati correttamente."
    )


if __name__ == "__main__":
    main()
