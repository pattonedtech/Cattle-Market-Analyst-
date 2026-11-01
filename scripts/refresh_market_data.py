#!/usr/bin/env python3
"""Refresh public cattle cash-market observations and indicative futures benchmarks.

Only reported prices are stored as observations. National/regional futures quotes,
auction prices, direct trades, video contracts, and price ranges remain distinct.
"""
from __future__ import annotations
import json, re, hashlib, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, quote
from io import BytesIO
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "market_observations.json"
FUTURES_OUT = DATA / "futures.json"
S = requests.Session()
S.headers.update({"User-Agent": "FlyingP-CattleMarketAnalyst/2.0 (public market data aggregation)"})


def stable_id(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:18]


def fetch(url):
    r = S.get(url, timeout=45)
    r.raise_for_status()
    return r


def parse_date(text):
    pat = r"(?:SALE DATE:\s*)?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?[,]?\s*(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})\s*(?:st|nd|rd|th)?[,]?\s+(20\d{2})"
    m = re.search(pat, text, flags=re.I)
    if not m:
        return None
    try:
        month = m.group(1).title()
        fmt = "%B %d %Y" if len(month) > 3 else "%b %d %Y"
        return datetime.strptime(f"{month} {m.group(2)} {m.group(3)}", fmt).date().isoformat()
    except ValueError:
        return None

def record(date, market, region, category, sex, breed, weight, price, head, source, url,
           record_type, price_low=None, price_high=None, sale_type="Auction", notes=""):
    return {
        "id": stable_id(market, date, category, sex, weight, price, head, url),
        "date": date, "market": market, "region": region, "category": category,
        "sex": sex, "breed": breed, "weight": float(weight), "price": float(price),
        "price_low": float(price_low if price_low is not None else price),
        "price_high": float(price_high if price_high is not None else price),
        "head": int(head) if head not in (None, "") else None,
        "sale": sale_type, "source": source, "source_url": url,
        "price_unit": "USD/cwt", "record_type": record_type, "notes": notes
    }


def needs_backfill():
    if not OUT.exists():
        return True
    try:
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        return int(payload.get("schema_version", 0)) < 4
    except Exception:
        return True


def pdf_text(url):
    r = fetch(url)
    reader = PdfReader(BytesIO(r.content))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def parse_usda_pdf(url, report_id, market, region, record_type):
    try:
        text = pdf_text(url)
    except Exception as e:
        print(f"USDA {report_id} PDF fetch failed {url}: {e}", file=sys.stderr)
        return []
    date = parse_date(text[:1800]) or parse_date(text)
    if not date:
        print(f"USDA {report_id}: report date not identified in {url}", file=sys.stderr)
        return []
    records = []
    normalized = re.sub(r"\s+", " ", text)
    section_matches = list(re.finditer(
        r"(DAIRY STEERS?|BRED COWS?|COW-CALF PAIRS?|STEERS?|HEIFERS?|COWS?|BULLS?)\s*-\s*"
        r"(?:Medium|Large|Small|Utility|Boner|Lean|Breaker|Cutter)[^:]{0,100}?\(Per Cwt[^)]*\)",
        normalized, flags=re.I
    ))
    row_re = re.compile(
        r"(\d{1,4})\s+(\d{2,4}(?:-\d{2,4})?)\s+(\d{2,4})\s+"
        r"([\d,.]+(?:-[\d,.]+)?)\s+([\d,.]+)"
    )
    for idx, section_match in enumerate(section_matches):
        heading = section_match.group(0).upper()
        segment_end = section_matches[idx+1].start() if idx+1 < len(section_matches) else len(normalized)
        segment = normalized[section_match.end():segment_end]
        section = section_match.group(1).upper()
        if "HEIFER" in section:
            sex, category = "Heifer", "Feeder heifer"
        elif "STEER" in section:
            sex, category = "Steer", "Feeder steer"
        elif "COW" in section:
            sex, category = "Cow", "Cull cow"
        else:
            sex, category = "Bull", "Bull"
        for row in row_re.finditer(segment):
            try:
                head = int(row.group(1).replace(",", ""))
                avg_weight = float(row.group(3).replace(",", ""))
                avg_price = float(row.group(5).replace(",", ""))
                if head <= 0 or avg_weight < 100 or avg_price <= 0:
                    continue
            except ValueError:
                continue
            breed = "Dairy / Holstein" if "DAIRY" in section else "USDA grade; breed not specified"
            records.append(record(date, market, region, category, sex, breed, avg_weight, avg_price, head,
                                  "USDA AMS Market News", url, record_type,
                                  notes=f"USDA report {report_id}; table {heading}; avg price/cwt"))
    if not records and "Direct" in text:
        for section, sex, category in [
            ("Steers", "Steer", "Feeder steer"), ("Heifers", "Heifer", "Feeder heifer")
        ]:
            pattern = re.compile(
                rf"{section}\s*-\s*Medium and Large[^ ]*.*?Current\s+(?:FOB|DEL)\s+"
                r"(\d{1,4})\s+(\d{2,4})\s+(\d{2,4})\s+([\d,.]+)\s+([\d,.]+)",
                re.I
            )
            for row in pattern.finditer(normalized):
                head, wt_range, avg_wt, price_range, avg_price = row.groups()
                records.append(record(date, market, region, category, sex, "USDA grade; breed not specified",
                                      float(avg_wt), float(avg_price), int(head), "USDA AMS Market News", url,
                                      record_type, notes=f"USDA report {report_id}; direct {section} trade"))
    return records


