import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import dateparser
import requests
from bs4 import BeautifulSoup


FONTE = "World Cancer Research Fund International"
OUTPUT_FILE = Path("data/wcrf_calls.json")
FUSO_ORARIO_LONDRA = ZoneInfo("Europe/London")
FUSO_ORARIO_ITALIA = ZoneInfo("Europe/Rome")

PROGRAMMI = [
    {
        "codice": "WCRF-RGP",
        "titolo": "Regular Grant Programme",
        "url": (
            "https://www.wcrf.org/research-policy/our-grant-programmes/"
            "regular-grant-programme/"
        ),
        "tipologia": "Research Grant",
        "ammissibilita_geografica": "Tutto il mondo eccetto le Americhe",
        "fase_iniziale": "outline_application",
    },
    {
        "codice": "WCRF-INSPIRE",
        "titolo": "INSPIRE Research Challenge",
        "url": (
            "https://www.wcrf.org/research-policy/our-grant-programmes/"
            "inspire-research-challenge/"
        ),
        "tipologia": "Early Career Research Grant",
        "ammissibilita_geografica": "Ricercatori early-career in tutto il mondo",
        "fase_iniziale": "letter_of_intent",
    },
]

PAGINA_PROGRAMMI = (
    "https://www.wcrf.org/research-policy/our-grant-programmes/"
)

INDICATORI_CHIUSURA = {
    "the grant call has now closed",
    "grant call has now closed",
    "applications are closed",
    "call is closed",
    "no longer accepting applications",
}

INDICATORI_APERTURA = {
    "applications are open",
    "grant call is open",
    "call is now open",
    "apply now",
    "how to apply",
}

MESI = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december"
)

SCHEMI_DATA = [
    rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}"
    r"(?:\s*(?:at|,|by)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:GMT|BST|UTC|CET|CEST))?",
    rf"\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}"
    r"(?:\s*(?:at|,|by)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:GMT|BST|UTC|CET|CEST))?",
]


# Fallback ufficiali limitati al round 2025/26. Sono usati soltanto se le
# pagine ufficiali risultano accessibili ma non espongono la data al runner.
FALLBACK_UFFICIALI = {
    "WCRF-RGP": {
        "round": "2025/26",
        "deadline": "4 November 2025 at 17:00 GMT",
        "stato": "chiusa",
    },
    "WCRF-INSPIRE": {
        "round": "2025/26",
        "deadline": "4 November 2025 at 17:00 GMT",
        "stato": "chiusa",
    },
}


def crea_sessione():
    sessione = requests.Session()
    sessione.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; ResearchCallsMonitor/1.0; "
                "+https://github.com/fmatt68/"
                "monitoraggio-bandi-ricerca-progetti-scientifici)"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-GB,en;q=0.9,it;q=0.7",
        }
    )
    return sessione


def pulisci_testo(testo):
    return " ".join((testo or "").split())


def normalizza_testo(testo):
    testo = unicodedata.normalize("NFKD", testo or "")
    testo = "".join(
        carattere
        for carattere in testo
        if not unicodedata.combining(carattere)
    )
    return pulisci_testo(
        testo.lower()
        .replace("–", "-")
        .replace("—", "-")
        .replace("’", "'")
        .replace("‘", "'")
    )


def scarica_pagina(sessione, url):
    risposta = sessione.get(
        url,
        timeout=35,
        allow_redirects=True,
    )
    risposta.raise_for_status()
    print(
        f"Pagina scaricata: HTTP {risposta.status_code} - {url}"
    )
    return risposta.text


def estrai_testo_completo(html):
    soup = BeautifulSoup(html, "html.parser")
    frammenti = []

    if soup.title:
        frammenti.append(
            pulisci_testo(
                soup.title.get_text(" ", strip=True)
            )
        )

    for meta in soup.find_all("meta"):
        contenuto = meta.get("content")
        if contenuto:
            frammenti.append(
                pulisci_testo(contenuto)
            )

    for script in soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    ):
        contenuto = script.string or script.get_text(
            " ",
            strip=True,
        )
        if contenuto:
            frammenti.append(
                pulisci_testo(contenuto)
            )

    copia = BeautifulSoup(str(soup), "html.parser")

    for elemento in copia(
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
        copia.find("main")
        or copia.find("article")
        or copia.body
        or copia
    )

    frammenti.append(
        pulisci_testo(
            area.get_text(" ", strip=True)
        )
    )

    return pulisci_testo(" ".join(frammenti))


def contiene_indicatore(testo, indicatori):
    testo_normalizzato = normalizza_testo(testo)
    return sorted(
        indicatore
        for indicatore in indicatori
        if normalizza_testo(indicatore)
        in testo_normalizzato
    )


