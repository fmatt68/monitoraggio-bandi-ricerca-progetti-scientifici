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


FONTE = "Ministero della Salute - Ricerca Finalizzata"
FEED_UFFICIALE = "https://www.salute.gov.it/new/rss/RSS_notizie.xml"
OUTPUT_FILE = Path("data/ricerca_finalizzata_calls.json")
FUSO_ORARIO_ITALIA = ZoneInfo("Europe/Rome")

QUERY_BING = [
    'site:salute.gov.it "ricerca finalizzata"',
    'site:salute.gov.it "bando ricerca sanitaria"',
    'site:salute.gov.it "giovani ricercatori" bando',
    'site:salute.gov.it "starting grant" ricerca',
]

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

TERMINI_SUBMISSION = {
    "scadenza", "termine ultimo", "termine per la presentazione",
    "deadline", "presentazione delle domande", "presentazione delle proposte",
    "presentazione dei progetti", "submission", "entro e non oltre",
}

TERMINI_CHIUSURA = {
    "bando chiuso", "avviso chiuso", "procedura conclusa", "procedura chiusa",
    "termine scaduto", "termini scaduti", "progetti finanziati",
    "graduatoria finale", "graduatorie finali", "esiti finali",
    "risultati finali",
}

INDICATORI_BLOCCO = {
    "browser validation page", "site verification",
    "using security service for protection", "validation is complete",
    "gcore", "checking your browser", "enable javascript and cookies",
    "access denied",
}

MESI = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|"
    "settembre|ottobre|novembre|dicembre|january|february|march|"
    "april|may|june|july|august|september|october|november|december"
)

SCHEMI_DATA = [
    rf"\b\d{{1,2}}\s+(?:{MESI})\s+\d{{4}}(?:\s*(?:alle|ore|at|,)\s*\d{{1,2}}(?:[:.]\d{{2}})?)?(?:\s*(?:CET|CEST|UTC|GMT))?",
    rf"\b(?:{MESI})\s+\d{{1,2}},?\s+\d{{4}}(?:\s*(?:alle|ore|at|,)\s*\d{{1,2}}(?:[:.]\d{{2}})?)?(?:\s*(?:CET|CEST|UTC|GMT))?",
    r"\b\d{1,2}/\d{1,2}/\d{4}(?:\s*(?:alle|ore|at|,)\s*\d{1,2}(?:[:.]\d{2})?)?",
    r"\b\d{4}-\d{2}-\d{2}(?:[T\s]\d{1,2}:\d{2})?",
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
            "Accept": "application/rss+xml,application/xml,text/xml,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
        }
    )
    return sessione


def pulisci_testo(testo):
    if not testo:
        return ""
    return " ".join(BeautifulSoup(testo, "html.parser").get_text(" ", strip=True).split())


def normalizza_testo(testo):
    testo = unicodedata.normalize("NFKD", testo or "")
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return " ".join(testo.lower().replace("–", "-").replace("—", "-").split())


def trova_termini(testo, termini):
    testo_normalizzato = normalizza_testo(testo)
    risultati = []
    for termine in termini:
        termine_normalizzato = normalizza_testo(termine)
        schema = r"(?<![a-z0-9])" + re.escape(termine_normalizzato) + r"(?![a-z0-9])"
        if re.search(schema, testo_normalizzato):
            risultati.append(termine)
    return sorted(risultati)


def trova_indicatori_blocco(contenuto):
    testo = normalizza_testo(pulisci_testo(contenuto))
    return sorted(x for x in INDICATORI_BLOCCO if x in testo)


def scarica_contenuto(sessione, url):
    risposta = sessione.get(url, timeout=40, allow_redirects=True)
    risposta.raise_for_status()
    return risposta.text


def feed_da_contenuto(contenuto, descrizione):
    feed = feedparser.parse(contenuto)
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"Feed {descrizione} non interpretabile: {feed.bozo_exception}")
    return feed


def leggi_feed_ufficiale(sessione):
    print(f"Controllo feed RSS ufficiale: {FEED_UFFICIALE}")
    contenuto = scarica_contenuto(sessione, FEED_UFFICIALE)
    indicatori = trova_indicatori_blocco(contenuto)
    if indicatori:
        raise RuntimeError("Feed ufficiale bloccato: " + ", ".join(indicatori))
    feed = feed_da_contenuto(contenuto, "ufficiale")
    print(f"Feed ufficiale letto: {len(feed.entries)} elementi")
    return feed.entries, ["feed_rss_ufficiale"]


def costruisci_url_bing(query):
    return (
        "https://www.bing.com/news/search?"
        f"q={quote_plus(query)}&format=rss&setlang=it&cc=it"
    )


