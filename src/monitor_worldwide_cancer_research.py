import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlunparse
from zoneinfo import ZoneInfo

import dateparser
import feedparser
import requests
from bs4 import BeautifulSoup


FONTE = "Worldwide Cancer Research"
PAGINA_UFFICIALE = "https://www.worldwidecancerresearch.org/for-researchers/"
OUTPUT_FILE = Path("data/worldwide_cancer_research_calls.json")
FUSO_ORARIO_EUROPA = ZoneInfo("Europe/Rome")

QUERY_FALLBACK = [
    'site:worldwidecancerresearch.org/for-researchers "Grant Round"',
    'site:worldwidecancerresearch.org/for-researchers "applications" "open"',
    'site:worldwidecancerresearch.org/for-researchers "application deadline"',
    'site:worldwidecancerresearch.org/for-researchers "reached our cap"',
]

TERMINI_APERTURA = {
    "grant round is now open",
    "grant round is open",
    "applications are now open",
    "applications now open",
    "open for submissions",
    "accepting applications",
    "apply now",
}

TERMINI_CHIUSURA = {
    "grant round is now closed",
    "grant round is closed",
    "applications are closed",
    "applications closed",
    "reached our cap",
    "application cap reached",
    "no longer accepting applications",
    "close when we reach",
}

TERMINI_FUTURO = {
    "stay up to date about our",
    "future funding opportunities",
    "next grant round",
    "upcoming grant round",
    "will be in 2027",
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
            "Accept": (
                "application/rss+xml,application/xml,text/xml,"
                "text/html;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-GB,en;q=0.9,it;q=0.7",
            "Cache-Control": "no-cache",
        }
    )
    return sessione


def pulisci_testo(testo):
    if not testo:
        return ""
    testo = BeautifulSoup(testo, "html.parser").get_text(" ", strip=True)
    return " ".join(testo.split())


def normalizza_testo(testo):
    testo = unicodedata.normalize("NFKD", testo or "")
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return pulisci_testo(
        testo.lower().replace("–", "-").replace("—", "-")
    )


def scarica_contenuto(sessione, url):
    risposta = sessione.get(url, timeout=35, allow_redirects=True)
    risposta.raise_for_status()
    return risposta.text


def prova_pagina_ufficiale(sessione):
    print(f"Controllo pagina ufficiale: {PAGINA_UFFICIALE}")
    contenuto = scarica_contenuto(sessione, PAGINA_UFFICIALE)
    print("Pagina ufficiale scaricata correttamente.")
    return contenuto


