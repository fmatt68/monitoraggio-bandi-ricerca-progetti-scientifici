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


FONTE = "Fondazione Umberto Veronesi"
PAGINA_BANDI = "https://www.fondazioneveronesi.it/temi/bandi"
PAGINA_FELLOWSHIP_2027 = (
    "https://www.fondazioneveronesi.it/news/"
    "aperti-i-bandi-post-doctoral-fellowship-2027-di-fondazione-veronesi"
)
PORTALE_CANDIDATURE = "https://grant.fondazioneveronesi.it/"
OUTPUT_FILE = Path("data/fondazione_veronesi_calls.json")
FUSO_ORARIO_ITALIA = ZoneInfo("Europe/Rome")

PAGINE_SEME = [
    PAGINA_BANDI,
    PAGINA_FELLOWSHIP_2027,
]

TERMINI_BANDO = {
    "post-doctoral fellowship",
    "post-doctoral fellowships",
    "post doctoral fellowship",
    "borsa post-dottorato",
    "borse post-dottorato",
    "borse di ricerca",
    "bando experimental",
    "bando clinical",
}

TERMINI_ONCOLOGICI = {
    "cancer", "oncology", "oncological", "tumor", "tumour", "tumori",
    "oncologia", "oncologico", "oncologica", "neoplasia", "neoplasie",
    "carcinoma", "metastatic", "metastasi", "immuno-oncology",
    "immunotherapy", "precision medicine", "prevenzione oncologica",
}

TERMINI_CHIUSURA = {
    "candidature chiuse",
    "bando chiuso",
    "applications are closed",
    "call is closed",
    "deadline has passed",
}

MESI = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|"
    "settembre|ottobre|novembre|dicembre|january|february|march|"
    "april|may|june|july|august|september|october|november|december"
)

SCHEMI_DATA = [
    rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}"
    r"(?:\s*(?:alle|ore|at|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?",
    rf"\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}"
    r"(?:\s*(?:alle|ore|at|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT))?",
    r"\b\d{1,2}/\d{1,2}/\d{4}"
    r"(?:\s*(?:alle|ore|at|,)\s*\d{1,2}(?:[:.]\d{2})?)?",
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
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
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


def normalizza_url(indirizzo, pagina_base):
    assoluto = urljoin(pagina_base, (indirizzo or "").strip())
    elementi = urlparse(assoluto)
    return urlunparse(
        (elementi.scheme.lower(), elementi.netloc.lower(), elementi.path, "", "", "")
    )


def scarica_pagina(sessione, url):
    risposta = sessione.get(url, timeout=35, allow_redirects=True)
    risposta.raise_for_status()
    print(f"Pagina scaricata: HTTP {risposta.status_code} - {url}")
    return risposta.text


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


def trova_termini(testo, termini):
    testo_norm = normalizza_testo(testo)
    risultati = []

    for termine in termini:
        termine_norm = normalizza_testo(termine)
        schema = r"(?<![a-z0-9])" + re.escape(termine_norm) + r"(?![a-z0-9])"
        if re.search(schema, testo_norm):
            risultati.append(termine)

    return sorted(risultati)


def estrai_link_bandi(html, pagina_base):
    """
    Estrae soltanto articoli che sembrano annunci di bando o fellowship.

    Esclude pagine generiche, profili dei ricercatori e vecchi articoli
    non utili al monitoraggio corrente.
    """

    soup = BeautifulSoup(html, "html.parser")
    risultati = []
    visti = set()
    anno_minimo = datetime.now(FUSO_ORARIO_ITALIA).year - 2

    for link in soup.find_all("a", href=True):
        testo_link = pulisci_testo(link.get_text(" ", strip=True))
        url = normalizza_url(link.get("href", ""), pagina_base)
        elementi = urlparse(url)
        percorso_norm = normalizza_testo(elementi.path)
        testo_completo = normalizza_testo(f"{testo_link} {url}")

        if elementi.netloc not in {
            "fondazioneveronesi.it",
            "www.fondazioneveronesi.it",
            "grant.fondazioneveronesi.it",
        }:
            continue

        if elementi.netloc == "grant.fondazioneveronesi.it":
            continue

        if not elementi.path.startswith("/news/"):
            continue

        if not any(
            parola in testo_completo
            for parola in (
                "bando",
                "fellowship",
                "borse post-dottorato",
                "borse-post-dottorato",
            )
        ):
            continue

        anni = [int(anno) for anno in re.findall(r"20\d{2}", percorso_norm)]
        if anni and max(anni) < anno_minimo:
            continue

        if url in visti:
            continue

        visti.add(url)
        risultati.append(
            {
                "titolo_indice": testo_link or "Bando Fondazione Veronesi",
                "url": url,
            }
        )

    return risultati


