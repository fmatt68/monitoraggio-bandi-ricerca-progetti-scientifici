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
EP_PERMED_URL = "https://www.eppermed.eu/funding-projects/calls/"
OUTPUT_FILE = Path("data/ep_permed_calls.json")
REQUEST_DELAY_SECONDS = 0.4
FUSO_ORARIO_EUROPA = ZoneInfo("Europe/Rome")

TERMINI_ONCOLOGICI = {
    "cancer", "cancers", "oncology", "oncological", "oncologic",
    "tumor", "tumors", "tumour", "tumours", "neoplasm", "neoplasms",
    "neoplastic", "malignancy", "malignancies", "malignant",
    "carcinoma", "carcinomas", "sarcoma", "sarcomas", "leukemia",
    "leukaemia", "lymphoma", "lymphomas", "myeloma", "metastasis",
    "metastases", "metastatic", "melanoma", "glioma", "glioblastoma",
    "mesothelioma", "neuroblastoma", "retinoblastoma", "medulloblastoma",
    "cancro", "tumore", "tumori", "oncologia", "oncologico",
    "oncologica", "oncologici", "oncologiche", "neoplasia", "neoplasie",
    "neoplastico", "neoplastica", "neoplastici", "neoplastiche",
    "carcinomi", "sarcomi", "leucemia", "linfoma", "linfomi", "mieloma",
    "metastasi",
}

TERMINI_DA_VERIFICARE = {
    "precision medicine", "personalised medicine", "personalized medicine",
    "precision oncology", "immunotherapy", "immunotherapies",
    "cell therapy", "cell therapies", "gene therapy", "gene therapies",
    "advanced therapy", "advanced therapies", "biomarker", "biomarkers",
    "liquid biopsy", "liquid biopsies", "circulating tumour dna",
    "circulating tumor dna", "genomic profiling", "molecular profiling",
    "early detection", "screening",
}

INDICATORI_NESSUNA_CALL_APERTA = {
    "there are currently no open calls available",
    "there are no open calls available",
    "no open calls available",
}

MESI = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december|gennaio|febbraio|marzo|"
    "aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
)

SCHEMI_DATA = [
    rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}"
    r"(?:\s*(?:at|alle|ore|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT|BST))?",
    rf"\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}"
    r"(?:\s*(?:at|alle|ore|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT|BST))?",
    r"\b\d{1,2}/\d{1,2}/\d{4}"
    r"(?:\s*(?:at|alle|ore|,)\s*\d{1,2}(?:[:.]\d{2})?)?",
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
            "Accept-Language": "en-US,en;q=0.9,it;q=0.8",
        }
    )
    return sessione


def scarica_pagina(sessione, url):
    risposta = sessione.get(url, timeout=30)
    risposta.raise_for_status()
    print(f"Pagina scaricata: HTTP {risposta.status_code} - {url}")
    return risposta.text


def pulisci_testo(testo):
    return " ".join((testo or "").split())


def normalizza_testo(testo):
    testo = unicodedata.normalize("NFKD", testo or "")
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return pulisci_testo(
        testo.lower().replace("–", "-").replace("—", "-")
    )


def normalizza_url(indirizzo):
    assoluto = urljoin(EP_PERMED_URL, (indirizzo or "").strip())
    elementi = urlparse(assoluto)
    percorso = elementi.path or "/"
    if not percorso.endswith("/"):
        percorso += "/"
    return urlunparse(
        (elementi.scheme.lower(), elementi.netloc.lower(), percorso, "", "", "")
    )


def pagina_dichiara_nessuna_call_aperta(html):
    soup = BeautifulSoup(html, "html.parser")
    testo = normalizza_testo(soup.get_text(" ", strip=True))
    return any(indicatore in testo for indicatore in INDICATORI_NESSUNA_CALL_APERTA)


def trova_sezione_open_calls(soup):
    for intestazione in soup.find_all(["h1", "h2", "h3", "h4"]):
        if normalizza_testo(intestazione.get_text(" ", strip=True)) != "open calls":
            continue

        contenuti = []
        for fratello in intestazione.next_siblings:
            nome = getattr(fratello, "name", None)
            if nome in {"h1", "h2", "h3", "h4"}:
                break
            contenuti.append(str(fratello))

        return BeautifulSoup("".join(contenuti), "html.parser")

    return None


def trova_titolo(link):
    titolo = pulisci_testo(link.get_text(" ", strip=True))
    if normalizza_testo(titolo) not in {"read more", "learn more", "more information"}:
        return titolo

    contenitore = link
    for _ in range(8):
        contenitore = contenitore.parent
        if contenitore is None:
            break
        intestazione = contenitore.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if intestazione:
            possibile = pulisci_testo(intestazione.get_text(" ", strip=True))
            if possibile:
                return possibile

    return titolo