def estrai_testo_principale(html):
    soup = BeautifulSoup(html, "html.parser")
    for elemento in soup(
        ["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]
    ):
        elemento.decompose()
    area = soup.find("main") or soup.find("article") or soup.body or soup
    return pulisci_testo(area.get_text(" ", strip=True))


def costruisci_url_bing_web(query):
    return (
        "https://www.bing.com/search?"
        f"q={quote_plus(query)}&format=rss&setlang=en-GB&cc=GB"
    )


def leggi_fallback_bing(sessione):
    entries = []
    fonti = []
    errori = []

    for query in QUERY_FALLBACK:
        url = costruisci_url_bing_web(query)
        print(f"Controllo fallback Bing RSS: {query}")

        try:
            contenuto = scarica_contenuto(sessione, url)
            feed = feedparser.parse(contenuto)

            if feed.bozo and not feed.entries:
                raise RuntimeError(str(feed.bozo_exception))

            print(f"  Elementi ricevuti: {len(feed.entries)}")
            entries.extend(feed.entries)
            fonti.append(url)

        except (requests.RequestException, RuntimeError) as errore:
            print(f"  Fallback non disponibile: {errore}")
            errori.append(f"{query}: {errore}")

    if not fonti:
        raise RuntimeError(
            "Nessun feed di fallback disponibile. " + " | ".join(errori)
        )

    return entries, fonti


def estrai_url_ufficiale(entry):
    candidati = [
        entry.get("link", ""),
        entry.get("id", ""),
        entry.get("guid", ""),
    ]

    testo = " ".join(
        [
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("description", ""),
        ]
    )

    candidati.extend(re.findall(r"https?://[^\s<>'\"]+", testo))

    for indirizzo in candidati:
        if not indirizzo:
            continue

        indirizzo = indirizzo.replace("&amp;", "&")
        elementi = urlparse(indirizzo)
        parametri = parse_qs(elementi.query)

        for chiave in ("url", "u", "r"):
            if chiave in parametri and parametri[chiave]:
                possibile = unquote(parametri[chiave][0])
                if "worldwidecancerresearch.org" in urlparse(possibile).netloc.lower():
                    indirizzo = possibile
                    elementi = urlparse(indirizzo)
                    break

        if "worldwidecancerresearch.org" not in elementi.netloc.lower():
            continue

        return urlunparse(
            (
                elementi.scheme or "https",
                elementi.netloc.lower(),
                elementi.path,
                "",
                "",
                "",
            )
        )

    return ""


def aggrega_testo_fallback(entries):
    frammenti = []
    url_ufficiali = set()
    visti = set()

    for entry in entries:
        titolo = pulisci_testo(entry.get("title", ""))
        descrizione = pulisci_testo(
            entry.get("summary", entry.get("description", ""))
        )
        chiave = f"{titolo}|{descrizione}"

        if chiave in visti:
            continue

        visti.add(chiave)
        frammenti.append(f"{titolo}. {descrizione}")

        url = estrai_url_ufficiale(entry)
        if url:
            url_ufficiali.add(url)

    return pulisci_testo(" ".join(frammenti)), sorted(url_ufficiali)


def trova_anni_round(testo):
    schemi = [
        r"our\s+(20\d{2})\s+grant round",
        r"(20\d{2})\s+grant round",
        r"grant round\s+(20\d{2})",
    ]
    anni = set()

    for schema in schemi:
        anni.update(re.findall(schema, testo, flags=re.IGNORECASE))

    return sorted(anni)


def determina_round_e_stato(testo):
    testo_norm = normalizza_testo(testo)
    anni = trova_anni_round(testo)
    anno_chiuso = None
    anno_futuro = None
    indicatori = []

    for anno in anni:
        finestre = re.findall(
            rf".{{0,140}}{re.escape(anno)}\s+grant round.{{0,220}}",
            testo_norm,
            flags=re.IGNORECASE,
        )
        contesto = " ".join(finestre) or testo_norm

        chiusure = sorted(
            termine
            for termine in TERMINI_CHIUSURA
            if normalizza_testo(termine) in contesto
        )
        aperture = sorted(
            termine
            for termine in TERMINI_APERTURA
            if normalizza_testo(termine) in contesto
        )
        futuri = sorted(
            termine
            for termine in TERMINI_FUTURO
            if normalizza_testo(termine) in contesto
        )

        if aperture:
            return anno, "aperto", aperture
        if chiusure:
            anno_chiuso = anno
            indicatori = chiusure
        if futuri:
            anno_futuro = anno

    if anno_futuro:
        return anno_futuro, "annunciato_non_aperto", ["future funding opportunities"]

    if anno_chiuso:
        return anno_chiuso, "chiuso", indicatori

    chiusure_globali = sorted(
        termine
        for termine in TERMINI_CHIUSURA
        if normalizza_testo(termine) in testo_norm
    )
    aperture_globali = sorted(
        termine
        for termine in TERMINI_APERTURA
        if normalizza_testo(termine) in testo_norm
    )

    if aperture_globali:
        return anni[-1] if anni else None, "aperto", aperture_globali
    if chiusure_globali:
        return anni[-1] if anni else None, "chiuso", chiusure_globali

    return anni[-1] if anni else None, "non_determinato", []


def trova_limite_domande(testo):
    schemi = [
        r"cap of\s+(\d{2,5})\s+applications",
        r"maximum of\s+(\d{2,5})\s+applications",
        r"application cap(?:ped)? at\s+(\d{2,5})",
        r"(\d{2,5})[- ]application cap",
    ]

    for schema in schemi:
        corrispondenza = re.search(schema, testo, flags=re.IGNORECASE)
        if corrispondenza:
            return int(corrispondenza.group(1))

    return None


def interpreta_data(stringa_data):
    contiene_orario = bool(re.search(r"\b\d{1,2}[:.]\d{2}\b", stringa_data))
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
        data = data.replace(tzinfo=FUSO_ORARIO_EUROPA)

    if not contiene_orario:
        data = data.replace(hour=23, minute=59, second=59, microsecond=0)

    return data


def trova_deadline(testo, anno_round):
    candidati = []

    for schema_data in SCHEMI_DATA:
        schema_contesto = re.compile(
            r"(.{0,180}" + schema_data + r".{0,180})",
            flags=re.IGNORECASE,
        )

        for corrispondenza in schema_contesto.finditer(testo):
            contesto = pulisci_testo(corrispondenza.group(1))
            contesto_norm = normalizza_testo(contesto)

            if not any(
                parola in contesto_norm
                for parola in (
                    "deadline",
                    "applications close",
                    "closing date",
                    "open for submissions",
                    "application cap",
                )
            ):
                continue

            data_match = re.search(schema_data, contesto, flags=re.IGNORECASE)
            if not data_match:
                continue

            stringa_data = pulisci_testo(data_match.group(0))
            data = interpreta_data(stringa_data)

            if data is None:
                continue

            punteggio = 0
            criteri = {
                "application deadline": 40,
                "deadline": 30,
                "applications close": 40,
                "closing date": 35,
                "open for submissions": 15,
                "application cap": 10,
                "published": -20,
                "newsletter": -20,
            }

            for parola, valore in criteri.items():
                if parola in contesto_norm:
                    punteggio += valore

            if anno_round and str(data.year) == str(anno_round):
                punteggio += 20

            candidati.append((punteggio, data, stringa_data))

    if not candidati:
        return None

    candidati.sort(key=lambda elemento: (elemento[0], elemento[1]), reverse=True)
    punteggio, data, stringa_data = candidati[0]

    if punteggio < 25:
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


def confronta(precedente, corrente):
    campi = [
        "canale_utilizzato",
        "anno_round",
        "stato_round",
        "submission_aperta",
        "stato_submission",
        "deadline",
        "limite_domande",
        "numero_risultati",
    ]
    return [campo for campo in campi if precedente.get(campo) != corrente.get(campo)]


def salva_risultati(dati):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(dati, file, ensure_ascii=False, indent=2)
        file.write("\n")


def aggiungi_riepilogo_github(dati, campi_modificati, giorni_residui):
    percorso = os.environ.get("GITHUB_STEP_SUMMARY")
    if not percorso:
        return

    righe = [
        "# Worldwide Cancer Research",
        "",
        f"Canale utilizzato: **{dati['canale_utilizzato']}**",
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
        f"Opportunita attive: **{dati['numero_risultati']}**",
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

    if dati["canale_utilizzato"] != "pagina_ufficiale":
        righe.extend(
            [
                "Il risultato deriva da un fallback di ricerca limitato al dominio ufficiale.",
                "La call non e considerata confermata finche la pagina ufficiale resta inaccessibile.",
                "",
            ]
        )

    with open(percorso, "a", encoding="utf-8") as file:
        file.write("\n".join(righe) + "\n")


def main():
    print("=" * 60)
    print("MONITORAGGIO WORLDWIDE CANCER RESEARCH CON FALLBACK")
    print("=" * 60)

    precedente = carica_precedente()
    sessione = crea_sessione()
    fonti_fallback = []
    url_ufficiali = []

    try:
        html = prova_pagina_ufficiale(sessione)
        testo = estrai_testo_principale(html)
        canale = "pagina_ufficiale"
        stato_verifica = "confermato"
    except requests.RequestException as errore:
        print(f"Pagina ufficiale non disponibile: {errore}")

        try:
            entries, fonti_fallback = leggi_fallback_bing(sessione)
            testo, url_ufficiali = aggrega_testo_fallback(entries)
            canale = "bing_web_rss_fallback"
            stato_verifica = "fonte_ufficiale_non_accessibile"
        except (requests.RequestException, RuntimeError) as errore_fallback:
            print(f"Fallback non disponibile: {errore_fallback}")
            print("Il precedente archivio non verra sovrascritto.")
            raise SystemExit(1)

    anno_round, stato_round, indicatori_stato = determina_round_e_stato(testo)
    limite_domande = trova_limite_domande(testo)
    deadline_info = trova_deadline(testo, anno_round)
    deadline_iso = deadline_info["deadline"] if deadline_info else None
    valutazione = valuta_submission(stato_round, deadline_iso)

    calls = []
    if valutazione["submission_aperta"] is True:
        titolo = (
            f"Worldwide Cancer Research Grant Round {anno_round}"
            if anno_round
            else "Worldwide Cancer Research Grant Round"
        )
        calls.append(
            {
                "fonte": FONTE,
                "titolo": titolo,
                "url": PAGINA_UFFICIALE,
                "anno_round": anno_round,
                "rilevanza": "oncologica",
                "stato_verifica": stato_verifica,
                "submission_aperta": True,
                "stato_submission": "aperta",
                "deadline": deadline_iso,
                "deadline_testo": deadline_info["deadline_testo"] if deadline_info else None,
                "limite_domande": limite_domande,
            }
        )

    dati = {
        "fonte": FONTE,
        "pagina_monitorata": PAGINA_UFFICIALE,
        "canale_utilizzato": canale,
        "fonti_fallback": fonti_fallback,
        "url_ufficiali_rilevati": url_ufficiali,
        "stato_verifica": stato_verifica,
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

    campi_modificati = confronta(precedente, dati)
    salva_risultati(dati)
    aggiungi_riepilogo_github(
        dati,
        campi_modificati,
        valutazione.get("giorni_residui"),
    )

    print(f"Canale utilizzato: {canale}")
    print(f"Stato verifica: {stato_verifica}")
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