def parse_usda_report(report_id, market, region, record_type="usda_auction"):
    page_url = f"https://mymarketnews.ams.usda.gov/viewReport/{report_id}"
    fallback_url = f"https://www.ams.usda.gov/mnreports/ams_{report_id}.pdf"
    report_urls = []
    # The public AMS archive index can be very slow from automated runners. On routine
    # updates, fetch the known current-report PDF directly instead of waiting 45 seconds
    # for an archive page that is not needed for the latest observation.
    if needs_backfill():
        try:
            soup = BeautifulSoup(fetch(page_url).text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(page_url, a["href"])
                if ".pdf" in href.lower() and (str(report_id) in href or f"ams_{report_id}" in href.lower()):
                    if href not in report_urls:
                        report_urls.append(href)
        except Exception as e:
            print(f"USDA {report_id} archive index failed: {e}", file=sys.stderr)
        if not report_urls:
            report_urls = [fallback_url]
        report_urls = report_urls[:150]
    else:
        report_urls = [fallback_url]
    records = []
    for url in report_urls:
        records.extend(parse_usda_pdf(url, report_id, market, region, record_type))
    print(f"USDA {report_id} {market}: {len(records)} normalized rows from {len(report_urls)} reports")
    return records

def parse_marana():
    archive_url = "https://www.maranastockyards.com/market-report-archives"
    current_url = "https://www.maranastockyards.us/marketreport/current.html"
    report_urls = [current_url]
    try:
        archive = BeautifulSoup(fetch(archive_url).text, "html.parser")
        for a in archive.find_all("a", href=True):
            href = urljoin(archive_url, a["href"])
            if "maranastockyards.us/marketreport/" in href and href not in report_urls:
                report_urls.append(href)
    except Exception as e:
        print(f"Marana archive index failed: {e}", file=sys.stderr)
    records, cow_records = [], []
    for url in (report_urls[:220] if needs_backfill() else report_urls[:1]):
        try:
            soup = BeautifulSoup(fetch(url).text, "html.parser")
            body = soup.get_text(" ", strip=True)
            date = parse_date(body)
            if not date:
                continue
            section = ""
            for tr in soup.find_all("tr"):
                cells = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"])]
                upper = " ".join(cells).upper()
                if "BRED COWS" in upper and "PAIRS" in upper:
                    section = "bred_pairs"
                    continue
                if "BRED COWS" in upper:
                    section = "bred"
                    continue
                if re.search(r"\bPAIRS\b", upper) and ("DESCRIPTION" in upper or len(cells) <= 4):
                    section = "pairs"
                    continue
                if "STEERS" in upper and "HEIFERS" in upper:
                    section = "feeders"
                    continue
                if "BEEF COWS (CWT)" in upper or "HOLSTEIN COWS (CWT)" in upper or "WEIGH BULLS (CWT)" in upper:
                    section = "cull"
                    continue
                if not cells:
                    continue
                if section == "feeders":
                    groups = []
                    if len(cells) >= 3:
                        groups.append(("Steer", cells[0], cells[1], cells[2]))
                    if len(cells) >= 7:
                        groups.append(("Heifer", cells[4], cells[5], cells[6]))
                    for sex, desc, avg_text, price_text in groups:
                        wt = re.search(r"\d{3,4}(?:\.\d+)?", avg_text)
                        price = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", price_text)
                        head = re.search(r"^\s*(\d+)", desc)
                        if not wt or not price:
                            continue
                        try:
                            weight, value = float(wt.group(0)), float(price.group(1).replace(",", ""))
                        except (ValueError, TypeError):
                            continue
                        if not (200 <= weight <= 1600 and 50 <= value <= 1000):
                            continue
                        breed = "Angus" if "ANGUS" in desc.upper() else ("Beef cross / native" if any(x in desc.upper() for x in ["BLK", "BMF", "RMF", "BWF", "RED"]) else "Mixed / unknown")
                        records.append(record(date, "Marana Stockyards", "Arizona",
                                              "Feeder steer" if sex == "Steer" else "Feeder heifer",
                                              sex, breed, weight, value, int(head.group(1)) if head else None,
                                              "Marana Stockyards", url, "auction_lot_average",
                                              sale_type="Live auction", notes=f"Published lot description: {desc}"))
                elif section in ("bred", "pairs", "bred_pairs"):
                    groups = []
                    if section == "bred_pairs":
                        if len(cells) >= 7:
                            groups = [("bred", cells[0], cells[2]), ("pairs", cells[4], cells[6])]
                        elif len(cells) >= 4:
                            groups = [("bred", cells[0], cells[1]), ("pairs", cells[2], cells[3])]
                    else:
                        if len(cells) >= 3 and cells[1] == "":
                            groups = [(section, cells[0], cells[2])]
                        elif len(cells) >= 2:
                            groups = [(section, cells[0], cells[1])]
                    for cow_type, desc, price_text in groups:
                        price = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", price_text)
                        if not desc or not price:
                            continue
                        try:
                            value = float(price.group(1).replace(",", ""))
                        except (ValueError, TypeError):
                            continue
                        if value < 100:
                            continue
                        cow_records.append({
                            "id": stable_id("marana", date, cow_type, desc, value, url),
                            "date": date, "market": "Marana Stockyards", "region": "Arizona",
                            "category": "Bred cow" if cow_type == "bred" else "Cow-calf pair",
                            "description": desc, "price": value, "price_unit": "USD/head",
                            "source": "Marana Stockyards", "source_url": url
                        })
        except Exception as e:
            print(f"Marana report failed {url}: {e}", file=sys.stderr)
    print(f"Marana: {len(records)} feeder lots and {len(cow_records)} cow/pair records from {min(len(report_urls),220)} reports")
    return records, cow_records

def parse_fallon():
    index = "https://www.fallonlivestock.com/market-reports.html"
    report_urls = []
    try:
        soup = BeautifulSoup(fetch(index).text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(index, a["href"])
            label = a.get_text(" ", strip=True)
            if ".pdf" in href.lower() and ("market_report" in href.lower() or "market-report" in href.lower()):
                if href not in report_urls:
                    report_urls.append(href)
    except Exception as e:
        print(f"Fallon archive index failed: {e}", file=sys.stderr)
    records, cow_records = [], []
    for url in (report_urls[:150] if needs_backfill() else report_urls[:1]):
        try:
            text = pdf_text(url)
            date = parse_date(text[:1200]) or parse_date(url.replace("-", " "))
            if not date:
                continue
            feeder_section = False
            for line in text.splitlines():
                line = " ".join(line.split())
                upper = line.upper()
                if "CHOICE STEERS (CWT)" in upper and "CHOICE HEIFERS (CWT)" in upper:
                    feeder_section = True
                    continue
                if "CHOICE CATTLE (CWT)" in upper or "DAIRY CATTLE (CWT)" in upper:
                    feeder_section = False
                if feeder_section:
                    matches = list(re.finditer(r"(\d{3,4})\s*-\s*(\d{3,4})\s+(\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?)", line))
                    for n, m in enumerate(matches):
                        low_w, high_w, price_text = m.groups()
                        p = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?", price_text)
                        if not p:
                            continue
                        try:
                            lo, hi = float(p.group(1)), float(p.group(2) or p.group(1))
                            low_weight, high_weight = float(low_w), float(high_w)
                        except (ValueError, TypeError):
                            continue
                        sex = "Steer" if n == 0 else ("Heifer" if n == 1 else "Unknown")
                        if sex == "Unknown" or hi <= 0:
                            continue
                        records.append(record(date, "Fallon Livestock", "Nevada",
                                              "Feeder steer" if sex == "Steer" else "Feeder heifer",
                                              sex, "Choice; breed not specified",
                                              (low_weight+high_weight)/2, (lo+hi)/2, None,
                                              "Fallon Livestock", url, "published_weight_class_range",
                                              lo, hi, "Live auction",
                                              notes=f"Published weight class {low_w}-{high_w} lb; range, not weighted average"))
                # Bred cow and pair prices are per head and remain separate from cwt series.
                if "BRED COWS (HD)" in upper or "PAIRS (HD)" in upper:
                    for category, pattern in [
                        ("Bred cow", r"Bred Cows?\s*\(HD\)\s*([\d,.]+)(?:\s*-\s*([\d,.]+))?"),
                        ("Cow-calf pair", r"Pairs\s*\(HD\)\s*([\d,.]+)(?:\s*-\s*([\d,.]+))?")
                    ]:
                        m = re.search(pattern, line, flags=re.I)
                        if m:
                            lo = float(m.group(1).replace(",", ""))
                            hi = float((m.group(2) or m.group(1)).replace(",", ""))
                            cow_records.append({
                                "id": stable_id("fallon", date, category, lo, hi, url),
                                "date": date, "market": "Fallon Livestock", "region": "Nevada",
                                "category": category, "description": "Published per-head range",
                                "price": (lo+hi)/2, "price_low": lo, "price_high": hi,
                                "price_unit": "USD/head", "source": "Fallon Livestock", "source_url": url
                            })
        except Exception as e:
            print(f"Fallon report failed {url}: {e}", file=sys.stderr)
    print(f"Fallon: {len(records)} feeder weight-class ranges and {len(cow_records)} cow/pair price ranges from {len(report_urls)} reports")
    return records, cow_records

def parse_willcox():
    archive_url = "https://test.willcoxlivestockauction.com/market-report/"
    pages = [archive_url]
    report_pages = []
    try:
        soup = BeautifulSoup(fetch(archive_url).text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(archive_url, a["href"])
            label = a.get_text(" ", strip=True).lower()
            if "page/2" in href or label in ("2", "next", "older posts"):
                if href not in pages:
                    pages.append(href)
        for page_url in pages:
            page = BeautifulSoup(fetch(page_url).text, "html.parser")
            for a in page.find_all("a", href=True):
                href = urljoin(page_url, a["href"])
                if "/20" in href and "market-report-" in href and href not in report_pages:
                    report_pages.append(href)
    except Exception as e:
        print(f"Willcox archive index failed: {e}", file=sys.stderr)
    records, cow_records = [], []
    for page_url in (report_pages[:100] if needs_backfill() else report_pages[:1]):
        try:
            page = BeautifulSoup(fetch(page_url).text, "html.parser")
            pdf_url = None
            for a in page.find_all("a", href=True):
                href = urljoin(page_url, a["href"])
                if ".pdf" in href.lower() and "market-report" in href.lower():
                    pdf_url = href
                    break
            if not pdf_url:
                continue
            text = pdf_text(pdf_url)
            date = parse_date(text[:500]) or parse_date(page.get_text(" ", strip=True))
            if not date:
                continue
            feeder_section = False
            cow_section = False
            for line in text.splitlines():
                line = " ".join(line.split())
                upper = line.upper()
                if "STEERS" in upper and "HEIFERS" in upper:
                    feeder_section, cow_section = True, False
                    continue
                if "BRED COWS" in upper and "PAIRS" in upper:
                    cow_section, feeder_section = True, False
                    continue
                if "AUCTION SUMMARY" in upper:
                    feeder_section = cow_section = False
                if feeder_section:
                    lot_re = re.compile(r"(\d+)\s+([A-Z][A-Z/]*(?:\s+[A-Z][A-Z/]*){0,2})\s+(\d{3,4})\s+\$([\d,]+(?:\.\d{1,2})?)")
                    lots = list(lot_re.finditer(line))
                    for n, m in enumerate(lots):
                        head, desc, wt, price = m.groups()
                        sex = "Steer" if n == 0 else ("Heifer" if n == 1 else "Unknown")
                        if sex == "Unknown":
                            continue
                        records.append(record(date, "Willcox Livestock Auction", "Arizona",
                                              "Feeder steer" if sex == "Steer" else "Feeder heifer",
                                              sex, desc, float(wt), float(price.replace(",", "")), int(head),
                                              "Willcox Livestock Auction", pdf_url, "auction_lot_average",
                                              sale_type="Live auction", notes=f"Published lot description: {desc}"))
                elif cow_section:
                    cow_re = re.compile(r"(\d+)\s+([A-Z][A-Z/]*(?:\s+[A-Z][A-Z/]*){0,2})\s+\$([\d,]+(?:\.\d{1,2})?)")
                    lots = list(cow_re.finditer(line))
                    for n, m in enumerate(lots):
                        head, desc, price = m.groups()
                        cow_records.append({
                            "id": stable_id("willcox", date, n, desc, price, pdf_url),
                            "date": date, "market": "Willcox Livestock Auction", "region": "Arizona",
                            "category": "Bred cow" if n == 0 else "Cow-calf pair",
                            "description": f"{head} {desc}", "price": float(price.replace(",", "")),
                            "price_unit": "USD/head", "source": "Willcox Livestock Auction", "source_url": pdf_url
                        })
        except Exception as e:
            print(f"Willcox report failed {page_url}: {e}", file=sys.stderr)
    print(f"Willcox: {len(records)} feeder lots and {len(cow_records)} cow/pair records from {len(report_pages)} report pages")
    return records, cow_records


def parse_roswell_web():
    url = "https://www.roswelllivestockauction.com/market-report.html"
    try:
        text = BeautifulSoup(fetch(url).text, "html.parser").get_text(" ", strip=True)
        date = parse_date(text)
        if date:
            print(f"Roswell public market report found for {date}; USDA report is ingested separately")
    except Exception as e:
        print(f"Roswell page failed: {e}", file=sys.stderr)
    return []


def parse_overland():
    index_url = "https://www.overlandstockyard.com/market-reports"
    records, report_urls = [], {}
    try:
        soup = BeautifulSoup(fetch(index_url).text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(index_url, a["href"])
            m = re.search(r"/lot-listing/(\d+)/mr", href)
            if m:
                report_urls[m.group(1)] = href.split("?")[0]
    except Exception as e:
        print(f"Overland archive index failed: {e}", file=sys.stderr)
        return records
    for report_id, url in list(report_urls.items())[:200 if needs_backfill() else 10]:
        try:
            page = BeautifulSoup(fetch(url).text, "html.parser")
            body = page.get_text(" ", strip=True)
            date = parse_date(body)
            if not date:
                continue
            matches = list(re.finditer(r"\bLot\s+([A-Za-z0-9-]+)\b", body, flags=re.I))
            for i, match in enumerate(matches):
                chunk = body[match.start(): matches[i+1].start() if i+1 < len(matches) else len(body)]
                price_match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", chunk)
                weight_match = re.search(r"(\d+)\s*Hd\s*\(\s*([\d,]+)\s*lbs?\s*\)", chunk, flags=re.I)
                desc_match = re.search(r"\b(HOL\s*(?:STR|HFR)|HOLSTEIN\s*(?:STEER|HEIFER)?|NATIVE\s*(?:STR|HFR|STEER|HEIFER)|BEEF\s*(?:STR|HFR|STEER|HEIFER)|ANGUS\s*(?:STR|HFR|STEER|HEIFER)|HOL\s*BEEF\s*(?:STR|HFR))\b", chunk, flags=re.I)
                if not price_match or not weight_match:
                    continue
                price = float(price_match.group(1).replace(",", ""))
                head, weight = int(weight_match.group(1)), float(weight_match.group(2).replace(",", ""))
                desc = re.sub(r"\s+", " ", desc_match.group(1)).upper() if desc_match else "UNCLASSIFIED"
                if "HFR" in desc or "HEIFER" in desc:
                    sex, category = "Heifer", "Feeder heifer"
                elif "STR" in desc or "STEER" in desc:
                    sex, category = "Steer", "Feeder steer"
                else:
                    sex, category = "Unknown", "Other"
                breed = "Holstein / dairy" if "HOL" in desc or "HOLSTEIN" in desc else ("Angus" if "ANGUS" in desc else ("Beef cross / native" if "NATIVE" in desc or "BEEF" in desc else "Mixed / unknown"))
                if price <= 0 or weight <= 0 or head <= 0:
                    continue
                records.append(record(date, "Overland Stock Yard", "California", category, sex, breed, weight, price, head,
                                      "Overland Stock Yard", url, "video_lot_contract",
                                      sale_type="Video / internet", notes=f"Report {report_id}; lot {match.group(1)}; description {desc}"))
        except Exception as e:
            print(f"Overland report {url} failed: {e}", file=sys.stderr)
    print(f"Overland: {len(records)} lot records from {len(report_urls)} report pages")
    return records


def parse_visalia():
    page_url = "https://www.visalialivestock.com/market-snapshot.html"
    records = []
    try:
        soup = BeautifulSoup(fetch(page_url).text, "html.parser")
        pdf_urls = [urljoin(page_url, a["href"]) for a in soup.find_all("a", href=True)
                    if a["href"].lower().endswith(".pdf") and "snapshot" in a["href"].lower()]
        for url in sorted(set(pdf_urls)):
            try:
                text = pdf_text(url)
                date = parse_date(text[:800]) or parse_date(url.replace("_", " "))
                if not date:
                    print(f"Visalia date not identified: {url}", file=sys.stderr)
                    continue
                row_re = re.compile(
                    r"\$\s*([\d,]+(?:\.\d{1,2})?)\s*-\s*\$?\s*([\d,]+(?:\.\d{1,2})?)"
                    r"\s+(\d{3})\s+LBS?\s+"
                    r"\$\s*([\d,]+(?:\.\d{1,2})?)\s*-\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", re.I
                )
                for m in row_re.finditer(text):
                    w = int(m.group(3))
                    for category, sex, lo_i, hi_i in [("Feeder steer", "Steer", 1, 2), ("Feeder heifer", "Heifer", 4, 5)]:
                        lo, hi = float(m.group(lo_i).replace(",", "")), float(m.group(hi_i).replace(",", ""))
                        records.append(record(date, "Visalia Livestock Market", "California", category, sex,
                                              "Choice cattle; breed not specified", w, (lo+hi)/2, None,
                                              "Visalia Livestock Market", url, "published_price_range",
                                              lo, hi, "Regular sale snapshot"))
            except Exception as e:
                print(f"Visalia snapshot failed {url}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Visalia index failed: {e}", file=sys.stderr)
    print(f"Visalia: {len(records)} price-band observations")
    return records


def fetch_usda_outlook():
    index_url = "https://ers.usda.gov/topics/animal-products/cattle-beef/market-outlook"
    page_url = index_url
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "USDA Economic Research Service",
        "source_url": page_url,
        "report_date": None,
        "forecasts": {},
        "fundamentals": {},
        "status": "unavailable"
    }
    try:
        page = BeautifulSoup(fetch(index_url).text, "html.parser")
        report_page_url = None
        series_url = None
        for a in page.find_all("a", href=True):
            href = urljoin(index_url, a["href"])
            label = a.get_text(" ", strip=True).lower()
            # ERS topic pages link the latest LDP report with its report title, not
            # necessarily the word "report" (e.g. "Livestock, Dairy, and Poultry Outlook").
            if "/publications" in href and ("livestock" in label or "poultry outlook" in label or "ldp-m" in label):
                if re.search(r"/publications/\d+", href):
                    report_page_url = href
                    break
                series_url = href
        if not report_page_url and series_url:
            series_page = BeautifulSoup(fetch(series_url).text, "html.parser")
            for a in series_page.find_all("a", href=True):
                href = urljoin(series_url, a["href"])
                label = a.get_text(" ", strip=True).lower()
                if ("livestock, dairy, and poultry outlook" in label or "ldp-m-" in label) and re.search(r"/publications/\d+", href):
                    report_page_url = href
                    break
        if not report_page_url:
            # ERS currently exposes the latest report through a series page that can
            # time out from automated runners. Fall back to the verified October 2026
            # report page; update this ID if ERS changes the latest publication.
            report_page_url = "https://www.ers.usda.gov/publications/115633"
            print("ERS series listing unavailable; using verified latest LDP report page", file=sys.stderr)
        report_page = BeautifulSoup(fetch(report_page_url).text, "html.parser")
        pdf_url = None
        for a in report_page.find_all("a", href=True):
            href = urljoin(report_page_url, a["href"])
            if ".pdf" in href.lower() and "ldp-m-" in href.lower():
                pdf_url = href
                break
        if not pdf_url:
            raise ValueError("Latest ERS Outlook PDF link not found")
        text = pdf_text(pdf_url)
        result["report_page_url"] = report_page_url
        result["source_url"] = pdf_url
        result["report_date"] = parse_date(text[:3000])
        normalized = re.sub(r"\s+", " ", text)
        fed_q4 = re.search(
            r"slaughter steer price forecast for fourth quarter 2026[^.]*\$([\d.]+) per cwt",
            normalized, flags=re.I
        )
        fed_annual = re.search(
            r"2027 price outlook[^.]*\$([\d.]+) per cwt",
            normalized, flags=re.I
        )
        feeder_q4 = re.search(
            r"fourth-quarter price forecast.{0,180}?(?:to|at)\s*\$([\d]+(?:\.\d+)?)\s+per cwt",
            normalized, flags=re.I
        )
        feeder_annual = re.search(
            r"2027 feeder steer price outlook[^.]*\$([\d.]+) per cwt",
            normalized, flags=re.I
        )
        if fed_q4 or fed_annual:
            result["forecasts"]["fed_steer"] = {
                "2026_Q4": float(fed_q4.group(1)) if fed_q4 else None,
                "2027_annual": float(fed_annual.group(1)) if fed_annual else None,
                "unit": "USD/cwt", "basis": "USDA 5-area marketing region slaughter steer"
            }
        if feeder_q4 or feeder_annual:
            result["forecasts"]["feeder_steer"] = {
                "2026_Q4": float(feeder_q4.group(1)) if feeder_q4 else None,
                "2027_annual": float(feeder_annual.group(1)) if feeder_annual else None,
                "unit": "USD/cwt", "basis": "750–800 lb Medium and Large #1 steer, Oklahoma City National Stockyards"
            }
        placement = re.search(
            r"placements\s*\d*\s*in August were ([\d.]+) percent lower than last year at ([\d.]+) million head "
            r"and August marketings were down ([\d.]+) percent at ([\d.]+) million head",
            normalized, flags=re.I
        )
        if placement:
            result["fundamentals"] = {
                "august_placements_yoy_pct": -float(placement.group(1)),
                "august_placements_million_head": float(placement.group(2)),
                "august_marketings_yoy_pct": -float(placement.group(3)),
                "august_marketings_million_head": float(placement.group(4))
            }
        production = re.search(
            r"2026 beef production forecast.*?(\d+\.\d+) billion pounds.*?2027 beef production forecast.*?(\d+\.\d+) billion pounds",
            normalized, flags=re.I
        )
        if production:
            result["fundamentals"]["beef_production_2026_billion_lb"] = float(production.group(1))
            result["fundamentals"]["beef_production_2027_billion_lb"] = float(production.group(2))
        result["status"] = "updated" if result["forecasts"] else "pdf_loaded_forecast_rows_not_parsed"
    except Exception as e:
        result["error"] = str(e)
        print(f"USDA ERS outlook refresh failed: {e}", file=sys.stderr)
    (DATA / "usda_outlook.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"USDA ERS outlook: {result['status']} ({result.get('report_date')})")


def fetch_futures():
    # Farmbucks publishes an indicative deferred CME contract chain, supplied by DTN/CME.
    # Quotes are cents/lb; numerically equal to dollars/cwt (e.g. 337.78 cents/lb = $337.78/cwt).
    url = "https://farmbucks.com/futures/livestock"
    contracts = []
    try:
        soup = BeautifulSoup(fetch(url).text, "html.parser")
        text = " ".join(soup.get_text(" ", strip=True).split())
        pat = re.compile(r"\b(GF|LE)([A-Z])(\d{2})\s+([A-Za-z]{3})\s+(20\d{2})\s+([\d.]+)\s+¢/lb\s+([+-]?[\d.]+)\s+([+-]?[\d.]+)%", re.I)
        for m in pat.finditer(text):
            product, month_code, year2, month_name, year, price, change, pct = m.groups()
            code = product.upper() + month_code.upper() + year2
            contracts.append({
                "symbol": code, "product": "Feeder cattle" if product.upper()=="GF" else "Live cattle",
                "contract_month": month_name + " " + year, "month": datetime.strptime(month_name,"%b").month,
                "year": int(year), "price_cwt": float(price), "change": float(change),
                "change_pct": float(pct), "quote_unit": "cents/lb",
                "equivalent_unit": "USD/cwt", "source": "Farmbucks (data credited to DTN/CME)",
                "source_url": url
            })
    except Exception as e:
        print(f"Deferred futures chain unavailable: {e}", file=sys.stderr)
    FUTURES_OUT.parent.mkdir(parents=True, exist_ok=True)
    FUTURES_OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "Deferred futures are exchange benchmarks, not local cash bids. Apply a local basis and allow for contract-to-cash differences.",
        "contracts": contracts
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(contracts)} deferred futures contracts")

SOURCES = [
    {"name":"Visalia Livestock Market","state":"CA","kind":"auction snapshot","url":"https://www.visalialivestock.com/market-snapshot.html","status":"automated: published price ranges"},
    {"name":"Overland Stock Yard","state":"CA","kind":"auction / video lots","url":"https://www.overlandstockyard.com/market-reports","status":"automated: video lots; dairy and beef types remain separate"},
    {"name":"Tulare County Stockyard","state":"CA","kind":"live auction","url":"https://tularecountystockyard.com/","status":"listed; historical parser pending"},
    {"name":"Escalon Livestock Market","state":"CA","kind":"live auction","url":"https://www.escalonlivestockmarket.com/","status":"listed; weekly reports available; parser pending"},
    {"name":"Producers Livestock Marketing Association","state":"CA","kind":"live auction","url":"https://www.producerslivestock.com/","status":"listed; historical parser pending"},
    {"name":"Turlock Livestock Auction Yard","state":"CA","kind":"live auction","url":"https://www.turlocklivestock.com/","status":"listed; historical parser pending"},
    {"name":"Tulare Sales Yard","state":"CA","kind":"live auction","url":"https://tularesalesyard.com/","status":"listed; historical parser pending"},
    {"name":"101 Livestock Market","state":"CA","kind":"live auction","url":"https://www.101livestock.com/","status":"listed; historical parser pending"},
    {"name":"Modesto Livestock Commission","state":"CA","kind":"live auction","url":"https://www.modestolivestock.com/","status":"listed; historical parser pending"},
    {"name":"Dos Palos Y Auction","state":"CA","kind":"live auction","url":"https://www.dpyauction.com/market-reports","status":"listed; weekly report PDFs identified; parser pending"},
    {"name":"Producers Livestock Marketing Association","state":"CA","kind":"live auction","url":"https://www.producerslivestock.com/","status":"listed; historical parser pending"},
    {"name":"Fresno Livestock Commission","state":"CA","kind":"live auction","url":"https://www.fresnolivestock.com/","status":"listed; historical parser pending"},
    {"name":"A&M Livestock Auction","state":"CA","kind":"live / video auction","url":"https://www.amlivestockauction.com/","status":"listed; historical parser pending"},
    {"name":"Western Stockman's Market","state":"CA","kind":"live auction","url":"https://www.westernstockmans.com/","status":"listed; historical parser pending"},
    {"name":"Marana Stockyards","state":"AZ","kind":"live auction","url":"https://www.maranastockyards.com/market-report-archives","status":"automated: archive backfill from 2022 plus current reports"},
    {"name":"Willcox Livestock Auction","state":"AZ","kind":"live auction","url":"https://willcoxlivestockauction.com/","status":"automated: published weekly report PDFs"},
    {"name":"USDA Southwest Direct Cattle (AZ/CA/NV)","state":"AZ/CA/NV","kind":"direct trade weighted average","url":"https://mymarketnews.ams.usda.gov/viewReport/2940","status":"automated: current USDA report"},
    {"name":"Fallon Livestock","state":"NV","kind":"live auction","url":"https://www.fallonlivestock.com/market-reports.html","status":"automated: archived weekly weight-class ranges and bred-cow/pair values"},
    {"name":"Nevada Livestock Marketing","state":"NV","kind":"live auction","url":"https://www.nevadalivestock.us/cattle/","status":"listed; weekly feeder sale and public price parser pending"},
    {"name":"Roswell Livestock Auction","state":"NM","kind":"live auction","url":"https://www.roswelllivestockauction.com/market-report.html","status":"report page tracked; USDA series ingested"},
    {"name":"USDA Roswell Livestock Auction","state":"NM","kind":"USDA auction report","url":"https://mymarketnews.ams.usda.gov/viewReport/1782","status":"automated: current USDA report"},
    {"name":"USDA Clovis Livestock Auction","state":"NM","kind":"USDA auction report","url":"https://mymarketnews.ams.usda.gov/viewReport/1781","status":"automated: current USDA report"},
    {"name":"USDA Cattlemen’s Livestock Auction - Belen","state":"NM","kind":"USDA auction report","url":"https://mymarketnews.ams.usda.gov/viewReport/1783","status":"automated: current USDA report"},
    {"name":"USDA Santa Teresa Livestock Auction","state":"NM","kind":"USDA auction report","url":"https://mymarketnews.ams.usda.gov/viewReport/1290","status":"automated: current USDA report"},
    {"name":"USDA New Mexico Weekly Cattle Summary","state":"NM","kind":"state auction summary","url":"https://mymarketnews.ams.usda.gov/viewReport/1784","status":"automated: current USDA report"},
    {"name":"Clovis Livestock Auction","state":"NM","kind":"live auction","url":"https://esmis.nal.usda.gov/publication/clovis-livestock-cattle-auction-clovis-nm","status":"archive found; historical import pending"},
    {"name":"Cattlemen's Livestock Auction","state":"NM","kind":"live auction","url":"https://www.cattlemenslivestock.com/","status":"listed; historical parser pending"}
]


def main():
    records = []
    records += parse_overland()
    records += parse_visalia()
    marana_records, marana_cows = parse_marana()
    willcox_records, willcox_cows = parse_willcox()
    records += marana_records + willcox_records
    records += parse_usda_report(1782, "Roswell Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1781, "Clovis Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1783, "Cattlemen's Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1290, "Santa Teresa Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1784, "New Mexico Weekly Cattle Summary (USDA)", "New Mexico", "usda_state_summary")
    records += parse_usda_report(2940, "Southwest Direct Cattle Report (USDA)", "AZ/CA/NV", "usda_direct_trade")
    parse_roswell_web()
    fallon_records, fallon_cows = parse_fallon()
    records += fallon_records
    old = []
    old_cows = []
    if OUT.exists():
        try:
            old_payload = json.loads(OUT.read_text(encoding="utf-8"))
            old = old_payload.get("records", [])
            old_cows = old_payload.get("cow_records", [])
        except Exception:
            pass
    by_id = {r["id"]: r for r in old}
    for r in records:
        by_id[r["id"]] = r
    merged = sorted(by_id.values(), key=lambda r: (r.get("date", ""), r.get("market", ""), r.get("id", "")))
    cow_by_id = {r["id"]: r for r in old_cows}
    for r in marana_cows + willcox_cows + fallon_cows:
        cow_by_id[r["id"]] = r
    merged_cows = sorted(cow_by_id.values(), key=lambda r: (r.get("date", ""), r.get("market", "")))
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schema_version": 4, "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(merged), "cow_record_count": len(merged_cows), "sources": SOURCES, "records": merged, "cow_records": merged_cows,
        "methodology": "Auction prices, USDA state summaries, direct trades, video contracts and published price bands are retained as separate record types. They are not pooled into one price series."
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(merged)} unique market observations to {OUT}")
    fetch_futures()
    fetch_usda_outlook()


if __name__ == "__main__":
    main()
