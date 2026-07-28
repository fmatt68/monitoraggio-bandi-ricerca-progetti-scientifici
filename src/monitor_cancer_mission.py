import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

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

CODICE_CALL = "HORIZON-MISS-2026-02-CANCER"

SCADENZA = "2026-09-15T17:00:00+02:00"

STATO = "aperta"


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
        f"Pagina scaricata correttamente: "
        f"HTTP {risposta.status_code}"
    )

    return risposta.text


def pulisci_testo(testo):
    """
    Elimina spazi, tabulazioni e ritorni
    a capo ripetuti.
    """

    return " ".join(testo.split())


def normalizza_url(indirizzo):
    """
    Converte un URL relativo in un URL assoluto
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
    Estrae il testo informativo della pagina,
    eliminando menu, script, footer e altri
    elementi non rilevanti.
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


def trova_url_topic(
    soup,
    codice_topic,
):
    """
    Cerca nella pagina un collegamento associato
    allo specifico codice del topic.

    Se non viene trovato un collegamento diretto,
    restituisce la pagina generale della call.
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

        href = link.get(
            "href",
            "",
        )

        if (
            codice_topic in testo_link
            or codice_topic in href
        ):
            return normalizza_url(href)

    return PAGINA_CALLS


def trova_titolo_nel_testo(
    testo,
    codice_topic,
):
    """
    Cerca il titolo del topic nel testo della pagina.

    Il titolo viene individuato a partire dal codice
    HORIZON-MISS-2026-02-CANCER-XX.
    """

    schema = (
        re.escape(codice_topic)
        + r"\s*:\s*"
        + r"(.+?)"
        + r"(?="
        + re.escape(CODICE_CALL)
        + r"-\d{2}\s*:|$)"
    )

    corrispondenza = re.search(
        schema,
        testo,
        flags=re.IGNORECASE,
    )

    if not corrispondenza:
        return None

    titolo = pulisci_testo(
        corrispondenza.group(1)
    )

    frasi_finali = [
        "The European Commission",
        "Interested applicants",
        "Deadline",
        "Funding programme",
        "Programme Sector",
        "Tags",
    ]

    for frase in frasi_finali:
        posizione = titolo.find(frase)

        if posizione >= 0:
            titolo = titolo[:posizione]

    return titolo.strip(
        " .:-"
    )


def estrai_topics_da_testo(
    html,
):
    """
    Estrae automaticamente i topic della call
    dal testo della pagina HaDEA.
    """

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    testo = estrai_testo_principale(
        html
    )

    schema_codice = re.compile(
        re.escape(CODICE_CALL)
        + r"-\d{2}",
        flags=re.IGNORECASE,
    )

    codici_trovati = sorted(
        {
            codice.upper()
            for codice in schema_codice.findall(
                testo
            )
        }
    )

    risultati = []

    for codice_topic in codici_trovati:
        titolo = trova_titolo_nel_testo(
            testo,
            codice_topic,
        )

        if not titolo:
            print(
                "Attenzione: titolo non trovato "
                f"per {codice_topic}"
            )

            titolo = codice_topic

        url_topic = trova_url_topic(
            soup,
            codice_topic,
        )

        risultati.append(
            {
                "fonte": FONTE,
                "codice_call": CODICE_CALL,
                "codice_topic": codice_topic,
                "titolo": titolo,
                "url": url_topic,
                "pagina_fonte": PAGINA_CALLS,
                "stato": STATO,
                "scadenza": SCADENZA,
                "rilevanza": "oncologica",
                "parole_chiave_oncologiche": [
                    "cancer"
                ],
            }
        )

    risultati.sort(
        key=lambda elemento: (
            elemento["codice_topic"]
        )
    )

    return risultati


def carica_topics_precedenti():
    """
    Legge il JSON esistente.

    Se il file non esiste o non è valido,
    restituisce un elenco vuoto.
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
            dati = json.load(file)

        topics = dati.get(
            "calls",
            [],
        )

        if not isinstance(topics, list):
            print(
                "Il campo 'calls' "
                "non è un elenco."
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
    Identifica i topic non presenti
    nell'archivio precedente.
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
    Identifica i topic precedentemente archiviati
    che non compaiono più nella pagina.
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
    Identifica i topic presenti in entrambe
    le esecuzioni ma con dati modificati.
    """

    precedenti_per_codice = {
        topic.get("codice_topic"): topic
        for topic in topics_precedenti
        if topic.get("codice_topic")
    }

    modificati = []

    campi_da_confrontare = [
        "titolo",
        "url",
        "stato",
        "scadenza",
    ]

    for topic_corrente in topics_correnti:
        codice = topic_corrente.get(
            "codice_topic"
        )

        topic_precedente = (
            precedenti_per_codice.get(codice)
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
                    "titolo": topic_corrente[
                        "titolo"
                    ],
                    "url": topic_corrente[
                        "url"
                    ],
                    "campi_modificati": (
                        campi_modificati
                    ),
                }
            )

    return modificati