def raccogli_pagine_candidate(sessione):
    candidati = []
    visti = set()
    pagine_accessibili = 0
    errori = []

    for pagina in PAGINE_SEME:
        print(f"Controllo pagina seme: {pagina}")

        try:
            html = scarica_pagina(sessione, pagina)
        except requests.RequestException as errore:
            errori.append(f"{pagina}: {errore}")
            print(f"  Pagina non accessibile: {errore}")
            continue

        pagine_accessibili += 1

        if pagina == PAGINA_FELLOWSHIP_2027 and pagina not in visti:
            visti.add(pagina)
            candidati.append(
                {
                    "titolo_indice": "Post-Doctoral Fellowships 2027",
                    "url": pagina,
                }
            )

        for candidato in estrai_link_bandi(html, pagina):
            if candidato["url"] in visti:
                continue
            visti.add(candidato["url"])
            candidati.append(candidato)

    if pagine_accessibili == 0:
        raise RuntimeError(
            "Nessuna pagina della Fondazione Veronesi e risultata accessibile. "
            "Il precedente archivio non verra sovrascritto."
        )

    candidati.sort(key=lambda x: x["url"])
    return candidati, pagine_accessibili, errori


def interpreta_data(stringa_data):
    contiene_orario = bool(re.search(r"\b\d{1,2}[:.]\d{2}\b", stringa_data))
    data = dateparser.parse(
        stringa_data.replace("(", " ").replace(")", " "),
        languages=["it", "en"],
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


def calcola_punteggio_deadline(contesto):
    testo = normalizza_testo(contesto)
    positivi = {
        "candidature aperte fino al": 70,
        "candidature aperte fino": 65,
        "c'e tempo fino al": 60,
        "ce tempo fino al": 60,
        "application deadline": 55,
        "deadline": 40,
        "applications must be submitted": 40,
        "submit": 15,
        "candidature": 15,
    }
    negativi = {
        "risultati": -35,
        "pubblicati": -25,
        "published": -20,
        "selection": -15,
        "aperti": -5,
    }

    punteggio = 0
    for criterio, valore in positivi.items():
        if criterio in testo:
            punteggio += valore
    for criterio, valore in negativi.items():
        if criterio in testo:
            punteggio += valore
    return punteggio


def estrai_deadline(testo):
    """
    Estrae la scadenza della candidatura.

    Dà priorità a formulazioni esplicite come:
    - Candidature aperte fino al 17 luglio 2026
    - C'e tempo fino al 17 luglio 2026
    - Application deadline: 17 July 2026
    """

    testo_pulito = pulisci_testo(testo)

    prefissi = (
        r"candidature\s+aperte\s+fino\s+al|"
        r"c['’]?e\s+tempo\s+fino\s+al|"
        r"ce\s+tempo\s+fino\s+al|"
        r"termine\s+per\s+le\s+candidature|"
        r"scadenza\s+(?:delle\s+)?candidature|"
        r"application\s+deadline|"
        r"applications\s+must\s+be\s+submitted\s+by"
    )

    candidati = []

    for schema_data in SCHEMI_DATA:
        schema_diretto = re.compile(
            rf"(?:{prefissi})\s*(?::|-)?\s*(?P<data>{schema_data})",
            flags=re.IGNORECASE,
        )

        for corrispondenza in schema_diretto.finditer(testo_pulito):
            stringa_data = pulisci_testo(corrispondenza.group("data"))
            data = interpreta_data(stringa_data)

            if data is None:
                continue

            candidati.append(
                {
                    "data": data,
                    "testo": stringa_data,
                    "punteggio": 100,
                }
            )

    if not candidati:
        for schema_data in SCHEMI_DATA:
            for data_match in re.finditer(
                schema_data,
                testo_pulito,
                flags=re.IGNORECASE,
            ):
                inizio = max(0, data_match.start() - 100)
                fine = min(len(testo_pulito), data_match.end() + 100)
                contesto = pulisci_testo(testo_pulito[inizio:fine])
                contesto_norm = normalizza_testo(contesto)

                if not any(
                    parola in contesto_norm
                    for parola in (
                        "candidatur",
                        "deadline",
                        "submit",
                        "tempo fino",
                    )
                ):
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
        deadline = deadline.replace(tzinfo=FUSO_ORARIO_ITALIA)

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


def estrai_anno_bando(testo):
    schemi = [
        r"post[- ]doctoral fellowships?\s+(20\d{2})",
        r"year\s+(20\d{2})",
        r"borse post-dottorato\s+(20\d{2})",
    ]

    for schema in schemi:
        corrispondenza = re.search(schema, testo, flags=re.IGNORECASE)
        if corrispondenza:
            return corrispondenza.group(1)

    return None


def estrai_numero_fellowship(testo, tipologia):
    testo_norm = normalizza_testo(testo)

    if tipologia == "Experimental":
        schemi = [
            r"(\d{1,3})\s+borse\s+experimental",
            r"experimental.{0,180}?(\d{1,3})\s+borse",
            r"award\s+(\d{1,3})\s+fellowships",
        ]
    else:
        schemi = [
            r"fino a\s+(\d{1,3})\s+clinical",
            r"clinical.{0,180}?fino a\s+(\d{1,3})",
            r"up to\s+(\d{1,3})\s+one-year fellowships",
        ]

    for schema in schemi:
        corrispondenza = re.search(schema, testo_norm, flags=re.IGNORECASE)
        if corrispondenza:
            return int(corrispondenza.group(1))

    return None


def costruisci_opportunita(titolo_pagina, url, testo):
    testo_completo = f"{titolo_pagina} {testo}"
    termini_bando = trova_termini(testo_completo, TERMINI_BANDO)
    termini_oncologici = trova_termini(testo_completo, TERMINI_ONCOLOGICI)

    if not termini_bando or not termini_oncologici:
        return [], "non_pertinente"

    deadline_info = estrai_deadline(testo_completo)
    if deadline_info is None:
        return [], "deadline_non_rilevata"

    valutazione = valuta_deadline(deadline_info["deadline"])
    anno = estrai_anno_bando(testo_completo)
    opportunita = []

    tipologie = []
    testo_norm = normalizza_testo(testo_completo)

    if "experimental" in testo_norm:
        tipologie.append("Experimental")
    if "clinical" in testo_norm or "clinico" in testo_norm:
        tipologie.append("Clinical")

    if not tipologie:
        tipologie.append("Generale")

    indicatori_chiusura = trova_termini(testo_completo, TERMINI_CHIUSURA)
    stato_submission = valutazione["stato_submission"]
    submission_aperta = valutazione["submission_aperta"]

    if indicatori_chiusura:
        stato_submission = "dichiarata_chiusa"
        submission_aperta = False

    for tipologia in tipologie:
        titolo = (
            f"Post-Doctoral Fellowship {anno} - {tipologia}"
            if anno
            else f"Post-Doctoral Fellowship - {tipologia}"
        )

        opportunita.append(
            {
                "fonte": FONTE,
                "titolo": titolo,
                "anno_bando": anno,
                "tipologia": tipologia,
                "url": url,
                "portale_candidature": PORTALE_CANDIDATURE,
                "rilevanza": "oncologica",
                "parole_chiave_oncologiche": termini_oncologici,
                "numero_fellowship": estrai_numero_fellowship(
                    testo_completo,
                    tipologia,
                ),
                "submission_aperta": submission_aperta,
                "stato_submission": stato_submission,
                "deadline": deadline_info["deadline"],
                "deadline_testo": deadline_info["deadline_testo"],
                "deadline_affidabilita": deadline_info[
                    "deadline_affidabilita"
                ],
            }
        )

    return opportunita, stato_submission


def analizza_candidati(sessione, candidati):
    tutte = []
    statistiche = {
        "candidate": len(candidati),
        "pagine_accessibili": 0,
        "pagine_non_analizzabili": 0,
        "non_pertinenti": 0,
        "deadline_non_rilevate": 0,
        "scadute": 0,
        "aperte": 0,
    }

    for numero, candidato in enumerate(candidati, start=1):
        print(f"Analisi {numero}/{len(candidati)}: {candidato['url']}")

        try:
            html = scarica_pagina(sessione, candidato["url"])
        except requests.RequestException as errore:
            statistiche["pagine_non_analizzabili"] += 1
            print(f"  Pagina non analizzabile: {errore}")
            continue

        statistiche["pagine_accessibili"] += 1
        titolo = estrai_titolo(html, candidato["titolo_indice"])
        testo = estrai_testo_principale(html)
        opportunita, stato = costruisci_opportunita(
            titolo,
            candidato["url"],
            testo,
        )

        if stato == "non_pertinente":
            statistiche["non_pertinenti"] += 1
            print("  Esclusa: pagina non pertinente ai bandi oncologici.")
            continue

        if stato == "deadline_non_rilevata":
            statistiche["deadline_non_rilevate"] += 1
            print("  Esclusa prudenzialmente: deadline non rilevata.")
            continue

        for opportunita_singola in opportunita:
            tutte.append(opportunita_singola)

            print(f"  Opportunita: {opportunita_singola['titolo']}")
            print(f"  Deadline: {opportunita_singola['deadline']}")
            print(f"  Stato: {opportunita_singola['stato_submission']}")

            if opportunita_singola["submission_aperta"]:
                statistiche["aperte"] += 1
            else:
                statistiche["scadute"] += 1

    uniche = []
    chiavi_viste = set()

    for opportunita in tutte:
        chiave = (
            opportunita.get("anno_bando"),
            opportunita.get("tipologia"),
            opportunita.get("deadline"),
        )

        if chiave in chiavi_viste:
            continue

        chiavi_viste.add(chiave)
        uniche.append(opportunita)

    uniche.sort(
        key=lambda x: (
            x.get("deadline") or "",
            x.get("tipologia") or "",
        )
    )

    attive = [x for x in uniche if x.get("submission_aperta") is True]

    return uniche, attive, statistiche


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
    def chiave(elemento):
        return (
            elemento.get("anno_bando"),
            elemento.get("tipologia"),
        )

    precedenti_per_chiave = {chiave(x): x for x in precedenti}
    correnti_per_chiave = {chiave(x): x for x in correnti}

    nuovi = [
        x for k, x in correnti_per_chiave.items()
        if k not in precedenti_per_chiave
    ]

    rimossi = [
        x for k, x in precedenti_per_chiave.items()
        if k not in correnti_per_chiave
    ]

    campi = [
        "titolo",
        "url",
        "deadline",
        "stato_submission",
        "submission_aperta",
        "numero_fellowship",
    ]

    modificati = []

    for k, corrente in correnti_per_chiave.items():
        precedente = precedenti_per_chiave.get(k)
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
                    "url": corrente["url"],
                    "campi_modificati": cambiati,
                }
            )

    return nuovi, rimossi, modificati


