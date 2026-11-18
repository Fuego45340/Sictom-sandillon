#!/usr/bin/env python3
"""Find the next/current Sandillon PDF, extract it, validate it, and update JSON."""
from __future__ import annotations

import datetime as dt
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

from extract_calendar import download_pdf, extract

CALENDAR_PAGE = "https://sictom-chateauneuf.fr/calendrier-de-collecte/"
OUTPUT = Path("collectes.json")


def get_page(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; SandillonCalendarBot/1.0)"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def find_pdf(year: int) -> str:
    html = get_page(CALENDAR_PAGE)
    # Prefer the official calendar listing when it exposes the Sandillon PDF.
    links = re.findall(r"""href\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']""", html, re.I)
    for href in links:
        absolute = urljoin(CALENDAR_PAGE, href)
        if "sandillon" in absolute.lower() and re.search(rf"{year}", absolute):
            return absolute

    # The SICTOM's known Sandillon upload convention, used as a fallback
    # when the calendar listing page has not yet refreshed its links.
    candidates = [
        f"https://sictom-chateauneuf.fr/wp-content/uploads/{year - 1}/11/SANDILLON_-Calendrier-de-collecte-{year}.pdf",
        f"https://sictom-chateauneuf.fr/wp-content/uploads/{year - 1}/11/SANDILLON-Calendrier-de-collectes-{year}.pdf",
    ]
    for candidate in candidates:
        try:
            temp = Path("candidate-check.pdf")
            download_pdf(candidate, temp)
            temp.unlink(missing_ok=True)
            return candidate
        except Exception:
            Path("candidate-check.pdf").unlink(missing_ok=True)
    raise RuntimeError(f"Calendrier PDF de Sandillon {year} introuvable sur le site officiel.")


def target_year(now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(dt.timezone.utc)
    # From October onward, look for the following year's calendar.
    return now.year + 1 if now.month >= 10 else now.year


def main() -> int:
    year = target_year()
    pdf_url = find_pdf(year)
    pdf_path = Path("calendar-source.pdf")
    try:
        download_pdf(pdf_url, pdf_path)
        dates = extract(pdf_path, year)
        import json

        payload = {
            "commune": "Sandillon",
            "annee": year,
            "source": pdf_url,
            "consigne": "Sortir les poubelles la veille. Passage du camion l'après-midi.",
            **dates,
        }
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"Calendrier {year} validé : "
            f"{len(dates['ordures_menageres'])} collectes OM, "
            f"{len(dates['tri_selectif'])} collectes tri."
        )
        return 0
    except Exception as error:
        print(f"ERREUR : calendrier non mis à jour : {error}", file=sys.stderr)
        return 1
    finally:
        pdf_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
