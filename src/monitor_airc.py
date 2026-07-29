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


FONTE = "AIRC"
BASE_URL = "https://www.direzionescientifica.airc.it"
PAGINA_CALENDARIO = f"{BASE_URL}/funding-for-research/calls-calendar/"
PAGINA_INDIVIDUAL_GRANTS = f"{BASE_URL}/funding-for-research/individual-grants/"
PAGINA_FELLOWSHIPS = f"{BASE_URL}/funding-for-research/fellowship/"
PAGINA_MULTIUNIT = f"{BASE_URL}/funding-for-research/multiunit-reasearch/"
OUTPUT_FILE = Path("data/airc_calls.json")
FUSO_ORARIO_ITALIA = ZoneInfo("Europe/Rome")
REQUEST_DELAY_SECONDS = 0.5

PAGINE_SEME = [
    PAGINA_CALENDARIO,
    PAGINA_INDIVIDUAL_GRANTS,
    PAGINA_FELLOWSHIPS,
    PAGINA_MULTIUNIT,
]

PAGINE_PROGRAMMI_CONOSCIUTI = [
    f"{BASE_URL}/funding-for-research/individual-grants/investigator-grant/",
    f"{BASE_URL}/funding-for-research/individual-grants/my-first-airc-grant/",
    f"{BASE_URL}/funding-for-research/individual-grants/start-up-grant/",
    f"{BASE_URL}/funding-for-research/individual-grants/bridge-grant/",
    f"{BASE_URL}/funding-for-research/individual-grants/next-gen-clinician-scientist-grant/",
    f"{BASE_URL}/funding-for-research/individual-grants/southern-italy-scholars-sis/",
    f"{BASE_URL}/funding-for-research/fellowship/italy-pre-doc/",
    f"{BASE_URL}/funding-for-research/fellowship/italy-post-doc/",
    f"{BASE_URL}/funding-for-research/fellowship/abroad-pre-doc/",
    f"{BASE_URL}/funding-for-research/fellowship/abroad-post-doc/",
    PAGINA_MULTIUNIT,
]

# Fallback limitati a pagine ufficiali AIRC 2026 per le quali data e stato
# risultano pubblicati, ma non sono sempre inclusi nell'HTML reso al runner.
FALLBACK_UFFICIALI_2026 = {
    "/funding-for-research/individual-grants/investigator-grant/": {
        "anno": "2026",
        "deadline": "06 Mar 2026",
        "stato_dichiarato": "scaduto",
    },
    "/funding-for-research/individual-grants/my-first-airc-grant/": {
        "anno": "2026",
        "deadline": "03 Mar 2026",
        "stato_dichiarato": "scaduto",
    },
    "/funding-for-research/individual-grants/bridge-grant/": {
        "anno": "2026",
        "deadline": "06 Mar 2026",
        "stato_dichiarato": "scaduto",
    },
    "/funding-for-research/individual-grants/next-gen-clinician-scientist-grant/": {
        "anno": "2026",
        "deadline": "06 Jul 2026",
        "stato_dichiarato": "scaduto",
    },
    "/funding-for-research/fellowship/italy-pre-doc/": {
        "anno": "2026",
        "deadline": "11 May 2026",
        "stato_dichiarato": "scaduto",
    },
    "/funding-for-research/fellowship/italy-post-doc/": {
        "anno": "2026",
        "deadline": "11 May 2026",
        "stato_dichiarato": "scaduto",
    },
    "/funding-for-research/multiunit-reasearch/": {
        "anno": "2026",
        "deadline": "10 Mar 2026",
        "full_proposal_deadline": "30 Jun 2026 at 17:00 CET",
        "stato_dichiarato": "scaduto",
    },
}


PERCORSI_AMMESSI = (
    "/funding-for-research/individual-grants/",
    "/funding-for-research/fellowship/",
    "/funding-for-research/multiunit-reasearch/",
)

PERCORSI_DA_ESCLUDERE = {
    "/funding-for-research/individual-grants/",
    "/funding-for-research/fellowship/",
}

INDICATORI_SCADUTO = {
    "expired",
    "call expired",
    "call is closed",
    "applications are closed",
    "submission closed",
}

INDICATORI_APERTO = {
    "open",
    "apply",
    "applications are open",
    "submission is open",
    "call for proposals",
}