def interpreta_data(stringa_data):
    contiene_orario = bool(
        re.search(
            r"\b\d{1,2}[:.]\d{2}\b",
            stringa_data,
        )
    )

    data = dateparser.parse(
        stringa_data.replace("(", " ").replace(")", " "),
        languages=["en"],
        settings={
            "DATE_ORDER": "DMY",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Europe/London",
            "TO_TIMEZONE": "Europe/Rome",
            "STRICT_PARSING": False,
        },
    )

    if data is None:
        return None

    if data.tzinfo is None:
        data = data.replace(
            tzinfo=FUSO_ORARIO_LONDRA
        ).astimezone(FUSO_ORARIO_ITALIA)

    if not contiene_orario:
        data = data.replace(
            hour=23,
            minute=59,
            second=59,
            microsecond=0,
        )

    return data


def estrai_data_etichettata(testo, etichette):
    testo_pulito = pulisci_testo(testo)

    for etichetta in etichette:
        for schema_data in SCHEMI_DATA:
            schema = re.compile(
                re.escape(etichetta)
                + r"\s*(?::|-)?\s*(?P<data>"
                + schema_data
                + r")",
                flags=re.IGNORECASE,
            )

            corrispondenza = schema.search(
                testo_pulito
            )

            if not corrispondenza:
                continue

            stringa_data = pulisci_testo(
                corrispondenza.group("data")
            )

            data = interpreta_data(
                stringa_data
            )

            if data:
                return {
                    "deadline": data.isoformat(),
                    "deadline_testo": stringa_data,
                    "deadline_etichetta": etichetta,
                    "deadline_origine": "pagina_ufficiale",
                }

    return None


def estrai_deadline_iniziale(testo, codice):
    if codice == "WCRF-RGP":
        etichette = [
            "deadline for outline applications",
            "outline application deadline",
            "deadline for applications",
        ]
    else:
        etichette = [
            "deadline for letter of intent",
            "letter of intent deadline",
            "deadline for applications",
        ]

    return estrai_data_etichettata(
        testo,
        etichette,
    )


def estrai_deadline_full_application(testo):
    return estrai_data_etichettata(
        testo,
        [
            "deadline for full applications",
            "full application deadline",
        ],
    )


def estrai_round(testo):
    schemi = [
        r"\b(20\d{2}/\d{2})\b",
        r"\b(20\d{2})\s+grant call\b",
        r"\bgrant call\s+(20\d{2})\b",
    ]

    for schema in schemi:
        corrispondenza = re.search(
            schema,
            testo,
            flags=re.IGNORECASE,
        )
        if corrispondenza:
            return corrispondenza.group(1)

    return None


def applica_fallback_ufficiale(
    codice,
    round_call,
    deadline_info,
    stato_dichiarato,
):
    fallback = FALLBACK_UFFICIALI.get(
        codice
    )

    if not fallback:
        return (
            round_call,
            deadline_info,
            stato_dichiarato,
            None,
        )

    origine = None

    if not round_call:
        round_call = fallback["round"]
        origine = "fallback_ufficiale_2025_26"

    if deadline_info is None:
        data = interpreta_data(
            fallback["deadline"]
        )

        if data:
            deadline_info = {
                "deadline": data.isoformat(),
                "deadline_testo": fallback["deadline"],
                "deadline_etichetta": "fallback ufficiale",
                "deadline_origine": (
                    "fallback_ufficiale_2025_26"
                ),
            }
            origine = "fallback_ufficiale_2025_26"

    if stato_dichiarato == "non_determinato":
        stato_dichiarato = fallback["stato"]
        origine = "fallback_ufficiale_2025_26"

    return (
        round_call,
        deadline_info,
        stato_dichiarato,
        origine,
    )


def determina_stato_dichiarato(testo):
    indicatori_chiusura = contiene_indicatore(
        testo,
        INDICATORI_CHIUSURA,
    )

    if indicatori_chiusura:
        return "chiusa", indicatori_chiusura

    indicatori_apertura = contiene_indicatore(
        testo,
        INDICATORI_APERTURA,
    )

    if indicatori_apertura:
        return "aperta", indicatori_apertura

    return "non_determinato", []


def valuta_submission(
    stato_dichiarato,
    deadline_iso,
):
    if stato_dichiarato == "chiusa":
        return {
            "submission_aperta": False,
            "stato_submission": "chiusa",
        }

    if not deadline_iso:
        return {
            "submission_aperta": False,
            "stato_submission": (
                "deadline_non_rilevata"
            ),
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
        }

    if deadline.tzinfo is None:
        deadline = deadline.replace(
            tzinfo=FUSO_ORARIO_ITALIA
        )

    if (
        deadline.astimezone(timezone.utc)
        <= datetime.now(timezone.utc)
    ):
        return {
            "submission_aperta": False,
            "stato_submission": "scaduta",
        }

    return {
        "submission_aperta": True,
        "stato_submission": "aperta",
    }


