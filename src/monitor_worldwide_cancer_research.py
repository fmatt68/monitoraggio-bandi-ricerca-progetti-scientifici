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


FONTE = "Worldwide Cancer Research"
PAGINA_RICERCATORI = "https://www.worldwidecancerresearch.org/for-researchers/"
OUTPUT_FILE = Path("data/worldwide_cancer_research_calls.json")
FUSO_ORARIO_EUROPA = ZoneInfo("Europe/Rome")

TERMINI_APERTURA = {
    "grant round is now open",
    "grant round is open",
    "applications are now open",
    "applications now open",
    "apply now",
    "accepting applications",
}

TERMINI_CHIUSURA = {
    "grant round is now closed",
    "grant round is closed",
    "applications are closed",
    "applications closed",
    "reached our cap",
    "application cap reached",
    "no longer accepting applications",
}

TERMINI_FUTURO = {
    "stay up to date about our",
    "future funding opportunities",
    "next grant round",
    "upcoming grant round",
}

MESI = (
    "january|february|march|april|may|june|july|august|"
    "september|october|november|december"
)

SCHEMI_DATA = [
    rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}"
    r"(?:\s*(?:at|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT|BST))?",
    rf"\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}"
    r"(?:\s*(?:at|,)\s*\d{1,2}(?:[:.]\d{2})?)?"
    r"(?:\s*(?:CET|CEST|UTC|GMT|BST))?",
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
    if not testo:
        return ""
    return " ".join(testo.split())


def normalizza_testo(testo):
    testo = unicodedata.normalize("NFKD", testo or "")
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return pulisci_testo(
        testo.lower().replace("–", "-").replace("—", "-")
    )


def scarica_pagina(sessione, url):
    risposta = sessione.get(url, timeout=40, allow_redirects=True)
    risposta.raise_for_status()
    print(f"Pagina scaricata correttamente: HTTP {risposta.status_code}")
    return risposta.text


