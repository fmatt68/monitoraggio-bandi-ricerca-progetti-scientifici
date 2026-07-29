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


FONTE = "ERA4Health"
PAGINA_CALLS = "https://era4health.eu/calls/"
OUTPUT_FILE = Path("data/era4health_calls.json")
FUSO_ORARIO_EUROPA = ZoneInfo("Europe/Rome")
REQUEST_DELAY_SECONDS = 0.4

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

INDICATORI_ESCLUSIONE_ONCOLOGIA = {
    "cancer is out of scope",
    "cancer and/or infectious diseases are out of the scope",
    "cancer are out of scope",
    "cancers are out of scope",
    "oncology is out of scope",
    "tumours are out of scope",
    "tumors are out of scope",
}

INDICATORI_CHIUSURA = {
    "call is closed",
    "call closed",
    "applications are closed",
    "submission is closed",
    "submissions are closed",
    "closed call",
}

MESI = (
    "january|february|march|april|may|june|july|august|september|"
    "october|november|december|gennaio|febbraio|marzo|aprile|maggio|"
    "giugno|luglio|agosto|settembre|ottobre|novembre|dicembre"
)

SCHEMI_DATA = [
    rf"\b\d{{1,2}}\s+(?:{MESI})\s*,?\s*\d{{4}}"
    r"(?:\s*(?:at|alle|ore|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?",
    rf"\b(?:{MESI})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"
    r"(?:\s*(?:at|alle|ore|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?",
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
        testo.lower().replace("–", "-").replace("—", "-")
    )


def scarica_pagina(sessione, url):
    risposta = sessione.get(url, timeout=35, allow_redirects=True)
    risposta.raise_for_status()
    print(f"Pagina scaricata: HTTP {risposta.status_code} - {url}")
    return risposta.text


def normalizza_url(indirizzo, pagina_base=PAGINA_CALLS):
    """
    Normalizza i collegamenti ERA4Health evitando il percorso duplicato
    /calls/calls/ osservato quando l'indice usa href come calls/nome.php.
    """

    indirizzo = (indirizzo or "").strip()

    if not indirizzo:
        return ""

    if indirizzo.startswith("calls/"):
        assoluto = urljoin("https://era4health.eu/", indirizzo)
    else:
        assoluto = urljoin(pagina_base, indirizzo)

    elementi = urlparse(assoluto)
    percorso = re.sub(r"^/calls/calls/", "/calls/", elementi.path)

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


def estrai_testo_principale(html):
    soup = BeautifulSoup(html, "html.parser")
    for elemento in soup(
        ["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]
    ):
        elemento.decompose()
    area = soup.find("main") or soup.find("article") or soup.body or soup
    return pulisci_testo(area.get_text(" ", strip=True))


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
            return titolo
    return fallback


def estrai_link_calls(html):
    """
    Estrae soltanto le pagine delle call ERA4Health.

    Esclude menu, contatti, cookie, news, pubblicazioni e formazione.
    Accetta le pagine PHP direttamente sotto /calls/ il cui nome contiene
    un anno, per esempio trials4health2026.php o preventoo2026.php.
    """

    soup = BeautifulSoup(html, "html.parser")
    area = soup.find("main") or soup.find("article") or soup.body or soup

    risultati = []
    visti = set()

    schema_call = re.compile(
        r"^/calls/(?:pre_)?[a-z0-9_-]*20\d{2}\.php$",
        flags=re.IGNORECASE,
    )

    for link in area.find_all("a", href=True):
        url = normalizza_url(link.get("href", ""))

        if not url:
            continue

        elementi = urlparse(url)
        testo_link = pulisci_testo(link.get_text(" ", strip=True))

        if elementi.netloc not in {"era4health.eu", "www.era4health.eu"}:
            continue

        if not schema_call.match(elementi.path):
            continue

        if url in visti:
            continue

        visti.add(url)

        risultati.append(
            {
                "titolo_indice": (
                    testo_link
                    or elementi.path.rsplit("/", 1)[-1]
                ),
                "url": url,
            }
        )

    risultati.sort(key=lambda elemento: elemento["url"])

    return risultati


def trova_termini(testo, termini):
    testo_norm = normalizza_testo(testo)
    risultati = []

    for termine in termini:
        termine_norm = normalizza_testo(termine)
        schema = r"(?<![a-z0-9])" + re.escape(termine_norm) + r"(?![a-z0-9])"
        if re.search(schema, testo_norm):
            risultati.append(termine)

    return sorted(risultati)


def oncologia_esplicitamente_esclusa(testo):
    testo_norm = normalizza_testo(testo)
    return sorted(
        indicatore
        for indicatore in INDICATORI_ESCLUSIONE_ONCOLOGIA
        if normalizza_testo(indicatore) in testo_norm
    )


def pagina_dichiarata_chiusa(testo):
    testo_norm = normalizza_testo(testo)
    return sorted(
        indicatore
        for indicatore in INDICATORI_CHIUSURA
        if normalizza_testo(indicatore) in testo_norm
    )


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
        "deadline for pre-proposal submission": 55,
        "deadline for pre-proposals": 50,
        "pre-proposal submission": 40,
        "deadline for proposal submission": 45,
        "deadline for proposals": 40,
        "submission deadline": 40,
        "deadline": 20,
        "submission": 10,
    }
    negativi = {
        "full proposal": -10,
        "invited full proposal": -20,
        "communication of results": -25,
        "project start": -20,
        "webinar": -20,
        "pre-announcement": -10,
    }

    punteggio = 0
    for criterio, valore in positivi.items():
        if criterio in testo:
            punteggio += valore
    for criterio, valore in negativi.items():
        if criterio in testo:
            punteggio += valore
    return punteggio