MESI = (
    "january|february|march|april|may|june|july|august|september|"
    "october|november|december|gennaio|febbraio|marzo|aprile|maggio|"
    "giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
)

SCHEMI_DATA = [
    rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}"
    r"(?:\s*(?:at|alle|ore|,|by)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?",
    rf"\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}"
    r"(?:\s*(?:at|alle|ore|,|by)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?",
    r"\b\d{1,2}/\d{1,2}/\d{4}"
    r"(?:\s*(?:at|alle|ore|,|by)\s*\d{1,2}(?:[:.]\d{2})?)?",
]


def crea_sessione():
    sessione = requests.Session()
    sessione.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (compatible; ResearchCallsMonitor/1.0; "
                "+https://github.com/fmatt68/"
                "monitoraggio-bandi-ricerca-progetti-scientifici)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9,it;q=0.7",
        }
    )
    return sessione


def pulisci_testo(testo):
    return " ".join((testo or "").split())


def normalizza_testo(testo):
    testo = unicodedata.normalize("NFKD", testo or "")
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return pulisci_testo(
        testo.lower()
        .replace("–", "-")
        .replace("—", "-")
        .replace("’", "'")
        .replace("‘", "'")
    )


def normalizza_url(indirizzo, pagina_base=BASE_URL):
    assoluto = urljoin(pagina_base, (indirizzo or "").strip())
    elementi = urlparse(assoluto)
    percorso = elementi.path or "/"
    if not percorso.endswith("/") and "." not in percorso.rsplit("/", 1)[-1]:
        percorso += "/"
    if percorso == "/funding-for-research/individual-grants/southern-italy-scholars/":
        percorso = "/funding-for-research/individual-grants/southern-italy-scholars-sis/"

    return urlunparse(
        (elementi.scheme.lower(), elementi.netloc.lower(), percorso, "", "", "")
    )


def scarica_pagina(sessione, url):
    risposta = sessione.get(url, timeout=35, allow_redirects=True)
    risposta.raise_for_status()
    print(f"Pagina scaricata: HTTP {risposta.status_code} - {url}")
    return risposta.text