def estrai_testo_principale(html):
    soup = BeautifulSoup(html, "html.parser")
    for elemento in soup(
        ["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]
    ):
        elemento.decompose()
    area = soup.find("main") or soup.find("article") or soup.body or soup
    return pulisci_testo(area.get_text(" ", strip=True))


def trova_anno_round(testo):
    schemi = [
        r"our\s+(20\d{2})\s+grant round",
        r"(20\d{2})\s+grant round",
        r"grant round\s+(20\d{2})",
    ]
    for schema in schemi:
        corrispondenza = re.search(schema, testo, flags=re.IGNORECASE)
        if corrispondenza:
            return corrispondenza.group(1)
    return None


def contiene_uno_dei_termini(testo, termini):
    testo_normalizzato = normalizza_testo(testo)
    return sorted(
        termine for termine in termini if normalizza_testo(termine) in testo_normalizzato
    )


def determina_stato_round(testo):
    chiusura = contiene_uno_dei_termini(testo, TERMINI_CHIUSURA)
    apertura = contiene_uno_dei_termini(testo, TERMINI_APERTURA)
    futuro = contiene_uno_dei_termini(testo, TERMINI_FUTURO)

    if chiusura:
        return "chiuso", chiusura
    if apertura:
        return "aperto", apertura
    if futuro:
        return "annunciato_non_aperto", futuro
    return "non_determinato", []


def trova_limite_domande(testo):
    schemi = [
        r"cap of\s+(\d{2,5})\s+applications",
        r"maximum of\s+(\d{2,5})\s+applications",
        r"application cap(?:ped)? at\s+(\d{2,5})",
    ]
    for schema in schemi:
        corrispondenza = re.search(schema, testo, flags=re.IGNORECASE)
        if corrispondenza:
            return int(corrispondenza.group(1))
    return None


def trova_deadline(testo):
    candidati = []
    for schema_data in SCHEMI_DATA:
        schema_contesto = re.compile(
            r"(.{0,180}" + schema_data + r".{0,180})",
            flags=re.IGNORECASE,
        )
        for corrispondenza in schema_contesto.finditer(testo):
            contesto = pulisci_testo(corrispondenza.group(1))
            contesto_min = normalizza_testo(contesto)
            if not any(
                parola in contesto_min
                for parola in ("deadline", "close", "closing", "submit", "application")
            ):
                continue
            data_match = re.search(schema_data, contesto, flags=re.IGNORECASE)
            if not data_match:
                continue
            stringa_data = pulisci_testo(data_match.group(0))
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
                continue
            if data.tzinfo is None:
                data = data.replace(tzinfo=FUSO_ORARIO_EUROPA)
            if not re.search(r"\b\d{1,2}[:.]\d{2}\b", stringa_data):
                data = data.replace(hour=23, minute=59, second=59, microsecond=0)
            punteggio = 0
            for parola, valore in {
                "application deadline": 40,
                "deadline": 30,
                "applications close": 35,
                "closing date": 35,
                "submit": 10,
                "application": 5,
                "newsletter": -15,
                "published": -15,
            }.items():
                if parola in contesto_min:
                    punteggio += valore
            candidati.append((punteggio, data, stringa_data))

    if not candidati:
        return None
    candidati.sort(key=lambda x: (x[0], x[1]), reverse=True)
    punteggio, data, stringa_data = candidati[0]
    if punteggio < 20:
        return None
    return {
        "deadline": data.isoformat(),
        "deadline_testo": stringa_data,
        "deadline_affidabilita": punteggio,
    }


def valuta_submission(stato_round, deadline_iso):
    if stato_round != "aperto":
        return {
            "submission_aperta": False,
            "stato_submission": stato_round,
            "giorni_residui": None,
        }
    if not deadline_iso:
        return {
            "submission_aperta": None,
            "stato_submission": "aperta_deadline_non_rilevata",
            "giorni_residui": None,
        }
    try:
        deadline = datetime.fromisoformat(deadline_iso)
    except ValueError:
        return {
            "submission_aperta": None,
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


def carica_precedente():
    if not OUTPUT_FILE.exists():
        return {}
    try:
        with OUTPUT_FILE.open("r", encoding="utf-8") as file:
            dati = json.load(file)
        return dati if isinstance(dati, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def costruisci_call_attiva(anno_round, deadline_info, limite_domande):
    if not anno_round:
        titolo = "Worldwide Cancer Research Grant Round"
    else:
        titolo = f"Worldwide Cancer Research Grant Round {anno_round}"
    return {
        "fonte": FONTE,
        "titolo": titolo,
        "url": PAGINA_RICERCATORI,
        "anno_round": anno_round,
        "rilevanza": "oncologica",
        "submission_aperta": True,
        "stato_submission": "aperta",
        "deadline": deadline_info["deadline"] if deadline_info else None,
        "deadline_testo": deadline_info["deadline_testo"] if deadline_info else None,
        "limite_domande": limite_domande,
    }


def salva_risultati(
    anno_round,
    stato_round,
    indicatori_stato,
    deadline_info,
    valutazione,
    limite_domande,
    calls,
):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    dati = {
        "fonte": FONTE,
        "pagina_monitorata": PAGINA_RICERCATORI,
        "criterio": "grant oncologici con submission aperta",
        "anno_round": anno_round,
        "stato_round": stato_round,
        "indicatori_stato": indicatori_stato,
        "submission_aperta": valutazione["submission_aperta"],
        "stato_submission": valutazione["stato_submission"],
        "deadline": deadline_info["deadline"] if deadline_info else None,
        "deadline_testo": deadline_info["deadline_testo"] if deadline_info else None,
        "limite_domande": limite_domande,
        "numero_risultati": len(calls),
        "calls": calls,
    }
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(dati, file, ensure_ascii=False, indent=2)
        file.write("\n")


def confronta(precedente, corrente):
    campi = [
        "anno_round",
        "stato_round",
        "submission_aperta",
        "stato_submission",
        "deadline",
        "limite_domande",
        "numero_risultati",
    ]
    return [campo for campo in campi if precedente.get(campo) != corrente.get(campo)]


def formatta_deadline(deadline_iso):
    if not deadline_iso:
        return "non pubblicata"
    try:
        deadline = datetime.fromisoformat(deadline_iso)
    except ValueError:
        return deadline_iso
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=FUSO_ORARIO_EUROPA)
    return deadline.astimezone(FUSO_ORARIO_EUROPA).strftime(
        "%d/%m/%Y alle %H:%M %Z"
    )


def aggiungi_riepilogo_github(dati, campi_modificati, giorni_residui):
    percorso = os.environ.get("GITHUB_STEP_SUMMARY")
    if not percorso:
        return
    righe = [
        "# Worldwide Cancer Research",
        "",
        f"Round rilevato: **{dati.get('anno_round') or 'non determinato'}**",
        "",
        f"Stato round: **{dati['stato_round']}**",
        "",
        f"Stato submission: **{dati['stato_submission']}**",
        "",
        f"Deadline: **{formatta_deadline(dati.get('deadline'))}**",
        "",
        f"Limite domande: **{dati.get('limite_domande') or 'non rilevato'}**",
        "",
        f"Opportunità attive: **{dati['numero_risultati']}**",
        "",
        f"Campi modificati: **{len(campi_modificati)}**",
        "",
    ]
    if giorni_residui is not None:
        righe.extend([f"Giorni residui: **{giorni_residui}**", ""])
    if dati["calls"]:
        righe.extend(["## Call attive", ""])
        for call in dati["calls"]:
            righe.append(f"- [{call['titolo']}]({call['url']})")
        righe.append("")
    else:
        righe.extend(["Nessuna submission attualmente aperta.", ""])
    if campi_modificati:
        righe.extend(["## Modifiche rilevate", ""])
        for campo in campi_modificati:
            righe.append(f"- {campo}")
        righe.append("")
    with open(percorso, "a", encoding="utf-8") as file:
        file.write("\n".join(righe) + "\n")


def main():
    print("=" * 60)
    print("MONITORAGGIO WORLDWIDE CANCER RESEARCH")
    print("=" * 60)

    precedente = carica_precedente()
    sessione = crea_sessione()

    try:
        print(f"Controllo della pagina: {PAGINA_RICERCATORI}")
        html = scarica_pagina(sessione, PAGINA_RICERCATORI)
        testo = estrai_testo_principale(html)
    except requests.RequestException as errore:
        print(f"Errore durante il download: {errore}")
        print("Il precedente archivio non verra sovrascritto.")
        raise SystemExit(1)

    anno_round = trova_anno_round(testo)
    stato_round, indicatori_stato = determina_stato_round(testo)
    limite_domande = trova_limite_domande(testo)
    deadline_info = trova_deadline(testo)
    deadline_iso = deadline_info["deadline"] if deadline_info else None
    valutazione = valuta_submission(stato_round, deadline_iso)

    calls = []
    if valutazione["submission_aperta"] is True:
        calls.append(
            costruisci_call_attiva(anno_round, deadline_info, limite_domande)
        )

    dati_correnti = {
        "fonte": FONTE,
        "pagina_monitorata": PAGINA_RICERCATORI,
        "criterio": "grant oncologici con submission aperta",
        "anno_round": anno_round,
        "stato_round": stato_round,
        "indicatori_stato": indicatori_stato,
        "submission_aperta": valutazione["submission_aperta"],
        "stato_submission": valutazione["stato_submission"],
        "deadline": deadline_iso,
        "deadline_testo": deadline_info["deadline_testo"] if deadline_info else None,
        "limite_domande": limite_domande,
        "numero_risultati": len(calls),
        "calls": calls,
    }

    campi_modificati = confronta(precedente, dati_correnti)
    salva_risultati(
        anno_round,
        stato_round,
        indicatori_stato,
        deadline_info,
        valutazione,
        limite_domande,
        calls,
    )
    aggiungi_riepilogo_github(
        dati_correnti,
        campi_modificati,
        valutazione.get("giorni_residui"),
    )

    print(f"Round rilevato: {anno_round}")
    print(f"Stato round: {stato_round}")
    print(f"Stato submission: {valutazione['stato_submission']}")
    print(f"Deadline: {formatta_deadline(deadline_iso)}")
    print(f"Limite domande: {limite_domande}")
    print(f"Opportunita attive: {len(calls)}")
    print(f"Campi modificati: {len(campi_modificati)}")
    print(f"File aggiornato: {OUTPUT_FILE}")
    print("Monitoraggio Worldwide Cancer Research completato correttamente.")


if __name__ == "__main__":
    main()
