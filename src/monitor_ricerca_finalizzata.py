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


FONTE = (
    "Ministero della Salute - Ricerca Finalizzata"
)

PAGINE_INDICE = [
    (
        "https://www.salute.gov.it/new/it/tema/"
        "sistema-ricerca-del-ssn-enti-e-finanziamenti/"
        "la-ricerca-finalizzata/"
    ),
    (
        "https://www.salute.gov.it/new/it/tema/"
        "sistema-ricerca-del-ssn-enti-e-finanziamenti/"
    ),
]

PAGINE_CONOSCIUTE = [
    (
        "https://www.salute.gov.it/new/it/tema/"
        "sistema-ricerca-del-ssn-enti-e-finanziamenti/"
        "ricerca-finalizzata-2024-0/"
    ),
]

OUTPUT_FILE = Path(
    "data/ricerca_finalizzata_calls.json"
)

FUSO_ORARIO_ITALIA = ZoneInfo(
    "Europe/Rome"
)

REQUEST_DELAY_SECONDS = 0.5


TERMINI_ONCOLOGICI = {
    "cancer",
    "cancers",
    "oncology",
    "oncological",
    "oncologic",
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
    "glioma",
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
    "carcinoma",
    "carcinomi",
    "sarcoma",
    "sarcomi",
    "leucemia",
    "linfoma",
    "linfomi",
    "mieloma",
    "metastasi",
}


TERMINI_BANDO = {
    "bando",
    "avviso",
    "call",
    "ricerca finalizzata",
    "progetti di ricerca",
    "presentazione dei progetti",
    "presentazione delle domande",
    "presentazione delle proposte",
}


TERMINI_SUBMISSION = {
    "scadenza",
    "termine",
    "deadline",
    "presentazione",
    "presentare",
    "sottomissione",
    "submission",
    "domande",
    "proposte",
    "progetti",
}


TERMINI_CHIUSURA = {
    "bando chiuso",
    "avviso chiuso",
    "procedura conclusa",
    "procedura chiusa",
    "termine scaduto",
    "termini scaduti",
    "graduatoria",
    "graduatorie",
    "progetti finanziati",
    "esiti",
    "risultati finali",
    "finanziati",
}


INDICATORI_BLOCCO = {
    "browser validation page",
    "site verification",
    "using security service for protection",
    "validation is complete",
    "gcore",
    "checking your browser",
    "enable javascript and cookies",
    "access denied",
}


MESI = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|"
    "luglio|agosto|settembre|ottobre|novembre|dicembre|"
    "january|february|march|april|may|june|july|"
    "august|september|october|november|december"
)


SCHEMI_DATA = [
    (
        rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}"
        r"(?:\s*(?:alle|ore|at|,)\s*"
        r"\d{1,2}(?:[:.]\d{2})?)?"
        r"(?:\s*(?:CET|CEST|UTC|GMT))?"
    ),
    (
        rf"\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}"
        r"(?:\s*(?:alle|ore|at|,)\s*"
        r"\d{1,2}(?:[:.]\d{2})?)?"
        r"(?:\s*(?:CET|CEST|UTC|GMT))?"
    ),
    (
        r"\b\d{1,2}/\d{1,2}/\d{4}"
        r"(?:\s*(?:alle|ore|at|,)\s*"
        r"\d{1,2}(?:[:.]\d{2})?)?"
    ),
    (
        r"\b\d{4}-\d{2}-\d{2}"
        r"(?:[T\s]\d{1,2}:\d{2})?"
    ),
]


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
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": (
                "it-IT,it;q=0.9,en;q=0.7"
            ),
        }
    )

    return sessione


def pulisci_testo(testo):
    """
    Elimina gli spazi ripetuti.
    """

    if not testo:
        return ""

    return " ".join(
        testo.split()
    )


