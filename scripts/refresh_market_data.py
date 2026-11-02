#!/usr/bin/env python3
"""Refresh public cattle cash-market observations and indicative futures benchmarks.

Only reported prices are stored as observations. National/regional futures quotes,
auction prices, direct trades, video contracts, and price ranges remain distinct.
"""
from __future__ import annotations
import json, re, hashlib, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, quote, urlparse
from io import BytesIO
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from market_model import normalize_observation, build_benchmarks, merge_quarantine_history

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "market_observations.json"
QA_OUT = DATA / "market_data_qa.json"
FUTURES_OUT = DATA / "futures.json"
SOURCE_FETCH_HEALTH = {}
S = requests.Session()
S.headers.update({
    # USDA AMS blocks some non-browser default clients at its legacy PDF endpoint.
    # Use a standard browser UA while retaining an identifiable application string.
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 FlyingP-CattleMarketAnalyst/2.0",
    "Accept": "text/html,application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
})


def stable_id(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:18]


def fetch(url):
    """Fetch with one bounded retry and retain domain-level source health."""
    domain = urlparse(url).netloc or "unknown"
    health = SOURCE_FETCH_HEALTH.setdefault(domain, {
        "requests": 0, "attempts": 0, "successes": 0,
        "failed_requests": 0, "transient_failures": 0,
        "failed_urls": [], "last_error": None,
        "last_failure_url": None, "last_failure_at": None,
        "last_success_at": None
    })
    health["requests"] += 1
    last_error = None
    for attempt in range(2):
        health["attempts"] += 1
        try:
            response = S.get(url, timeout=45)
            response.raise_for_status()
            health["successes"] += 1
            health["last_success_at"] = datetime.now(timezone.utc).isoformat()
            return response
        except Exception as exc:
            last_error = exc
            health["transient_failures"] += 1
            health["last_error"] = str(exc)[:500]
            health["last_failure_url"] = url
            health["last_failure_at"] = datetime.now(timezone.utc).isoformat()
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Retry network/timeouts, rate limits, and server errors. A 4xx such
            # as 404/403 is a source/link problem, not a transient outage.
            if status is not None and status < 500 and status != 429:
                break
            if attempt == 0:
                time.sleep(1)
    health["failed_requests"] += 1
    if url not in health["failed_urls"]:
        health["failed_urls"].append(url)
        health["failed_urls"] = health["failed_urls"][-10:]
    raise last_error


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

# Geographic attribution is based on the physical location of the reporting auction,
# not the origin of cattle in the lots. State-level and multi-state reports remain
# county-neutral rather than being assigned an invented county.
STATE_NAMES = {
    "CA": "California", "AZ": "Arizona", "NV": "Nevada", "NM": "New Mexico",
    "ID": "Idaho", "MT": "Montana", "WY": "Wyoming", "UT": "Utah", "WA": "Washington", "OR": "Oregon",
    "California": "California", "Arizona": "Arizona", "Nevada": "Nevada", "New Mexico": "New Mexico",
    "Idaho": "Idaho", "Montana": "Montana", "Wyoming": "Wyoming", "Utah": "Utah", "Washington": "Washington", "Oregon": "Oregon"
}
MARKET_COUNTIES = [
    ("visalia", "California", "Tulare"),
    ("ranchers choice", "California", "San Benito"),
    ("101 livestock", "California", "San Benito"),
    ("overland", "California", "Kings"),
    ("dos palos", "California", "Merced"),
    ("escalon", "California", "San Joaquin"),
    ("turlock", "California", "Stanislaus"),
    ("modesto livestock", "California", "Stanislaus"),
    ("tulare sales yard", "California", "Tulare"),
    ("tulare county stockyard", "California", "Tulare"),
    ("burley", "Idaho", "Cassia"),
    ("twin falls", "Idaho", "Twin Falls"),
    ("jerome", "Idaho", "Jerome"),
    ("blackfoot", "Idaho", "Bingham"),
    ("caldwell", "Idaho", "Canyon"),
    ("emmett", "Idaho", "Gem"),
    ("cottonwood", "Idaho", "Idaho"),
    ("lewiston", "Idaho", "Nez Perce"),
    ("northwest livestock commission", "Oregon", "Umatilla"),
    ("producers vale", "Oregon", "Malheur"),
    ("vale", "Oregon", "Malheur"),
    ("madras", "Oregon", "Jefferson"),
    ("eugene", "Oregon", "Lane"),
    ("la grande", "Oregon", "Union"),
    ("klamath falls", "Oregon", "Klamath"),
    ("lebanon auction yard", "Oregon", "Linn"),
    ("marana", "Arizona", "Pima"),
    ("willcox", "Arizona", "Cochise"),
    ("fallon", "Nevada", "Churchill"),
    ("roswell", "New Mexico", "Chaves"),
    ("clovis", "New Mexico", "Curry"),
    ("cattlemen's livestock auction", "New Mexico", "Valencia"),
    ("santa teresa", "New Mexico", "Doña Ana")
]

def geographic_fields(market, region):
    raw_state = str(region or "").strip()
    state = STATE_NAMES.get(raw_state, STATE_NAMES.get(raw_state.upper()))
    county = None
    if state:
        label = str(market or "").lower()
        for marker, market_state, market_county in MARKET_COUNTIES:
            if marker in label and market_state == state:
                county = market_county
                break
    return state, county

def record(date, market, region, category, sex, breed, weight, price, head, source, url,
           record_type, price_low=None, price_high=None, sale_type="Auction", notes=""):
    state, county = geographic_fields(market, region)
    return {
        "id": stable_id(market, date, category, sex, weight, price, head, url),
        "date": date, "market": market, "market_id": stable_id("market", market), "region": region, "state": state, "county": county, "geography_method": "verified_market_registry" if county else ("source_report_state_only" if state else "regional_report_unassigned"), "category": category,
        "sex": sex, "breed": breed, "weight": float(weight) if weight not in (None, "") and float(weight) > 0 else None, "price": float(price),
        "price_low": float(price_low if price_low is not None else price),
        "price_high": float(price_high if price_high is not None else price),
        "head": int(head) if head not in (None, "") else None,
        "sale": sale_type, "source": source, "source_url": url,
        "price_unit": "USD/cwt", "record_type": record_type, "notes": notes
    }


def needs_source_backfill(source_name):
    if not OUT.exists():
        return True
    try:
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        versions = payload.get("source_versions", {})
        return int(versions.get(source_name, 0)) < 1
    except Exception:
        return True


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
        r"(DAIRY STEERS?|REPLACEMENT HEIFERS?|BRED COWS?|BRED HEIFERS?|COW-CALF PAIRS?|STEERS?|HEIFERS?|COWS?|BULLS?)\s*-\s*"
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
        if "REPLACEMENT" in section:
            sex, category = "Heifer", "Replacement heifer"
        elif "BRED HEIFER" in section:
            sex, category = "Heifer", "Bred heifer"
        elif "HEIFER" in section:
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


