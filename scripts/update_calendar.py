#!/usr/bin/env python3
"""Maintain the active Sandillon calendar and prepare the following year."""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

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
    links = re.findall(r"""href\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']""", html, re.I)
    for href in links:
        absolute = urljoin(CALENDAR_PAGE, href)
        if "sandillon" in absolute.lower() and re.search(rf"{year}", absolute):
            return absolute

    candidates = [
        f"https://sictom-chateauneuf.fr/wp-content/uploads/{year - 1}/11/SANDILLON_-Calendrier-de-collecte-{year}.pdf",
        f"https://sictom-chateauneuf.fr/wp-content/uploads/{year - 1}/11/SANDILLON-Calendrier-de-collectes-{year}.pdf",
    ]
    for candidate in candidates:
        temp = Path("candidate-check.pdf")
        try:
            download_pdf(candidate, temp)
            return candidate
        except Exception:
            pass
        finally:
            temp.unlink(missing_ok=True)
    raise RuntimeError(f"Calendrier PDF de Sandillon {year} introuvable sur le site officiel.")


def archive_path(year: int) -> Path:
    return Path(f"collectes-{year}.json")


def read_valid_calendar(path: Path, year: int) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("commune") != "Sandillon" or payload.get("annee") != year:
        raise RuntimeError(f"{path} ne contient pas un calendrier Sandillon valide pour {year}.")
    for key in ("ordures_menageres", "tri_selectif"):
        dates = payload.get(key)
        if not isinstance(dates, list) or not dates:
            raise RuntimeError(f"{path}: liste {key} absente ou vide.")
    return payload


def build_calendar(year: int) -> dict:
    pdf_url = find_pdf(year)
    pdf_path = Path(f"calendar-source-{year}.pdf")
    try:
        download_pdf(pdf_url, pdf_path)
        dates = extract(pdf_path, year)
        return {
            "commune": "Sandillon",
            "annee": year,
            "source": pdf_url,
            "consigne": "Sortir les poubelles la veille. Passage du camion l'après-midi.",
            **dates,
        }
    finally:
        pdf_path.unlink(missing_ok=True)


def save_calendar(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    # Use French local time so the active calendar changes at local New Year.
    current_year = dt.datetime.now(ZoneInfo("Europe/Paris")).year
    next_year = current_year + 1

    for year in (current_year, next_year):
        archive = archive_path(year)
        try:
            payload = build_calendar(year)
            save_calendar(archive, payload)
            print(
                f"Calendrier {year} validé : "
                f"{len(payload['ordures_menageres'])} collectes OM, "
                f"{len(payload['tri_selectif'])} collectes tri."
            )
        except Exception as error:
            if archive.exists():
                # Keep the last validated copy if the SICTOM site is temporarily unavailable
                # or has not published the following year's PDF yet.
                read_valid_calendar(archive, year)
                print(f"Calendrier {year} conservé (source non disponible : {error}).")
            elif year == current_year:
                raise RuntimeError(f"Calendrier actif {year} indisponible : {error}") from error
            else:
                print(f"Calendrier suivant {year} pas encore publié : {error}")

    active_archive = archive_path(current_year)
    active_payload = read_valid_calendar(active_archive, current_year)
    save_calendar(OUTPUT, active_payload)
    print(f"Calendrier actif publié : {current_year}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERREUR : publication interrompue : {error}", file=sys.stderr)
        raise SystemExit(1)