def normalizza_testo(testo):
    """
    Converte il testo in minuscolo,
    elimina gli accenti e uniforma i trattini.
    """

    testo = unicodedata.normalize(
        "NFKD",
        testo or "",
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


def normalizza_url(indirizzo, pagina_base):
    """
    Converte un URL relativo in assoluto
    ed elimina parametri e frammenti.
    """

    url_assoluto = urljoin(
        pagina_base,
        indirizzo.strip(),
    )

    elementi = urlparse(
        url_assoluto
    )

    return urlunparse(
        (
            elementi.scheme.lower(),
            elementi.netloc.lower(),
            elementi.path,
            "",
            "",
            "",
        )
    )


def pagina_bloccata(html):
    """
    Riconosce le pagine di verifica o blocco
    restituite dalla protezione del sito.
    """

    testo = normalizza_testo(
        BeautifulSoup(
            html,
            "html.parser",
        ).get_text(
            " ",
            strip=True,
        )
    )

    corrispondenze = [
        indicatore
        for indicatore in INDICATORI_BLOCCO
        if indicatore in testo
    ]

    return corrispondenze


def scarica_pagina(sessione, url):
    """
    Scarica una pagina e verifica che non si tratti
    di una pagina di protezione automatica.
    """

    risposta = sessione.get(
        url,
        timeout=40,
        allow_redirects=True,
    )

    risposta.raise_for_status()

    blocchi = pagina_bloccata(
        risposta.text
    )

    if blocchi:
        raise RuntimeError(
            "Il sito ha restituito una pagina "
            "di verifica o protezione: "
            + ", ".join(blocchi)
        )

    print(
        "Pagina scaricata: "
        f"HTTP {risposta.status_code} - {url}"
    )

    return risposta.text


def estrai_testo_principale(html):
    """
    Estrae il testo principale della pagina.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for elemento in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "header",
            "form",
        ]
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


def estrai_titolo(html):
    """
    Estrae il titolo della pagina.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    intestazione = soup.find(
        ["h1", "h2"]
    )

    if intestazione:
        titolo = pulisci_testo(
            intestazione.get_text(
                " ",
                strip=True,
            )
        )

        if titolo:
            return titolo

    if soup.title:
        titolo = pulisci_testo(
            soup.title.get_text(
                " ",
                strip=True,
            )
        )

        titolo = re.sub(
            r"^Ministero della Salute\s*-\s*",
            "",
            titolo,
            flags=re.IGNORECASE,
        )

        return titolo

    return "Bando Ricerca Finalizzata"


def contiene_termine(testo, termini):
    """
    Verifica se almeno un termine è presente.
    """

    testo_normalizzato = normalizza_testo(
        testo
    )

    return any(
        normalizza_testo(termine)
        in testo_normalizzato
        for termine in termini
    )


def trova_parole_oncologiche(testo):
    """
    Restituisce le parole oncologiche trovate.
    """

    testo_normalizzato = normalizza_testo(
        testo
    )

    risultati = []

    for termine in TERMINI_ONCOLOGICI:
        termine_normalizzato = normalizza_testo(
            termine
        )

        schema = (
            r"(?<![a-z0-9])"
            + re.escape(termine_normalizzato)
            + r"(?![a-z0-9])"
        )

        if re.search(
            schema,
            testo_normalizzato,
        ):
            risultati.append(
                termine
            )

    return sorted(
        risultati
    )


def collegamento_candidato(
    titolo,
    indirizzo,
):
    """
    Verifica se un collegamento può rappresentare
    una pagina relativa alla Ricerca Finalizzata.
    """

    elementi = urlparse(
        indirizzo
    )

    if elementi.netloc not in {
        "salute.gov.it",
        "www.salute.gov.it",
    }:
        return False

    testo = normalizza_testo(
        f"{titolo} {indirizzo}"
    )

    if "ricerca-finalizzata" in testo:
        return True

    if (
        "ricerca finalizzata" in testo
        and "bando" in testo
    ):
        return True

    return False


def estrai_pagine_candidate(
    html,
    pagina_base,
):
    """
    Estrae le pagine candidate dalla pagina indice.
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
        titolo = pulisci_testo(
            link.get_text(
                " ",
                strip=True,
            )
        )

        indirizzo = normalizza_url(
            link.get("href", ""),
            pagina_base,
        )

        if not collegamento_candidato(
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
                "titolo_indice": titolo,
                "url": indirizzo,
            }
        )

    return risultati


def raccogli_pagine_candidate(sessione):
    """
    Controlla le pagine indice e raccoglie
    i collegamenti ai bandi.

    Le pagine conosciute vengono aggiunte come fallback.
    """

    risultati = []
    url_visti = set()
    pagine_indice_accessibili = 0
    errori = []

    for pagina in PAGINE_INDICE:
        print()
        print(
            f"Controllo indice: {pagina}"
        )

        try:
            html = scarica_pagina(
                sessione,
                pagina,
            )

            pagine_indice_accessibili += 1

            candidati = estrai_pagine_candidate(
                html,
                pagina,
            )

            for candidato in candidati:
                if candidato["url"] in url_visti:
                    continue

                url_visti.add(
                    candidato["url"]
                )

                risultati.append(
                    candidato
                )

        except (
            requests.RequestException,
            RuntimeError,
        ) as errore:
            messaggio = (
                f"{pagina}: {errore}"
            )

            errori.append(
                messaggio
            )

            print(
                "Avviso: indice non accessibile. "
                f"{errore}"
            )

    for pagina in PAGINE_CONOSCIUTE:
        if pagina in url_visti:
            continue

        url_visti.add(
            pagina
        )

        risultati.append(
            {
                "titolo_indice": (
                    "Ricerca finalizzata"
                ),
                "url": pagina,
            }
        )

    if (
        pagine_indice_accessibili == 0
        and not risultati
    ):
        raise RuntimeError(
            "Nessuna pagina del Ministero "
            "è risultata accessibile."
        )

    return (
        risultati,
        pagine_indice_accessibili,
        errori,
    )


def estrai_stringhe_data(testo):
    """
    Estrae stringhe che rappresentano date.
    """

    risultati = []

    for schema in SCHEMI_DATA:
        for corrispondenza in re.finditer(
            schema,
            testo,
            flags=re.IGNORECASE,
        ):
            valore = pulisci_testo(
                corrispondenza.group(0)
            )

            if valore not in risultati:
                risultati.append(
                    valore
                )

    return risultati


def calcola_punteggio_deadline(contesto):
    """
    Assegna un punteggio al contesto
    di una possibile deadline.
    """

    testo = normalizza_testo(
        contesto
    )

    punteggio = 0

    criteri_positivi = {
        "scadenza per la presentazione": 50,
        "termine per la presentazione": 50,
        "scadenza presentazione": 45,
        "presentazione delle proposte": 30,
        "presentazione dei progetti": 30,
        "presentazione delle domande": 30,
        "termine ultimo": 35,
        "entro e non oltre": 35,
        "deadline": 30,
        "submission deadline": 40,
        "scadenza": 20,
        "termine": 10,
        "presentazione": 10,
        "submission": 10,
    }

    criteri_negativi = {
        "pubblicazione": -15,
        "aggiornato": -20,
        "graduatoria": -30,
        "finanziati": -30,
        "risultati": -20,
        "esiti": -20,
        "avvio": -15,
        "webinar": -20,
        "comunicazione": -10,
    }

    for criterio, valore in criteri_positivi.items():
        if criterio in testo:
            punteggio += valore

    for criterio, valore in criteri_negativi.items():
        if criterio in testo:
            punteggio += valore

    return punteggio


def estrai_candidati_deadline(testo):
    """
    Cerca date vicine a parole relative
    alla presentazione delle proposte.
    """

    testo = pulisci_testo(
        testo
    )

    candidati = []

    for schema_data in SCHEMI_DATA:
        schema = re.compile(
            r"(.{0,220}"
            + schema_data
            + r".{0,220})",
            flags=re.IGNORECASE,
        )

        for corrispondenza in schema.finditer(
            testo
        ):
            contesto = pulisci_testo(
                corrispondenza.group(1)
            )

            if not contiene_termine(
                contesto,
                TERMINI_SUBMISSION,
            ):
                continue

            date = estrai_stringhe_data(
                contesto
            )

            for stringa_data in date:
                candidati.append(
                    {
                        "stringa_data": (
                            stringa_data
                        ),
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

    Se non è riportato un orario,
    viene utilizzata la fine della giornata.
    """

    contiene_orario = bool(
        re.search(
            r"\b\d{1,2}[:.]\d{2}\b",
            stringa_data,
        )
    )

    stringa_pulita = (
        stringa_data.replace(
            "(",
            " ",
        ).replace(
            ")",
            " ",
        )
    )

    stringa_pulita = pulisci_testo(
        stringa_pulita
    )

    data = dateparser.parse(
        stringa_pulita,
        languages=[
            "it",
            "en",
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
            tzinfo=FUSO_ORARIO_ITALIA
        )

    if not contiene_orario:
        data = data.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )

    return data


def estrai_deadline(testo):
    """
    Seleziona la deadline più affidabile.
    """

    candidati = estrai_candidati_deadline(
        testo
    )

    candidati_validi = []

    for candidato in candidati:
        data = interpreta_data(
            candidato["stringa_data"]
        )

        if data is None:
            continue

        candidati_validi.append(
            {
                **candidato,
                "data": data,
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

    if migliore["punteggio"] < 25:
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
    Verifica se la deadline è futura.
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
            tzinfo=FUSO_ORARIO_ITALIA
        )

    adesso = datetime.now(
        timezone.utc
    )

    differenza = (
        deadline.astimezone(timezone.utc)
        - adesso
    )

    secondi = differenza.total_seconds()

    if secondi <= 0:
        return {
            "submission_aperta": False,
            "stato_submission": "scaduta",
            "giorni_residui": 0,
        }

    giorni = int(
        secondi // 86400
    )

    if secondi % 86400:
        giorni += 1

    return {
        "submission_aperta": True,
        "stato_submission": "aperta",
        "giorni_residui": giorni,
    }


def pagina_dichiarata_chiusa(testo):
    """
    Cerca indicatori espliciti di chiusura
    o conclusione del bando.
    """

    testo_normalizzato = normalizza_testo(
        testo
    )

    indicatori = [
        termine
        for termine in TERMINI_CHIUSURA
        if normalizza_testo(termine)
        in testo_normalizzato
    ]

    return sorted(
        indicatori
    )


def analizza_candidati(sessione, candidati):
    """
    Analizza i possibili bandi.

    Vengono salvati solo quelli:
    - pertinenti all'oncologia;
    - non dichiarati conclusi;
    - con deadline rilevata;
    - con deadline futura.
    """

    risultati = []

    statistiche = {
        "candidate": len(candidati),
        "pagine_accessibili": 0,
        "pagine_bloccate": 0,
        "non_pertinenti": 0,
        "dichiarate_chiuse": 0,
        "scadute": 0,
        "deadline_non_rilevate": 0,
        "deadline_non_valide": 0,
    }

    for numero, candidato in enumerate(
        candidati,
        start=1,
    ):
        print()
        print(
            f"Analisi {numero}/{len(candidati)}: "
            f"{candidato['url']}"
        )

        try:
            html = scarica_pagina(
                sessione,
                candidato["url"],
            )

        except (
            requests.RequestException,
            RuntimeError,
        ) as errore:
            statistiche[
                "pagine_bloccate"
            ] += 1

            print(
                "  Pagina non analizzabile: "
                f"{errore}"
            )

            continue

        statistiche[
            "pagine_accessibili"
        ] += 1

        titolo = estrai_titolo(
            html
        )

        testo = estrai_testo_principale(
            html
        )

        testo_completo = (
            f"{titolo} {testo}"
        )

        if not contiene_termine(
            testo_completo,
            TERMINI_BANDO,
        ):
            print(
                "  Esclusa: la pagina non sembra "
                "riguardare un bando."
            )

            continue

        parole_oncologiche = (
            trova_parole_oncologiche(
                testo_completo
            )
        )

        if not parole_oncologiche:
            statistiche[
                "non_pertinenti"
            ] += 1

            print(
                "  Esclusa: nessun termine "
                "oncologico rilevato."
            )

            continue

        indicatori_chiusura = (
            pagina_dichiarata_chiusa(
                testo_completo
            )
        )

        if indicatori_chiusura:
            statistiche[
                "dichiarate_chiuse"
            ] += 1

            print(
                "  Esclusa: indicatori di "
                "conclusione rilevati: "
                + ", ".join(
                    indicatori_chiusura
                )
            )

            continue

        informazioni_deadline = (
            estrai_deadline(
                testo_completo
            )
        )

        if informazioni_deadline is None:
            statistiche[
                "deadline_non_rilevate"
            ] += 1

            print(
                "  Esclusa prudenzialmente: "
                "deadline non rilevata."
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
            "  Stato submission: "
            f"{valutazione['stato_submission']}"
        )

        if (
            valutazione["stato_submission"]
            == "scaduta"
        ):
            statistiche[
                "scadute"
            ] += 1

            print(
                "  Esclusa: submission scaduta."
            )

            continue

        if (
            valutazione["stato_submission"]
            == "deadline_non_valida"
        ):
            statistiche[
                "deadline_non_valide"
            ] += 1

            print(
                "  Esclusa: deadline non valida."
            )

            continue

        if valutazione[
            "submission_aperta"
        ]:
            risultati.append(
                {
                    "fonte": FONTE,
                    "titolo": titolo,
                    "url": candidato["url"],
                    "rilevanza": "oncologica",
                    "parole_chiave_oncologiche": (
                        parole_oncologiche
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
                }
            )

            print(
                "  Inclusa: bando oncologico "
                "con submission aperta."
            )

        if numero < len(candidati):
            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    risultati.sort(
        key=lambda elemento: (
            elemento["deadline"],
            elemento["titolo"].lower(),
            elemento["url"],
        )
    )

    return (
        risultati,
        statistiche,
    )


def carica_risultati_precedenti():
    """
    Legge l'archivio precedente.
    """

    if not OUTPUT_FILE.exists():
        print(
            "Nessun archivio precedente "
            "Ricerca Finalizzata trovato."
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

        risultati = dati.get(
            "calls",
            [],
        )

        if not isinstance(
            risultati,
            list,
        ):
            return []

        print(
            "Risultati nell'archivio precedente: "
            f"{len(risultati)}"
        )

        return risultati

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
    precedenti,
    correnti,
):
    """
    Identifica i nuovi bandi in base all'URL.
    """

    url_precedenti = {
        elemento.get("url")
        for elemento in precedenti
        if elemento.get("url")
    }

    return [
        elemento
        for elemento in correnti
        if elemento["url"] not in url_precedenti
    ]


def identifica_calls_rimosse(
    precedenti,
    correnti,
):
    """
    Identifica i bandi non più attivi.
    """

    url_correnti = {
        elemento.get("url")
        for elemento in correnti
        if elemento.get("url")
    }

    return [
        elemento
        for elemento in precedenti
        if elemento.get("url")
        and elemento["url"] not in url_correnti
    ]


def identifica_calls_modificate(
    precedenti,
    correnti,
):
    """
    Identifica modifiche ai dati stabili.
    """

    precedenti_per_url = {
        elemento.get("url"): elemento
        for elemento in precedenti
        if elemento.get("url")
    }

    campi = [
        "titolo",
        "deadline",
        "stato_submission",
        "rilevanza",
    ]

    modificati = []

    for corrente in correnti:
        precedente = precedenti_per_url.get(
            corrente.get("url")
        )

        if not precedente:
            continue

        campi_modificati = [
            campo
            for campo in campi
            if precedente.get(campo)
            != corrente.get(campo)
        ]

        if campi_modificati:
            modificati.append(
                {
                    "titolo": corrente[
                        "titolo"
                    ],
                    "url": corrente[
                        "url"
                    ],
                    "campi_modificati": (
                        campi_modificati
                    ),
                }
            )

    return modificati


def salva_risultati(
    risultati,
    statistiche,
):
    """
    Salva il JSON stabile.

    I giorni residui non vengono salvati,
    così non si creano commit quotidiani.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": FONTE,
        "pagine_indice": PAGINE_INDICE,
        "criterio": (
            "oncologia e submission non scaduta"
        ),
        "totale_pagine_candidate": (
            statistiche["candidate"]
        ),
        "totale_pagine_accessibili": (
            statistiche["pagine_accessibili"]
        ),
        "totale_pagine_non_analizzabili": (
            statistiche["pagine_bloccate"]
        ),
        "totale_non_pertinenti": (
            statistiche["non_pertinenti"]
        ),
        "totale_dichiarate_chiuse": (
            statistiche[
                "dichiarate_chiuse"
            ]
        ),
        "totale_scadute": (
            statistiche["scadute"]
        ),
        "totale_deadline_non_rilevate": (
            statistiche[
                "deadline_non_rilevate"
            ]
        ),
        "numero_risultati": len(
            risultati
        ),
        "calls": risultati,
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

    if not deadline_iso:
        return "non rilevata"

    try:
        deadline = datetime.fromisoformat(
            deadline_iso
        )

    except ValueError:
        return deadline_iso

    if deadline.tzinfo is None:
        deadline = deadline.replace(
            tzinfo=FUSO_ORARIO_ITALIA
        )

    return deadline.astimezone(
        FUSO_ORARIO_ITALIA
    ).strftime(
        "%d/%m/%Y alle %H:%M %Z"
    )


def calcola_giorni_residui(
    deadline_iso,
):
    """
    Calcola i giorni residui senza salvarli nel JSON.
    """

    valutazione = valuta_deadline(
        deadline_iso
    )

    return valutazione.get(
        "giorni_residui"
    )


def aggiungi_riepilogo_github(
    risultati,
    nuove_calls,
    calls_rimosse,
    calls_modificate,
    statistiche,
    errori_indice,
):
    """
    Aggiunge il riepilogo al Job summary.
    """

    percorso = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if not percorso:
        return

    righe = [
        "# Ricerca Finalizzata",
        "",
        (
            "Bandi oncologici con submission "
            f"aperta: **{len(risultati)}**"
        ),
        "",
        (
            "Pagine candidate analizzate: "
            f"**{statistiche['candidate']}**"
        ),
        "",
        (
            "Pagine non analizzabili: "
            f"**{statistiche['pagine_bloccate']}**"
        ),
        "",
        (
            "Bandi esclusi come conclusi: "
            f"**{statistiche['dichiarate_chiuse']}**"
        ),
        "",
        (
            "Bandi esclusi perché scaduti: "
            f"**{statistiche['scadute']}**"
        ),
        "",
        (
            "Bandi esclusi perché non oncologici: "
            f"**{statistiche['non_pertinenti']}**"
        ),
        "",
        (
            "Nuovi bandi attivi: "
            f"**{len(nuove_calls)}**"
        ),
        "",
        (
            "Bandi modificati: "
            f"**{len(calls_modificate)}**"
        ),
        "",
        (
            "Bandi non più attivi: "
            f"**{len(calls_rimosse)}**"
        ),
        "",
    ]

    if risultati:
        righe.extend(
            [
                "## Bandi attivi",
                "",
            ]
        )

        for risultato in risultati:
            giorni = calcola_giorni_residui(
                risultato["deadline"]
            )

            righe.append(
                f"- [{risultato['titolo']}]"
                f"({risultato['url']})"
            )

            righe.append(
                "  - Deadline: "
                f"**{formatta_deadline(risultato['deadline'])}**"
            )

            righe.append(
                "  - Giorni residui: "
                f"**{giorni}**"
            )

        righe.append("")

    else:
        righe.extend(
            [
                "Nessun bando oncologico "
                "con submission aperta rilevato.",
                "",
            ]
        )

    if errori_indice:
        righe.extend(
            [
                "## Avvisi tecnici",
                "",
            ]
        )

        for errore in errori_indice:
            righe.append(
                f"- {errore}"
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


def stampa_elenco(titolo, risultati):
    """
    Stampa un elenco leggibile nel log.
    """

    print()
    print(titolo)
    print("-" * len(titolo))

    if not risultati:
        print("Nessun risultato.")
        return

    for numero, risultato in enumerate(
        risultati,
        start=1,
    ):
        print(
            f"{numero}. {risultato['titolo']}"
        )

        print(
            f"   {risultato['url']}"
        )

        print(
            "   Deadline: "
            f"{formatta_deadline(risultato['deadline'])}"
        )

        print(
            "   Giorni residui: "
            f"{calcola_giorni_residui(risultato['deadline'])}"
        )

        print(
            "   Termini oncologici: "
            + ", ".join(
                risultato[
                    "parole_chiave_oncologiche"
                ]
            )
        )


def main():
    """
    Funzione principale.
    """

    print("=" * 60)
    print(
        "MONITORAGGIO RICERCA FINALIZZATA"
    )
    print("=" * 60)

    precedenti = (
        carica_risultati_precedenti()
    )

    sessione = crea_sessione()

    try:
        (
            candidati,
            indici_accessibili,
            errori_indice,
        ) = raccogli_pagine_candidate(
            sessione
        )

        print()
        print(
            "Pagine indice accessibili: "
            f"{indici_accessibili}"
        )

        print(
            "Pagine candidate trovate: "
            f"{len(candidati)}"
        )

        (
            risultati,
            statistiche,
        ) = analizza_candidati(
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
        precedenti,
        risultati,
    )

    calls_rimosse = identifica_calls_rimosse(
        precedenti,
        risultati,
    )

    calls_modificate = (
        identifica_calls_modificate(
            precedenti,
            risultati,
        )
    )

    salva_risultati(
        risultati,
        statistiche,
    )

    aggiungi_riepilogo_github(
        risultati,
        nuove_calls,
        calls_rimosse,
        calls_modificate,
        statistiche,
        errori_indice,
    )

    print()
    print(
        "Bandi oncologici attivi: "
        f"{len(risultati)}"
    )

    print(
        "Bandi non pertinenti: "
        f"{statistiche['non_pertinenti']}"
    )

    print(
        "Bandi dichiarati conclusi: "
        f"{statistiche['dichiarate_chiuse']}"
    )

    print(
        "Bandi scaduti: "
        f"{statistiche['scadute']}"
    )

    print(
        "Deadline non rilevate: "
        f"{statistiche['deadline_non_rilevate']}"
    )

    print(
        "Pagine non analizzabili: "
        f"{statistiche['pagine_bloccate']}"
    )

    print(
        "Nuovi bandi attivi: "
        f"{len(nuove_calls)}"
    )

    print(
        "Bandi modificati: "
        f"{len(calls_modificate)}"
    )

    print(
        "Bandi non più attivi: "
        f"{len(calls_rimosse)}"
    )

    print(
        f"File aggiornato: {OUTPUT_FILE}"
    )

    stampa_elenco(
        "BANDI ONCOLOGICI CON SUBMISSION APERTA",
        risultati,
    )

    stampa_elenco(
        "NUOVI BANDI ATTIVI",
        nuove_calls,
    )

    stampa_elenco(
        "BANDI NON PIÙ ATTIVI",
        calls_rimosse,
    )

    print()
    print(
        "Monitoraggio Ricerca Finalizzata "
        "completato correttamente."
    )


if __name__ == "__main__":
    main()