def leggi_feed_bing(sessione):
    entries = []
    fonti = []
    errori = []
    for query in QUERY_BING:
        url = costruisci_url_bing(query)
        print(f"Controllo fallback Bing News RSS: {query}")
        try:
            contenuto = scarica_contenuto(sessione, url)
            feed = feed_da_contenuto(contenuto, query)
            entries.extend(feed.entries)
            fonti.append(url)
            print(f"  Elementi ricevuti: {len(feed.entries)}")
        except (requests.RequestException, RuntimeError) as errore:
            errori.append(f"{query}: {errore}")
            print(f"  Fallback non disponibile: {errore}")
    if not fonti:
        raise RuntimeError("Nessun feed Bing disponibile. " + " | ".join(errori))
    return entries, fonti


def estrai_url_ufficiale(entry):
    candidati = [
        entry.get("link", ""),
        entry.get("id", ""),
        entry.get("guid", ""),
    ]
    testo = " ".join(
        [entry.get("summary", ""), entry.get("description", ""), entry.get("title", "")]
    )
    candidati.extend(re.findall(r"https?://[^\s<>'\"]+", testo))

    for indirizzo in candidati:
        if not indirizzo:
            continue
        indirizzo = indirizzo.replace("&amp;", "&")
        parsed = urlparse(indirizzo)
        query = parse_qs(parsed.query)
        for chiave in ("url", "u", "r"):
            if chiave in query and query[chiave]:
                possibile = unquote(query[chiave][0])
                if "salute.gov.it" in urlparse(possibile).netloc.lower():
                    indirizzo = possibile
                    parsed = urlparse(indirizzo)
                    break
        if "salute.gov.it" not in parsed.netloc.lower():
            continue
        return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), parsed.path, "", "", ""))
    return ""


def estrai_data_pubblicazione(entry):
    struttura = entry.get("published_parsed") or entry.get("updated_parsed")
    if struttura:
        return datetime(
            struttura.tm_year, struttura.tm_mon, struttura.tm_mday,
            struttura.tm_hour, struttura.tm_min, struttura.tm_sec,
            tzinfo=timezone.utc,
        ).isoformat()
    valore = entry.get("published") or entry.get("updated")
    if not valore:
        return None
    data = dateparser.parse(
        valore,
        languages=["it", "en"],
        settings={
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "Europe/Rome",
            "TO_TIMEZONE": "Europe/Rome",
        },
    )
    return data.isoformat() if data else None


def estrai_candidati(entries, origine):
    risultati = []
    visti = set()
    for entry in entries:
        titolo = pulisci_testo(entry.get("title", ""))
        descrizione = pulisci_testo(entry.get("summary", entry.get("description", "")))
        testo = f"{titolo} {descrizione}"
        termini = trova_termini(testo, TERMINI_RICERCA_FINALIZZATA)
        if not termini:
            continue
        url = estrai_url_ufficiale(entry)
        if not url or url in visti:
            continue
        visti.add(url)
        risultati.append(
            {
                "fonte": FONTE,
                "titolo": titolo,
                "descrizione_rss": descrizione,
                "url": url,
                "data_pubblicazione": estrai_data_pubblicazione(entry),
                "termini_ricerca_finalizzata": termini,
                "origine_rilevazione": origine,
            }
        )
    risultati.sort(key=lambda x: (x.get("data_pubblicazione") or "", x["titolo"]), reverse=True)
    return risultati


def estrai_testo_principale(html):
    soup = BeautifulSoup(html, "html.parser")
    for elemento in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        elemento.decompose()
    area = soup.find("main") or soup.find("article") or soup.body or soup
    return pulisci_testo(area.get_text(" ", strip=True))


def scarica_pagina_html(sessione, url):
    contenuto = scarica_contenuto(sessione, url)
    indicatori = trova_indicatori_blocco(contenuto)
    if indicatori:
        raise RuntimeError("Pagina ufficiale bloccata: " + ", ".join(indicatori))
    return contenuto


