import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import dateparser
import feedparser
import requests
from bs4 import BeautifulSoup


FONTE = (
    "Ministero della Salute - Ricerca Finalizzata"
)

FEED_RSS = (
    "https://www.salute.gov.it/new/rss/"
    "RSS_notizie.xml"
)

OUTPUT_FILE = Path(
    "data/ricerca_finalizzata_calls.json"
)

FUSO_ORARIO_ITALIA = ZoneInfo(
    "Europe/Rome"
)


TERMINI_RICERCA_FINALIZZATA = {
    "ricerca finalizzata",
    "bando ricerca finalizzata",
    "bando della ricerca finalizzata",
    "bando per la ricerca finalizzata",
    "ricerca sanitaria finalizzata",
    "progetti di ricerca sanitaria",
    "bando ricerca sanitaria",
    "giovani ricercatori",
    "starting grant",
}


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
    "carcinomi",
    "sarcomi",
    "leucemia",
    "linfoma",
    "linfomi",
    "mieloma",
    "metastasi",
}


TERMINI_SUBMISSION = {
    "scadenza",
    "termine ultimo",
    "termine per la presentazione",
    "deadline",
    "presentazione delle domande",
    "presentazione delle proposte",
    "presentazione dei progetti",
    "submission",
    "entro e non oltre",
}


TERMINI_CHIUSURA = {
    "bando chiuso",
    "avviso chiuso",
    "procedura conclusa",
    "procedura chiusa",
    "termine scaduto",
    "termini scaduti",
    "progetti finanziati",
    "graduatoria finale",
    "graduatorie finali",
    "esiti finali",
    "risultati finali",
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
                "application/rss+xml,"
                "application/xml,"
                "text/xml,"
                "text/html;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": (
                "it-IT,it;q=0.9,en;q=0.7"
            ),
            "Cache-Control": "no-cache",
        }
    )

    return sessione


def pulisci_testo(testo):
    """
    Rimuove tag HTML e spazi ripetuti.
    """

    if not testo:
        return ""

    testo_senza_html = BeautifulSoup(
        testo,
        "html.parser",
    ).get_text(
        " ",
        strip=True,
    )

    return " ".join(
        testo_senza_html.split()
    )