def parse_usda_replacement_pdf(url, report_id, market, region):
    """Parse USDA replacement cattle tables quoted per head or per family."""
    try:
        text = pdf_text(url)
    except Exception as e:
        print(f"USDA {report_id} replacement table fetch failed: {e}", file=sys.stderr)
        return []
    date = parse_date(text[:1800]) or parse_date(text)
    if not date:
        return []
    normalized = re.sub(r"\s+", " ", text)
    heading_re = re.compile(
        r"(BRED COWS?|BRED HEIFERS?|COW-CALF PAIRS?)\s*-\s*"
        r".{0,180}?\(Per (Head|Family)[^)]*\)", re.I
    )
    headings = list(heading_re.finditer(normalized))
    row_re = re.compile(
        r"(\S+)\s+((?:\d{1,2}-\d{1,2})|<2|>8)\s+"
        r"(T1-3|T1-2|T2-3|T1|T2|T3|O|E|B)\s+"
        r"(\d{1,4})\s+([\d,]+(?:\s*-\s*[\d,]+)?)\s+"
        r"([\d,]+)\s+([\d,.]+(?:\s*-\s*[\d,.]+)?)\s+([\d,.]+)",
        re.I
    )
    out = []
    state, county = geographic_fields(market, region)
    for i, heading in enumerate(headings):
        end = headings[i+1].start() if i+1 < len(headings) else len(normalized)
        segment = normalized[heading.end():end]
        label = heading.group(1).upper()
        unit = "USD/family" if heading.group(2).lower() == "family" else "USD/head"
        category = "Bred cow" if "COW" in label and "PAIR" not in label else ("Bred heifer" if "HEIFER" in label else "Cow-calf pair")
        for m in row_re.finditer(segment):
            delivery, age, stage, head, wt_range, avg_wt, price_range, avg_price = m.groups()
            try:
                price = float(avg_price.replace(",", ""))
                count = int(head.replace(",", ""))
                weight = float(avg_wt.replace(",", ""))
            except ValueError:
                continue
            if price <= 0 or count <= 0:
                continue
            range_values = [float(x.replace(",", "")) for x in re.findall(r"[\d,.]+", price_range)]
            out.append({
                "id": stable_id(report_id, date, category, age, stage, delivery, price, url),
                "date": date, "market": market, "region": region, "state": state, "county": county,
                "category": category, "description": f"Age {age}; pregnancy stage {stage}; delivery {delivery}",
                "age": age, "pregnancy_stage": stage, "delivery": delivery,
                "weight": weight, "head": count, "price": price,
                "price_low": min(range_values) if range_values else price,
                "price_high": max(range_values) if range_values else price,
                "price_unit": unit, "source": "USDA AMS Market News", "source_url": url,
                "record_type": "usda_replacement_weighted_average"
            })
    return out


def parse_usda_report(report_id, market, region, record_type="usda_auction", cow_records_out=None):
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
        if cow_records_out is not None:
            cow_records_out.extend(parse_usda_replacement_pdf(url, report_id, market, region))
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


