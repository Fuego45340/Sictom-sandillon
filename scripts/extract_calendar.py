#!/usr/bin/env python3
"""Extract Sandillon collection dates from the official SICTOM PDF."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF

MONTHS = {
    "Janvier": 1, "Février": 2, "Mars": 3, "Avril": 4, "Mai": 5, "Juin": 6,
    "Juillet": 7, "Août": 8, "Septembre": 9, "Octobre": 10, "Novembre": 11,
    "Décembre": 12,
}
MIN_DATES = 20
MAX_DATES = 35


def download_pdf(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; SandillonCalendarBot/1.0)"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
        content_type = response.headers.get("Content-Type", "").lower()
    if not data.startswith(b"%PDF-"):
        raise RuntimeError(f"La source ne renvoie pas un PDF valide (Content-Type: {content_type}).")
    destination.write_bytes(data)


def classify_marker(fill):
    if not fill or len(fill) < 3:
        return None
    red, green, blue = fill[:3]
    if red > 0.95 and 0.75 < green < 0.88 and blue < 0.15:
        return "tri_selectif"
    if 0.45 < red < 0.55 and 0.45 < green < 0.55 and 0.50 < blue < 0.58:
        return "ordures_menageres"
    return None


def extract(pdf_path: Path, expected_year: int) -> dict:
    document = fitz.open(pdf_path)
    if len(document) != 2:
        raise RuntimeError(f"PDF inattendu : 2 pages attendues, {len(document)} trouvée(s).")

    result = {"ordures_menageres": [], "tri_selectif": []}
    found_year = False

    for page in document:
        words = page.get_text("words")
        page_text = page.get_text()
        if re.search(rf"Calendrier de collectes\s+{expected_year}\b", page_text, re.I):
            found_year = True

        headings = [
            (MONTHS[word[4]], (word[0] + word[2]) / 2)
            for word in words if word[4] in MONTHS
        ]
        if len(headings) != 6:
            raise RuntimeError(
                f"Disposition inattendue : 6 mois attendus sur chaque page, {len(headings)} détecté(s)."
            )

        for drawing in page.get_drawings():
            rect = drawing["rect"]
            if not (65 < rect.width < 80 and 10 < rect.height < 20):
                continue
            collection = classify_marker(drawing.get("fill"))
            if collection is None:
                continue

            contained = [
                word for word in words
                if rect.x0 - 1 <= (word[0] + word[2]) / 2 <= rect.x1 + 1
                and rect.y0 - 1 <= (word[1] + word[3]) / 2 <= rect.y1 + 1
            ]
            days = [int(word[4]) for word in contained if re.fullmatch(r"\d{1,2}", word[4])]
            if len(days) != 1:
                raise RuntimeError(f"Impossible d'identifier le jour du repère {collection}.")

            month = min(headings, key=lambda item: abs(item[1] - (rect.x0 + rect.x1) / 2))[0]
            result[collection].append(dt.date(expected_year, month, days[0]).isoformat())

    if not found_year:
        raise RuntimeError(f"Le PDF ne semble pas être le calendrier {expected_year}.")

    for collection, dates in result.items():
        if len(dates) != len(set(dates)):
            raise RuntimeError(f"Dates en double dans {collection}.")
        dates.sort()
        if not MIN_DATES <= len(dates) <= MAX_DATES:
            raise RuntimeError(f"Nombre de dates incohérent pour {collection} : {len(dates)}.")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="URL du PDF officiel")
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--output", default="collectes.json")
    args = parser.parse_args()

    temp_pdf = Path("calendar-source.pdf")
    try:
        download_pdf(args.url, temp_pdf)
        dates = extract(temp_pdf, args.year)
        payload = {
            "commune": "Sandillon",
            "annee": args.year,
            "source": args.url,
            "consigne": "Sortir les poubelles la veille. Passage du camion l'après-midi.",
            **dates,
        }
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({
            "annee": args.year,
            "ordures_menageres": len(dates["ordures_menageres"]),
            "tri_selectif": len(dates["tri_selectif"]),
        }, ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"ERREUR : {error}", file=sys.stderr)
        return 1
    finally:
        temp_pdf.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