def normalizza_testo(testo):
    """
    Converte il testo in minuscolo,
    elimina gli accenti e normalizza i trattini.
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

    return " ".join(
        testo.split()
    )


def normalizza_url(indirizzo, pagina_base):
    """
    Converte un URL relativo in URL assoluto
    e rimuove parametri e frammenti.
    """

    if not indirizzo:
        return ""

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


def trova_termini(testo, termini):
    """
    Restituisce i termini trovati nel testo.
    """

    testo_normalizzato = normalizza_testo(
        testo
    )

    risultati = []

    for termine in termini:
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


def trova_indicatori_blocco(contenuto):
    """
    Riconosce le pagine di verifica Gcore.
    """

    testo = normalizza_testo(
        pulisci_testo(contenuto)
    )

    return sorted(
        indicatore
        for indicatore in INDICATORI_BLOCCO
        if indicatore in testo
    )


def scarica_contenuto(sessione, url):
    """
    Scarica un contenuto remoto.
    """

    risposta = sessione.get(
        url,
        timeout=40,
        allow_redirects=True,
    )

    risposta.raise_for_status()

    return risposta.text


def scarica_pagina_html(sessione, url):
    """
    Scarica una pagina HTML e riconosce
    eventuali blocchi Gcore.
    """

    contenuto = scarica_contenuto(
        sessione,
        url,
    )

    indicatori = trova_indicatori_blocco(
        contenuto
    )

    if indicatori:
        raise RuntimeError(
            "Pagina bloccata dalla protezione "
            "del sito: "
            + ", ".join(indicatori)
        )

    print(
        f"Pagina HTML accessibile: {url}"
    )

    return contenuto


def leggi_feed_rss(sessione):
    """
    Scarica e interpreta il feed RSS ufficiale.
    """

    print(
        f"Controllo feed RSS: {FEED_RSS}"
    )

    contenuto = scarica_contenuto(
        sessione,
        FEED_RSS,
    )

    indicatori = trova_indicatori_blocco(
        contenuto
    )

    if indicatori:
        raise RuntimeError(
            "Il feed RSS è stato bloccato: "
            + ", ".join(indicatori)
        )

    feed = feedparser.parse(
        contenuto
    )

    if feed.bozo and not feed.entries:
        raise RuntimeError(
            "Il feed RSS non è interpretabile: "
            f"{feed.bozo_exception}"
        )

    print(
        "Feed RSS letto correttamente."
    )

    print(
        "Elementi presenti nel feed: "
        f"{len(feed.entries)}"
    )

    titolo_feed = feed.feed.get(
        "title",
        "Notizie dal Ministero",
    )

    return (
        feed.entries,
        titolo_feed,
    )


def estrai_data_pubblicazione(entry):
    """
    Ricava la data di pubblicazione
    di un elemento RSS in formato ISO.
    """

    struttura_data = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
    )

    if struttura_data:
        data = datetime(
            struttura_data.tm_year,
            struttura_data.tm_mon,
            struttura_data.tm_mday,
            struttura_data.tm_hour,
            struttura_data.tm_min,
            struttura_data.tm_sec,
            tzinfo=timezone.utc,
        )

        return data.isoformat()

    data_testuale = (
        entry.get("published")
        or entry.get("updated")
    )

    if not data_testuale:
        return None

    data = dateparser.parse(
        data_testuale,
        languages=[
            "it",
            "en",
        ],
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Europe/Rome",
            "TO_TIMEZONE": "Europe/Rome",
        },
    )

    if data is None:
        return None

    return data.isoformat()


def estrai_elementi_rss(entries):
    """
    Cerca nel feed le notizie relative
    alla Ricerca Finalizzata.
    """

    candidati = []
    url_visti = set()

    for entry in entries:
        titolo = pulisci_testo(
            entry.get(
                "title",
                "",
            )
        )

        descrizione = pulisci_testo(
            entry.get(
                "summary",
                entry.get(
                    "description",
                    "",
                ),
            )
        )

        collegamento = normalizza_url(
            entry.get(
                "link",
                "",
            ),
            FEED_RSS,
        )

        testo_completo = (
            f"{titolo} {descrizione}"
        )

        termini_trovati = trova_termini(
            testo_completo,
            TERMINI_RICERCA_FINALIZZATA,
        )

        if not termini_trovati:
            continue

        if not collegamento:
            continue

        if collegamento in url_visti:
            continue

        url_visti.add(
            collegamento
        )

        candidati.append(
            {
                "fonte": FONTE,
                "titolo": titolo,
                "descrizione_rss": descrizione,
                "url": collegamento,
                "data_pubblicazione": (
                    estrai_data_pubblicazione(
                        entry
                    )
                ),
                "termini_ricerca_finalizzata": (
                    termini_trovati
                ),
                "origine_rilevazione": (
                    "feed_rss_ufficiale"
                ),
            }
        )

    candidati.sort(
        key=lambda elemento: (
            elemento.get(
                "data_pubblicazione"
            )
            or "",
            elemento["titolo"].lower(),
        ),
        reverse=True,
    )

    return candidati


def estrai_testo_principale(html):
    """
    Estrae il testo principale da una pagina HTML.
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


