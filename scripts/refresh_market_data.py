#!/usr/bin/env python3
"""Collect public cattle-market observations from official market report pages.

Outputs normalized, source-linked records. It deliberately preserves price ranges
and cattle type rather than blending incomparable lots.
"""
from __future__ import annotations
import json, re, hashlib, os, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from io import BytesIO

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "market_observations.json"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "FlyingP-CattleMarketAnalyst/1.0 (market data aggregation; contact via repository)"})


def clean_num(s):
    return float(s.replace(",", "").replace("$", ""))


def stable_id(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def get_text(url):
    r = SESSION.get(url, timeout=45)
    r.raise_for_status()
    return r.text


def parse_overland():
    index_url = "https://www.overlandstockyard.com/market-reports"
    soup = BeautifulSoup(get_text(index_url), "html.parser")
    report_urls = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(index_url, a["href"])
        m = re.search(r"/lot-listing/(\d+)/mr", href)
        if m:
            report_urls[m.group(1)] = href.split("?")[0]
    records = []
    for report_id, url in report_urls.items():
        try:
            page = BeautifulSoup(get_text(url), "html.parser")
            body = page.get_text(" ", strip=True)
            title = page.find("h1").get_text(" ", strip=True) if page.find("h1") else "Overland market report"
            date_match = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", body)
            if not date_match:
                print(f"Overland: date not found for {url}", file=sys.stderr)
                continue
            sale_date = datetime.strptime(date_match.group(1), "%B %d, %Y").date().isoformat()
            # Lot sections are consistently headed "Lot <identifier>" on the public listing.
            matches = list(re.finditer(r"\bLot\s+([A-Za-z0-9-]+)\b", body, flags=re.I))
            for i, match in enumerate(matches):
                chunk = body[match.start(): matches[i+1].start() if i+1 < len(matches) else len(body)]
                price_match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", chunk)
                weight_match = re.search(r"(\d+)\s*Hd\s*\(\s*([\d,]+)\s*lbs?\s*\)", chunk, flags=re.I)
                if not price_match or not weight_match:
                    continue
                price = clean_num(price_match.group(1))
                head = int(weight_match.group(1))
                weight = clean_num(weight_match.group(2))
                # Type labels are taken from the lot's own description, never inferred from price.
                desc_match = re.search(r"\b(HOL\s*(?:STR|HFR)|HOLSTEIN\s*(?:STEER|HEIFER)?|NATIVE\s*(?:STR|HFR|STEER|HEIFER)|BEEF\s*(?:STR|HFR|STEER|HEIFER)|ANGUS\s*(?:STR|HFR|STEER|HEIFER)|HOL\s*BEEF\s*(?:STR|HFR))\b", chunk, flags=re.I)
                desc = re.sub(r"\s+", " ", desc_match.group(1)).upper() if desc_match else "UNCLASSIFIED"
                upper = chunk.upper()
                if "HFR" in desc or "HEIFER" in desc:
                    sex, category = "Heifer", "Feeder heifer"
                elif "STR" in desc or "STEER" in desc:
                    sex, category = "Steer", "Feeder steer"
                else:
                    sex, category = "Unknown", "Other"
                if "HOL" in desc or "HOLSTEIN" in desc or "DAIRY" in upper:
                    breed = "Holstein / dairy"
                elif "ANGUS" in desc:
                    breed = "Angus / Angus-cross"
                elif "NATIVE" in desc or "BEEF" in desc:
                    breed = "Beef cross / native"
                else:
                    breed = "Mixed / unknown"
                # A few report pages contain placeholder lot rows with zero values; omit them.
                if price <= 0 or weight <= 0 or head <= 0:
                    continue
                lot_match = re.search(r"\bLot\s+([A-Za-z0-9-]+)", chunk, flags=re.I)
                lot = lot_match.group(1) if lot_match else str(i)
                records.append({
                    "id": stable_id("overland", report_id, lot),
                    "date": sale_date, "market": "Overland Stock Yard",
                    "category": category, "breed": breed, "sex": sex,
                    "weight": weight, "price": price, "price_low": price, "price_high": price,
                    "head": head, "sale": "Video / internet",
                    "lot_id": lot, "description": desc, "report_title": title,
                    "source": "Overland Stock Yard", "source_url": url,
                    "price_unit": "USD/cwt", "record_type": "lot_contract"
                })
        except Exception as exc:
            print(f"Overland report {url} failed: {exc}", file=sys.stderr)
    print(f"Overland: collected {len(records)} lot records from {len(report_urls)} report pages")
    return records


def parse_visalia():
    page_url = "https://www.visalialivestock.com/market-snapshot.html"
    soup = BeautifulSoup(get_text(page_url), "html.parser")
    pdf_urls = []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if href.lower().endswith(".pdf") and "snapshot" in href.lower():
            pdf_urls.append(href)
    records = []
    for url in sorted(set(pdf_urls)):
        try:
            response = SESSION.get(url, timeout=45)
            response.raise_for_status()
            reader = PdfReader(BytesIO(response.content))
            text = "\n".join(p.extract_text() or "" for p in reader.pages)
            # Snapshot headings vary: "January 7th thru 28th 2026",
            # "March 11th, 18th & 25th 2026", or a single dated sale.
            heading = text[:500]
            period = re.search(r"([A-Z][a-z]+)\s+(.{1,100}?)\s+(20\d{2})", heading, flags=re.S)
            if not period:
                print(f"Visalia: could not identify snapshot date in {url}", file=sys.stderr)
                continue
            month_name, date_phrase, year = period.group(1), period.group(2), int(period.group(3))
            day_values = [int(x) for x in re.findall(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?", date_phrase)]
            day_values = [d for d in day_values if 1 <= d <= 31]
            if not day_values:
                print(f"Visalia: could not identify day in {url}", file=sys.stderr)
                continue
            month_num = datetime.strptime(month_name, "%B").month
            start = datetime(year, month_num, min(day_values)).date()
            end = datetime(year, month_num, max(day_values)).date()
            # Snapshot table rows: steer low-high, weight, heifer low-high.
            row_re = re.compile(
                r"\$\s*([\d,]+(?:\.\d{1,2})?)\s*-\s*\$?\s*([\d,]+(?:\.\d{1,2})?)"
                r"\s+(\d{3})\s+LBS?\s+"
                r"\$\s*([\d,]+(?:\.\d{1,2})?)\s*-\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
                flags=re.I
            )
            for m in row_re.finditer(text):
                w = int(m.group(3))
                for category, sex, lo_i, hi_i in [
                    ("Feeder steer", "Steer", 1, 2),
                    ("Feeder heifer", "Heifer", 4, 5)
                ]:
                    lo, hi = clean_num(m.group(lo_i)), clean_num(m.group(hi_i))
                    records.append({
                        "id": stable_id("visalia", url, w, category),
                        "date": end.isoformat(), "period_start": start.isoformat(), "period_end": end.isoformat(),
                        "market": "Visalia Livestock Market", "category": category,
                        "breed": "Choice cattle (breed not specified)", "sex": sex,
                        "weight": w, "price": round((lo + hi) / 2, 2),
                        "price_low": lo, "price_high": hi, "head": None,
                        "sale": "Regular sale snapshot", "lot_id": None,
                        "report_title": "Market Snapshot", "source": "Visalia Livestock Market",
                        "source_url": url, "price_unit": "USD/cwt", "record_type": "published_range"
                    })
        except Exception as exc:
            print(f"Visalia snapshot {url} failed: {exc}", file=sys.stderr)
    print(f"Visalia: collected {len(records)} price-band records from {len(set(pdf_urls))} snapshot PDFs")
    return records


def main():
    all_records = parse_overland() + parse_visalia()
    # Preserve previously collected history if a source temporarily removes old links.
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("records", [])
        except Exception:
            old = []
    by_id = {r["id"]: r for r in old}
    for r in all_records:
        by_id[r["id"]] = r
    merged = sorted(by_id.values(), key=lambda r: (r.get("date", ""), r.get("market", ""), r.get("id", "")))
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(merged),
        "sources_checked": [
            "https://www.overlandstockyard.com/market-reports",
            "https://www.visalialivestock.com/market-snapshot.html"
        ],
        "records": merged
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(merged)} unique records to {OUT}")


if __name__ == "__main__":
    main()
