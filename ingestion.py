"""
ingestion.py — AnalytiQ Pro
=============================
Charge un fichier CSV ou XLSX et retourne un DataFrame propre
accompagné d'un log d'ingestion structuré.

Comportement :
  - CSV  : auto-détection séparateur (, ; \\t) et encodage
  - XLSX : première feuille par défaut, toutes les autres signalées
  - Aucune modification des données — lecture pure
  - Colonnes 100% NaN : signalées dans le log (pas supprimées)
  - Doublons de lignes : comptés dans le log (pas supprimés)
  - Fichier illisible → (None, log avec erreur)

Encodings testés dans l'ordre : utf-8, utf-8-sig, latin-1, cp1252

Signature obligatoire : ingest(file_path) -> (df, log_dict)

Auteur  : Othmane Afif — othmane.afif@outlook.com
Projet  : AnalytiQ Pro — analytiq-pro.com
Version : 1.0 — 2025
"""

from __future__ import annotations

import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chardet
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

# ─────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────

ENCODINGS_TO_TRY = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
CSV_SEPARATORS   = [",", ";", "\t", "|"]
SUPPORTED_FORMATS = {".csv", ".tsv", ".xlsx", ".xls"}


# ─────────────────────────────────────────────
# UTILITAIRES INTERNES
# ─────────────────────────────────────────────

def _detect_encoding(file_path: Path, sample_bytes: int = 65_536) -> str:
    """
    Détecte l'encodage probable d'un fichier via chardet.
    Retourne 'utf-8' si la détection échoue ou le score est < 0.6.
    """
    try:
        with open(file_path, "rb") as f:
            raw = f.read(sample_bytes)
        result = chardet.detect(raw)
        encoding = result.get("encoding") or "utf-8"
        confidence = result.get("confidence", 0.0)
        if confidence < 0.6:
            return "utf-8"
        # Normaliser les aliases
        encoding = encoding.lower().replace("-", "_")
        known_aliases = {
            "utf_8_sig": "utf-8-sig",
            "utf_8":     "utf-8",
            "latin_1":   "latin-1",
            "iso_8859_1": "latin-1",
            "windows_1252": "cp1252",
        }
        return known_aliases.get(encoding, encoding)
    except Exception:
        return "utf-8"


def _detect_separator(file_path: Path, encoding: str) -> str:
    """
    Détecte le séparateur dominant dans les 5 premières lignes d'un CSV.
    Retourne ',' par défaut si indécis.
    """
    try:
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            sample = "".join(f.readline() for _ in range(5))
        counts = {sep: sample.count(sep) for sep in CSV_SEPARATORS}
        best = max(counts, key=counts.get)
        if counts[best] == 0:
            return ","
        return best
    except Exception:
        return ","


def _read_csv(file_path: Path) -> tuple[pd.DataFrame | None, dict]:
    """
    Tente de lire un CSV avec auto-détection encodage + séparateur.
    Retourne (df, partial_log) avec les infos de lecture.
    """
    partial: dict[str, Any] = {}

    # 1. Détection encodage via chardet, puis fallback sur la liste
    chardet_enc = _detect_encoding(file_path)
    encoding_order = list(dict.fromkeys([chardet_enc] + ENCODINGS_TO_TRY))

    detected_encoding = None
    df = None
    separator = ","

    for enc in encoding_order:
        try:
            sep = _detect_separator(file_path, enc)
            df_try = pd.read_csv(
                file_path,
                sep=sep,
                encoding=enc,
                engine="python",
                on_bad_lines="warn",
                dtype=str,           # lecture pure — pas de cast automatique
                keep_default_na=False,
            )
            # Succès si on obtient au moins 1 colonne
            if len(df_try.columns) >= 1:
                df = df_try
                detected_encoding = enc
                separator = sep
                break
        except Exception:
            continue

    if df is None:
        partial["error"] = f"Impossible de lire le CSV avec les encodings : {encoding_order}"
        return None, partial

    partial["encoding_detected"] = detected_encoding
    partial["separator_detected"] = repr(separator)
    return df, partial


def _read_xlsx(file_path: Path) -> tuple[pd.DataFrame | None, dict]:
    """
    Lit la première feuille d'un fichier XLSX/XLS.
    Signale les autres feuilles dans le log.
    """
    partial: dict[str, Any] = {}

    try:
        engine = "xlrd" if file_path.suffix.lower() == ".xls" else "openpyxl"
        xl = pd.ExcelFile(file_path, engine=engine)
        all_sheets = xl.sheet_names
        partial["sheets_available"] = all_sheets
        partial["sheet_loaded"] = all_sheets[0]
        if len(all_sheets) > 1:
            partial["sheets_not_loaded"] = all_sheets[1:]

        df = pd.read_excel(
            file_path,
            sheet_name=all_sheets[0],
            engine=engine,
            dtype=str,
            keep_default_na=False,
        )
        partial["encoding_detected"] = "N/A (binary format)"
        return df, partial

    except Exception as exc:
        partial["error"] = f"Erreur lecture XLSX : {exc}"
        return None, partial