def salva_risultati(tutte, attive, statistiche, pagine_seme_accessibili):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    dati = {
        "fonte": FONTE,
        "pagine_monitorate": PAGINE_SEME,
        "portale_candidature": PORTALE_CANDIDATURE,
        "criterio": "opportunita oncologiche con candidatura aperta",
        "totale_pagine_seme_accessibili": pagine_seme_accessibili,
        "totale_pagine_candidate": statistiche["candidate"],
        "totale_pagine_accessibili": statistiche["pagine_accessibili"],
        "totale_pagine_non_analizzabili": statistiche[
            "pagine_non_analizzabili"
        ],
        "totale_bandi_oncologici_rilevati": len(tutte),
        "totale_bandi_scaduti": len(
            [x for x in tutte if x.get("submission_aperta") is False]
        ),
        "totale_deadline_non_rilevate": statistiche[
            "deadline_non_rilevate"
        ],
        "numero_risultati": len(attive),
        "calls": attive,
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(dati, file, ensure_ascii=False, indent=2)
        file.write("\n")


def aggiungi_riepilogo(tutte, attive, statistiche, nuovi, rimossi, modificati):
    percorso = os.environ.get("GITHUB_STEP_SUMMARY")
    if not percorso:
        return

    righe = [
        "# Fondazione Umberto Veronesi",
        "",
        f"Bandi oncologici rilevati: **{len(tutte)}**",
        "",
        f"Bandi con candidatura aperta: **{len(attive)}**",
        "",
        f"Bandi scaduti o chiusi: **{len([x for x in tutte if x.get('submission_aperta') is False])}**",
        "",
        f"Deadline non rilevate: **{statistiche['deadline_non_rilevate']}**",
        "",
        f"Nuove opportunita attive: **{len(nuovi)}**",
        "",
        f"Opportunita modificate: **{len(modificati)}**",
        "",
        f"Opportunita non piu attive: **{len(rimossi)}**",
        "",
    ]

    if attive:
        righe.extend(["## Opportunita attive", ""])
        for call in attive:
            righe.append(f"- [{call['titolo']}]({call['url']})")
            righe.append(f"  - Deadline: **{call['deadline']}**")
        righe.append("")
    else:
        righe.extend(["Nessuna candidatura attualmente aperta.", ""])

    if tutte:
        righe.extend(["## Bandi rilevati", ""])
        for call in tutte:
            righe.append(
                f"- **{call['titolo']}**: {call['stato_submission']} "
                f"({call['deadline_testo']})"
            )
        righe.append("")

    with open(percorso, "a", encoding="utf-8") as file:
        file.write("\n".join(righe) + "\n")


def main():
    print("=" * 60)
    print("MONITORAGGIO FONDAZIONE UMBERTO VERONESI")
    print("=" * 60)

    precedenti = carica_precedenti()
    print(f"Risultati attivi nell'archivio precedente: {len(precedenti)}")
    sessione = crea_sessione()

    try:
        candidati, pagine_seme_accessibili, errori = raccogli_pagine_candidate(
            sessione
        )

        print(f"Pagine seme accessibili: {pagine_seme_accessibili}")
        print(f"Pagine candidate trovate: {len(candidati)}")

        if not candidati:
            raise RuntimeError(
                "Nessuna pagina candidata Fondazione Veronesi individuata. "
                "Il precedente archivio non verra sovrascritto."
            )

        tutte, attive, statistiche = analizza_candidati(
            sessione,
            candidati,
        )

        if statistiche["pagine_accessibili"] == 0:
            raise RuntimeError(
                "Nessuna pagina candidata e risultata accessibile. "
                "Il precedente archivio non verra sovrascritto."
            )

    except (requests.RequestException, RuntimeError) as errore:
        print(f"Errore durante il monitoraggio: {errore}")
        raise SystemExit(1)

    nuovi, rimossi, modificati = confronta(precedenti, attive)

    salva_risultati(
        tutte,
        attive,
        statistiche,
        pagine_seme_accessibili,
    )

    aggiungi_riepilogo(
        tutte,
        attive,
        statistiche,
        nuovi,
        rimossi,
        modificati,
    )

    print(f"Bandi oncologici rilevati: {len(tutte)}")
    print(f"Bandi con candidatura aperta: {len(attive)}")
    print(
        "Bandi scaduti o chiusi: "
        f"{len([x for x in tutte if x.get('submission_aperta') is False])}"
    )
    print(f"Nuove opportunita attive: {len(nuovi)}")
    print(f"Opportunita modificate: {len(modificati)}")
    print(f"Opportunita non piu attive: {len(rimossi)}")
    print(f"File aggiornato: {OUTPUT_FILE}")
    print("Monitoraggio Fondazione Umberto Veronesi completato correttamente.")


if __name__ == "__main__":
    main()
