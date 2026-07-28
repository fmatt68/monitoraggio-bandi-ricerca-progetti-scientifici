import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import dateparser
import requests
from bs4 import BeautifulSoup


FONTE = "EU Mission on Cancer"

PAGINA_CALLS = (
    "https://hadea.ec.europa.eu/"
    "calls-proposals/cancer-mission-calls-2026_en"
)

OUTPUT_FILE = Path(
    "data/eu_mission_cancer_calls.json"
)

FUSO_ORARIO_EUROPA = ZoneInfo(
    "Europe/Rome"
)

PREFISSO_TOPIC = (
    "HORIZON-MISS-2026-02-CANCER"
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
    Scarica la pagina ufficiale HaDEA.
    """

    risposta = sessione.get(
        url,
        timeout=30,
    )

    risposta.raise_for_status()

    print(
        "Pagina scaricata correttamente: "
        f"HTTP {risposta.status_code}"
    )

    return risposta.text


def pulisci_testo(testo):
    """
    Elimina spazi, tabulazioni e ritorni
    a capo ripetuti.
    """

    return " ".join(
        testo.split()
    )


def normalizza_testo(testo):
    """
    Converte il testo in minuscolo ed elimina
    accenti e spazi ripetuti.
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
    Converte un indirizzo relativo in URL assoluto
    e rimuove parametri e frammenti.
    """

    indirizzo_assoluto = urljoin(
        PAGINA_CALLS,
        indirizzo.strip(),
    )

    elementi = urlparse(
        indirizzo_assoluto
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


def estrai_testo_principale(html):
    """
    Estrae il testo principale eliminando menu,
    footer, script e altri elementi non informativi.
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


def estrai_valore_etichetta(
    testo,
    etichetta,
    etichette_successive,
):
    """
    Estrae il valore che segue un'etichetta
    della pagina HaDEA.

    Esempio:
    Reference HORIZON-MISS-2026-02-CANCER
    Publication date 4 February 2026
    """

    parti_finali = "|".join(
        re.escape(elemento)
        for elemento in etichette_successive
    )

    schema = (
        re.escape(etichetta)
        + r"\s+"
        + r"(.+?)"
        + r"(?=\s+(?:"
        + parti_finali
        + r")\s+|$)"
    )

    corrispondenza = re.search(
        schema,
        testo,
        flags=re.IGNORECASE,
    )

    if not corrispondenza:
        return None

    return pulisci_testo(
        corrispondenza.group(1)
    )


def estrai_metadati_pagina(testo):
    """
    Estrae i principali metadati dichiarati
    nella pagina ufficiale HaDEA.
    """

    etichette = [
        "Status",
        "Reference",
        "Publication date",
        "Opening date",
        "Deadline model",
        "Deadline date",
        "Funding programme",
        "Programme Sector",
        "Programme",
        "Tags",
        "Description",
    ]

    stato_dichiarato = estrai_valore_etichetta(
        testo,
        "Status",
        etichette[1:],
    )

    riferimento = estrai_valore_etichetta(
        testo,
        "Reference",
        etichette[2:],
    )

    data_pubblicazione_testo = (
        estrai_valore_etichetta(
            testo,
            "Publication date",
            etichette[3:],
        )
    )

    data_apertura_testo = (
        estrai_valore_etichetta(
            testo,
            "Opening date",
            etichette[4:],
        )
    )

    modello_deadline = estrai_valore_etichetta(
        testo,
        "Deadline model",
        etichette[5:],
    )

    deadline_testo = estrai_valore_etichetta(
        testo,
        "Deadline date",
        etichette[6:],
    )

    return {
        "stato_dichiarato": stato_dichiarato,
        "riferimento": riferimento,
        "data_pubblicazione_testo": (
            data_pubblicazione_testo
        ),
        "data_apertura_testo": (
            data_apertura_testo
        ),
        "modello_deadline": modello_deadline,
        "deadline_testo": deadline_testo,
    }


def interpreta_data(
    stringa_data,
    fine_giornata_se_senza_orario=False,
):
    """
    Converte una data testuale in un oggetto datetime
    con fuso orario europeo.

    Esempio:
    15 September 2026, 17:00 (CEST)
    """

    if not stringa_data:
        return None

    contiene_orario = bool(
        re.search(
            r"\b\d{1,2}[:.]\d{2}\b",
            stringa_data,
        )
    )

    stringa_pulita = stringa_data.replace(
        "(",
        " ",
    ).replace(
        ")",
        " ",
    )

    stringa_pulita = pulisci_testo(
        stringa_pulita
    )

    data = dateparser.parse(
        stringa_pulita,
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

    if (
        fine_giornata_se_senza_orario
        and not contiene_orario
    ):
        data = data.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )

    return data


def valuta_submission(
    data_apertura,
    deadline,
):
    """
    Determina lo stato effettivo della submission.

    Possibili stati:
    - programmata;
    - aperta;
    - scaduta;
    - deadline_non_rilevata.
    """

    adesso = datetime.now(
        timezone.utc
    )

    if deadline is None:
        return {
            "submission_aperta": False,
            "stato_submission": (
                "deadline_non_rilevata"
            ),
            "giorni_residui": None,
        }

    deadline_utc = deadline.astimezone(
        timezone.utc
    )

    if (
        data_apertura is not None
        and adesso
        < data_apertura.astimezone(timezone.utc)
    ):
        differenza = (
            data_apertura.astimezone(timezone.utc)
            - adesso
        )

        giorni_apertura = int(
            differenza.total_seconds() // 86400
        )

        if differenza.total_seconds() % 86400:
            giorni_apertura += 1

        return {
            "submission_aperta": False,
            "stato_submission": "programmata",
            "giorni_residui": None,
            "giorni_all_apertura": giorni_apertura,
        }

    differenza = deadline_utc - adesso

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


def pulisci_titolo_topic(titolo):
    """
    Elimina eventuale testo appartenente
    alle sezioni successive.
    """

    indicatori_finali = [
        "The European Commission",
        "HaDEA has published",
        "Interested applicants",
        "Interested parties",
        "Deadline to apply",
        "Deadline date",
        "Funding programme",
        "Programme Sector",
        "Programme Horizon",
        "Tags",
        "Relevant links",
        "Background",
    ]

    titolo_pulito = titolo

    for indicatore in indicatori_finali:
        posizione = titolo_pulito.find(
            indicatore
        )

        if posizione >= 0:
            titolo_pulito = (
                titolo_pulito[:posizione]
            )

    return titolo_pulito.strip(
        " .:-"
    )


def estrai_topics(testo):
    """
    Estrae codici e titoli dei topic della call.

    Esempio:
    HORIZON-MISS-2026-02-CANCER-01:
    Virtual Human Twin Models for Cancer Research
    """

    schema = re.compile(
        r"("
        + re.escape(PREFISSO_TOPIC)
        + r"-\d{2})"
        + r"\s*:\s*"
        + r"(.+?)"
        + r"(?="
        + re.escape(PREFISSO_TOPIC)
        + r"-\d{2}\s*:|$)",
        flags=(
            re.IGNORECASE
            | re.DOTALL
        ),
    )

    topics = []
    codici_visti = set()

    for corrispondenza in schema.finditer(
        testo
    ):
        codice_topic = (
            corrispondenza.group(1).upper()
        )

        if codice_topic in codici_visti:
            continue

        titolo = pulisci_testo(
            corrispondenza.group(2)
        )

        titolo = pulisci_titolo_topic(
            titolo
        )

        if not titolo:
            titolo = codice_topic

        codici_visti.add(
            codice_topic
        )

        topics.append(
            {
                "codice_topic": codice_topic,
                "titolo": titolo,
            }
        )

    topics.sort(
        key=lambda elemento: (
            elemento["codice_topic"]
        )
    )

    return topics


def trova_url_topic(
    soup,
    codice_topic,
):
    """
    Cerca un collegamento diretto per il topic.

    Se non esiste, utilizza la pagina generale HaDEA.
    """

    for link in soup.find_all(
        "a",
        href=True,
    ):
        testo_link = pulisci_testo(
            link.get_text(
                " ",
                strip=True,
            )
        )

        indirizzo = link.get(
            "href",
            "",
        )

        if (
            codice_topic.lower()
            in testo_link.lower()
            or codice_topic.lower()
            in indirizzo.lower()
        ):
            return normalizza_url(
                indirizzo
            )

    return PAGINA_CALLS


def costruisci_risultati(
    html,
    testo,
    metadati,
    valutazione,
    data_pubblicazione,
    data_apertura,
    deadline,
):
    """
    Costruisce l'elenco dei topic.

    I topic vengono restituiti soltanto se la
    submission risulta ancora aperta.
    """

    if not valutazione["submission_aperta"]:
        return []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    topics_estratti = estrai_topics(
        testo
    )

    risultati = []

    for topic in topics_estratti:
        codice_topic = topic[
            "codice_topic"
        ]

        risultati.append(
            {
                "fonte": FONTE,
                "codice_call": (
                    metadati["riferimento"]
                    or PREFISSO_TOPIC
                ),
                "codice_topic": codice_topic,
                "titolo": topic["titolo"],
                "url": trova_url_topic(
                    soup,
                    codice_topic,
                ),
                "pagina_fonte": PAGINA_CALLS,
                "rilevanza": "oncologica",
                "parole_chiave_oncologiche": [
                    "cancer"
                ],
                "stato_dichiarato": (
                    metadati[
                        "stato_dichiarato"
                    ]
                ),
                "submission_aperta": True,
                "stato_submission": "aperta",
                "data_pubblicazione": (
                    data_pubblicazione.isoformat()
                    if data_pubblicazione
                    else None
                ),
                "data_apertura": (
                    data_apertura.isoformat()
                    if data_apertura
                    else None
                ),
                "deadline": (
                    deadline.isoformat()
                ),
                "deadline_testo": (
                    metadati["deadline_testo"]
                ),
                "giorni_residui": (
                    valutazione["giorni_residui"]
                ),
                "modello_deadline": (
                    metadati[
                        "modello_deadline"
                    ]
                ),
            }
        )

    return risultati


def carica_topics_precedenti():
    """
    Legge il JSON esistente.
    """

    if not OUTPUT_FILE.exists():
        print(
            "Nessun archivio precedente "
            "EU Mission on Cancer trovato."
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

        topics = dati.get(
            "calls",
            [],
        )

        if not isinstance(
            topics,
            list,
        ):
            print(
                "Il campo 'calls' non è un elenco."
            )

            return []

        print(
            "Risultati nell'archivio precedente: "
            f"{len(topics)}"
        )

        return topics

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


def identifica_nuovi_topics(
    topics_precedenti,
    topics_correnti,
):
    """
    Identifica i topic nuovi in base al codice.
    """

    codici_precedenti = {
        topic.get("codice_topic")
        for topic in topics_precedenti
        if topic.get("codice_topic")
    }

    return [
        topic
        for topic in topics_correnti
        if topic["codice_topic"]
        not in codici_precedenti
    ]


def identifica_topics_rimossi(
    topics_precedenti,
    topics_correnti,
):
    """
    Identifica i topic precedentemente attivi
    che non risultano più nell'elenco corrente.
    """

    codici_correnti = {
        topic.get("codice_topic")
        for topic in topics_correnti
        if topic.get("codice_topic")
    }

    return [
        topic
        for topic in topics_precedenti
        if topic.get("codice_topic")
        and topic["codice_topic"]
        not in codici_correnti
    ]


def identifica_topics_modificati(
    topics_precedenti,
    topics_correnti,
):
    """
    Identifica variazioni a titolo, URL,
    apertura, deadline o stato.
    """

    precedenti_per_codice = {
        topic.get("codice_topic"): topic
        for topic in topics_precedenti
        if topic.get("codice_topic")
    }

    campi_da_confrontare = [
        "titolo",
        "url",
        "data_apertura",
        "deadline",
        "stato_submission",
    ]

    modificati = []

    for topic_corrente in topics_correnti:
        codice = topic_corrente.get(
            "codice_topic"
        )

        topic_precedente = (
            precedenti_per_codice.get(
                codice
            )
        )

        if not topic_precedente:
            continue

        campi_modificati = [
            campo
            for campo in campi_da_confrontare
            if topic_precedente.get(campo)
            != topic_corrente.get(campo)
        ]

        if campi_modificati:
            modificati.append(
                {
                    "codice_topic": codice,
                    "titolo": topic_corrente.get(
                        "titolo"
                    ),
                    "url": topic_corrente.get(
                        "url"
                    ),
                    "campi_modificati": (
                        campi_modificati
                    ),
                }
            )

    return modificati


def salva_risultati(
    topics,
    metadati,
    valutazione,
    data_pubblicazione,
    data_apertura,
    deadline,
    totale_topics_pagina,
):
    """
    Salva soltanto topic con submission aperta.

    Se la call è scaduta, il JSON resta valido
    ma l'elenco calls sarà vuoto.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": FONTE,
        "pagina_monitorata": PAGINA_CALLS,
        "criterio": (
            "oncologia e submission non scaduta"
        ),
        "codice_call": (
            metadati["riferimento"]
            or PREFISSO_TOPIC
        ),
        "stato_dichiarato": (
            metadati["stato_dichiarato"]
        ),
        "stato_submission": (
            valutazione["stato_submission"]
        ),
        "submission_aperta": (
            valutazione["submission_aperta"]
        ),
        "data_pubblicazione": (
            data_pubblicazione.isoformat()
            if data_pubblicazione
            else None
        ),
        "data_apertura": (
            data_apertura.isoformat()
            if data_apertura
            else None
        ),
        "deadline": (
            deadline.isoformat()
            if deadline
            else None
        ),
        "deadline_testo": (
            metadati["deadline_testo"]
        ),
        "modello_deadline": (
            metadati["modello_deadline"]
        ),
        "giorni_residui": (
            valutazione.get(
                "giorni_residui"
            )
        ),
        "totale_topics_nella_pagina": (
            totale_topics_pagina
        ),
        "numero_risultati": len(
            topics
        ),
        "calls": topics,
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


def formatta_data(data):
    """
    Converte una data in formato leggibile.
    """

    if data is None:
        return "non rilevata"

    data_locale = data.astimezone(
        FUSO_ORARIO_EUROPA
    )

    return data_locale.strftime(
        "%d/%m/%Y alle %H:%M %Z"
    )


def aggiungi_riepilogo_github(
    topics_correnti,
    nuovi_topics,
    topics_rimossi,
    topics_modificati,
    metadati,
    valutazione,
    deadline,
):
    """
    Aggiunge il riepilogo alla pagina
    dell'esecuzione GitHub Actions.
    """

    percorso = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if not percorso:
        return

    righe = [
        "# EU Mission on Cancer",
        "",
        (
            "Call monitorata: "
            f"**{metadati['riferimento'] or PREFISSO_TOPIC}**"
        ),
        "",
        (
            "Stato dichiarato dalla fonte: "
            f"**{metadati['stato_dichiarato'] or 'non rilevato'}**"
        ),
        "",
        (
            "Stato effettivo della submission: "
            f"**{valutazione['stato_submission']}**"
        ),
        "",
        (
            "Deadline: "
            f"**{formatta_data(deadline)}**"
        ),
        "",
        (
            "Deadline originale: "
            f"**{metadati['deadline_testo'] or 'non rilevata'}**"
        ),
        "",
        (
            "Giorni residui: "
            f"**{valutazione.get('giorni_residui')}**"
        ),
        "",
        (
            "Topic attivi rilevati: "
            f"**{len(topics_correnti)}**"
        ),
        "",
        (
            "Nuovi topic: "
            f"**{len(nuovi_topics)}**"
        ),
        "",
        (
            "Topic modificati: "
            f"**{len(topics_modificati)}**"
        ),
        "",
        (
            "Topic non più attivi: "
            f"**{len(topics_rimossi)}**"
        ),
        "",
    ]

    if topics_correnti:
        righe.extend(
            [
                "## Topic con submission aperta",
                "",
            ]
        )

        for topic in topics_correnti:
            righe.append(
                f"- **{topic['codice_topic']}**: "
                f"[{topic['titolo']}]"
                f"({topic['url']})"
            )

            righe.append(
                f"  - Deadline: "
                f"**{formatta_data(deadline)}**"
            )

            righe.append(
                f"  - Giorni residui: "
                f"**{topic['giorni_residui']}**"
            )

        righe.append("")

    else:
        righe.extend(
            [
                "Nessun topic con submission aperta.",
                "",
            ]
        )

    if topics_modificati:
        righe.extend(
            [
                "## Topic modificati",
                "",
            ]
        )

        for topic in topics_modificati:
            campi = ", ".join(
                topic["campi_modificati"]
            )

            righe.append(
                f"- **{topic['codice_topic']}**: "
                f"{campi}"
            )

        righe.append("")

    if topics_rimossi:
        righe.extend(
            [
                "## Topic non più attivi",
                "",
            ]
        )

        for topic in topics_rimossi:
            righe.append(
                f"- **{topic.get('codice_topic')}**: "
                f"{topic.get('titolo')}"
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


def stampa_elenco(titolo, topics):
    """
    Stampa un elenco leggibile nel registro.
    """

    print()
    print(titolo)
    print("-" * len(titolo))

    if not topics:
        print("Nessun risultato.")
        return

    for numero, topic in enumerate(
        topics,
        start=1,
    ):
        print(
            f"{numero}. "
            f"{topic.get('codice_topic')}"
        )

        print(
            f"   {topic.get('titolo')}"
        )

        print(
            f"   {topic.get('url')}"
        )

        deadline = topic.get(
            "deadline"
        )

        if deadline:
            data_deadline = (
                datetime.fromisoformat(
                    deadline
                )
            )

            print(
                "   Deadline: "
                f"{formatta_data(data_deadline)}"
            )

        giorni_residui = topic.get(
            "giorni_residui"
        )

        if giorni_residui is not None:
            print(
                "   Giorni residui: "
                f"{giorni_residui}"
            )


def main():
    """
    Funzione principale.
    """

    print("=" * 60)
    print(
        "MONITORAGGIO EU MISSION ON CANCER"
    )
    print("=" * 60)

    topics_precedenti = (
        carica_topics_precedenti()
    )

    sessione = crea_sessione()

    try:
        print(
            "Controllo della pagina: "
            f"{PAGINA_CALLS}"
        )

        html = scarica_pagina(
            sessione,
            PAGINA_CALLS,
        )

        testo = estrai_testo_principale(
            html
        )

        metadati = estrai_metadati_pagina(
            testo
        )

        data_pubblicazione = interpreta_data(
            metadati[
                "data_pubblicazione_testo"
            ]
        )

        data_apertura = interpreta_data(
            metadati[
                "data_apertura_testo"
            ]
        )

        deadline = interpreta_data(
            metadati["deadline_testo"],
            fine_giornata_se_senza_orario=True,
        )

        valutazione = valuta_submission(
            data_apertura,
            deadline,
        )

        topics_nella_pagina = estrai_topics(
            testo
        )

        if not topics_nella_pagina:
            raise RuntimeError(
                "Nessun topic Cancer Mission "
                "individuato nella pagina."
            )

        topics_correnti = costruisci_risultati(
            html,
            testo,
            metadati,
            valutazione,
            data_pubblicazione,
            data_apertura,
            deadline,
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

    nuovi_topics = identifica_nuovi_topics(
        topics_precedenti,
        topics_correnti,
    )

    topics_rimossi = identifica_topics_rimossi(
        topics_precedenti,
        topics_correnti,
    )

    topics_modificati = (
        identifica_topics_modificati(
            topics_precedenti,
            topics_correnti,
        )
    )

    salva_risultati(
        topics_correnti,
        metadati,
        valutazione,
        data_pubblicazione,
        data_apertura,
        deadline,
        len(topics_nella_pagina),
    )

    aggiungi_riepilogo_github(
        topics_correnti,
        nuovi_topics,
        topics_rimossi,
        topics_modificati,
        metadati,
        valutazione,
        deadline,
    )

    print()
    print(
        "Riferimento: "
        f"{metadati['riferimento']}"
    )

    print(
        "Stato dichiarato: "
        f"{metadati['stato_dichiarato']}"
    )

    print(
        "Apertura: "
        f"{formatta_data(data_apertura)}"
    )

    print(
        "Deadline originale: "
        f"{metadati['deadline_testo']}"
    )

    print(
        "Deadline interpretata: "
        f"{formatta_data(deadline)}"
    )

    print(
        "Stato effettivo submission: "
        f"{valutazione['stato_submission']}"
    )

    print(
        "Giorni residui: "
        f"{valutazione.get('giorni_residui')}"
    )

    print(
        "Topic presenti nella pagina: "
        f"{len(topics_nella_pagina)}"
    )

    print(
        "Topic con submission aperta: "
        f"{len(topics_correnti)}"
    )

    print(
        "Nuovi topic: "
        f"{len(nuovi_topics)}"
    )

    print(
        "Topic modificati: "
        f"{len(topics_modificati)}"
    )

    print(
        "Topic non più attivi: "
        f"{len(topics_rimossi)}"
    )

    print(
        f"File aggiornato: {OUTPUT_FILE}"
    )

    stampa_elenco(
        "TOPIC CON SUBMISSION APERTA",
        topics_correnti,
    )

    stampa_elenco(
        "NUOVI TOPIC",
        nuovi_topics,
    )

    stampa_elenco(
        "TOPIC NON PIÙ ATTIVI",
        topics_rimossi,
    )

    print()
    print(
        "Monitoraggio EU Mission on Cancer "
        "completato correttamente."
    )


if __name__ == "__main__":
    main()