def _build_log(
    file_path: Path,
    fmt: str,
    df: pd.DataFrame | None,
    partial: dict,
    error: str | None = None,
) -> dict[str, Any]:
    """
    Construit le log d'ingestion complet.
    """
    log: dict[str, Any] = {
        "analyzer":          "ingestion",
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "filename":          file_path.name,
        "file_path":         str(file_path),
        "format":            fmt,
        "encoding_detected": partial.get("encoding_detected", "N/A"),
        "separator_detected": partial.get("separator_detected", "N/A"),
    }

    # Infos sheets XLSX
    if "sheet_loaded" in partial:
        log["sheet_loaded"]       = partial["sheet_loaded"]
        log["sheets_available"]   = partial["sheets_available"]
        log["sheets_not_loaded"]  = partial.get("sheets_not_loaded", [])

    if error or df is None:
        log["status"] = "ERROR"
        log["error"]  = error or partial.get("error", "Unknown error")
        log["rows"]    = 0
        log["columns"] = 0
        return log

    rows, cols = df.shape
    log["status"]  = "OK"
    log["rows"]    = rows
    log["columns"] = cols
    log["column_names"] = df.columns.tolist()

    # Colonnes 100% NaN (vides) — signalées, pas supprimées
    # Note : on travaille en dtype=str, donc NaN = chaîne vide ou pd.NA
    all_empty_cols = []
    for col in df.columns:
        # Considérer une colonne comme vide si toutes les valeurs sont
        # chaîne vide, "nan", "NaN", ou vraiment NaN
        non_empty = df[col].replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA}).dropna()
        if len(non_empty) == 0:
            all_empty_cols.append(col)
    log["columns_all_null"]       = all_empty_cols
    log["columns_all_null_count"] = len(all_empty_cols)

    # Doublons de lignes — comptés uniquement
    duplicate_count = int(df.duplicated().sum())
    log["duplicate_rows_count"] = duplicate_count
    log["duplicate_rows_pct"]   = (
        round(duplicate_count / rows * 100, 2) if rows > 0 else 0.0
    )

    # Aperçu de la densité par colonne (ratio valeurs non-vides)
    col_density = {}
    for col in df.columns:
        non_empty = df[col].replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA}).dropna()
        col_density[col] = round(len(non_empty) / rows, 4) if rows > 0 else 0.0
    log["column_density"] = col_density

    return log


# ─────────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────────

def ingest(file_path: str | Path) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """
    Charge un fichier CSV ou XLSX et retourne un DataFrame brut + log.

    Paramètres
    ----------
    file_path : str | Path
        Chemin vers le fichier à ingérer.

    Retourne
    --------
    (df, log_dict) :
        df       — DataFrame complet, dtype=str, aucune modification.
                   None si le fichier est illisible.
        log_dict — dict structuré pour le Transparency Ledger :
            {
                "analyzer"            : "ingestion",
                "timestamp"           : "...",
                "filename"            : "...",
                "format"              : "CSV" | "XLSX" | "UNKNOWN",
                "status"              : "OK" | "ERROR",
                "encoding_detected"   : "...",
                "separator_detected"  : "...",
                "rows"                : int,
                "columns"             : int,
                "column_names"        : [...],
                "columns_all_null"    : [...],
                "columns_all_null_count" : int,
                "duplicate_rows_count": int,
                "duplicate_rows_pct"  : float,
                "column_density"      : {col: float, ...},
            }
    """
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    # ── Extension non supportée
    if suffix not in SUPPORTED_FORMATS:
        log = _build_log(
            file_path, "UNKNOWN", None, {},
            error=f"Format non supporté : '{suffix}'. Formats acceptés : {SUPPORTED_FORMATS}",
        )
        return None, log

    # ── Fichier introuvable
    if not file_path.exists():
        log = _build_log(
            file_path, suffix.upper().lstrip("."), None, {},
            error=f"Fichier introuvable : {file_path}",
        )
        return None, log

    # ── Lecture selon le format
    fmt = "XLSX" if suffix in {".xlsx", ".xls"} else "CSV"

    if fmt == "CSV":
        df, partial = _read_csv(file_path)
    else:
        df, partial = _read_xlsx(file_path)

    # ── Erreur de lecture
    if df is None:
        log = _build_log(file_path, fmt, None, partial)
        return None, log

    # ── Succès
    log = _build_log(file_path, fmt, df, partial)
    return df, log