def estrai_stringhe_data(testo):
    """
    Estrae le possibili date presenti nel testo.
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
        "submission deadline": 40,
        "deadline": 30,
        "scadenza": 20,
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
    }

    for criterio, valore in criteri_positivi.items():
        if criterio in testo:
            punteggio += valore

    for criterio, valore in criteri_negativi.items():
        if criterio in testo:
            punteggio += valore

    return punteggio


def interpreta_data(stringa_data):
    """
    Converte una data testuale in datetime.

    Se non è presente un orario,
    utilizza la fine della giornata.
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
    Cerca la deadline di presentazione più affidabile.
    """

    candidati = []

    for schema_data in SCHEMI_DATA:
        schema_contesto = re.compile(
            r"(.{0,220}"
            + schema_data
            + r".{0,220})",
            flags=re.IGNORECASE,
        )

        for corrispondenza in schema_contesto.finditer(
            testo
        ):
            contesto = pulisci_testo(
                corrispondenza.group(1)
            )

            termini_submission = trova_termini(
                contesto,
                TERMINI_SUBMISSION,
            )

            if not termini_submission:
                continue

            stringhe_data = estrai_stringhe_data(
                contesto
            )

            for stringa_data in stringhe_data:
                data = interpreta_data(
                    stringa_data
                )

                if data is None:
                    continue

                candidati.append(
                    {
                        "data": data,
                        "stringa_data": (
                            stringa_data
                        ),
                        "punteggio": (
                            calcola_punteggio_deadline(
                                contesto
                            )
                        ),
                    }
                )

    if not candidati:
        return None

    candidati.sort(
        key=lambda elemento: (
            elemento["punteggio"],
            elemento["data"],
        ),
        reverse=True,
    )

    migliore = candidati[0]

    if migliore["punteggio"] < 25:
        return None

    return {
        "deadline": (
            migliore["data"].isoformat()
        ),
        "deadline_testo": (
            migliore["stringa_data"]
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
            "submission_aperta": None,
            "stato_submission": (
                "deadline_non_verificata"
            ),
            "giorni_residui": None,
        }

    try:
        deadline = datetime.fromisoformat(
            deadline_iso
        )

    except ValueError:
        return {
            "submission_aperta": None,
            "stato_submission": (
                "deadline_non_valida"
            ),
            "giorni_residui": None,
        }

    if deadline.tzinfo is None:
        deadline = deadline.replace(
            tzinfo=FUSO_ORARIO_ITALIA
        )

    differenza = (
        deadline.astimezone(timezone.utc)
        - datetime.now(timezone.utc)
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


def verifica_candidato_html(
    sessione,
    candidato,
):
    """
    Prova a verificare la notizia RSS
    aprendo la pagina collegata.

    Se Gcore blocca la pagina, il candidato
    viene conservato come verifica bloccata.
    """

    try:
        html = scarica_pagina_html(
            sessione,
            candidato["url"],
        )

    except (
        requests.RequestException,
        RuntimeError,
    ) as errore:
        return {
            **candidato,
            "stato_verifica": (
                "verifica_bloccata"
            ),
            "motivo_verifica": str(errore),
            "rilevanza": (
                "bando_generale_da_verificare"
            ),
            "parole_chiave_oncologiche": [],
            "submission_aperta": None,
            "stato_submission": (
                "non_verificato"
            ),
            "deadline": None,
            "deadline_testo": None,
        }

    testo = estrai_testo_principale(
        html
    )

    testo_completo = (
        f"{candidato['titolo']} "
        f"{candidato['descrizione_rss']} "
        f"{testo}"
    )

    indicatori_chiusura = trova_termini(
        testo_completo,
        TERMINI_CHIUSURA,
    )

    parole_oncologiche = trova_termini(
        testo_completo,
        TERMINI_ONCOLOGICI,
    )

    informazioni_deadline = estrai_deadline(
        testo_completo
    )

    if indicatori_chiusura:
        return {
            **candidato,
            "stato_verifica": "confermato",
            "rilevanza": (
                "oncologica"
                if parole_oncologiche
                else "bando_generale"
            ),
            "parole_chiave_oncologiche": (
                parole_oncologiche
            ),
            "submission_aperta": False,
            "stato_submission": (
                "dichiarata_conclusa"
            ),
            "indicatori_chiusura": (
                indicatori_chiusura
            ),
            "deadline": None,
            "deadline_testo": None,
        }

    if informazioni_deadline:
        valutazione = valuta_deadline(
            informazioni_deadline[
                "deadline"
            ]
        )

        return {
            **candidato,
            "stato_verifica": "confermato",
            "rilevanza": (
                "oncologica"
                if parole_oncologiche
                else "bando_generale"
            ),
            "parole_chiave_oncologiche": (
                parole_oncologiche
            ),
            "submission_aperta": (
                valutazione[
                    "submission_aperta"
                ]
            ),
            "stato_submission": (
                valutazione[
                    "stato_submission"
                ]
            ),
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

    return {
        **candidato,
        "stato_verifica": (
            "pagina_accessibile_deadline_non_rilevata"
        ),
        "rilevanza": (
            "oncologica"
            if parole_oncologiche
            else "bando_generale_da_verificare"
        ),
        "parole_chiave_oncologiche": (
            parole_oncologiche
        ),
        "submission_aperta": None,
        "stato_submission": (
            "deadline_non_verificata"
        ),
        "deadline": None,
        "deadline_testo": None,
    }


def seleziona_risultati_da_archiviare(
    candidati_verificati,
):
    """
    Conserva:

    - bandi confermati con submission aperta;
    - candidati con verifica bloccata;
    - candidati con deadline non verificata.

    Esclude bandi confermati come scaduti
    o dichiarati conclusi.
    """

    risultati = []

    for candidato in candidati_verificati:
        stato_submission = candidato.get(
            "stato_submission"
        )

        if stato_submission in {
            "scaduta",
            "dichiarata_conclusa",
        }:
            continue

        risultati.append(
            candidato
        )

    risultati.sort(
        key=lambda elemento: (
            elemento.get(
                "data_pubblicazione"
            )
            or "",
            elemento.get(
                "titolo",
                "",
            ).lower(),
        ),
        reverse=True,
    )

    return risultati


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


def identifica_nuovi_risultati(
    precedenti,
    correnti,
):
    """
    Identifica i nuovi risultati in base all'URL.
    """

    url_precedenti = {
        elemento.get("url")
        for elemento in precedenti
        if elemento.get("url")
    }

    return [
        elemento
        for elemento in correnti
        if elemento.get("url")
        not in url_precedenti
    ]


def identifica_risultati_rimossi(
    precedenti,
    correnti,
):
    """
    Identifica i risultati non più presenti.
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
        and elemento.get("url")
        not in url_correnti
    ]


