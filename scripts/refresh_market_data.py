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
    pat = r"(?:SALE DATE:\s*)?(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?[,]?\s*(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})"
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


def pdf_text(url):
    r = fetch(url)
    reader = PdfReader(BytesIO(r.content))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def parse_usda_report(report_id, market, region, record_type="usda_auction"):
    url = f"https://www.ams.usda.gov/mnreports/ams_{report_id}.pdf"
    try:
        text = pdf_text(url)
    except Exception as e:
        print(f"USDA {report_id} fetch failed: {e}", file=sys.stderr)
        return []
    date = parse_date(text[:1800]) or parse_date(text)
    if not date:
        print(f"USDA {report_id}: report date not identified", file=sys.stderr)
        return []
    records = []
    # USDA reports contain multi-line tables. Normalize whitespace before parsing rows.
    normalized = re.sub(r"\s+", " ", text)
    section_matches = list(re.finditer(
        r"(STEERS?|HEIFERS?|DAIRY STEERS?|COWS?|BULLS?|BRED COWS?|COW-CALF PAIRS?)\s*-\s*"
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
    # Direct-trade report tables use a separate structure (Current FOB/DEL rows).
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
    print(f"USDA {report_id} {market}: {len(records)} normalized rows for {date}")
    return records

def parse_marana():
    url = "https://www.maranastockyards.us/marketreport/current.html"
    try:
        soup = BeautifulSoup(fetch(url).text, "html.parser")
        text = soup.get_text(" ", strip=True)
        date = parse_date(text)
        if not date:
            print("Marana: report date not found", file=sys.stderr)
            return []
        records, section = [], None
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            joined = " ".join(cells)
            if re.search(r"\bSTEERS\b", joined, re.I):
                section = "Steer"
                continue
            if re.search(r"\bHEIFERS\b", joined, re.I):
                section = "Heifer"
                continue
            if not section or len(cells) < 3:
                continue
            desc = cells[0]
            wt = re.search(r"\b(\d{3,4})\b", cells[1])
            price = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", cells[2])
            head = re.search(r"^\s*(\d+)\b", desc)
            if not wt or not price:
                continue
            w, p = float(wt.group(1)), float(price.group(1).replace(",", ""))
            h = int(head.group(1)) if head else None
            if w < 200 or w > 1600 or p < 50 or p > 1000:
                continue
            records.append(record(date, "Marana Stockyards", "Arizona",
                                  "Feeder heifer" if section == "Heifer" else "Feeder steer",
                                  section, desc, w, p, h, "Marana Stockyards", url,
                                  "auction_lot_average", sale_type="Live auction",
                                  notes="Average weight and price from published sale table"))
        print(f"Marana: {len(records)} rows for {date}")
        return records
    except Exception as e:
        print(f"Marana failed: {e}", file=sys.stderr)
        return []


def parse_fallon():
    index = "https://www.fallonlivestock.com/market-reports.html"
    try:
        soup = BeautifulSoup(fetch(index).text, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href, label = urljoin(index, a["href"]), a.get_text(" ", strip=True)
            if ".pdf" in href.lower() and label:
                links.append((label, href))
        for label, url in links[:12]:
            try:
                text = pdf_text(url)
                date = parse_date(label) or parse_date(text[:1200])
                if date:
                    # Keep archive discovery visible. Fallon tables need a format-specific
                    # parser before individual averages can be ingested without guessing.
                    print(f"Fallon archive report found: {label} ({date}); awaiting table parser")
            except Exception as e:
                print(f"Fallon report failed {url}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Fallon archive index failed: {e}", file=sys.stderr)
    return []


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
    for report_id, url in report_urls.items():
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
    {"name":"Tulare Sales Yard","state":"CA","kind":"live auction","url":"https://tularesalesyard.com/","status":"listed; historical parser pending"},
    {"name":"Fresno Livestock Commission","state":"CA","kind":"live auction","url":"https://www.fresnolivestock.com/","status":"listed; historical parser pending"},
    {"name":"A&M Livestock Auction","state":"CA","kind":"live / video auction","url":"https://www.amlivestockauction.com/","status":"listed; historical parser pending"},
    {"name":"Western Stockman's Market","state":"CA","kind":"live auction","url":"https://www.westernstockmans.com/","status":"listed; historical parser pending"},
    {"name":"Marana Stockyards","state":"AZ","kind":"live auction","url":"https://www.maranastockyards.com/market-report","status":"automated: current published sale table"},
    {"name":"USDA Southwest Direct Cattle (AZ/CA/NV)","state":"AZ/CA/NV","kind":"direct trade weighted average","url":"https://mymarketnews.ams.usda.gov/viewReport/2940","status":"automated: current USDA report"},
    {"name":"Fallon Livestock Exchange","state":"NV","kind":"live auction","url":"https://www.fallonlivestock.com/market-reports.html","status":"archive found; parser pending"},
    {"name":"Roswell Livestock Auction","state":"NM","kind":"live auction","url":"https://www.roswelllivestockauction.com/market-report.html","status":"report page tracked; USDA series ingested"},
    {"name":"USDA Roswell Livestock Auction","state":"NM","kind":"USDA auction report","url":"https://mymarketnews.ams.usda.gov/viewReport/1782","status":"automated: current USDA report"},
    {"name":"USDA New Mexico Weekly Cattle Summary","state":"NM","kind":"state auction summary","url":"https://mymarketnews.ams.usda.gov/viewReport/1784","status":"automated: current USDA report"},
    {"name":"Clovis Livestock Auction","state":"NM","kind":"live auction","url":"https://esmis.nal.usda.gov/publication/clovis-livestock-cattle-auction-clovis-nm","status":"archive found; historical import pending"},
    {"name":"Cattlemen's Livestock Auction","state":"NM","kind":"live auction","url":"https://www.cattlemenslivestock.com/","status":"listed; historical parser pending"}
]


def main():
    records = []
    records += parse_overland()
    records += parse_visalia()
    records += parse_marana()
    records += parse_usda_report(1782, "Roswell Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1781, "Clovis Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1783, "Cattlemen's Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1290, "Santa Teresa Livestock Auction (USDA)", "New Mexico")
    records += parse_usda_report(1784, "New Mexico Weekly Cattle Summary (USDA)", "New Mexico", "usda_state_summary")
    records += parse_usda_report(2940, "Southwest Direct Cattle Report (USDA)", "AZ/CA/NV", "usda_direct_trade")
    parse_roswell_web()
    parse_fallon()
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("records", [])
        except Exception:
            pass
    by_id = {r["id"]: r for r in old}
    for r in records:
        by_id[r["id"]] = r
    merged = sorted(by_id.values(), key=lambda r: (r.get("date", ""), r.get("market", ""), r.get("id", "")))
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "schema_version": 2, "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(merged), "sources": SOURCES, "records": merged,
        "methodology": "Auction prices, USDA state summaries, direct trades, video contracts and published price bands are retained as separate record types. They are not pooled into one price series."
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(merged)} unique market observations to {OUT}")
    fetch_futures()


if __name__ == "__main__":
    main()