def interpreta_data(stringa_data):
    contiene_orario = bool(re.search(r"\b\d{1,2}[:.]\d{2}\b", stringa_data))
    pulita = pulisci_testo(stringa_data.replace("(", " ").replace(")", " "))
    data = dateparser.parse(
        pulita,
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
    }
    negativi = {
        "pubblicazione": -15,
        "aggiornato": -20,
        "graduatoria": -30,
        "finanziati": -30,
        "risultati": -20,
        "esiti": -20,
        "webinar": -20,
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
    candidati = []
    for schema_data in SCHEMI_DATA:
        schema = re.compile(r"(.{0,220}" + schema_data + r".{0,220})", re.IGNORECASE)
        for corrispondenza in schema.finditer(testo):
            contesto = pulisci_testo(corrispondenza.group(1))
            if not trova_termini(contesto, TERMINI_SUBMISSION):
                continue
            for schema_singolo in SCHEMI_DATA:
                for data_match in re.finditer(schema_singolo, contesto, re.IGNORECASE):
                    stringa = pulisci_testo(data_match.group(0))
                    data = interpreta_data(stringa)
                    if data:
                        candidati.append(
                            {
                                "data": data,
                                "testo": stringa,
                                "punteggio": calcola_punteggio_deadline(contesto),
                            }
                        )
    if not candidati:
        return None
    candidati.sort(key=lambda x: (x["punteggio"], x["data"]), reverse=True)
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
        return {"submission_aperta": None, "stato_submission": "deadline_non_verificata"}
    try:
        deadline = datetime.fromisoformat(deadline_iso)
    except ValueError:
        return {"submission_aperta": None, "stato_submission": "deadline_non_valida"}
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=FUSO_ORARIO_ITALIA)
    if deadline.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        return {"submission_aperta": False, "stato_submission": "scaduta"}
    return {"submission_aperta": True, "stato_submission": "aperta"}


def verifica_candidato(sessione, candidato):
    try:
        html = scarica_pagina_html(sessione, candidato["url"])
    except (requests.RequestException, RuntimeError) as errore:
        return {
            **candidato,
            "stato_verifica": "fonte_ufficiale_non_accessibile",
            "motivo_verifica": str(errore),
            "rilevanza": "bando_generale_da_verificare",
            "parole_chiave_oncologiche": [],
            "submission_aperta": None,
            "stato_submission": "non_verificato",
            "deadline": None,
            "deadline_testo": None,
        }

    testo = estrai_testo_principale(html)
    completo = f"{candidato['titolo']} {candidato['descrizione_rss']} {testo}"
    oncologia = trova_termini(completo, TERMINI_ONCOLOGICI)
    chiusura = trova_termini(completo, TERMINI_CHIUSURA)
    if chiusura:
        return {
            **candidato,
            "stato_verifica": "confermato",
            "rilevanza": "oncologica" if oncologia else "bando_generale",
            "parole_chiave_oncologiche": oncologia,
            "submission_aperta": False,
            "stato_submission": "dichiarata_conclusa",
            "deadline": None,
            "deadline_testo": None,
        }
    info = estrai_deadline(completo)
    if info:
        valutazione = valuta_deadline(info["deadline"])
        return {
            **candidato,
            "stato_verifica": "confermato",
            "rilevanza": "oncologica" if oncologia else "bando_generale",
            "parole_chiave_oncologiche": oncologia,
            **valutazione,
            **info,
        }
    return {
        **candidato,
        "stato_verifica": "pagina_accessibile_deadline_non_rilevata",
        "rilevanza": "oncologica" if oncologia else "bando_generale_da_verificare",
        "parole_chiave_oncologiche": oncologia,
        "submission_aperta": None,
        "stato_submission": "deadline_non_verificata",
        "deadline": None,
        "deadline_testo": None,
    }


def carica_precedenti():
    if not OUTPUT_FILE.exists():
        return []
    try:
        with OUTPUT_FILE.open("r", encoding="utf-8") as file:
            dati = json.load(file)
        return dati.get("calls", []) if isinstance(dati.get("calls", []), list) else []
    except (OSError, json.JSONDecodeError):
        return []


def seleziona_attivi(candidati):
    return [
        candidato
        for candidato in candidati
        if candidato.get("stato_submission") not in {"scaduta", "dichiarata_conclusa"}
    ]


def confronta(precedenti, correnti):
    precedenti_per_url = {x.get("url"): x for x in precedenti if x.get("url")}
    correnti_per_url = {x.get("url"): x for x in correnti if x.get("url")}
    nuovi = [x for url, x in correnti_per_url.items() if url not in precedenti_per_url]
    rimossi = [x for url, x in precedenti_per_url.items() if url not in correnti_per_url]
    campi = ["titolo", "stato_verifica", "rilevanza", "submission_aperta", "stato_submission", "deadline"]
    modificati = []
    for url, corrente in correnti_per_url.items():
        precedente = precedenti_per_url.get(url)
        if not precedente:
            continue
        cambiati = [campo for campo in campi if precedente.get(campo) != corrente.get(campo)]
        if cambiati:
            modificati.append({"titolo": corrente["titolo"], "url": url, "campi_modificati": cambiati})
    return nuovi, rimossi, modificati