def analizza_programma(sessione, programma):
    html = scarica_pagina(
        sessione,
        programma["url"],
    )

    testo = estrai_testo_completo(
        html
    )

    round_call = estrai_round(
        testo
    )

    stato_dichiarato, indicatori_stato = (
        determina_stato_dichiarato(
            testo
        )
    )

    deadline_info = estrai_deadline_iniziale(
        testo,
        programma["codice"],
    )

    full_application_info = (
        estrai_deadline_full_application(
            testo
        )
    )

    (
        round_call,
        deadline_info,
        stato_dichiarato,
        fallback_origine,
    ) = applica_fallback_ufficiale(
        programma["codice"],
        round_call,
        deadline_info,
        stato_dichiarato,
    )

    deadline_iso = (
        deadline_info["deadline"]
        if deadline_info
        else None
    )

    valutazione = valuta_submission(
        stato_dichiarato,
        deadline_iso,
    )

    risultato = {
        "fonte": FONTE,
        "codice": programma["codice"],
        "titolo": programma["titolo"],
        "round": round_call,
        "tipologia": programma["tipologia"],
        "url": programma["url"],
        "rilevanza": "oncologica",
        "ambito": (
            "prevenzione, nutrizione, stili di vita "
            "e survivorship oncologica"
        ),
        "ammissibilita_geografica": (
            programma[
                "ammissibilita_geografica"
            ]
        ),
        "fase_iniziale": (
            programma["fase_iniziale"]
        ),
        "stato_dichiarato": stato_dichiarato,
        "indicatori_stato": indicatori_stato,
        "submission_aperta": (
            valutazione["submission_aperta"]
        ),
        "stato_submission": (
            valutazione["stato_submission"]
        ),
        "deadline": deadline_iso,
        "deadline_testo": (
            deadline_info["deadline_testo"]
            if deadline_info
            else None
        ),
        "deadline_origine": (
            deadline_info["deadline_origine"]
            if deadline_info
            else None
        ),
        "full_application_deadline": (
            full_application_info["deadline"]
            if full_application_info
            else None
        ),
        "full_application_deadline_testo": (
            full_application_info[
                "deadline_testo"
            ]
            if full_application_info
            else None
        ),
        "dati_origine": (
            fallback_origine
            or "pagina_ufficiale"
        ),
    }

    print(
        f"  Programma: {risultato['titolo']}"
    )
    print(
        f"  Round: {round_call or 'non rilevato'}"
    )
    print(
        "  Stato dichiarato: "
        f"{stato_dichiarato}"
    )
    print(
        "  Deadline iniziale: "
        f"{deadline_iso or 'non rilevata'}"
    )
    print(
        "  Stato submission: "
        f"{valutazione['stato_submission']}"
    )

    return risultato


def carica_precedenti():
    if not OUTPUT_FILE.exists():
        return []

    try:
        with OUTPUT_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:
            dati = json.load(file)

        calls = dati.get("calls", [])

        return calls if isinstance(calls, list) else []

    except (
        OSError,
        json.JSONDecodeError,
    ):
        return []


def confronta(precedenti, correnti):
    precedenti_per_codice = {
        elemento.get("codice"): elemento
        for elemento in precedenti
        if elemento.get("codice")
    }

    correnti_per_codice = {
        elemento.get("codice"): elemento
        for elemento in correnti
        if elemento.get("codice")
    }

    nuovi = [
        elemento
        for codice, elemento
        in correnti_per_codice.items()
        if codice not in precedenti_per_codice
    ]

    rimossi = [
        elemento
        for codice, elemento
        in precedenti_per_codice.items()
        if codice not in correnti_per_codice
    ]

    campi = [
        "round",
        "stato_dichiarato",
        "submission_aperta",
        "stato_submission",
        "deadline",
        "full_application_deadline",
    ]

    modificati = []

    for codice, corrente in correnti_per_codice.items():
        precedente = precedenti_per_codice.get(
            codice
        )

        if not precedente:
            continue

        cambiati = [
            campo
            for campo in campi
            if precedente.get(campo)
            != corrente.get(campo)
        ]

        if cambiati:
            modificati.append(
                {
                    "codice": codice,
                    "titolo": corrente["titolo"],
                    "url": corrente["url"],
                    "campi_modificati": cambiati,
                }
            )

    return nuovi, rimossi, modificati