def estrai_deadline_iniziale(testo):
    candidati = []

    for schema_data in SCHEMI_DATA:
        schema = re.compile(r"(.{0,220}" + schema_data + r".{0,220})", re.IGNORECASE)
        for corrispondenza in schema.finditer(testo):
            contesto = pulisci_testo(corrispondenza.group(1))
            contesto_norm = normalizza_testo(contesto)

            if not any(
                parola in contesto_norm
                for parola in ("deadline", "submission", "pre-proposal", "proposal")
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
                    "contesto": contesto,
                    "punteggio": calcola_punteggio_deadline(contesto),
                }
            )

    if not candidati:
        return None

    candidati.sort(key=lambda x: (x["punteggio"], -x["data"].timestamp()), reverse=True)
    migliore = candidati[0]

    if migliore["punteggio"] < 25:
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
    risultati = []

    statistiche = {
        "candidate": len(candidati),
        "pagine_accessibili": 0,
        "pagine_non_analizzabili": 0,
        "non_oncologiche": 0,
        "oncologia_esclusa": 0,
        "dichiarate_chiuse": 0,
        "scadute": 0,
        "deadline_non_rilevate": 0,
    }

    for numero, candidato in enumerate(
        candidati,
        start=1,
    ):
        print(
            f"Analisi {numero}/{len(candidati)}: "
            f"{candidato['url']}"
        )

        try:
            html = scarica_pagina(
                sessione,
                candidato["url"],
            )

        except requests.RequestException as errore:
            statistiche[
                "pagine_non_analizzabili"
            ] += 1

            print(
                "  Pagina non analizzabile: "
                f"{errore}"
            )

        else:
            statistiche[
                "pagine_accessibili"
            ] += 1

            titolo = estrai_titolo(
                html,
                candidato["titolo_indice"],
            )

            testo = estrai_testo_principale(
                html
            )

            testo_completo = (
                f"{titolo} {testo}"
            )

            esclusioni = oncologia_esplicitamente_esclusa(
                testo_completo
            )

            if esclusioni:
                statistiche[
                    "oncologia_esclusa"
                ] += 1

                print(
                    "  Esclusa: il testo dichiara "
                    "l'oncologia fuori ambito."
                )

            else:
                parole_oncologiche = trova_termini(
                    testo_completo,
                    TERMINI_ONCOLOGICI,
                )

                if not parole_oncologiche:
                    statistiche[
                        "non_oncologiche"
                    ] += 1

                    print(
                        "  Esclusa: nessun termine "
                        "oncologico rilevato."
                    )

                else:
                    chiusure = pagina_dichiarata_chiusa(
                        testo_completo
                    )

                    if chiusure:
                        statistiche[
                            "dichiarate_chiuse"
                        ] += 1

                        print(
                            "  Esclusa: call dichiarata chiusa."
                        )

                    else:
                        info_deadline = estrai_deadline_iniziale(
                            testo_completo
                        )

                        if info_deadline is None:
                            statistiche[
                                "deadline_non_rilevate"
                            ] += 1

                            print(
                                "  Esclusa prudenzialmente: "
                                "deadline iniziale non rilevata."
                            )

                        else:
                            valutazione = valuta_deadline(
                                info_deadline["deadline"]
                            )

                            print(
                                "  Deadline iniziale: "
                                f"{info_deadline['deadline']}"
                            )

                            print(
                                "  Stato submission: "
                                f"{valutazione['stato_submission']}"
                            )

                            if not valutazione[
                                "submission_aperta"
                            ]:
                                statistiche[
                                    "scadute"
                                ] += 1

                            else:
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
                                            info_deadline["deadline"]
                                        ),
                                        "deadline_testo": (
                                            info_deadline["deadline_testo"]
                                        ),
                                        "deadline_affidabilita": (
                                            info_deadline[
                                                "deadline_affidabilita"
                                            ]
                                        ),
                                    }
                                )

        if numero < len(candidati):
            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    risultati.sort(
        key=lambda elemento: (
            elemento["deadline"],
            elemento["titolo"].lower(),
        )
    )

    return risultati, statistiche


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
    campi = ["titolo", "deadline", "stato_submission", "rilevanza"]
    modificati = []

    for url, corrente in correnti_per_url.items():
        precedente = precedenti_per_url.get(url)
        if not precedente:
            continue
        cambiati = [campo for campo in campi if precedente.get(campo) != corrente.get(campo)]
        if cambiati:
            modificati.append(
                {
                    "titolo": corrente["titolo"],
                    "url": url,
                    "campi_modificati": cambiati,
                }
            )

    return nuovi, rimossi, modificati