def salva_risultati(risultati, canale, fonti_feed, totale_elementi, totale_candidati):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    dati = {
        "fonte": FONTE,
        "canale_utilizzato": canale,
        "fonti_feed": fonti_feed,
        "criterio": "allerta Ricerca Finalizzata, pertinenza oncologica e deadline",
        "nota": (
            "I risultati da motore di ricerca sono segnalazioni verso pagine ufficiali "
            "salute.gov.it e non bandi confermati finché la pagina ufficiale non è accessibile."
        ),
        "totale_elementi_feed": totale_elementi,
        "totale_candidati": totale_candidati,
        "numero_risultati": len(risultati),
        "calls": risultati,
    }
    with OUTPUT_FILE.open("w", encoding="utf-8") as file:
        json.dump(dati, file, ensure_ascii=False, indent=2)
        file.write("\n")


def aggiungi_riepilogo(risultati, nuovi, rimossi, modificati, canale):
    percorso = os.environ.get("GITHUB_STEP_SUMMARY")
    if not percorso:
        return
    righe = [
        "# Ricerca Finalizzata",
        "",
        f"Canale utilizzato: **{canale}**",
        "",
        f"Risultati correnti: **{len(risultati)}**",
        f"Nuovi risultati: **{len(nuovi)}**",
        f"Risultati modificati: **{len(modificati)}**",
        f"Risultati rimossi: **{len(rimossi)}**",
        "",
    ]
    if risultati:
        righe.extend(["## Segnalazioni correnti", ""])
        for risultato in risultati:
            righe.append(f"- [{risultato['titolo']}]({risultato['url']})")
            righe.append(f"  - Stato verifica: **{risultato['stato_verifica']}**")
            righe.append(f"  - Rilevanza: **{risultato['rilevanza']}**")
            righe.append(f"  - Submission: **{risultato['stato_submission']}**")
        righe.append("")
    else:
        righe.extend(["Nessuna segnalazione pertinente rilevata.", ""])
    with open(percorso, "a", encoding="utf-8") as file:
        file.write("\n".join(righe) + "\n")


def stampa_risultati(risultati):
    print("\nRISULTATI RICERCA FINALIZZATA")
    print("--------------------------------")
    if not risultati:
        print("Nessuna segnalazione pertinente.")
        return
    for numero, risultato in enumerate(risultati, start=1):
        print(f"{numero}. {risultato['titolo']}")
        print(f"   {risultato['url']}")
        print(f"   Stato verifica: {risultato['stato_verifica']}")
        print(f"   Rilevanza: {risultato['rilevanza']}")
        print(f"   Submission: {risultato['stato_submission']}")


def main():
    print("=" * 60)
    print("MONITORAGGIO RICERCA FINALIZZATA CON FALLBACK")
    print("=" * 60)

    precedenti = carica_precedenti()
    print(f"Risultati nell'archivio precedente: {len(precedenti)}")
    sessione = crea_sessione()

    try:
        entries, fonti_feed = leggi_feed_ufficiale(sessione)
        canale = "feed_rss_ufficiale"
        candidati = estrai_candidati(entries, canale)
    except (requests.RequestException, RuntimeError) as errore_ufficiale:
        print(f"Feed ufficiale non disponibile: {errore_ufficiale}")
        try:
            entries, fonti_feed = leggi_feed_bing(sessione)
            canale = "bing_news_rss_fallback"
            candidati = estrai_candidati(entries, canale)
        except (requests.RequestException, RuntimeError) as errore_fallback:
            print(f"Fallback non disponibile: {errore_fallback}")
            print("Il precedente archivio non verra sovrascritto.")
            raise SystemExit(1)

    print(f"Canale utilizzato: {canale}")
    print(f"Elementi complessivi ricevuti: {len(entries)}")
    print(f"Candidati ufficiali trovati: {len(candidati)}")

    verificati = []
    for numero, candidato in enumerate(candidati, start=1):
        print(f"Verifica {numero}/{len(candidati)}: {candidato['titolo']}")
        verificato = verifica_candidato(sessione, candidato)
        print(f"  Stato: {verificato['stato_verifica']}")
        verificati.append(verificato)

    risultati = seleziona_attivi(verificati)
    nuovi, rimossi, modificati = confronta(precedenti, risultati)
    salva_risultati(risultati, canale, fonti_feed, len(entries), len(candidati))
    aggiungi_riepilogo(risultati, nuovi, rimossi, modificati, canale)

    print(f"Risultati archiviati: {len(risultati)}")
    print(f"Nuovi risultati: {len(nuovi)}")
    print(f"Risultati modificati: {len(modificati)}")
    print(f"Risultati rimossi: {len(rimossi)}")
    print(f"File aggiornato: {OUTPUT_FILE}")
    stampa_risultati(risultati)
    print("\nMonitoraggio Ricerca Finalizzata completato correttamente.")


if __name__ == "__main__":
    main()