def estrai_link_calls_aperte(html):
    soup = BeautifulSoup(html, "html.parser")
    sezione = trova_sezione_open_calls(soup)

    if sezione is None:
        return []

    risultati = []
    url_visti = set()

    for link in sezione.find_all("a", href=True):
        titolo = trova_titolo(link)
        indirizzo = normalizza_url(link.get("href", ""))
        elementi = urlparse(indirizzo)

        if elementi.netloc not in {"eppermed.eu", "www.eppermed.eu"}:
            continue
        if not elementi.path.startswith("/funding-projects/calls/"):
            continue
        if indirizzo.rstrip("/") == EP_PERMED_URL.rstrip("/"):
            continue
        if not titolo or normalizza_testo(titolo) in {"read more", "learn more"}:
            continue
        if indirizzo in url_visti:
            continue

        url_visti.add(indirizzo)
        risultati.append({"fonte": FONTE, "titolo": titolo, "url": indirizzo})

    return risultati


def estrai_testo_principale(html):
    soup = BeautifulSoup(html, "html.parser")
    for elemento in soup(
        ["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]
    ):
        elemento.decompose()
    area = soup.find("main") or soup.find("article") or soup.body or soup
    return pulisci_testo(area.get_text(" ", strip=True))


def termine_presente(testo_normalizzato, termine):
    termine_normalizzato = normalizza_testo(termine)
    schema = r"(?<![a-z0-9])" + re.escape(termine_normalizzato) + r"(?![a-z0-9])"
    return re.search(schema, testo_normalizzato) is not None


def trova_corrispondenze(testo, termini):
    testo_normalizzato = normalizza_testo(testo)
    return sorted(
        termine
        for termine in termini
        if termine_presente(testo_normalizzato, termine)
    )


def classifica_call(titolo, testo_pagina):
    testo_completo = f"{titolo} {testo_pagina}"
    oncologiche = trova_corrispondenze(testo_completo, TERMINI_ONCOLOGICI)
    secondarie = trova_corrispondenze(testo_completo, TERMINI_DA_VERIFICARE)

    if oncologiche:
        rilevanza = "oncologica"
    elif len(secondarie) >= 2:
        rilevanza = "da_verificare"
    else:
        rilevanza = "non_pertinente"

    return rilevanza, oncologiche, secondarie


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
        data = data.replace(tzinfo=FUSO_ORARIO_EUROPA)
    if not contiene_orario:
        data = data.replace(hour=23, minute=59, second=59, microsecond=0)
    return data


def calcola_punteggio_deadline(contesto):
    testo = normalizza_testo(contesto)
    positivi = {
        "deadline for proposals submissions": 50,
        "deadline for proposal submission": 50,
        "proposal submission deadline": 45,
        "submission deadline": 40,
        "deadline for proposals": 35,
        "deadline for applications": 35,
        "application deadline": 35,
        "deadline": 20,
        "submission": 10,
        "proposal": 5,
        "application": 5,
    }
    negativi = {
        "opening": -20,
        "webinar": -25,
        "matchmaking": -20,
        "notification": -20,
        "eligibility check": -20,
        "final results": -25,
        "kick off": -20,
        "contracting": -15,
    }

    punteggio = 0
    for criterio, valore in positivi.items():
        if criterio in testo:
            punteggio += valore
    for criterio, valore in negativi.items():
        if criterio in testo:
            punteggio += valore
    return punteggio


def estrai_deadline_submission(testo):
    candidati = []

    for schema_data in SCHEMI_DATA:
        schema = re.compile(r"(.{0,220}" + schema_data + r".{0,220})", re.IGNORECASE)
        for corrispondenza in schema.finditer(testo):
            contesto = pulisci_testo(corrispondenza.group(1))
            contesto_norm = normalizza_testo(contesto)

            if not any(
                parola in contesto_norm
                for parola in ("deadline", "submission", "submit", "proposal", "application")
            ):
                continue

            data_match = re.search(schema_data, contesto, re.IGNORECASE)
            if not data_match:
                continue

            stringa_data = pulisci_testo(data_match.group(0))
            data = interpreta_data(stringa_data)
            if data is None:
                continue

            candidati.append(
                {
                    "data": data,
                    "testo": stringa_data,
                    "punteggio": calcola_punteggio_deadline(contesto),
                }
            )

    if not candidati:
        return None

    candidati.sort(key=lambda x: (x["punteggio"], x["data"]), reverse=True)
    migliore = candidati[0]

    if migliore["punteggio"] < 20:
        return None

    return {
        "deadline": migliore["data"].isoformat(),
        "deadline_testo": migliore["testo"],
        "deadline_affidabilita": migliore["punteggio"],
    }


def valuta_deadline(deadline_iso):
    if not deadline_iso:
        return {
            "submission_aperta": False,
            "stato_submission": "deadline_non_rilevata",
            "giorni_residui": None,
        }

    try:
        deadline = datetime.fromisoformat(deadline_iso)
    except ValueError:
        return {
            "submission_aperta": False,
            "stato_submission": "deadline_non_valida",
            "giorni_residui": None,
        }

    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=FUSO_ORARIO_EUROPA)

    differenza = deadline.astimezone(timezone.utc) - datetime.now(timezone.utc)
    secondi = differenza.total_seconds()

    if secondi <= 0:
        return {
            "submission_aperta": False,
            "stato_submission": "scaduta",
            "giorni_residui": 0,
        }

    giorni = int(secondi // 86400)
    if secondi % 86400:
        giorni += 1

    return {
        "submission_aperta": True,
        "stato_submission": "aperta",
        "giorni_residui": giorni,
    }


def analizza_calls(sessione, candidati):
    selezionate = []
    statistiche = {
        "non_pertinenti": 0,
        "scadute": 0,
        "deadline_non_rilevate": 0,
        "deadline_non_valide": 0,
    }

    for numero, call in enumerate(candidati, start=1):
        print(f"Analisi {numero}/{len(candidati)}: {call['titolo']}")
        html = scarica_pagina(sessione, call["url"])
        testo = estrai_testo_principale(html)
        rilevanza, oncologiche, secondarie = classifica_call(call["titolo"], testo)
        print(f"  Classificazione: {rilevanza}")

        if rilevanza == "non_pertinente":
            statistiche["non_pertinenti"] += 1
            continue

        info_deadline = estrai_deadline_submission(testo)
        if info_deadline is None:
            statistiche["deadline_non_rilevate"] += 1
            print("  Esclusa: deadline di submission non rilevata.")
            continue

        valutazione = valuta_deadline(info_deadline["deadline"])
        print(f"  Deadline: {info_deadline['deadline']}")
        print(f"  Stato submission: {valutazione['stato_submission']}")

        if valutazione["stato_submission"] == "scaduta":
            statistiche["scadute"] += 1
            continue
        if valutazione["stato_submission"] == "deadline_non_valida":
            statistiche["deadline_non_valide"] += 1
            continue
        if not valutazione["submission_aperta"]:
            continue

        selezionate.append(
            {
                **call,
                "rilevanza": rilevanza,
                "parole_chiave_oncologiche": oncologiche,
                "parole_chiave_secondarie": secondarie,
                "submission_aperta": True,
                "stato_submission": "aperta",
                "deadline": info_deadline["deadline"],
                "deadline_testo": info_deadline["deadline_testo"],
                "deadline_affidabilita": info_deadline["deadline_affidabilita"],
            }
        )

        if numero < len(candidati):
            time.sleep(REQUEST_DELAY_SECONDS)

    selezionate.sort(key=lambda x: (x["deadline"], x["titolo"].lower()))
    return selezionate, statistiche


def carica_calls_precedenti():
    if not OUTPUT_FILE.exists():
        return []
    try:
        with OUTPUT_FILE.open("r", encoding="utf-8") as file:
            dati = json.load(file)
        calls = dati.get("calls", [])
        return calls if isinstance(calls, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def identifica_nuove_calls(precedenti, correnti):
    url_precedenti = {x.get("url") for x in precedenti if x.get("url")}
    return [x for x in correnti if x.get("url") not in url_precedenti]


def identifica_calls_rimosse(precedenti, correnti):
    url_correnti = {x.get("url") for x in correnti if x.get("url")}
    return [x for x in precedenti if x.get("url") and x.get("url") not in url_correnti]


def identifica_calls_modificate(precedenti, correnti):
    precedenti_per_url = {x.get("url"): x for x in precedenti if x.get("url")}
    campi = ["titolo", "rilevanza", "deadline", "stato_submission"]
    modificati = []

    for corrente in correnti:
        precedente = precedenti_per_url.get(corrente.get("url"))
        if not precedente:
            continue
        cambiati = [campo for campo in campi if precedente.get(campo) != corrente.get(campo)]
        if cambiati:
            modificati.append(
                {
                    "titolo": corrente["titolo"],
                    "url": corrente["url"],
                    "campi_modificati": cambiati,
                }
            )

    return modificati


def salva_risultati(calls, totale_candidati, statistiche, stato_indice):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    dati = {
        "fonte": FONTE,
        "pagina_monitorata": EP_PERMED_URL,
        "criterio": "oncologia e submission non scaduta",
        "stato_indice": stato_indice,
        "totale_pagine_candidate": totale_candidati,
        "totale_non_pertinenti": statistiche["non_pertinenti"],
        "totale_call_scadute": statistiche["scadute"],
        "totale_deadline_non_rilevate": statistiche["deadline_non_rilevate"],
        "totale_deadline_non_valide": statistiche["deadline_non_valide"],
        "numero_risultati": len(calls),
        "calls": calls,
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(dati, file, ensure_ascii=False, indent=2)
        file.write("\n")


def aggiungi_riepilogo_github(calls, nuove, rimosse, modificate, statistiche, stato_indice):
    percorso = os.environ.get("GITHUB_STEP_SUMMARY")
    if not percorso:
        return

    righe = [
        "# Monitoraggio oncologico EP PerMed",
        "",
        f"Stato indice: **{stato_indice}**",
        "",
        f"Call attive: **{len(calls)}**",
        "",
        f"Nuove call: **{len(nuove)}**",
        "",
        f"Call modificate: **{len(modificate)}**",
        "",
        f"Call non piu attive: **{len(rimosse)}**",
        "",
    ]

    if calls:
        righe.extend(["## Call attive", ""])
        for call in calls:
            righe.append(f"- [{call['titolo']}]({call['url']})")
            righe.append(f"  - Deadline: **{call['deadline']}**")
        righe.append("")
    else:
        righe.extend(["Nessuna call oncologica con submission aperta.", ""])

    with open(percorso, "a", encoding="utf-8") as file:
        file.write("\n".join(righe) + "\n")


def main():
    print("=" * 60)
    print("MONITORAGGIO ONCOLOGICO EP PERMED")
    print("=" * 60)

    calls_precedenti = carica_calls_precedenti()
    print(f"Risultati nell'archivio precedente: {len(calls_precedenti)}")
    sessione = crea_sessione()

    try:
        print(f"Controllo dell'indice: {EP_PERMED_URL}")
        html_indice = scarica_pagina(sessione, EP_PERMED_URL)

        if pagina_dichiara_nessuna_call_aperta(html_indice):
            print("EP PerMed dichiara che non sono disponibili call aperte.")
            candidati = []
            calls_correnti = []
            statistiche = {
                "non_pertinenti": 0,
                "scadute": 0,
                "deadline_non_rilevate": 0,
                "deadline_non_valide": 0,
            }
            stato_indice = "nessuna_call_aperta_dichiarata"
        else:
            candidati = estrai_link_calls_aperte(html_indice)

            if not candidati:
                print()
                print(
                    "Avviso: la pagina EP PerMed e stata scaricata, ma il contenuto "
                    "ricevuto dal runner GitHub non consente di verificare la sezione Open Calls."
                )
                print(
                    "Il precedente archivio viene conservato senza modifiche e il workflow prosegue."
                )

                percorso_riepilogo = os.environ.get("GITHUB_STEP_SUMMARY")
                if percorso_riepilogo:
                    with open(percorso_riepilogo, "a", encoding="utf-8") as file:
                        file.write(
                            "# Monitoraggio oncologico EP PerMed\n\n"
                            "Stato del controllo: **indice non interpretabile dal runner GitHub**\n\n"
                            "La pagina ha risposto con HTTP 200, ma non e stato possibile "
                            "verificare la sezione Open Calls. L'archivio precedente e stato "
                            "conservato senza modifiche.\n\n"
                        )

                print("Monitoraggio EP PerMed concluso con avviso, senza sovrascrivere il JSON.")
                return

            print(f"Call aperte candidate trovate: {len(candidati)}")
            calls_correnti, statistiche = analizza_calls(sessione, candidati)
            stato_indice = "call_aperte_analizzate"

    except (requests.RequestException, RuntimeError) as errore:
        print(f"Errore durante il monitoraggio: {errore}")
        print("Il file precedente non verra sovrascritto.")
        raise SystemExit(1)

    nuove = identifica_nuove_calls(calls_precedenti, calls_correnti)
    rimosse = identifica_calls_rimosse(calls_precedenti, calls_correnti)
    modificate = identifica_calls_modificate(calls_precedenti, calls_correnti)

    salva_risultati(calls_correnti, len(candidati), statistiche, stato_indice)
    aggiungi_riepilogo_github(
        calls_correnti,
        nuove,
        rimosse,
        modificate,
        statistiche,
        stato_indice,
    )

    print(f"Stato indice: {stato_indice}")
    print(f"Pagine candidate analizzate: {len(candidati)}")
    print(f"Call attive selezionate: {len(calls_correnti)}")
    print(f"Nuove call attive: {len(nuove)}")
    print(f"Call modificate: {len(modificate)}")
    print(f"Call non piu attive: {len(rimosse)}")
    print(f"File aggiornato: {OUTPUT_FILE}")
    print("Monitoraggio EP PerMed completato correttamente.")


if __name__ == "__main__":
    main()