def salva_risultati(risultati, statistiche):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    dati = {
        "fonte": FONTE,
        "pagina_monitorata": PAGINA_CALLS,
        "criterio": "oncologia e submission iniziale non scaduta",
        "totale_pagine_candidate": statistiche["candidate"],
        "totale_pagine_accessibili": statistiche["pagine_accessibili"],
        "totale_pagine_non_analizzabili": statistiche["pagine_non_analizzabili"],
        "totale_non_oncologiche": statistiche["non_oncologiche"],
        "totale_oncologia_esclusa": statistiche["oncologia_esclusa"],
        "totale_dichiarate_chiuse": statistiche["dichiarate_chiuse"],
        "totale_scadute": statistiche["scadute"],
        "totale_deadline_non_rilevate": statistiche["deadline_non_rilevate"],
        "numero_risultati": len(risultati),
        "calls": risultati,
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(dati, file, ensure_ascii=False, indent=2)
        file.write("\n")


def aggiungi_riepilogo(risultati, statistiche, nuovi, rimossi, modificati):
    percorso = os.environ.get("GITHUB_STEP_SUMMARY")
    if not percorso:
        return

    righe = [
        "# ERA4Health",
        "",
        f"Call oncologiche attive: **{len(risultati)}**",
        "",
        f"Pagine candidate: **{statistiche['candidate']}**",
        "",
        f"Call non oncologiche: **{statistiche['non_oncologiche']}**",
        "",
        f"Call con oncologia esplicitamente fuori ambito: **{statistiche['oncologia_esclusa']}**",
        "",
        f"Call scadute o non aperte: **{statistiche['scadute']}**",
        "",
        f"Nuove call: **{len(nuovi)}**",
        "",
        f"Call modificate: **{len(modificati)}**",
        "",
        f"Call non piu attive: **{len(rimossi)}**",
        "",
    ]

    if risultati:
        righe.extend(["## Call attive", ""])
        for call in risultati:
            righe.append(f"- [{call['titolo']}]({call['url']})")
            righe.append(f"  - Deadline: **{call['deadline']}**")
        righe.append("")
    else:
        righe.extend(["Nessuna call oncologica con submission iniziale aperta.", ""])

    with open(percorso, "a", encoding="utf-8") as file:
        file.write("\n".join(righe) + "\n")


def main():
    print("=" * 60)
    print("MONITORAGGIO ERA4HEALTH")
    print("=" * 60)

    precedenti = carica_precedenti()
    print(f"Risultati nell'archivio precedente: {len(precedenti)}")
    sessione = crea_sessione()

    try:
        print(f"Controllo pagina delle call: {PAGINA_CALLS}")
        html_indice = scarica_pagina(sessione, PAGINA_CALLS)
        candidati = estrai_link_calls(html_indice)

        if not candidati:
            raise RuntimeError(
                "Nessuna pagina di call individuata nell'indice ERA4Health. "
                "Il precedente archivio non verra sovrascritto."
            )

        print(f"Pagine candidate trovate: {len(candidati)}")
        risultati, statistiche = analizza_calls(sessione, candidati)

        if statistiche["pagine_accessibili"] == 0:
            raise RuntimeError(
                "Nessuna pagina candidata ERA4Health e risultata accessibile. "
                "Il precedente archivio non verra sovrascritto."
            )

    except (requests.RequestException, RuntimeError) as errore:
        print(f"Errore durante il monitoraggio: {errore}")
        raise SystemExit(1)

    nuovi, rimossi, modificati = confronta(precedenti, risultati)
    salva_risultati(risultati, statistiche)
    aggiungi_riepilogo(risultati, statistiche, nuovi, rimossi, modificati)

    print(f"Call oncologiche attive: {len(risultati)}")
    print(f"Nuove call: {len(nuovi)}")
    print(f"Call modificate: {len(modificati)}")
    print(f"Call non piu attive: {len(rimossi)}")
    print(f"File aggiornato: {OUTPUT_FILE}")
    print("Monitoraggio ERA4Health completato correttamente.")


if __name__ == "__main__":
    main()