def identifica_risultati_modificati(
    precedenti,
    correnti,
):
    """
    Identifica modifiche ai campi stabili.
    """

    precedenti_per_url = {
        elemento.get("url"): elemento
        for elemento in precedenti
        if elemento.get("url")
    }

    campi = [
        "titolo",
        "stato_verifica",
        "rilevanza",
        "submission_aperta",
        "stato_submission",
        "deadline",
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
                    "titolo": corrente.get(
                        "titolo",
                        "Titolo non disponibile",
                    ),
                    "url": corrente.get(
                        "url",
                        "",
                    ),
                    "campi_modificati": (
                        campi_modificati
                    ),
                }
            )

    return modificati


def salva_risultati(
    risultati,
    numero_elementi_feed,
    numero_candidati_rss,
):
    """
    Salva il risultato del controllo RSS.

    Zero risultati indica che il feed corrente
    non contiene segnalazioni pertinenti.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    confermati_aperti = sum(
        elemento.get("submission_aperta")
        is True
        for elemento in risultati
    )

    verifiche_bloccate = sum(
        elemento.get("stato_verifica")
        == "verifica_bloccata"
        for elemento in risultati
    )

    dati = {
        "fonte": FONTE,
        "feed_rss": FEED_RSS,
        "criterio": (
            "allerta Ricerca Finalizzata, "
            "pertinenza oncologica e deadline"
        ),
        "monitoraggio_rss_completato": True,
        "nota": (
            "L'assenza di risultati indica che "
            "nel feed RSS corrente non sono state "
            "trovate segnalazioni pertinenti. "
            "Non certifica l'assenza assoluta "
            "di bandi sul portale."
        ),
        "totale_elementi_feed": (
            numero_elementi_feed
        ),
        "totale_candidati_rss": (
            numero_candidati_rss
        ),
        "totale_confermati_aperti": (
            confermati_aperti
        ),
        "totale_verifiche_bloccate": (
            verifiche_bloccate
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
        return "non verificata"

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


def aggiungi_riepilogo_github(
    risultati,
    candidati_rss,
    nuovi,
    rimossi,
    modificati,
    numero_elementi_feed,
):
    """
    Aggiunge il riepilogo alla pagina Summary
    dell'esecuzione GitHub Actions.
    """

    percorso = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if not percorso:
        return

    confermati_aperti = [
        elemento
        for elemento in risultati
        if elemento.get("submission_aperta")
        is True
    ]

    verifiche_bloccate = [
        elemento
        for elemento in risultati
        if elemento.get("stato_verifica")
        == "verifica_bloccata"
    ]

    righe = [
        "# Ricerca Finalizzata",
        "",
        (
            "Canale controllato: "
            "**feed RSS ufficiale del Ministero**"
        ),
        "",
        (
            "Elementi presenti nel feed: "
            f"**{numero_elementi_feed}**"
        ),
        "",
        (
            "Notizie candidate: "
            f"**{len(candidati_rss)}**"
        ),
        "",
        (
            "Bandi confermati con submission aperta: "
            f"**{len(confermati_aperti)}**"
        ),
        "",
        (
            "Candidati con verifica bloccata: "
            f"**{len(verifiche_bloccate)}**"
        ),
        "",
        (
            "Nuovi risultati: "
            f"**{len(nuovi)}**"
        ),
        "",
        (
            "Risultati modificati: "
            f"**{len(modificati)}**"
        ),
        "",
        (
            "Risultati rimossi: "
            f"**{len(rimossi)}**"
        ),
        "",
    ]

    if risultati:
        righe.extend(
            [
                "## Risultati correnti",
                "",
            ]
        )

        for risultato in risultati:
            titolo = risultato.get(
                "titolo",
                "Titolo non disponibile",
            )

            url = risultato.get(
                "url",
                "",
            )

            stato_verifica = risultato.get(
                "stato_verifica",
                "non disponibile",
            )

            rilevanza = risultato.get(
                "rilevanza",
                "non disponibile",
            )

            stato_submission = risultato.get(
                "stato_submission",
                "non verificato",
            )

            deadline = formatta_deadline(
                risultato.get("deadline")
            )

            if url:
                righe.append(
                    f"- {url}"
                )
            else:
                righe.append(
                    f"- {titolo}"
                )

            righe.append(
                "  - Stato verifica: "
                f"**{stato_verifica}**"
            )

            righe.append(
                "  - Rilevanza: "
                f"**{rilevanza}**"
            )

            righe.append(
                "  - Stato submission: "
                f"**{stato_submission}**"
            )

            righe.append(
                "  - Deadline: "
                f"**{deadline}**"
            )

        righe.append("")

    else:
        righe.extend(
            [
                (
                    "Nessuna nuova segnalazione relativa "
                    "alla Ricerca Finalizzata è presente "
                    "nel feed RSS corrente."
                ),
                "",
            ]
        )

    if nuovi:
        righe.extend(
            [
                "## Nuovi risultati",
                "",
            ]
        )

        for risultato in nuovi:
            titolo = risultato.get(
                "titolo",
                "Titolo non disponibile",
            )

            url = risultato.get(
                "url",
                "",
            )

            if url:
                righe.append(
                    f"- {url}"
                )
            else:
                righe.append(
                    f"- {titolo}"
                )

        righe.append("")

    if modificati:
        righe.extend(
            [
                "## Risultati modificati",
                "",
            ]
        )

        for risultato in modificati:
            titolo = risultato.get(
                "titolo",
                "Titolo non disponibile",
            )

            url = risultato.get(
                "url",
                "",
            )

            campi_modificati = ", ".join(
                risultato.get(
                    "campi_modificati",
                    [],
                )
            )

            if url:
                righe.append(
                    f"- {url}: "
                    f"{campi_modificati}"
                )
            else:
                righe.append(
                    f"- {titolo}: "
                    f"{campi_modificati}"
                )

        righe.append("")

    if rimossi:
        righe.extend(
            [
                "## Risultati non più presenti",
                "",
            ]
        )

        for risultato in rimossi:
            titolo = risultato.get(
                "titolo",
                "Titolo non disponibile",
            )

            url = risultato.get(
                "url",
                "",
            )

            if url:
                righe.append(
                    f"- {url}"
                )
            else:
                righe.append(
                    f"- {titolo}"
                )

        righe.append("")

    if (
        not nuovi
        and not modificati
        and not rimossi
    ):
        righe.extend(
            [
                (
                    "Nessuna variazione rispetto "
                    "all'esecuzione precedente."
                ),
                "",
            ]
        )

    with open(
        percorso,
        "a",
        encoding="utf-8",
    ) as file:
        file.write(
            "\n".join(righe)
        )

        file.write("\n")


def stampa_risultati(risultati):
    """
    Stampa i risultati nel log.
    """

    print()
    print(
        "RISULTATI RSS RICERCA FINALIZZATA"
    )

    print(
        "--------------------------------"
    )

    if not risultati:
        print(
            "Nessuna segnalazione pertinente "
            "nel feed RSS corrente."
        )

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
            "   Stato verifica: "
            f"{risultato['stato_verifica']}"
        )

        print(
            "   Rilevanza: "
            f"{risultato['rilevanza']}"
        )

        print(
            "   Stato submission: "
            f"{risultato['stato_submission']}"
        )

        print(
            "   Deadline: "
            f"{formatta_deadline(risultato.get('deadline'))}"
        )


def main():
    """
    Funzione principale.
    """

    print("=" * 60)

    print(
        "MONITORAGGIO RSS RICERCA FINALIZZATA"
    )

    print("=" * 60)

    precedenti = (
        carica_risultati_precedenti()
    )

    sessione = crea_sessione()

    try:
        (
            entries,
            titolo_feed,
        ) = leggi_feed_rss(
            sessione
        )

    except (
        requests.RequestException,
        RuntimeError,
    ) as errore:
        print(
            "Errore durante il controllo RSS: "
            f"{errore}"
        )

        print(
            "Il precedente archivio non verrà "
            "sovrascritto."
        )

        raise SystemExit(1)

    print(
        f"Titolo del feed: {titolo_feed}"
    )

    candidati_rss = estrai_elementi_rss(
        entries
    )

    print(
        "Candidati Ricerca Finalizzata "
        f"trovati nel feed: {len(candidati_rss)}"
    )

    candidati_verificati = []

    for numero, candidato in enumerate(
        candidati_rss,
        start=1,
    ):
        print()

        print(
            f"Verifica candidato "
            f"{numero}/{len(candidati_rss)}: "
            f"{candidato['titolo']}"
        )

        verificato = verifica_candidato_html(
            sessione,
            candidato,
        )

        print(
            "  Stato verifica: "
            f"{verificato['stato_verifica']}"
        )

        print(
            "  Stato submission: "
            f"{verificato['stato_submission']}"
        )

        candidati_verificati.append(
            verificato
        )

    risultati = (
        seleziona_risultati_da_archiviare(
            candidati_verificati
        )
    )

    nuovi = identifica_nuovi_risultati(
        precedenti,
        risultati,
    )

    rimossi = identifica_risultati_rimossi(
        precedenti,
        risultati,
    )

    modificati = (
        identifica_risultati_modificati(
            precedenti,
            risultati,
        )
    )

    salva_risultati(
        risultati,
        len(entries),
        len(candidati_rss),
    )

    aggiungi_riepilogo_github(
        risultati,
        candidati_rss,
        nuovi,
        rimossi,
        modificati,
        len(entries),
    )

    print()

    print(
        "Elementi nel feed: "
        f"{len(entries)}"
    )

    print(
        "Candidati RSS: "
        f"{len(candidati_rss)}"
    )

    print(
        "Risultati archiviati: "
        f"{len(risultati)}"
    )

    print(
        "Nuovi risultati: "
        f"{len(nuovi)}"
    )

    print(
        "Risultati modificati: "
        f"{len(modificati)}"
    )

    print(
        "Risultati rimossi: "
        f"{len(rimossi)}"
    )

    print(
        f"File aggiornato: {OUTPUT_FILE}"
    )

    stampa_risultati(
        risultati
    )

    print()

    print(
        "Monitoraggio RSS Ricerca Finalizzata "
        "completato correttamente."
    )


if __name__ == "__main__":
    main()