def parse_burley():
    """Parse Burley Livestock Auction's own public weekly sale tables."""
    index_url = "https://burleylivestock.auction/reports"
    urls = ["https://burleylivestock.auction/report"]
    try:
        soup = BeautifulSoup(fetch(index_url).text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(index_url, a["href"]).split("#")[0]
            if urlparse(href).netloc.endswith("burleylivestock.auction") and "/report" in urlparse(href).path:
                if href not in urls:
                    urls.append(href)
    except Exception as e:
        print(f"Burley archive index failed: {e}", file=sys.stderr)
    out = []
    for url in urls[:40 if needs_backfill() else 1]:
        try:
            soup = BeautifulSoup(fetch(url).text, "html.parser")
            body = soup.get_text(" ", strip=True)
            date = parse_date(body[:1000]) or parse_date(url.replace("-", " "))
            if not date:
                print(f"Burley report date not identified: {url}", file=sys.stderr)
                continue
            for table in soup.find_all("table"):
                heading = table.find_previous(["h2", "h3", "h4"])
                section = heading.get_text(" ", strip=True).lower() if heading else ""
                if "heiferette" in section or "calvy" in section or "calf" in section:
                    continue
                if "steer" in section:
                    category, sex = "Feeder steer", "Steer"
                elif "heifer" in section:
                    category, sex = "Feeder heifer", "Heifer"
                elif "cow" in section:
                    category, sex = "Cull cow", "Cow"
                elif "bull" in section:
                    category, sex = "Bull", "Bull"
                else:
                    continue
                for tr in table.find_all("tr"):
                    cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                    if len(cells) < 5 or "town" in cells[0].lower() or "head" in cells[1].lower():
                        continue
                    town, head_text, kind, weight_text, price_text = cells[:5]
                    head_match = re.search(r"\d+", head_text.replace(",", ""))
                    weight_match = re.search(r"\d+(?:\.\d+)?", weight_text.replace(",", ""))
                    price_match = re.search(r"\$?\s*([\d,]+(?:\.\d{1,2})?)", price_text)
                    if not head_match or not weight_match or not price_match:
                        continue
                    try:
                        head = int(head_match.group())
                        weight = float(weight_match.group())
                        price = float(price_match.group(1).replace(",", ""))
                    except ValueError:
                        continue
                    # X-weight / cull-only entries are not feeder weight observations.
                    if not (head > 0 and 100 <= weight <= 3000 and 20 <= price <= 1500):
                        continue
                    row = record(
                        date, "Burley Livestock Auction", "Idaho", category, sex,
                        "Not specified", weight, price, head,
                        "Burley Livestock Auction", url, "auction_lot_average",
                        sale_type="Live auction",
                        notes=f"Published lot; consignor town {town}; source type code {kind}"
                    )
                    row["id"] = stable_id("burley", date, category, town, head, kind, weight, price, url)
                    out.append(row)
        except Exception as e:
            print(f"Burley report failed {url}: {e}", file=sys.stderr)
    print(f"Burley: {len(out)} lot observations from {min(len(urls), 40 if needs_backfill() else 1)} report pages")
    return out


def parse_cottonwood():
    """Parse Cottonwood Livestock Auction's public 2022-2026 PDF archive."""
    index_url = "https://cottonwoodlivestock.com/market-report/"
    try:
        soup = BeautifulSoup(fetch(index_url).text, "html.parser")
    except Exception as e:
        print(f"Cottonwood archive index failed: {e}", file=sys.stderr)
        return [], [], False
    urls = []
    for a in soup.find_all("a", href=True):
        href = urljoin(index_url, a["href"]).split("#")[0]
        if ".pdf" in href.lower() and "cottonwoodlivestock.com" in urlparse(href).netloc:
            if href not in urls:
                urls.append(href)
    full_backfill = needs_source_backfill("cottonwood")
    limit = 100 if full_backfill else 3
    records, cow_records = [], []
    parsed_reports = 0
    for url in urls[:limit]:
        try:
            text = pdf_text(url)
        except Exception as e:
            print(f"Cottonwood report failed {url}: {e}", file=sys.stderr)
            continue
        date = parse_date(text[:800]) or parse_date(url.replace("-", " ").replace("/", " "))
        if not date:
            print(f"Cottonwood report date not identified: {url}", file=sys.stderr)
            continue
        parsed_reports += 1
        normalized = " ".join(text.split())
        def add_weight_classes(heading, next_heading, category, sex):
            start_match = re.search(rf"\b{heading}\s*:", normalized, re.I)
            if not start_match:
                return
            tail = normalized[start_match.end():]
            end_match = re.search(rf"\b{next_heading}\s*:", tail, re.I)
            section = tail[:end_match.start()] if end_match else tail
            classes = list(re.finditer(r"(\d{3,4})\s*-\s*(\d{3,4})\s*lbs?", section, re.I))
            for idx, cls in enumerate(classes):
                chunk = section[cls.end():classes[idx+1].start() if idx+1 < len(classes) else len(section)]
                values = re.findall(r"(?<![\d,])(\d{2,4}(?:\.\d{1,2})?)(?![\d,])", chunk)
                if len(values) < 2:
                    continue
                lo_w, hi_w = float(cls.group(1)), float(cls.group(2))
                lo_p, hi_p = float(values[0]), float(values[1])
                if not (50 <= lo_w <= hi_w <= 3000 and 0 < lo_p <= hi_p <= 100000):
                    continue
                row = record(
                    date, "Cottonwood Livestock Auction", "Idaho", category, sex,
                    "Not specified", (lo_w+hi_w)/2, (lo_p+hi_p)/2, None,
                    "Cottonwood Livestock Auction", url, "published_weight_class_range",
                    lo_p, hi_p, "Live auction",
                    notes=f"Published {heading.lower()} weight class {int(lo_w)}-{int(hi_w)} lb; price range in USD/cwt; report total head is not allocated to this class"
                )
                row["weight_low"], row["weight_high"] = lo_w, hi_w
                records.append(row)
        add_weight_classes("Steers", "Heifers", "Feeder steer", "Steer")
        add_weight_classes("Heifers", "Cows", "Feeder heifer", "Heifer")
        bull_match = re.search(r"Slaughter\s+Bulls\s*:\s*([\d,.]+)\s*to\s*([\d,.]+)", normalized, re.I)
        if bull_match:
            lo, hi = [float(x.replace(",", "")) for x in bull_match.groups()]
            if 0 < lo <= hi <= 100000:
                records.append(record(
                    date, "Cottonwood Livestock Auction", "Idaho", "Bull", "Bull",
                    "Not specified", None, (lo+hi)/2, None,
                    "Cottonwood Livestock Auction", url, "published_price_range",
                    lo, hi, "Live auction", notes="Published slaughter-bull price range in USD/cwt"
                ))
        pair_match = re.search(r"Pairs\s*:\s*([\d,.]+)\s*to\s*([\d,.]+)\s*per\s*pair", normalized, re.I)
        if pair_match:
            lo, hi = [float(x.replace(",", "")) for x in pair_match.groups()]
            if 0 < lo <= hi <= 100000:
                cow_records.append({
                    "id": stable_id("cottonwood", date, "Cow-calf pair", lo, hi, url),
                    "date": date, "market": "Cottonwood Livestock Auction",
                    "market_id": stable_id("market", "Cottonwood Livestock Auction"),
                    "region": "Idaho", "state": "Idaho", "county": "Idaho",
                    "geography_method": "verified_market_registry",
                    "category": "Cow-calf pair", "price": (lo+hi)/2,
                    "price_low": lo, "price_high": hi, "price_unit": "USD/family",
                    "head": None, "source": "Cottonwood Livestock Auction",
                    "source_url": url, "record_type": "published_price_range",
                    "description": "Published cow-calf pair price range"
                })
    print(f"Cottonwood: {len(records)} market and {len(cow_records)} pair observations from {parsed_reports} reports")
    return records, cow_records, bool(parsed_reports and full_backfill)


def parse_ranchers_choice():
    """Parse Rancher's Choice Stockyard's public dated PDF market-report archive."""
    index_url = "https://rancherschoicestockyard.com/market-reports/"
    try:
        soup = BeautifulSoup(fetch(index_url).text, "html.parser")
    except Exception as e:
        print(f"Rancher's Choice report index failed: {e}", file=sys.stderr)
        return [], []
    urls = []
    for a in soup.find_all("a", href=True):
        href = urljoin(index_url, a["href"]).split("#")[0]
        if ".pdf" in href.lower() and "rancherschoicestockyard.com" in urlparse(href).netloc:
            if href not in urls:
                urls.append(href)
    records, cow_records = [], []
    for url in urls[:40]:
        try:
            text = pdf_text(url)
        except Exception as e:
            print(f"Rancher's Choice PDF failed {url}: {e}", file=sys.stderr)
            continue
        date = parse_date(text[:1200]) or parse_date(url.replace("-", " ").replace(".", " "))
        if not date:
            print(f"Rancher's Choice report date not identified: {url}", file=sys.stderr)
            continue
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        feeder_mode = False
        bull_class_mode = False
        highlight_mode = False
        cow_mode = False
        bull_price_mode = False
        breeding_mode = False
        cow_per_lb = bool(re.search(r"Top Cow\s*[-–]\s*\d[\d,]*\s*lbs?\s*[-–]\s*\$?[\d,.]+\s*/\s*lb", text, re.I))
        for line in lines:
            low_line = line.lower()
            if "steers" in low_line and "heifers" in low_line:
                feeder_mode, bull_class_mode, highlight_mode = True, False, False
            if "feeder bulls" in low_line:
                feeder_mode, bull_class_mode, highlight_mode = False, True, False
            if "calf highlights" in low_line:
                feeder_mode = bull_class_mode = False
                highlight_mode = True
            if "slaughter cows" in low_line or "cow market" in low_line:
                cow_mode, bull_price_mode = True, False
            if "bull market" in low_line:
                cow_mode, bull_price_mode = False, True
            if "bred & pair market" in low_line or "bred and pair market" in low_line:
                breeding_mode = True
            # Weight-class tables report price per pound. Keep the original class
            # bounds and convert only the price unit to USD/cwt.
            if feeder_mode or bull_class_mode:
                matches = list(re.finditer(
                    r"(\d{3,4})\s*-\s*(\d{3,4})\s*lbs?\s*\$?([\d,.]+)\s*-\s*\$?([\d,.]+)\s*/\s*lb",
                    line, re.I
                ))
                if feeder_mode and len(matches) >= 2:
                    classes = [(matches[0], "Feeder steer", "Steer"), (matches[1], "Feeder heifer", "Heifer")]
                elif bull_class_mode and matches:
                    classes = [(matches[0], "Bull", "Bull")]
                else:
                    classes = []
                for match, category, sex in classes:
                    lo_w, hi_w, lo_p, hi_p = match.groups()
                    try:
                        weight_low, weight_high = float(lo_w), float(hi_w)
                        price_low, price_high = float(lo_p.replace(",", ""))*100, float(hi_p.replace(",", ""))*100
                    except ValueError:
                        continue
                    if not (50 <= weight_low <= weight_high <= 3000 and 0 < price_low <= price_high <= 100000):
                        continue
                    row = record(
                        date, "Ranchers Choice Stockyard", "California", category, sex,
                        "Not specified", (weight_low+weight_high)/2,
                        (price_low+price_high)/2, None, "Ranchers Choice Stockyard",
                        url, "published_weight_class_range", price_low, price_high,
                        "Live auction", notes=f"Published weight class {lo_w}-{hi_w} lb; source prices per lb converted to USD/cwt; no class head count reported"
                    )
                    row["weight_low"], row["weight_high"] = weight_low, weight_high
                    records.append(row)
            # Highlighted lots are kept separate from published class ranges.
            if highlight_mode:
                lot_re = re.compile(
                    r"(\d+)\s+((?:[A-Za-z]+\s+)*?)(Steers?|Heifers?)\s+(?:Calves\s+)?(\d{3,4})\s*lbs?\s*\$?([\d,.]+)\s*/\s*lb",
                    re.I
                )
                for match in lot_re.finditer(line):
                    head, descriptor, sex_text, weight, price = match.groups()
                    sex = "Steer" if sex_text.lower().startswith("steer") else "Heifer"
                    category = "Feeder steer" if sex == "Steer" else "Feeder heifer"
                    try:
                        weight_n, price_cwt = float(weight), float(price.replace(",", ""))*100
                        head_n = int(head)
                    except ValueError:
                        continue
                    if head_n <= 0 or not (100 <= weight_n <= 3000) or not (0 < price_cwt <= 100000):
                        continue
                    records.append(record(
                        date, "Ranchers Choice Stockyard", "California", category, sex,
                        "Not specified", weight_n, price_cwt, head_n,
                        "Ranchers Choice Stockyard", url, "published_lot_highlight",
                        sale_type="Live auction",
                        notes=f"Published lot highlight; description {descriptor.strip() or 'not specified'}; source price per lb converted to USD/cwt"
                    ))
            # Cow and bull ranges can be printed either as $/cwt tables or $/lb.
            if cow_mode or bull_price_mode:
                label = "Bull" if (bull_price_mode or line.lower().startswith("bull")) else "Cull cow"
                m = re.search(
                    r"(?:High[- ]yielding cows?|Medium[- ]yielding cows?|Low[- ]yielding cows?|High Yielding|Medium Yielding|Low Yielding|Bulls?)\s+\$?([\d,.]+)\s*(?:-\s*\$?([\d,.]+))?",
                    line, re.I
                )
                if m:
                    try:
                        lo = float(m.group(1).replace(",", ""))
                        hi = float((m.group(2) or m.group(1)).replace(",", ""))
                    except ValueError:
                        lo = hi = 0
                    factor = 100 if cow_per_lb else 1
                    lo, hi = lo*factor, hi*factor
                    if 0 < lo <= hi <= 100000:
                        records.append(record(
                            date, "Ranchers Choice Stockyard", "California", label,
                            "Bull" if (bull_price_mode or line.lower().startswith("bull")) else "Cow", "Not specified", None,
                            (lo+hi)/2, None, "Ranchers Choice Stockyard", url,
                            "published_price_range", lo, hi, "Live auction",
                            notes=f"Published {'$/lb converted to USD/cwt' if cow_per_lb else 'USD/cwt'} range; {line}"
                        ))
            if breeding_mode:
                bred = re.search(r"Bred Cows?\s+\$?([\d,]+)(?:\s*-\s*\$?([\d,]+))?", line, re.I)
                pairs = re.search(r"Pairs\s+\$?([\d,]+)(?:\s*-\s*\$?([\d,]+))?", line, re.I)
                for match, category, unit in [(bred, "Bred cow", "USD/head"), (pairs, "Cow-calf pair", "USD/family")]:
                    if not match:
                        continue
                    lo = float(match.group(1).replace(",", ""))
                    hi = float((match.group(2) or match.group(1)).replace(",", ""))
                    if lo <= 0 or hi < lo:
                        continue
                    cow_records.append({
                        "id": stable_id("ranchers-choice", date, category, lo, hi, url),
                        "date": date, "market": "Ranchers Choice Stockyard",
                        "market_id": stable_id("market", "Ranchers Choice Stockyard"),
                        "region": "California", "state": "California", "county": "San Benito",
                        "geography_method": "verified_market_registry",
                        "category": category, "price": (lo+hi)/2, "price_low": lo,
                        "price_high": hi, "price_unit": unit, "head": None,
                        "source": "Ranchers Choice Stockyard", "source_url": url,
                        "record_type": "published_price_range",
                        "description": "Published breeding-cattle price range"
                    })
    print(f"Rancher's Choice: {len(records)} feeder/cull observations and {len(cow_records)} breeding observations from {len(urls)} public PDFs")
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


def parse_cattle_channel_west():
    """Add public Idaho/Oregon sale summaries from Cattle Channel."""
    fallback_urls = [
        "https://cattlechannel.com/market-report/idaho/burley-livestock-auction/2026-10-08",
        "https://cattlechannel.com/market-report/idaho/twin-falls-livestock-commission/2026-08-26",
        "https://cattlechannel.com/market-report/oregon/northwest-livestock-commission/2026-10-06",
        "https://cattlechannel.com/market-report/oregon/producers-vale/2026-09-09",
    ]
    urls = set(fallback_urls)
    for sitemap_url in [
        "https://cattlechannel.com/wp-sitemap.xml",
        "https://cattlechannel.com/sitemap_index.xml",
        "https://cattlechannel.com/wp-sitemap-posts-market-report-1.xml",
        "https://cattlechannel.com/sitemap.xml",
        "https://cattlechannel.com/feed/",
    ]:
        try:
            sitemap = BeautifulSoup(fetch(sitemap_url).text, "xml")
            nodes = sitemap.find_all(["loc", "link"])
            for node in nodes:
                href = node.get_text(strip=True) or node.get("href", "")
                if href and re.search(r"/market-report/(?:idaho|oregon)/", href, re.I):
                    urls.add(href)
        except Exception:
            continue
    for archive_url in [
        "https://cattlechannel.com/market-report/idaho/",
        "https://cattlechannel.com/market-report/oregon/",
        "https://cattlechannel.com/market-report/",
    ]:
        try:
            archive = BeautifulSoup(fetch(archive_url).text, "html.parser")
            for link in archive.find_all("a", href=True):
                href = urljoin(archive_url, link["href"])
                if re.search(r"/market-report/(?:idaho|oregon)/[^/]+/20\d{2}-\d{2}-\d{2}/?$", href, re.I):
                    urls.add(href)
        except Exception as e:
            print(f"Cattle Channel archive unavailable {archive_url}: {e}", file=sys.stderr)
    records, seen = [], set()
    def report_date_from_url(url):
        match = re.search(r"/(20\d{2}-\d{2}-\d{2})/?$", url)
        return match.group(1) if match else ""
    # Keep the newest reports for each state; do not crawl the entire archive daily.
    urls_by_state = {"idaho": [], "oregon": []}
    for candidate in urls:
        parts = urlparse(candidate).path.strip("/").split("/")
        state_key = parts[1].lower() if len(parts) > 1 else ""
        if state_key in urls_by_state:
            urls_by_state[state_key].append(candidate)
    urls = []
    for state_key in ("idaho", "oregon"):
        urls.extend(sorted(urls_by_state[state_key], key=report_date_from_url, reverse=True)[:20])
    print("Cattle Channel report pages selected: " + "; ".join(f"{k}={len(v)}" for k, v in urls_by_state.items()))
    for url in urls:
        try:
            soup = BeautifulSoup(fetch(url).text, "html.parser")
            body = soup.get_text("\n", strip=True)
            title = soup.find(["h1", "h2"])
            title_text = title.get_text(" ", strip=True) if title else url
            date_match = re.search(
                r"Cattle Market Report\s*[—-]\s*([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})",
                title_text, flags=re.I
            )
            if date_match:
                date = parse_date(date_match.group(1))
            else:
                url_date = re.search(r"/(20\d{2}-\d{2}-\d{2})/?$", url)
                date = url_date.group(1) if url_date else None
            if not date:
                date = parse_date(body[:1000])
            path_parts = urlparse(url).path.strip("/").split("/")
            state_slug = path_parts[1] if len(path_parts) > 1 else ""
            state = {"idaho": "Idaho", "oregon": "Oregon"}.get(state_slug.lower())
            if not date or not state or (url, date) in seen:
                continue
            seen.add((url, date))
            market = title_text.split(" Cattle Market Report")[0].strip()
            if not market:
                market = "Cattle Channel sale report"
            sections = list(re.finditer(r"(Steers|Heifers|Bulls|Cows) By Weight|Questions about this sale", body, flags=re.I))
            for idx, section_match in enumerate(sections):
                section = section_match.group(1).lower() if section_match.group(1) else ""
                if section not in ("steers", "heifers"):
                    continue
                segment_end = sections[idx+1].start() if idx+1 < len(sections) else len(body)
                segment = body[section_match.end():segment_end]
                row_re = re.compile(
                    r"(\d{3,4})\s*-\s*(\d{3,4})\s+(\d{1,4})\s+([\d,.]+)\s+\$([\d,.]+)",
                    re.I
                )
                for row in row_re.finditer(segment):
                    low_wt, high_wt, head, avg_wt, avg_price = row.groups()
                    try:
                        head_n = int(head)
                        avg_weight = float(avg_wt.replace(",", ""))
                        price = float(avg_price.replace(",", ""))
                    except ValueError:
                        continue
                    if head_n <= 0 or avg_weight <= 0 or price <= 0:
                        continue
                    sex = "Steer" if section == "steers" else "Heifer"
                    category = "Feeder steer" if sex == "Steer" else "Feeder heifer"
                    records.append(record(
                        date, market, state, category, sex, "Not specified",
                        avg_weight, price, head_n, "Cattle Channel (sale report)",
                        url, "third_party_auction_weighted_average",
                        sale_type="Live auction",
                        notes=f"Cattle Channel reported weighted average; {low_wt}-{high_wt} lb class"
                    ))
        except Exception as e:
            print(f"Cattle Channel state report failed {url}: {e}", file=sys.stderr)
    print(f"Cattle Channel: {len(records)} Idaho/Oregon feeder weight-class observations")
    return records


def parse_belen():
    """Capture Corriente, fresh Corriente, bred-cow and pair ranges from Belen NM."""
    url = "https://belenlivestockauction.com/"
    records, cow_records = [], []
    try:
        soup = BeautifulSoup(fetch(url).text, "html.parser")
        body = soup.get_text("\n", strip=True)
        headers = list(re.finditer(
            r"SALE OF\s+((?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2},\s*20\d{2})",
            body, flags=re.I
        ))
        state, county = geographic_fields("Cattlemen's Livestock Auction - Belen", "New Mexico")
        for i, header in enumerate(headers):
            segment_end = headers[i+1].start() if i+1 < len(headers) else len(body)
            segment = body[header.start():segment_end]
            date = parse_date(header.group(1))
            if not date:
                continue

            def add_head_range(label, category):
                m = re.search(
                    rf"{label}\s*\$?\s*([\d,]+(?:\.\d{{1,2}})?)\s*[-–]\s*\$?\s*([\d,]+(?:\.\d{{1,2}})?)\s*hd\b",
                    segment, flags=re.I
                )
                if not m:
                    return
                lo, hi = (float(x.replace(",", "")) for x in m.groups())
                if lo <= 0 or hi < lo:
                    return
                cow_records.append({
                    "id": stable_id("belen", date, category, lo, hi, url),
                    "date": date, "market": "Cattlemen's Livestock Auction - Belen",
                    "region": "New Mexico", "state": state, "county": county,
                    "category": category, "description": "Published auction price range; origin/type not specified",
                    "price": (lo+hi)/2, "price_low": lo, "price_high": hi,
                    "price_unit": "USD/head", "source": "Cattlemen's Livestock Auction - Belen",
                    "source_url": url, "record_type": "published_price_range",
                    "head": None, "breed": "Corriente — type not specified" if "Corriente" in category else "Not specified"
                })

            m = re.search(r"\bCorrientes\s+\$?([\d,]+(?:\.\d{1,2})?)\s*[-–]\s*\$?([\d,]+(?:\.\d{1,2})?)\s*cwt\b", segment, flags=re.I)
            if m:
                lo, hi = (float(x.replace(",", "")) for x in m.groups())
                if lo > 0 and hi >= lo:
                    records.append(record(
                        date, "Cattlemen's Livestock Auction - Belen", "New Mexico",
                        "Corriente cattle (unclassified)", "Mixed", "Corriente — type not specified",
                        0, (lo+hi)/2, None, "Cattlemen's Livestock Auction - Belen", url,
                        "published_price_range", lo, hi, "Live auction",
                        notes="Published Corrientes range; source does not distinguish native vs Mexican M-branded origin"
                    ))
            add_head_range(r"Fresh Corrientes", "Fresh Corriente")
            add_head_range(r"Old Bred Cows?", "Bred cow")
            add_head_range(r"Young Cow/Calf Prs", "Cow-calf pair")
            add_head_range(r"Older Cow/Calf Pairs?", "Cow-calf pair")
    except Exception as e:
        print(f"Belen market report failed: {e}", file=sys.stderr)
    print(f"Belen: {len(records)} unclassified Corriente $/cwt observations and {len(cow_records)} per-head replacement/Corriente records")
    return records, cow_records


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
                desc_match = re.search(r"\b(MEXICAN\s+BRANDED\s+CORRIENTE\s*(?:STR|HFR|STEER|HEIFER)?|M[- ]?BRANDED\s+CORRIENTE\s*(?:STR|HFR|STEER|HEIFER)?|NATIVE\s+CORRIENTE\s*(?:STR|HFR|STEER|HEIFER)?|CORRIENTE\s*(?:STR|HFR|STEER|HEIFER)?|HOL\s*(?:STR|HFR)|HOLSTEIN\s*(?:STEER|HEIFER)?|NATIVE\s*(?:STR|HFR|STEER|HEIFER)|BEEF\s*(?:STR|HFR|STEER|HEIFER)|ANGUS\s*(?:STR|HFR|STEER|HEIFER)|HOL\s*BEEF\s*(?:STR|HFR))\b", chunk, flags=re.I)
                if not price_match or not weight_match:
                    continue
                price = float(price_match.group(1).replace(",", ""))
                head, weight = int(weight_match.group(1)), float(weight_match.group(2).replace(",", ""))
                desc = re.sub(r"\s+", " ", desc_match.group(1)).upper() if desc_match else "UNCLASSIFIED"
                if "HFR" in desc or "HEIFER" in desc:
                    sex, category = "Heifer", "Corriente heifer" if "CORRIENTE" in desc else "Feeder heifer"
                elif "STR" in desc or "STEER" in desc:
                    sex, category = "Steer", "Corriente steer" if "CORRIENTE" in desc else "Feeder steer"
                elif "CORRIENTE" in desc:
                    # Origin may be identifiable even when sex/class is not.
                    # Preserve it as unclassified Corriente, never infer a sex.
                    sex, category = "Mixed", "Corriente cattle (unclassified)"
                else:
                    sex, category = "Unknown", "Other"
                breed = "Holstein / dairy" if "HOL" in desc or "HOLSTEIN" in desc else ("Angus" if "ANGUS" in desc else ("Mexican M-branded Corriente" if ("MEXICAN" in desc or "M-BRANDED" in desc or "M BRANDED" in desc) and "CORRIENTE" in desc else ("Native Corriente" if "NATIVE CORRIENTE" in desc else ("Corriente — type not specified" if "CORRIENTE" in desc else ("Beef cross / native" if "NATIVE" in desc or "BEEF" in desc else "Mixed / unknown")))))
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
        # Prefer the publication date shown on the ERS HTML report page. The PDF
        # front matter also contains its next-release date, which can be mistaken
        # for the report date by a first-match text parser.
        page_text = report_page.get_text(" ", strip=True)
        publication_date = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", page_text)
        if publication_date:
            result["report_date"] = datetime.strptime(
                "/".join(publication_date.groups()), "%m/%d/%Y"
            ).date().isoformat()
        else:
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
    {"name":"Visalia Livestock Market","state":"CA","kind":"auction snapshot","url":"https://www.visalialivestock.com/market-snapshot.html","status":"automated: published price ranges; current public archive is limited to recent snapshots"},
    {"name":"Dos Palos Y Auction","state":"CA","kind":"live auction","url":"https://www.dpyauction.com/market-reports","status":"official weekly archive verified; parser pending QA; reports currently include dated downloadable PDFs"},
    {"name":"Ranchers Choice Stockyard","state":"CA","kind":"live auction","url":"https://rancherschoicestockyard.com/market-reports/","status":"automated: dated 2026 PDF reports; weight-class ranges, lot highlights, cow/bull ranges and breeding prices kept distinct"},
    {"name":"California Wool Growers Market Reports","state":"CA","kind":"auction market directory","url":"https://californiawoolgrowers.org/resources/market-reports/","status":"industry directory used to verify California auction yards and source report links"},
    {"name":"Burley Livestock Auction","state":"ID","kind":"live auction","url":"https://burleylivestock.auction/reports","status":"automated: public weekly lot tables; archived reports"},
    {"name":"Idaho Farm Bureau Beef Market Reports","state":"ID","kind":"market report directory","url":"https://www.idahofb.org/commodities/beef-market-reports/","status":"industry directory for Idaho auction report sources"},
    {"name":"Cottonwood Livestock Auction","state":"ID","kind":"live auction","url":"https://cottonwoodlivestock.com/market-report/","status":"automated: dated PDF archive 2022-2026; feeder weight-class ranges and pair prices"},
    {"name":"Idaho Public Livestock Markets","state":"ID","kind":"government market directory","url":"https://agri.idaho.gov/wp-content/uploads/animal-industries/livestock-market-list.pdf","status":"official state market registry; used to verify market names and physical locations"},
    {"name":"Overland Stock Yard","state":"CA","kind":"auction / video lots","url":"https://www.overlandstockyard.com/market-reports","status":"automated: video lots; dairy and beef types remain separate"},
    {"name":"Tulare County Stockyard","state":"CA","kind":"live auction","url":"https://tularecountystockyard.com/","status":"listed; historical parser pending"},
    {"name":"Escalon Livestock Market","state":"CA","kind":"live auction","url":"https://www.escalonlivestockmarket.com/","status":"listed; weekly reports available; parser pending"},
    {"name":"Producers Livestock Marketing Association","state":"CA","kind":"live auction","url":"https://www.producerslivestock.com/","status":"listed; historical parser pending"},
    {"name":"Turlock Livestock Auction Yard","state":"CA","kind":"live auction","url":"https://www.turlocklivestock.com/","status":"listed; historical parser pending"},
    {"name":"Tulare Sales Yard","state":"CA","kind":"live auction","url":"https://tularesalesyard.com/","status":"listed; historical parser pending"},
    {"name":"101 Livestock Market (historical)","state":"CA","kind":"live auction","url":"https://101livestock.com/market-report/","status":"historical archive through December 2025; same Aromas facility now operates as Ranchers Choice Stockyard"},
    {"name":"Modesto Livestock Commission","state":"CA","kind":"live auction","url":"https://www.modestolivestock.com/","status":"listed; historical parser pending"},
    {"name":"Dos Palos Y Auction","state":"CA","kind":"live auction","url":"https://www.dpyauction.com/market-reports","status":"listed; weekly report PDFs identified; parser pending"},
    {"name":"Producers Livestock Marketing Association","state":"CA","kind":"live auction","url":"https://www.producerslivestock.com/","status":"listed; historical parser pending"},
    {"name":"Fresno Livestock Commission","state":"CA","kind":"live auction","url":"https://www.fresnolivestock.com/","status":"listed; historical parser pending"},
    {"name":"A&M Livestock Auction","state":"CA","kind":"live / video auction","url":"https://www.amlivestockauction.com/","status":"listed; historical parser pending"},
    {"name":"Western Stockman's Market","state":"CA","kind":"live auction","url":"https://www.westernstockmans.com/","status":"listed; historical parser pending"},
    {"name":"Marana Stockyards","state":"AZ","kind":"live auction","url":"https://www.maranastockyards.com/market-report-archives","status":"automated: archive backfill from 2022 plus current reports"},
    {"name":"Cattlemen's Livestock Auction - Belen","state":"NM","kind":"live auction","url":"https://belenlivestockauction.com/","status":"automated: current weekly Corriente, bred-cow and pair price ranges; Corriente origin not distinguished"},
    {"name":"Montana Weekly Livestock Auction Summary","state":"MT","kind":"state auction summary","url":"https://mymarketnews.ams.usda.gov/viewReport/1778","status":"automated: current USDA state report"},
    {"name":"Utah Weekly Cattle Auction Summary","state":"UT","kind":"state auction summary","url":"https://mymarketnews.ams.usda.gov/viewReport/2039","status":"automated: current USDA state report"},
    {"name":"Wyoming Weekly Cattle Auction Summary","state":"WY","kind":"state auction summary","url":"https://mymarketnews.ams.usda.gov/viewReport/2106","status":"automated: current USDA state report"},
    {"name":"Toppenish Livestock Commission","state":"WA","kind":"local auction","url":"https://mymarketnews.ams.usda.gov/viewReport/2132","status":"automated: current USDA auction report"},
    {"name":"Northwest Direct Cattle Report","state":"WA/OR/ID/UT","kind":"multi-state direct trade","url":"https://mymarketnews.ams.usda.gov/viewReport/3059","status":"automated: regional only; not assigned to individual states"},
    {"name":"Western Video Market","state":"Multi-state","kind":"video auction","url":"https://mymarketnews.ams.usda.gov/viewReport/3242","status":"automated: western regional report; not assigned to individual states"},
    {"name":"Cattle Country Video Auction","state":"Multi-state","kind":"video auction","url":"https://mymarketnews.ams.usda.gov/viewReport/3241","status":"automated: multi-state lots; not assigned to Wyoming"},
    {"name":"Cattle Channel — Idaho sale reports","state":"ID","kind":"third-party auction summaries","url":"https://cattlechannel.com/market-report/idaho/burley-livestock-auction/2026-10-08","status":"automated third-party summaries; official Burley reports are collected separately"},
    {"name":"Cattle Channel — Oregon sale reports","state":"OR","kind":"third-party auction summaries","url":"https://cattlechannel.com/market-report/oregon/northwest-livestock-commission/2026-10-06","status":"automated fetch of published report pages; new report URLs need periodic discovery"},
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


def quality_check(rows, collection):
    """Reject structurally unsafe observations; retain source-linked rejection details."""
    accepted, rejected = [], []
    seen = set()
    allowed_units = {"USD/cwt", "USD/head", "USD/family"}
    valid_states = set(STATE_NAMES.values())
    for original in rows:
        row = normalize_observation(original)
        issues, warnings = [], []
        if row.get("weight") in (0, "0", ""):
            row["weight"] = None
            warnings.append("weight_not_reported")
        inferred_state, inferred_county = geographic_fields(row.get("market"), row.get("region") or row.get("state"))
        if not row.get("state") and inferred_state:
            row["state"] = inferred_state
        if not row.get("county") and inferred_county:
            row["county"] = inferred_county
        if row.get("market") and not row.get("market_id"):
            row["market_id"] = stable_id("market", row["market"])
        if row.get("market") and not row.get("geography_method"):
            row["geography_method"] = "verified_market_registry" if row.get("county") else ("source_report_state_only" if row.get("state") else "regional_report_unassigned")
        if not row.get("id"):
            issues.append("missing_stable_record_id")
        if not row.get("date"):
            issues.append("missing_sale_date")
        else:
            try:
                parsed_date = datetime.strptime(str(row["date"]), "%Y-%m-%d").date()
                if parsed_date > datetime.now(timezone.utc).date():
                    issues.append("sale_date_in_future")
            except ValueError:
                issues.append("sale_date_not_iso_yyyy_mm_dd")
        for field in ("market", "category", "source"):
            if not str(row.get(field) or "").strip():
                issues.append("missing_" + field)
        if collection == "records" and (row.get("category") == "Other" or row.get("sex") == "Unknown"):
            issues.append("animal_category_not_classifiable")
        source_url = str(row.get("source_url") or "")
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            issues.append("missing_or_invalid_https_source_url")
        if row.get("state") and row["state"] not in valid_states:
            issues.append("unrecognized_state")
        if inferred_state and row.get("state") and inferred_state != row["state"]:
            issues.append("market_state_conflicts_with_location_registry")
        if inferred_county and row.get("county") and inferred_county != row["county"]:
            issues.append("market_county_conflicts_with_location_registry")
        if row.get("county") and not row.get("state"):
            issues.append("county_without_state")
        if row.get("state") and not row.get("county"):
            warnings.append("county_not_identified")
        unit = row.get("price_unit")
        if unit not in allowed_units:
            issues.append("unknown_price_unit")
        try:
            price = float(row.get("price"))
            if not (0 < price <= 100000):
                issues.append("price_outside_sanity_bounds")
        except (TypeError, ValueError):
            price = None
            issues.append("price_not_numeric")
        try:
            low = float(row.get("price_low", price))
            high = float(row.get("price_high", price))
            if low <= 0 or high < low or (price is not None and not (low - 0.02 <= price <= high + 0.02)):
                issues.append("invalid_price_range_or_average")
        except (TypeError, ValueError):
            issues.append("price_range_not_numeric")
        weight = row.get("weight")
        if weight is not None:
            try:
                weight = float(weight)
                if not (50 <= weight <= 3000):
                    issues.append("weight_outside_50_3000_lb")
            except (TypeError, ValueError):
                issues.append("weight_not_numeric")
        if weight is None and unit == "USD/cwt" and row.get("record_type") not in ("published_price_range", "published_weight_class_range"):
            issues.append("missing_weight_for_cwt_record")
        if row.get("weight_low") is not None or row.get("weight_high") is not None:
            try:
                weight_low = float(row.get("weight_low"))
                weight_high = float(row.get("weight_high"))
                if not (50 <= weight_low <= weight_high <= 3000) or (weight is not None and not (weight_low <= weight <= weight_high)):
                    issues.append("invalid_weight_class_bounds")
            except (TypeError, ValueError):
                issues.append("weight_class_bounds_not_numeric")
        head = row.get("head")
        if head not in (None, ""):
            try:
                if int(head) <= 0:
                    issues.append("head_count_not_positive")
            except (TypeError, ValueError):
                issues.append("head_count_not_integer")
        if collection == "records" and not row.get("record_type"):
            issues.append("missing_record_type")
        if price is not None and unit == "USD/cwt" and (price < 50 or price > 900):
            warnings.append("unusual_cwt_price_review_source")
        if row.get("id") in seen:
            issues.append("duplicate_record_id")
        seen.add(row.get("id"))
        if issues:
            rejected.append({
                "id": row.get("id"), "date": row.get("date"), "market": row.get("market"),
                "state": row.get("state"), "county": row.get("county"), "category": row.get("category"),
                "weight": row.get("weight"), "price": row.get("price"), "price_unit": row.get("price_unit"),
                "source_url": row.get("source_url"), "collection": collection, "issues": sorted(set(issues)), "warnings": sorted(set(warnings))
            })
        else:
            row["qa_status"] = "passed_with_warnings" if warnings else "passed"
            row["qa_warnings"] = sorted(set(warnings))
            accepted.append(row)
    return accepted, rejected


def build_qa_report(records, cow_records, rejected_records, rejected_cows, source_fetch_health=None):
    by_state, by_county, by_market, by_source, by_domain, by_geo_method = {}, {}, {}, {}, {}, {}
    by_category, by_record_type, by_corriente_origin, by_sale_regionality = {}, {}, {}, {}
    missing_state = missing_county = warning_records = 0
    for row in records:
        state = row.get("state") or "Regional / unassigned"
        by_state[state] = by_state.get(state, 0) + 1
        if not row.get("state"):
            missing_state += 1
        county_key = f"{row.get('state') or 'Unassigned'} / {row.get('county') or 'County not identified'}"
        by_county[county_key] = by_county.get(county_key, 0) + 1
        if not row.get("county"):
            missing_county += 1
        market = row.get("market") or "Unknown"
        by_market[market] = by_market.get(market, 0) + 1
        source = row.get("source") or "Unknown"
        by_source[source] = by_source.get(source, 0) + 1
        domain = urlparse(row.get("source_url") or "").netloc or "Missing"
        by_domain[domain] = by_domain.get(domain, 0) + 1
        method = row.get("geography_method") or "not_recorded"
        by_geo_method[method] = by_geo_method.get(method, 0) + 1
        category = row.get("category") or "Unknown"
        by_category[category] = by_category.get(category, 0) + 1
        record_type = row.get("record_type") or "unclassified"
        by_record_type[record_type] = by_record_type.get(record_type, 0) + 1
        origin = row.get("corriente_origin") or "not_applicable"
        if origin != "not_applicable":
            by_corriente_origin[origin] = by_corriente_origin.get(origin, 0) + 1
        regionality = row.get("sale_regionality") or "not verified"
        by_sale_regionality[regionality] = by_sale_regionality.get(regionality, 0) + 1
        if row.get("qa_warnings"):
            warning_records += 1
    cow_by_category, cow_by_record_type, cow_by_market = {}, {}, {}
    for row in cow_records:
        category = row.get("category") or "Unknown"
        cow_by_category[category] = cow_by_category.get(category, 0) + 1
        record_type = row.get("record_type") or "unclassified"
        cow_by_record_type[record_type] = cow_by_record_type.get(record_type, 0) + 1
        market = row.get("market") or "Unknown"
        cow_by_market[market] = cow_by_market.get(market, 0) + 1
    return {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": "Only source-linked observations passing structural, geographic, unit, date, and price checks are published. Rejected rows are listed with source URLs and reason codes; regional reports are not assigned to a yard or county.",
        "market_observations": {"accepted": len(records), "rejected": len(rejected_records)},
        "cow_market_observations": {"accepted": len(cow_records), "rejected": len(rejected_cows)},
        "geographic_quality": {
            "market_records_missing_state": missing_state,
            "market_records_missing_county": missing_county,
            "market_records_with_warnings": warning_records,
            "accepted_by_geography_method": dict(sorted(by_geo_method.items())),
            "accepted_by_county": dict(sorted(by_county.items()))
        },
        "accepted_by_state": dict(sorted(by_state.items())),
        "accepted_by_market": dict(sorted(by_market.items())),
        "accepted_by_category": dict(sorted(by_category.items())),
        "accepted_by_record_type": dict(sorted(by_record_type.items())),
        "accepted_by_cow_category": dict(sorted(cow_by_category.items())),
        "accepted_by_cow_record_type": dict(sorted(cow_by_record_type.items())),
        "accepted_by_cow_market": dict(sorted(cow_by_market.items())),
        "accepted_corriente_origin": dict(sorted(by_corriente_origin.items())),
        "accepted_sale_regionality": dict(sorted(by_sale_regionality.items())),
        "accepted_by_source": dict(sorted(by_source.items())),
        "accepted_by_source_domain": dict(sorted(by_domain.items())),
        "source_fetch_health": dict(sorted((source_fetch_health or {}).items())),
        "rejected_observations": rejected_records + rejected_cows,
        "source_registry": SOURCES
    }



def main():
    records = []
    records += parse_overland()
    records += parse_visalia()
    records += parse_burley()
    cottonwood_records, cottonwood_cows, cottonwood_backfill_complete = parse_cottonwood()
    records += cottonwood_records
    ranchers_records, ranchers_cows = parse_ranchers_choice()
    records += ranchers_records
    marana_records, marana_cows = parse_marana()
    willcox_records, willcox_cows = parse_willcox()
    records += marana_records + willcox_records
    usda_cow_records = []
    records += parse_usda_report(1782, "Roswell Livestock Auction (USDA)", "New Mexico", "usda_auction", usda_cow_records)
    records += parse_usda_report(1781, "Clovis Livestock Auction (USDA)", "New Mexico", "usda_auction", usda_cow_records)
    records += parse_usda_report(1783, "Cattlemen's Livestock Auction (USDA)", "New Mexico", "usda_auction", usda_cow_records)
    records += parse_usda_report(1290, "Santa Teresa Livestock Auction (USDA)", "New Mexico", "usda_auction", usda_cow_records)
    records += parse_usda_report(1784, "New Mexico Weekly Cattle Summary (USDA)", "New Mexico", "usda_state_summary", usda_cow_records)
    records += parse_usda_report(1778, "Montana Weekly Livestock Auction Summary (USDA)", "Montana", "usda_state_summary", usda_cow_records)
    records += parse_usda_report(2039, "Utah Weekly Cattle Auction Summary (USDA)", "Utah", "usda_state_summary", usda_cow_records)
    records += parse_usda_report(2106, "Wyoming Weekly Cattle Auction Summary (USDA)", "Wyoming", "usda_state_summary", usda_cow_records)
    records += parse_usda_report(2132, "Toppenish Livestock Commission (USDA)", "Washington", "usda_auction", usda_cow_records)
    records += parse_usda_report(3059, "Northwest Direct Cattle Report (USDA)", "Northwest Region (WA/OR/ID/UT)", "usda_direct_trade", usda_cow_records)
    records += parse_usda_report(3242, "Western Video Market (USDA)", "Multi-state Western Region (AZ/CA/ID/NV/OR/UT/WA)", "usda_video_regional", usda_cow_records)
    records += parse_usda_report(3241, "Cattle Country Video Auction (USDA)", "Multi-state video auction", "usda_video_regional", usda_cow_records)
    records += parse_usda_report(2940, "Southwest Direct Cattle Report (USDA)", "Southwest Region (AZ/CA/NV)", "usda_direct_trade", usda_cow_records)
    parse_roswell_web()
    belen_records, belen_cows = parse_belen()
    records += belen_records
    records += parse_cattle_channel_west()
    fallon_records, fallon_cows = parse_fallon()
    records += fallon_records
    old = []
    old_cows = []
    old_quarantine = []
    if OUT.exists():
        try:
            old_payload = json.loads(OUT.read_text(encoding="utf-8"))
            old = old_payload.get("records", [])
            old_cows = old_payload.get("cow_records", [])
        except Exception:
            pass
    if QA_OUT.exists():
        try:
            previous_qa = json.loads(QA_OUT.read_text(encoding="utf-8"))
            old_quarantine = previous_qa.get("rejected_observations", [])
        except Exception:
            old_quarantine = []
    by_id = {}
    # Backfill geography on previously published observations so the feature
    # works across the full historical archive, not only new daily records.
    for r in old:
        state, county = geographic_fields(r.get("market"), r.get("region"))
        r["state"] = state
        r["county"] = county
        by_id[r["id"]] = r
    for r in records:
        by_id[r["id"]] = r
    merged = sorted(by_id.values(), key=lambda r: (r.get("date", ""), r.get("market", ""), r.get("id", "")))
    cow_by_id = {r["id"]: r for r in old_cows}
    for r in marana_cows + willcox_cows + fallon_cows + usda_cow_records + belen_cows + ranchers_cows + cottonwood_cows:
        cow_by_id[r["id"]] = r
    merged_cows = sorted(cow_by_id.values(), key=lambda r: (r.get("date", ""), r.get("market", "")))
    new_ids = {r.get("id") for r in records}
    merged, rejected_records = quality_check(merged, "records")
    merged_cows, rejected_cows = quality_check(merged_cows, "cow_records")
    # Quarantine is durable history. Keep prior rejected rows unless the same
    # stable ID is now accepted or a fresh validation produced an updated rejection.
    accepted_market_ids = {r.get("id") for r in merged}
    accepted_cow_ids = {r.get("id") for r in merged_cows}
    rejected_records, rejected_cows = merge_quarantine_history(
        rejected_records, rejected_cows, old_quarantine,
        accepted_market_ids, accepted_cow_ids
    )
    rejected_new = sum(
        1 for r in rejected_records
        if r.get("id") in new_ids and "animal_category_not_classifiable" not in r.get("issues", [])
    )
    newly_unclassified = sum(
        1 for r in rejected_records
        if r.get("id") in new_ids and "animal_category_not_classifiable" in r.get("issues", [])
    )
    rejection_reasons = {}
    for rejected in rejected_records:
        if rejected.get("id") in new_ids and "animal_category_not_classifiable" not in rejected.get("issues", []):
            for reason in rejected.get("issues", []):
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    if newly_unclassified:
        print(f"QA: quarantined {newly_unclassified} newly collected lots with unclassifiable animal categories; these remain out of published benchmarks.")
    qa_report = build_qa_report(merged, merged_cows, rejected_records, rejected_cows, SOURCE_FETCH_HEALTH)
    QA_OUT.write_text(json.dumps(qa_report, indent=2) + "\n", encoding="utf-8")
    # Record-level failures are quarantined; the batch threshold below is a circuit breaker.
    rejection_rate = rejected_new / len(new_ids) if new_ids else 0
    if rejection_rate >= 0.20:
        raise RuntimeError(
            f"QA safety gate blocked publishing: {rejected_new}/{len(new_ids)} "
            f"new market observations rejected ({rejection_rate:.1%}); "
            f"reason counts: {rejection_reasons}. See data/market_data_qa.json."
        )
    if rejection_rate >= 0.05:
        print(
            f"QA WARNING: quarantined {rejected_new}/{len(new_ids)} newly collected "
            f"market observations ({rejection_rate:.1%}); rejected rows are excluded. "
            f"Reason counts: {rejection_reasons}"
        )
    print(f"QA: accepted {len(merged)} market and {len(merged_cows)} cow records; quarantined {len(rejected_records)} market and {len(rejected_cows)} cow records")
    category_counts = {}
    for r in merged:
        category = r.get("category", "Unknown")
        category_counts[category] = category_counts.get(category, 0) + 1
    cow_category_counts = {}
    for r in merged_cows:
        category = r.get("category", "Unknown")
        cow_category_counts[category] = cow_category_counts.get(category, 0) + 1
    state_counts = {}
    for r in merged:
        state = r.get("state") or "Regional / unassigned"
        state_counts[state] = state_counts.get(state, 0) + 1
    corriente_counts = {}
    for r in merged:
        breed = str(r.get("breed", ""))
        if "corriente" in breed.lower():
            corriente_counts[breed] = corriente_counts.get(breed, 0) + 1
    print("Market records by category: " + "; ".join(f"{k}={v}" for k, v in sorted(category_counts.items())))
    print("Cow-market records by category: " + "; ".join(f"{k}={v}" for k, v in sorted(cow_category_counts.items())))
    print("Market records by state: " + "; ".join(f"{k}={v}" for k, v in sorted(state_counts.items())))
    print("Corriente records by classification: " + ("; ".join(f"{k}={v}" for k, v in sorted(corriente_counts.items())) or "none identified in published descriptions"))
    DATA.mkdir(parents=True, exist_ok=True)
    source_versions = dict(old_payload.get("source_versions", {})) if "old_payload" in locals() and isinstance(old_payload, dict) else {}
    if cottonwood_backfill_complete:
        source_versions["cottonwood"] = 1
    OUT.write_text(json.dumps({
        "schema_version": 7, "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(merged), "cow_record_count": len(merged_cows), "sources": SOURCES, "source_versions": source_versions, "records": merged, "cow_records": merged_cows, "benchmarks": build_benchmarks(merged, merged_cows), "qa_report_url": "data/market_data_qa.json", "qa_summary": {"market_records_accepted": len(merged), "market_records_rejected": len(rejected_records), "cow_records_accepted": len(merged_cows), "cow_records_rejected": len(rejected_cows)},
        "methodology": "Auction prices, USDA state summaries, direct trades, video contracts and published price bands are retained as separate record types. They are not pooled into one price series."
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(merged)} unique market observations to {OUT}")
    fetch_futures()
    fetch_usda_outlook()


if __name__ == "__main__":
    main()