def estrai_testo_completo(html):
    """Estrae testo visibile, metadati e contenuti incorporati utili."""

    soup = BeautifulSoup(html, "html.parser")
    frammenti = []

    if soup.title:
        frammenti.append(pulisci_testo(soup.title.get_text(" ", strip=True)))

    for meta in soup.find_all("meta"):
        contenuto = meta.get("content")
        if contenuto:
            frammenti.append(pulisci_testo(contenuto))

    for script in soup.find_all("script"):
        contenuto = script.string or script.get_text(" ", strip=True)
        if contenuto:
            frammenti.append(pulisci_testo(contenuto))

    for elemento in soup.find_all(True):
        for nome, valore in elemento.attrs.items():
            if not nome.startswith("data-"):
                continue
            if isinstance(valore, list):
                valore = " ".join(str(x) for x in valore)
            frammenti.append(pulisci_testo(str(valore)))

    copia = BeautifulSoup(str(soup), "html.parser")
    for elemento in copia(
        ["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]
    ):
        elemento.decompose()

    area = copia.find("main") or copia.find("article") or copia.body or copia
    frammenti.append(pulisci_testo(area.get_text(" ", strip=True)))

    return pulisci_testo(" ".join(frammenti))


def estrai_titolo(html, fallback):
    soup = BeautifulSoup(html, "html.parser")
    intestazione = soup.find("h1") or soup.find("h2")
    if intestazione:
        titolo = pulisci_testo(intestazione.get_text(" ", strip=True))
        if titolo:
            return titolo
    if soup.title:
        titolo = pulisci_testo(soup.title.get_text(" ", strip=True))
        if titolo:
            return titolo.split(" - AIRC", 1)[0].strip()
    return fallback


def url_programma_valido(url):
    elementi = urlparse(url)
    if elementi.netloc not in {
        "direzionescientifica.airc.it",
        "www.direzionescientifica.airc.it",
    }:
        return False

    percorso = elementi.path
    if percorso in PERCORSI_DA_ESCLUDERE:
        return False
    if percorso == "/funding-for-research/calls-calendar/":
        return False
    if not any(percorso.startswith(prefisso) for prefisso in PERCORSI_AMMESSI):
        return False
    if any(
        parte in percorso.lower()
        for parte in (
            "/downloads/",
            "/previous-funding/",
            "/supporting-info/",
            "/privacy",
        )
    ):
        return False
    return True


def estrai_link_programmi(html, pagina_base):
    soup = BeautifulSoup(html, "html.parser")
    area = soup.find("main") or soup.find("article") or soup.body or soup
    risultati = []
    visti = set()

    for link in area.find_all("a", href=True):
        url = normalizza_url(link.get("href", ""), pagina_base)
        if not url_programma_valido(url):
            continue
        if url in visti:
            continue

        titolo = pulisci_testo(link.get_text(" ", strip=True))
        visti.add(url)
        risultati.append(
            {
                "titolo_indice": titolo or urlparse(url).path.rstrip("/").rsplit("/", 1)[-1],
                "url": url,
            }
        )

    return risultati


def raccogli_programmi(sessione):
    candidati = []
    visti = set()
    pagine_seme_accessibili = 0
    errori = []

    for pagina in PAGINE_SEME:
        print(f"Controllo pagina seme: {pagina}")
        try:
            html = scarica_pagina(sessione, pagina)
        except requests.RequestException as errore:
            errori.append(f"{pagina}: {errore}")
            print(f"  Pagina seme non accessibile: {errore}")
            continue

        pagine_seme_accessibili += 1
        for candidato in estrai_link_programmi(html, pagina):
            if candidato["url"] in visti:
                continue
            visti.add(candidato["url"])
            candidati.append(candidato)

    for pagina in PAGINE_PROGRAMMI_CONOSCIUTI:
        pagina = normalizza_url(pagina)
        if pagina in visti:
            continue
        visti.add(pagina)
        candidati.append(
            {
                "titolo_indice": urlparse(pagina).path.rstrip("/").rsplit("/", 1)[-1],
                "url": pagina,
            }
        )

    if pagine_seme_accessibili == 0:
        raise RuntimeError(
            "Nessuna pagina seme AIRC e risultata accessibile. "
            "Il precedente archivio non verra sovrascritto."
        )

    candidati.sort(key=lambda elemento: elemento["url"])
    return candidati, pagine_seme_accessibili, errori


def interpreta_data(stringa_data):
    contiene_orario = bool(re.search(r"\b\d{1,2}[:.]\d{2}\b", stringa_data))
    data = dateparser.parse(
        stringa_data.replace("(", " ").replace(")", " "),
        languages=["en", "it"],
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
        data = data.replace(tzinfo=FUSO_ORARIO_ITALIA)
    if not contiene_orario:
        data = data.replace(hour=23, minute=59, second=59, microsecond=0)
    return data


def estrai_data_etichettata(testo, etichetta):
    testo_pulito = pulisci_testo(testo)
    for schema_data in SCHEMI_DATA:
        schema = re.compile(
            re.escape(etichetta) + r"\s*(?::|-)?\s*(?P<data>" + schema_data + r")",
            flags=re.IGNORECASE,
        )
        corrispondenza = schema.search(testo_pulito)
        if not corrispondenza:
            continue
        stringa_data = pulisci_testo(corrispondenza.group("data"))
        data = interpreta_data(stringa_data)
        if data:
            return {
                "data": data.isoformat(),
                "testo": stringa_data,
            }
    return None


def estrai_deadline(testo):
    etichette = [
        "deadline for pre-submission",
        "deadline for pre-proposal submission",
        "deadline for submission",
        "application deadline",
        "deadline",
    ]

    for etichetta in etichette:
        risultato = estrai_data_etichettata(testo, etichetta)
        if risultato:
            return {
                "deadline": risultato["data"],
                "deadline_testo": risultato["testo"],
                "deadline_tipo": etichetta,
            }

    return None


def estrai_full_proposal_deadline(testo):
    etichette = [
        "deadline for full proposal",
        "deadline for full-proposal submission",
        "full proposal deadline",
    ]
    for etichetta in etichette:
        risultato = estrai_data_etichettata(testo, etichetta)
        if risultato:
            return {
                "deadline": risultato["data"],
                "deadline_testo": risultato["testo"],
            }
    return None


def estrai_anno(titolo, testo):
    testo_completo = f"{titolo} {testo}"
    anni = re.findall(r"\b20\d{2}\b", testo_completo)
    return max(anni) if anni else None


def determina_tipologia(url, titolo):
    percorso = urlparse(url).path.lower()
    titolo_norm = normalizza_testo(titolo)

    if "/fellowship/" in percorso:
        return "Fellowship"
    if "multiunit" in percorso or "5 per mille" in titolo_norm:
        return "Multi-unit Research Program"
    return "Individual Grant"


def determina_stato_dichiarato(testo):
    testo_norm = normalizza_testo(testo)

    if any(normalizza_testo(x) in testo_norm for x in INDICATORI_SCADUTO):
        return "scaduto"
    if any(normalizza_testo(x) in testo_norm for x in INDICATORI_APERTO):
        return "aperto"
    return "non_determinato"


def valuta_submission(stato_dichiarato, deadline_iso):
    if stato_dichiarato == "scaduto":
        return {
            "submission_aperta": False,
            "stato_submission": "scaduta",
        }

    if not deadline_iso:
        return {
            "submission_aperta": False,
            "stato_submission": "deadline_non_rilevata",
        }

    try:
        deadline = datetime.fromisoformat(deadline_iso)
    except ValueError:
        return {
            "submission_aperta": False,
            "stato_submission": "deadline_non_valida",
        }

    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=FUSO_ORARIO_ITALIA)

    if deadline.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        return {
            "submission_aperta": False,
            "stato_submission": "scaduta",
        }

    return {
        "submission_aperta": True,
        "stato_submission": "aperta",
    }


def applica_fallback_ufficiale_2026(url, anno, stato_dichiarato, deadline_info, full_deadline_info):
    """Applica fallback circoscritti alle sole pagine ufficiali AIRC mappate."""

    percorso = urlparse(url).path
    fallback = FALLBACK_UFFICIALI_2026.get(percorso)

    if not fallback:
        return anno, stato_dichiarato, deadline_info, full_deadline_info, None

    origine = None

    if not anno:
        anno = fallback.get("anno")
        origine = "fallback_ufficiale_2026"

    if stato_dichiarato == "non_determinato" and fallback.get("stato_dichiarato"):
        stato_dichiarato = fallback["stato_dichiarato"]
        origine = "fallback_ufficiale_2026"

    if deadline_info is None and fallback.get("deadline"):
        data = interpreta_data(fallback["deadline"])
        if data:
            deadline_info = {
                "deadline": data.isoformat(),
                "deadline_testo": fallback["deadline"],
                "deadline_tipo": "fallback pagina ufficiale",
            }
            origine = "fallback_ufficiale_2026"

    if full_deadline_info is None and fallback.get("full_proposal_deadline"):
        data = interpreta_data(fallback["full_proposal_deadline"])
        if data:
            full_deadline_info = {
                "deadline": data.isoformat(),
                "deadline_testo": fallback["full_proposal_deadline"],
            }
            origine = "fallback_ufficiale_2026"

    return anno, stato_dichiarato, deadline_info, full_deadline_info, origine


def analizza_programmi(sessione, candidati):
    tutti = []
    statistiche = {
        "candidate": len(candidati),
        "pagine_accessibili": 0,
        "pagine_non_analizzabili": 0,
        "non_determinati": 0,
        "deadline_non_rilevate": 0,
        "scaduti": 0,
        "aperti": 0,
    }

    for numero, candidato in enumerate(candidati, start=1):
        print(f"Analisi {numero}/{len(candidati)}: {candidato['url']}")

        try:
            html = scarica_pagina(sessione, candidato["url"])
        except requests.RequestException as errore:
            statistiche["pagine_non_analizzabili"] += 1
            print(f"  Pagina non analizzabile: {errore}")
            if numero < len(candidati):
                time.sleep(REQUEST_DELAY_SECONDS)
            continue

        statistiche["pagine_accessibili"] += 1
        titolo = estrai_titolo(html, candidato["titolo_indice"])
        testo = estrai_testo_completo(html)
        anno = estrai_anno(titolo, testo)
        tipologia = determina_tipologia(candidato["url"], titolo)
        stato_dichiarato = determina_stato_dichiarato(testo)
        deadline_info = estrai_deadline(testo)
        full_deadline_info = estrai_full_proposal_deadline(testo)

        (
            anno,
            stato_dichiarato,
            deadline_info,
            full_deadline_info,
            fallback_origine,
        ) = applica_fallback_ufficiale_2026(
            candidato["url"],
            anno,
            stato_dichiarato,
            deadline_info,
            full_deadline_info,
        )

        deadline_iso = deadline_info["deadline"] if deadline_info else None
        valutazione = valuta_submission(stato_dichiarato, deadline_iso)

        if valutazione["stato_submission"] == "deadline_non_rilevata":
            statistiche["deadline_non_rilevate"] += 1
        elif valutazione["submission_aperta"]:
            statistiche["aperti"] += 1
        else:
            statistiche["scaduti"] += 1

        if stato_dichiarato == "non_determinato" and not deadline_info:
            statistiche["non_determinati"] += 1

        programma = {
            "fonte": FONTE,
            "titolo": titolo,
            "anno_bando": anno,
            "tipologia": tipologia,
            "url": candidato["url"],
            "rilevanza": "oncologica",
            "sede_italiana_richiesta": (
                True if tipologia == "Individual Grant" else None
            ),
            "stato_dichiarato": stato_dichiarato,
            "submission_aperta": valutazione["submission_aperta"],
            "stato_submission": valutazione["stato_submission"],
            "deadline": deadline_iso,
            "deadline_testo": (
                deadline_info["deadline_testo"] if deadline_info else None
            ),
            "deadline_tipo": (
                deadline_info["deadline_tipo"] if deadline_info else None
            ),
            "full_proposal_deadline": (
                full_deadline_info["deadline"] if full_deadline_info else None
            ),
            "full_proposal_deadline_testo": (
                full_deadline_info["deadline_testo"] if full_deadline_info else None
            ),
            "dati_origine": (
                fallback_origine or "pagina_ufficiale"
            ),
        }

        tutti.append(programma)

        print(f"  Titolo: {titolo}")
        print(f"  Stato dichiarato: {stato_dichiarato}")
        print(f"  Deadline: {deadline_iso or 'non rilevata'}")
        print(f"  Stato submission: {valutazione['stato_submission']}")

        if numero < len(candidati):
            time.sleep(REQUEST_DELAY_SECONDS)

    unici = []
    visti = set()
    for programma in tutti:
        chiave = programma["url"]
        if chiave in visti:
            continue
        visti.add(chiave)
        unici.append(programma)

    unici.sort(key=lambda x: (x.get("deadline") or "", x["titolo"].lower()))
    attivi = [x for x in unici if x.get("submission_aperta") is True]
    return unici, attivi, statistiche


def carica_precedenti():
    if not OUTPUT_FILE.exists():
        return []
    try:
        with OUTPUT_FILE.open("r", encoding="utf-8") as file:
            dati = json.load(file)
        calls = dati.get("calls", [])
        return calls if isinstance(calls, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def confronta(precedenti, correnti):
    precedenti_per_url = {x.get("url"): x for x in precedenti if x.get("url")}
    correnti_per_url = {x.get("url"): x for x in correnti if x.get("url")}

    nuovi = [x for url, x in correnti_per_url.items() if url not in precedenti_per_url]
    rimossi = [x for url, x in precedenti_per_url.items() if url not in correnti_per_url]

    campi = [
        "titolo",
        "anno_bando",
        "deadline",
        "stato_submission",
        "submission_aperta",
        "full_proposal_deadline",
    ]

    modificati = []
    for url, corrente in correnti_per_url.items():
        precedente = precedenti_per_url.get(url)
        if not precedente:
            continue
        cambiati = [
            campo for campo in campi
            if precedente.get(campo) != corrente.get(campo)
        ]
        if cambiati:
            modificati.append(
                {
                    "titolo": corrente["titolo"],
                    "url": url,
                    "campi_modificati": cambiati,
                }
            )

    return nuovi, rimossi, modificati


def salva_risultati(tutti, attivi, statistiche, pagine_seme_accessibili):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    dati = {
        "fonte": FONTE,
        "pagine_monitorate": PAGINE_SEME,
        "criterio": "bandi oncologici AIRC con candidatura aperta",
        "totale_pagine_seme_accessibili": pagine_seme_accessibili,
        "totale_programmi_candidati": statistiche["candidate"],
        "totale_pagine_accessibili": statistiche["pagine_accessibili"],
        "totale_pagine_non_analizzabili": statistiche[
            "pagine_non_analizzabili"
        ],
        "totale_bandi_rilevati": len(tutti),
        "totale_bandi_scaduti": len(
            [x for x in tutti if x.get("stato_submission") == "scaduta"]
        ),
        "totale_deadline_non_rilevate": len(
            [
                x
                for x in tutti
                if x.get("stato_submission") == "deadline_non_rilevata"
            ]
        ),
        "numero_risultati": len(attivi),
        "calls": attivi,
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(dati, file, ensure_ascii=False, indent=2)
        file.write("\n")


def aggiungi_riepilogo(tutti, attivi, statistiche, nuovi, rimossi, modificati):
    percorso = os.environ.get("GITHUB_STEP_SUMMARY")
    if not percorso:
        return

    scaduti = [x for x in tutti if x.get("stato_submission") == "scaduta"]
    senza_deadline = [
        x for x in tutti if x.get("stato_submission") == "deadline_non_rilevata"
    ]

    righe = [
        "# AIRC",
        "",
        f"Bandi rilevati: **{len(tutti)}**",
        "",
        f"Bandi con candidatura aperta: **{len(attivi)}**",
        "",
        f"Bandi scaduti: **{len(scaduti)}**",
        "",
        f"Deadline non rilevate: **{len(senza_deadline)}**",
        "",
        f"Nuovi bandi attivi: **{len(nuovi)}**",
        "",
        f"Bandi modificati: **{len(modificati)}**",
        "",
        f"Bandi non piu attivi: **{len(rimossi)}**",
        "",
    ]

    if attivi:
        righe.extend(["## Bandi attivi", ""])
        for call in attivi:
            righe.append(f"- [{call['titolo']}]({call['url']})")
            righe.append(f"  - Deadline: **{call['deadline']}**")
            righe.append(f"  - Tipologia: **{call['tipologia']}**")
        righe.append("")
    else:
        righe.extend(["Nessun bando AIRC con candidatura aperta.", ""])

    with open(percorso, "a", encoding="utf-8") as file:
        file.write("\n".join(righe) + "\n")


def main():
    print("=" * 60)
    print("MONITORAGGIO AIRC")
    print("=" * 60)

    precedenti = carica_precedenti()
    print(f"Risultati attivi nell'archivio precedente: {len(precedenti)}")
    sessione = crea_sessione()

    try:
        candidati, pagine_seme_accessibili, errori = raccogli_programmi(sessione)
        print(f"Pagine seme accessibili: {pagine_seme_accessibili}")
        print(f"Programmi candidati trovati: {len(candidati)}")

        if not candidati:
            raise RuntimeError(
                "Nessun programma AIRC candidato individuato. "
                "Il precedente archivio non verra sovrascritto."
            )

        tutti, attivi, statistiche = analizza_programmi(sessione, candidati)

        if statistiche["pagine_accessibili"] == 0:
            raise RuntimeError(
                "Nessuna pagina di programma AIRC e risultata accessibile. "
                "Il precedente archivio non verra sovrascritto."
            )

    except (requests.RequestException, RuntimeError) as errore:
        print(f"Errore durante il monitoraggio: {errore}")
        raise SystemExit(1)

    nuovi, rimossi, modificati = confronta(precedenti, attivi)
    salva_risultati(tutti, attivi, statistiche, pagine_seme_accessibili)
    aggiungi_riepilogo(tutti, attivi, statistiche, nuovi, rimossi, modificati)

    print(f"Bandi rilevati: {len(tutti)}")
    print(f"Bandi con candidatura aperta: {len(attivi)}")
    print(
        "Bandi scaduti: "
        f"{len([x for x in tutti if x.get('stato_submission') == 'scaduta'])}"
    )
    print(
        "Deadline non rilevate: "
        f"{len([x for x in tutti if x.get('stato_submission') == 'deadline_non_rilevata'])}"
    )
    print(f"Nuovi bandi attivi: {len(nuovi)}")
    print(f"Bandi modificati: {len(modificati)}")
    print(f"Bandi non piu attivi: {len(rimossi)}")
    print(f"File aggiornato: {OUTPUT_FILE}")
    print("Monitoraggio AIRC completato correttamente.")


if __name__ == "__main__":
    main()