def salva_risultati(topics):
    """
    Salva il JSON in forma stabile.

    Non viene salvata la data di esecuzione,
    così non vengono creati commit inutili.
    """

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": FONTE,
        "pagina_monitorata": PAGINA_CALLS,
        "criterio": "oncologia",
        "codice_call": CODICE_CALL,
        "stato": STATO,
        "scadenza": SCADENZA,
        "numero_risultati": len(topics),
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


def aggiungi_riepilogo_github(
    topics_correnti,
    nuovi_topics,
    topics_rimossi,
    topics_modificati,
):
    """
    Inserisce un riepilogo nella pagina
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
            f"**{CODICE_CALL}**"
        ),
        "",
        (
            "Topic oncologici rilevati: "
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
            "Topic non più presenti: "
            f"**{len(topics_rimossi)}**"
        ),
        "",
        (
            "Scadenza: "
            "**15 settembre 2026, "
            "ore 17:00 CEST**"
        ),
        "",
        "## Topic correnti",
        "",
    ]

    for topic in topics_correnti:
        righe.append(
            f"- **{topic['codice_topic']}**: "
            f"[{topic['titolo']}]"
            f"({topic['url']})"
        )

    righe.append("")

    if nuovi_topics:
        righe.extend(
            [
                "## Nuovi topic",
                "",
            ]
        )

        for topic in nuovi_topics:
            righe.append(
                f"- **{topic['codice_topic']}**: "
                f"[{topic['titolo']}]"
                f"({topic['url']})"
            )

        righe.append("")

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
                f"campi modificati: {campi}"
            )

        righe.append("")

    if topics_rimossi:
        righe.extend(
            [
                "## Topic non più presenti",
                "",
            ]
        )

        for topic in topics_rimossi:
            righe.append(
                f"- **{topic.get('codice_topic')}**: "
                f"{topic.get('titolo')}"
            )

        righe.append("")

    if (
        not nuovi_topics
        and not topics_modificati
        and not topics_rimossi
    ):
        righe.extend(
            [
                "Nessuna variazione rispetto "
                "all'esecuzione precedente.",
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


def stampa_elenco(
    titolo,
    topics,
):
    """
    Stampa un elenco leggibile
    nel registro del workflow.
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
        codice = topic.get(
            "codice_topic",
            "Codice non disponibile",
        )

        titolo_topic = topic.get(
            "titolo",
            "Titolo non disponibile",
        )

        url = topic.get(
            "url",
            "URL non disponibile",
        )

        print(
            f"{numero}. {codice}"
        )

        print(
            f"   {titolo_topic}"
        )

        print(
            f"   {url}"
        )


def main():
    """
    Funzione principale del monitor.
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

        topics_correnti = (
            estrai_topics_da_testo(html)
        )

    except requests.RequestException as errore:
        print(
            "Errore durante il download: "
            f"{errore}"
        )

        print(
            "Il file precedente non verrà "
            "sovrascritto."
        )

        raise SystemExit(1)

    except Exception as errore:
        print(
            "Errore durante l'analisi: "
            f"{errore}"
        )

        print(
            "Il file precedente non verrà "
            "sovrascritto."
        )

        raise SystemExit(1)

    if not topics_correnti:
        print(
            "Errore: nessun topic Cancer Mission "
            "è stato individuato."
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
        topics_correnti
    )

    aggiungi_riepilogo_github(
        topics_correnti,
        nuovi_topics,
        topics_rimossi,
        topics_modificati,
    )

    print()
    print(
        "Topic oncologici rilevati: "
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
        "Topic non più presenti: "
        f"{len(topics_rimossi)}"
    )

    print(
        f"File aggiornato: {OUTPUT_FILE}"
    )

    stampa_elenco(
        "TOPIC CORRENTI",
        topics_correnti,
    )

    stampa_elenco(
        "NUOVI TOPIC",
        nuovi_topics,
    )

    stampa_elenco(
        "TOPIC NON PIÙ PRESENTI",
        topics_rimossi,
    )

    print()
    print(
        "Monitoraggio EU Mission on Cancer "
        "completato correttamente."
    )


if __name__ == "__main__":
    main()