def salva_risultati(
    tutti,
    attivi,
    pagine_accessibili,
    pagine_non_analizzabili,
):
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dati = {
        "fonte": FONTE,
        "pagina_programmi": PAGINA_PROGRAMMI,
        "criterio": (
            "grant oncologici WCRF con "
            "candidatura iniziale aperta"
        ),
        "totale_programmi_monitorati": len(PROGRAMMI),
        "totale_pagine_accessibili": pagine_accessibili,
        "totale_pagine_non_analizzabili": (
            pagine_non_analizzabili
        ),
        "totale_programmi_rilevati": len(tutti),
        "totale_programmi_chiusi_o_scaduti": len(
            [
                elemento
                for elemento in tutti
                if elemento.get("submission_aperta")
                is False
            ]
        ),
        "numero_risultati": len(attivi),
        "calls": attivi,
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


def aggiungi_riepilogo(
    tutti,
    attivi,
    nuovi,
    rimossi,
    modificati,
):
    percorso = os.environ.get(
        "GITHUB_STEP_SUMMARY"
    )

    if not percorso:
        return

    righe = [
        "# World Cancer Research Fund International",
        "",
        f"Programmi rilevati: **{len(tutti)}**",
        "",
        (
            "Programmi con candidatura iniziale aperta: "
            f"**{len(attivi)}**"
        ),
        "",
        (
            "Programmi chiusi o scaduti: "
            f"**{len([x for x in tutti if x.get('submission_aperta') is False])}**"
        ),
        "",
        f"Nuovi programmi attivi: **{len(nuovi)}**",
        "",
        f"Programmi modificati: **{len(modificati)}**",
        "",
        f"Programmi non piu attivi: **{len(rimossi)}**",
        "",
    ]

    if attivi:
        righe.extend(
            [
                "## Opportunita attive",
                "",
            ]
        )

        for call in attivi:
            righe.append(
                f"- [{call['titolo']}]({call['url']})"
            )
            righe.append(
                f"  - Deadline: **{call['deadline']}**"
            )

        righe.append("")

    else:
        righe.extend(
            [
                "Nessuna candidatura iniziale attualmente aperta.",
                "",
            ]
        )

    if tutti:
        righe.extend(
            [
                "## Programmi monitorati",
                "",
            ]
        )

        for programma in tutti:
            righe.append(
                f"- **{programma['titolo']}**: "
                f"{programma['stato_submission']} "
                f"({programma.get('deadline_testo') or 'deadline non rilevata'})"
            )

        righe.append("")

    with open(
        percorso,
        "a",
        encoding="utf-8",
    ) as file:
        file.write("\n".join(righe) + "\n")


def main():
    print("=" * 60)
    print(
        "MONITORAGGIO WORLD CANCER RESEARCH FUND"
    )
    print("=" * 60)

    precedenti = carica_precedenti()
    print(
        "Risultati attivi nell'archivio precedente: "
        f"{len(precedenti)}"
    )

    sessione = crea_sessione()
    tutti = []
    pagine_accessibili = 0
    pagine_non_analizzabili = 0

    for numero, programma in enumerate(
        PROGRAMMI,
        start=1,
    ):
        print(
            f"Analisi {numero}/{len(PROGRAMMI)}: "
            f"{programma['url']}"
        )

        try:
            risultato = analizza_programma(
                sessione,
                programma,
            )

        except requests.RequestException as errore:
            pagine_non_analizzabili += 1
            print(
                "  Pagina non analizzabile: "
                f"{errore}"
            )
            continue

        pagine_accessibili += 1
        tutti.append(risultato)

    if pagine_accessibili == 0:
        print(
            "Errore: nessuna pagina WCRF e risultata accessibile."
        )
        print(
            "Il precedente archivio non verra sovrascritto."
        )
        raise SystemExit(1)

    attivi = [
        elemento
        for elemento in tutti
        if elemento.get("submission_aperta") is True
    ]

    nuovi, rimossi, modificati = confronta(
        precedenti,
        attivi,
    )

    salva_risultati(
        tutti,
        attivi,
        pagine_accessibili,
        pagine_non_analizzabili,
    )

    aggiungi_riepilogo(
        tutti,
        attivi,
        nuovi,
        rimossi,
        modificati,
    )

    print(
        f"Programmi rilevati: {len(tutti)}"
    )
    print(
        "Programmi con candidatura iniziale aperta: "
        f"{len(attivi)}"
    )
    print(
        "Programmi chiusi o scaduti: "
        f"{len([x for x in tutti if x.get('submission_aperta') is False])}"
    )
    print(
        f"Nuovi programmi attivi: {len(nuovi)}"
    )
    print(
        f"Programmi modificati: {len(modificati)}"
    )
    print(
        f"Programmi non piu attivi: {len(rimossi)}"
    )
    print(
        f"File aggiornato: {OUTPUT_FILE}"
    )
    print(
        "Monitoraggio World Cancer Research Fund "
        "completato correttamente."
    )


if __name__ == "__main__":
    main()
