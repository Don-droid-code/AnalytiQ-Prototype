"""
finance_analyzer.py — AnalytiQ Pro
=====================================
Détecte les 23 traps Finance dans un DataFrame :
  - 11 traps Finance   : T01–T10 + T68
  - 12 traps CrossSector : T57–T67 + T69
Total : 23 traps

Formule DQS officielle :
    DQS = max(0, 100 − Σ(penalty_i × severity_mult × coverage_factor))
    penalty_i       : 20 si Critical, 8 si Common
    severity_mult   : occurrences / total_rows  (plafonné à 1.0)
    coverage_factor : 1.0 si > 25 % colonnes touchées, 0.5 sinon

Règle architecturale :
    Ce module ne fait PAS de déduplication avec d'autres analyseurs.
    T60 est détecté normalement ici.
    La déduplication inter-analyseurs est gérée par aci_calculator.py.

Signature obligatoire : analyze(df, yaml_path) → (df, log_dict)

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

import numpy as np
import pandas as pd
import yaml

warnings.filterwarnings("ignore", category=UserWarning)

# ─────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────

# IDs des 23 traps Finance actifs
FINANCE_TRAP_IDS = [
    "T01", "T02", "T03", "T04", "T05", "T06", "T07",
    "T08", "T09", "T10", "T68",          # 11 Finance
    "T57", "T58", "T59", "T60", "T61",   # 12 CrossSector
    "T62", "T63", "T64", "T65", "T66",
    "T67", "T69",
]

# Seuil de déclenchement : 5 % de valeurs suspectes
TRIGGER_RATIO = 0.05

# Mots-clés identifiant une colonne monétaire/financière
AMOUNT_KEYWORDS = [
    "amount", "revenue", "price", "cost", "salary", "wage", "payment",
    "balance", "invoice", "fee", "tax", "total", "profit", "loss",
    "credit", "debit", "montant", "revenu", "prix", "solde", "facture",
]

# Mots-clés identifiant une colonne date
DATE_KEYWORDS = [
    "date", "time", "ts", "at", "created", "updated", "posted",
    "accounting", "transaction", "start", "end", "effective",
]

# Correspondance pays → monnaie officielle (ISO 3166 → ISO 4217, extrait)
COUNTRY_CURRENCY_MAP = {
    "USA": ["USD"], "US": ["USD"], "UNITED STATES": ["USD"],
    "GBR": ["GBP"], "UK": ["GBP"], "UNITED KINGDOM": ["GBP"],
    "FRA": ["EUR"], "FRANCE": ["EUR"],
    "DEU": ["EUR"], "GERMANY": ["EUR"],
    "ESP": ["EUR"], "SPAIN": ["EUR"],
    "ITA": ["EUR"], "ITALY": ["EUR"],
    "MAR": ["MAD"], "MOROCCO": ["MAD"],
    "JPN": ["JPY"], "JAPAN": ["JPY"],
    "CHN": ["CNY"], "CHINA": ["CNY"],
    "CAN": ["CAD"], "CANADA": ["CAD"],
    "AUS": ["AUD"], "AUSTRALIA": ["AUD"],
    "IND": ["INR"], "INDIA": ["INR"],
    "BRA": ["BRL"], "BRAZIL": ["BRL"],
    "CHE": ["CHF"], "SWITZERLAND": ["CHF"],
    "SAU": ["SAR"], "SAUDI ARABIA": ["SAR"],
    "ARE": ["AED"], "UAE": ["AED"],
}

# Valeurs placeholder communes (T57)
PLACEHOLDER_VALUES = {
    "9999", "99999", "999999", "-1", "-999", "0000", "n/a", "na",
    "null", "none", "undefined", "unknown", "missing", "#n/a",
    "9999.0", "99999.0", "-1.0",
}

# Pattern email valide minimal
EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# Pattern numérique pur
NUMERIC_PATTERN = re.compile(r"^-?\d+([.,]\d+)?$")


# ─────────────────────────────────────────────
# CHARGEMENT DU CATALOGUE
# ─────────────────────────────────────────────

def load_finance_traps(yaml_path: str | Path) -> dict[str, dict]:
    """
    Charge traps_catalog.yaml et retourne un dict {trap_id: trap_dict}
    pour les 23 traps Finance actifs uniquement.
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    traps = {}
    for trap in catalog.get("data_traps", []):
        if trap["id"] in FINANCE_TRAP_IDS:
            traps[trap["id"]] = trap

    missing = [t for t in FINANCE_TRAP_IDS if t not in traps]
    if missing:
        raise ValueError(f"Traps manquants dans le YAML : {missing}")

    return traps


# ─────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────

def _amount_columns(df: pd.DataFrame) -> list[str]:
    """Colonnes dont le nom suggère un montant financier."""
    return [c for c in df.columns if any(kw in c.lower() for kw in AMOUNT_KEYWORDS)]


def _date_columns(df: pd.DataFrame) -> list[str]:
    """Colonnes dont le nom suggère une date."""
    return [c for c in df.columns if any(kw in c.lower() for kw in DATE_KEYWORDS)]


def _string_columns(df: pd.DataFrame) -> list[str]:
    """Toutes les colonnes object/string."""
    return list(df.select_dtypes(include=["object"]).columns)


def _to_numeric(series: pd.Series) -> pd.Series:
    """Coerce une série en numérique, retourne NaN si impossible."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", ".", regex=False).str.strip(),
        errors="coerce",
    )


def _to_date(series: pd.Series) -> pd.Series:
    """Coerce une série en datetime, retourne NaT si impossible."""
    return pd.to_datetime(series, errors="coerce")


def _triggered(ratio: float) -> bool:
    return ratio >= TRIGGER_RATIO


def _count_occurrences(mask: pd.Series) -> int:
    return int(mask.sum())


def _coverage_factor(flagged_cols: list[str], total_cols: int) -> float:
    """1.0 si > 25 % des colonnes touchées, 0.5 sinon."""
    if total_cols == 0:
        return 0.5
    return 1.0 if (len(flagged_cols) / total_cols) > 0.25 else 0.5


def _severity_mult(occurrences: int, total_rows: int) -> float:
    """occurrences / total_rows, plafonné à 1.0."""
    if total_rows == 0:
        return 0.0
    return min(1.0, occurrences / total_rows)


# ─────────────────────────────────────────────
# DÉTECTEURS — T01 à T10 + T68 (Finance)
# ─────────────────────────────────────────────

def _detect_T01(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T01 — Misinterpreted Negative Amounts"""
    flagged_cols = []
    total_neg = 0
    for col in _amount_columns(df):
        numeric = _to_numeric(df[col])
        neg_mask = numeric < 0
        n = int(neg_mask.sum())
        if n > 0 and _triggered(n / max(len(numeric.dropna()), 1)):
            flagged_cols.append(col)
            total_neg += n
    triggered = bool(flagged_cols)
    detail = (
        f"{total_neg} valeur(s) négative(s) dans : {flagged_cols}" if triggered
        else "Aucun montant négatif détecté au-dessus du seuil."
    )
    return triggered, detail, total_neg, flagged_cols


def _detect_T02(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T02 — Duplicate Entries / Double Billing"""
    dup_mask = df.duplicated(keep=False)
    n = int(dup_mask.sum())
    triggered = _triggered(n / max(len(df), 1))
    detail = (
        f"{n} lignes dupliquées détectées (keep=False)." if triggered
        else "Aucun doublon exact détecté."
    )
    flagged_cols = list(df.columns) if triggered else []
    return triggered, detail, n, flagged_cols


def _detect_T03(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T03 — Accounting Date vs. Transaction Date Mismatch"""
    GAP_DAYS = 7
    acct_kw = ["accounting", "posting", "posted", "ledger", "book"]
    tx_kw   = ["transaction_date", "sale_date", "trade_date", "transaction"]
    date_cols = _date_columns(df)

    accts = [c for c in date_cols if any(kw in c.lower() for kw in acct_kw)]
    txs   = [c for c in date_cols if any(kw in c.lower() for kw in tx_kw)]

    mismatches = []
    total_n = 0
    for ac in accts:
        for tc in txs:
            if ac == tc:
                continue
            s_a = _to_date(df[ac])
            s_t = _to_date(df[tc])
            common = s_a.notna() & s_t.notna()
            if common.sum() == 0:
                continue
            gap = (s_a[common] - s_t[common]).dt.days.abs()
            n = int((gap > GAP_DAYS).sum())
            if _triggered(n / max(common.sum(), 1)):
                mismatches.append(f"{ac} ↔ {tc}")
                total_n += n

    triggered = bool(mismatches)
    detail = (
        f"Écarts accounting/transaction > {GAP_DAYS}j : {mismatches}" if triggered
        else "Aucun écart comptable/transaction détecté."
    )
    flagged_cols = list(set(
        [c for pair in mismatches for c in pair.split(" ↔ ")]
    ))
    return triggered, detail, total_n, flagged_cols


def _detect_T04(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T04 — Missing or Inconsistent Currency"""
    currency_kw = ["currency", "devise", "currency_code", "curr"]
    currency_cols = [c for c in df.columns if any(kw in c.lower() for kw in currency_kw)]

    flagged_cols = []
    total_n = 0

    # Cas 1 : colonne currency avec valeurs multiples (> 1 devise distincte)
    for col in currency_cols:
        vals = df[col].dropna().astype(str).str.strip().str.upper()
        unique_currencies = vals[vals != ""].nunique()
        if unique_currencies > 1:
            flagged_cols.append(col)
            total_n += len(vals)

    # Cas 2 : colonnes montant sans aucune colonne currency associée
    if not currency_cols and _amount_columns(df):
        flagged_cols.extend(_amount_columns(df))
        total_n += len(df)

    triggered = bool(flagged_cols)
    detail = (
        f"Devises multiples ou absentes dans : {flagged_cols}" if triggered
        else "Colonnes currency cohérentes ou absentes."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T05(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T05 — Aggregation Performed Before Cleaning
    Proxy : colonne 'total' ou 'subtotal' présente alors que des doublons existent.
    """
    total_kw = ["total", "subtotal", "grand_total", "sum_", "cumul"]
    total_cols = [c for c in df.columns if any(kw in c.lower() for kw in total_kw)]
    dup_count = int(df.duplicated().sum())

    # Cas 1 : total pré-calculé + doublons présents (pipeline incorrect)
    if total_cols and dup_count > 0:
        triggered = True
        detail = (
            f"Colonnes total {total_cols} présentes avec {dup_count} doublon(s) "
            f"— agrégation probablement réalisée avant nettoyage."
        )
        return triggered, detail, dup_count, total_cols

    # Cas 2 : total déclaré ne correspond pas à la somme recalculée
    for tc in total_cols:
        numeric_tc = _to_numeric(df[tc])
        amount_cols = [c for c in _amount_columns(df) if c != tc]
        if not amount_cols:
            continue
        for ac in amount_cols:
            numeric_ac = _to_numeric(df[ac])
            # Vérifier si la colonne total = cumsum ou sum de la colonne montant
            # Proxy simple : comparer valeur max du total vs somme des montants
            total_sum = numeric_tc.dropna().sum()
            amount_sum = numeric_ac.dropna().sum()
            if abs(total_sum) > 0 and abs(amount_sum - total_sum) / abs(total_sum) < 0.01:
                continue  # cohérent
            # Incohérence potentielle
            if abs(total_sum) > 0:
                triggered = True
                detail = f"Colonne '{tc}' potentiellement agrégée sur données non nettoyées."
                return triggered, detail, len(df), [tc]

    return False, "Aucune agrégation prématurée détectée.", 0, []


def _detect_T06(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T06 — Zeros Used as Missing Values"""
    flagged_cols = []
    total_n = 0
    for col in _amount_columns(df):
        numeric = _to_numeric(df[col])
        zero_mask = (numeric == 0)
        n = int(zero_mask.sum())
        if n > 0 and _triggered(n / max(len(numeric.dropna()), 1)):
            flagged_cols.append(col)
            total_n += n
    triggered = bool(flagged_cols)
    detail = (
        f"{total_n} zéro(s) suspect(s) dans : {flagged_cols}" if triggered
        else "Aucun zéro suspect détecté dans les colonnes montant."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T07(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T07 — Totals Don't Match Line Items
    Compare la somme des lignes d'une colonne montant au total déclaré.
    """
    total_kw   = ["total", "grand_total", "subtotal"]
    line_kw    = ["amount", "revenue", "price", "cost", "line_amount", "item_amount"]

    total_cols = [c for c in df.columns if any(kw == c.lower() for kw in total_kw)]
    line_cols  = [c for c in df.columns if any(kw in c.lower() for kw in line_kw)]

    flagged = []
    total_n = 0

    for tc in total_cols:
        declared = _to_numeric(df[tc]).dropna()
        if declared.empty:
            continue
        declared_sum = declared.sum()
        for lc in line_cols:
            if lc == tc:
                continue
            computed = _to_numeric(df[lc]).dropna().sum()
            tolerance = 0.01
            declared_total = declared_sum
            if max(abs(declared_total), 1) > 0:
                discrepancy = abs(declared_total - computed) / max(abs(declared_total), 1)
                if discrepancy > tolerance:
                    flagged.append(f"{tc} vs {lc} (écart {discrepancy:.1%})")
                    total_n += 1

    triggered = bool(flagged)
    detail = (
        f"Incohérences total/lignes : {flagged}" if triggered
        else "Totaux cohérents avec les lignes détail."
    )
    flagged_cols = list({c for pair in flagged for c in pair.split(" vs ")[:2]})
    return triggered, detail, total_n, flagged_cols


def _detect_T08(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T08 — Inconsistent Rounding Across Systems
    Détecte des montants avec précisions décimales différentes dans la même colonne.
    """
    flagged_cols = []
    total_n = 0
    for col in _amount_columns(df):
        vals = df[col].dropna().astype(str).str.strip()
        # Extraire le nombre de décimales de chaque valeur
        decimals = vals.str.extract(r"\.(\d+)$")[0].dropna().str.len()
        if decimals.empty:
            continue
        unique_precisions = decimals.nunique()
        if unique_precisions > 2:  # plus de 2 niveaux de précision = incohérence
            flagged_cols.append(col)
            total_n += len(decimals)
    triggered = bool(flagged_cols)
    detail = (
        f"Précisions décimales incohérentes dans : {flagged_cols}" if triggered
        else "Arrondi cohérent dans les colonnes montant."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T09(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T09 — Legitimate Retroactive Transactions
    Proxy : présence d'une colonne 'retroactive', 'correction', 'adjustment'
    ou transactions avec dates significativement antérieures à la médiane.
    """
    retro_kw = ["retroactive", "retro", "correction", "adjustment", "restatement",
                "rectif", "annulation", "cancel"]
    retro_cols = [c for c in df.columns if any(kw in c.lower() for kw in retro_kw)]

    if retro_cols:
        triggered = True
        detail = f"Colonnes rétro-actives détectées sans audit trail : {retro_cols}"
        return triggered, detail, len(df), retro_cols

    # Proxy via dates : transaction_date très antérieure à la médiane
    date_cols = _date_columns(df)
    flagged_cols = []
    total_n = 0
    # TODO V2 : vectoriser cette détection pour gros volumes (>100k lignes)
    # via mask pandas. Acceptable pour V1 (prototype 500 lignes max).
    for col in date_cols:
        parsed = _to_date(df[col])
        valid = parsed.dropna()
        if len(valid) < 4:
            continue
        median_date = valid.median()
        # Rétro-actif = > 90 jours avant la médiane
        retro = valid[valid < (median_date - pd.Timedelta(days=90))]
        n = len(retro)
        if _triggered(n / len(valid)):
            flagged_cols.append(col)
            total_n += n

    triggered = bool(flagged_cols)
    detail = (
        f"Transactions rétro-actives (>90j avant médiane) dans : {flagged_cols}" if triggered
        else "Aucune transaction rétro-active détectée."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T10(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T10 — Accounting Rule Changes Over Time
    Proxy : colonne 'accounting_standard', 'norm', 'ifrs', 'gaap' avec
    valeurs multiples non versionnées ; ou absence totale de colonne standard.
    """
    standard_kw = ["accounting_standard", "norm", "ifrs", "gaap", "standard",
                   "referentiel", "pcg", "rule_version"]
    std_cols = [c for c in df.columns if any(kw in c.lower() for kw in standard_kw)]

    if std_cols:
        for col in std_cols:
            vals = df[col].dropna().astype(str).str.strip()
            if vals.nunique() > 1:
                triggered = True
                detail = f"Normes comptables multiples dans '{col}' : {vals.unique().tolist()}"
                return triggered, detail, vals.nunique(), [col]

    # Proxy : dataset multi-années sans colonne de standard → risque de rupture
    date_cols = _date_columns(df)
    for col in date_cols:
        parsed = _to_date(df[col])
        valid = parsed.dropna()
        if valid.empty:
            continue
        year_span = valid.dt.year.max() - valid.dt.year.min()
        if year_span >= 2 and not std_cols:
            triggered = True
            detail = (
                f"Données sur {year_span + 1} années dans '{col}' "
                f"sans colonne de norme comptable documentée."
            )
            return triggered, detail, int(year_span), [col]

    return False, "Aucun changement de règle comptable détecté.", 0, []


def _detect_T68(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T68 — Semantic Inconsistency — Country/Currency Mismatch"""
    country_kw  = ["country", "pays", "country_code", "nation"]
    currency_kw = ["currency", "devise", "currency_code", "curr"]

    country_cols  = [c for c in df.columns if any(kw in c.lower() for kw in country_kw)]
    currency_cols = [c for c in df.columns if any(kw in c.lower() for kw in currency_kw)]

    if not country_cols or not currency_cols:
        return False, "Colonnes country et/ou currency absentes — vérification impossible.", 0, []

    mismatches = []
    for cc in country_cols:
        for cu in currency_cols:
            countries  = df[cc].astype(str).str.strip().str.upper()
            currencies = df[cu].astype(str).str.strip().str.upper()
            common = (countries != "NAN") & (currencies != "NAN")
            for idx in df[common].index:
                c_country  = countries[idx]
                c_currency = currencies[idx]
                expected = COUNTRY_CURRENCY_MAP.get(c_country)
                if expected and c_currency not in expected:
                    mismatches.append(f"row {idx}: {c_country}→{c_currency} (attendu {expected})")

    n = len(mismatches)
    triggered = _triggered(n / max(len(df), 1))
    detail = (
        f"{n} incompatibilité(s) country/currency : {mismatches[:5]}{'...' if n > 5 else ''}"
        if triggered
        else "Cohérence country/currency vérifiée."
    )
    flagged_cols = country_cols + currency_cols if triggered else []
    return triggered, detail, n, flagged_cols


# ─────────────────────────────────────────────
# DÉTECTEURS — T57 à T69 (CrossSector)
# ─────────────────────────────────────────────

def _detect_T57(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T57 — Hidden Default / Placeholder Values
    Détecte sur la string BRUTE (avant conversion numérique) pour attraper
    '9999', '9999.0', '9999.00' et toutes leurs formes.
    """
    # Étendre les placeholders aux formes numériques courantes
    placeholder_extended = PLACEHOLDER_VALUES | {
        "9999.0", "9999.00", "99999.0", "99999.00",
        "-1.0", "-1.00", "999999.0", "999999.00",
    }
    flagged_cols = []
    total_n = 0
    for col in df.columns:
        series = df[col].dropna().astype(str).str.strip().str.lower()
        hits = series[series.isin(placeholder_extended)]
        n = len(hits)
        if n > 0 and _triggered(n / max(len(series), 1)):
            flagged_cols.append(col)
            total_n += n
    triggered = bool(flagged_cols)
    detail = (
        f"{total_n} valeur(s) placeholder dans : {flagged_cols}" if triggered
        else "Aucune valeur placeholder détectée."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T58(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T58 — Logical Duplicates (fuzzy — normalisation avant comparaison)"""
    flagged_cols = []
    total_n = 0
    for col in _string_columns(df):
        series = df[col].dropna().astype(str)
        # Normalisation : minuscules + strip + suppression ponctuation
        normalized = (
            series.str.lower()
                  .str.strip()
                  .str.replace(r"[^\w\s]", "", regex=True)
                  .str.replace(r"\s+", " ", regex=True)
        )
        # Doublons sur la version normalisée mais pas sur l'originale
        norm_dups = normalized.duplicated(keep=False)
        orig_dups = series.duplicated(keep=False)
        logical_dups = norm_dups & ~orig_dups
        n = int(logical_dups.sum())
        if n > 0 and _triggered(n / max(len(series), 1)):
            flagged_cols.append(col)
            total_n += n
    triggered = bool(flagged_cols)
    detail = (
        f"{total_n} doublon(s) logique(s) dans : {flagged_cols}" if triggered
        else "Aucun doublon logique détecté."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T59(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T59 — Over-Cleaning / Data Made Too Clean
    Proxy 1 : colonne avec très peu de valeurs distinctes par rapport au total
               (ratio distinct/total < 20%) dans des colonnes montant — signe
               que des extrêmes ont été agressivement supprimés ou tronqués.
    Proxy 2 : colonne numérique entièrement constante (std = 0).
    Proxy 3 : dataset avec placeholder systématique (ex: toutes valeurs = 9999).
    """
    flagged_cols = []
    total_n = 0

    for col in _amount_columns(df):
        series_raw = df[col].dropna().astype(str).str.strip()
        numeric = _to_numeric(df[col]).dropna()
        n_rows = len(numeric)
        if n_rows < 3:
            continue

        # Proxy 1 : trop peu de valeurs distinctes
        distinct_ratio = numeric.nunique() / n_rows
        if distinct_ratio < 0.20 and n_rows >= 5:
            flagged_cols.append(col)
            total_n += n_rows
            continue

        # Proxy 2 : std = 0 (colonne constante)
        if numeric.std() == 0:
            flagged_cols.append(col)
            total_n += n_rows
            continue

        # Proxy 3 : présence massive de la même valeur placeholder
        value_counts = series_raw.value_counts(normalize=True)
        if value_counts.iloc[0] > 0.60:
            flagged_cols.append(col)
            total_n += n_rows

    triggered = bool(flagged_cols)
    detail = (
        f"Over-cleaning probable dans : {flagged_cols} "
        f"(distribution anormalement uniforme ou constante)." if triggered
        else "Aucun over-cleaning détecté."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T60(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T60 — Temporal Logic Violated (end < start)
    Note : détecté normalement ici — pas de déduplication avec T79 de temporal_analyzer.
    La déduplication est gérée par aci_calculator.py.
    """
    date_cols = _date_columns(df)
    start_kw  = ["start", "begin", "from", "created", "open", "contract_start"]
    end_kw    = ["end", "close", "expir", "terminate", "contract_end", "finish"]

    starts = [c for c in date_cols if any(kw in c.lower() for kw in start_kw)]
    ends   = [c for c in date_cols if any(kw in c.lower() for kw in end_kw)]

    inversions = []
    total_n = 0
    for sc in starts:
        for ec in ends:
            if sc == ec:
                continue
            s_s = _to_date(df[sc])
            s_e = _to_date(df[ec])
            common = s_s.notna() & s_e.notna()
            inv = common & (s_e < s_s)
            n = int(inv.sum())
            if n > 0 and _triggered(n / max(common.sum(), 1)):
                inversions.append(f"{ec} < {sc}")
                total_n += n

    triggered = bool(inversions)
    detail = (
        f"Inversions temporelles : {inversions}" if triggered
        else "Aucune inversion temporelle détectée."
    )
    flagged_cols = list({c for pair in inversions for c in pair.replace(" < ", " ").split()})
    return triggered, detail, total_n, flagged_cols


def _detect_T61(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T61 — Business Rule Evolution Not Tracked
    Proxy : colonne 'rule_version', 'version', 'standard' avec valeurs multiples
    sans colonne 'effective_date' associée.
    """
    rule_kw = ["rule_version", "version", "standard", "policy", "referentiel",
               "accounting_rule", "norm_version"]
    eff_kw  = ["effective_date", "valid_from", "applicable_from", "date_application"]

    rule_cols = [c for c in df.columns if any(kw in c.lower() for kw in rule_kw)]
    eff_cols  = [c for c in df.columns if any(kw in c.lower() for kw in eff_kw)]

    flagged_cols = []
    total_n = 0
    for col in rule_cols:
        vals = df[col].dropna().astype(str)
        if vals.nunique() > 1 and not eff_cols:
            flagged_cols.append(col)
            total_n += vals.nunique()

    # Proxy secondaire : dataset multi-années + aucune colonne de version
    if not flagged_cols:
        date_cols = _date_columns(df)
        for col in date_cols:
            parsed = _to_date(df[col])
            valid  = parsed.dropna()
            if valid.empty:
                continue
            year_span = valid.dt.year.max() - valid.dt.year.min()
            if year_span >= 3 and not rule_cols and not eff_cols:
                flagged_cols.append(col)
                total_n += int(year_span)
                break

    triggered = bool(flagged_cols)
    detail = (
        f"Règles métier potentiellement non versionnées dans : {flagged_cols}" if triggered
        else "Aucun changement de règle métier non tracé détecté."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T62(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T62 — Uncontrolled Free-Text Fields
    Détecte des colonnes catégorielles avec un ratio de valeurs uniques > 50 %.
    """
    flagged_cols = []
    total_n = 0
    for col in _string_columns(df):
        # Exclure colonnes qui sont manifestement des IDs ou des textes libres
        if any(kw in col.lower() for kw in ["id", "description", "note", "comment",
                                              "email", "address", "adresse"]):
            continue
        vals = df[col].dropna().astype(str)
        if len(vals) < 5:
            continue
        unique_ratio = vals.nunique() / len(vals)
        if unique_ratio > 0.50 and vals.nunique() > 5:
            flagged_cols.append(col)
            total_n += vals.nunique()
    triggered = bool(flagged_cols)
    detail = (
        f"Champs texte libre non contrôlés (>50% uniques) : {flagged_cols}" if triggered
        else "Aucun champ texte libre non contrôlé détecté."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T63(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T63 — Missing Not At Random (MNAR)
    Proxy 1 : colonne avec taux de NaN/vide structurellement plus élevé
               que la moyenne des autres colonnes (> 2× moyenne + seuil).
    Proxy 2 : colonne où les valeurs manquantes sont concentrées sur
               certaines valeurs d'une colonne catégorielle (corrélation).
    """
    flagged_cols = []
    total_n = 0

    def _normalize_missing(series: pd.Series) -> pd.Series:
        return series.replace(
            ["", "nan", "NaN", "N/A", "None",
             "null", "NULL", "none"],
            pd.NA
        )

    null_rates = {
        col: _normalize_missing(df[col]).isna().sum() / max(len(df), 1)
        for col in df.columns
    }
    avg_null = sum(null_rates.values()) / max(len(null_rates), 1)

    for col, null_ratio in null_rates.items():
        if null_ratio >= TRIGGER_RATIO and null_ratio < 1.0:
            if null_ratio > max(2 * avg_null + TRIGGER_RATIO, TRIGGER_RATIO):
                flagged_cols.append(col)
                total_n += int(_normalize_missing(df[col]).isna().sum())

    # Proxy 2 : colonne avec une seule valeur dominante représentant > 70%
    # combinée à des NaN dans des lignes spécifiques (pattern non-aléatoire)
    if not flagged_cols:
        for col in _string_columns(df):
            vals = _normalize_missing(df[col]).fillna("__MISSING__").astype(str)
            missing_mask = vals == "__MISSING__"
            if missing_mask.sum() == 0:
                continue
            # Vérifier si les NaN sont concentrés sur un sous-groupe
            non_null_vals = vals[~missing_mask]
            if non_null_vals.empty:
                continue
            dominant_ratio = non_null_vals.value_counts(normalize=True).iloc[0]
            if dominant_ratio > 0.70 and missing_mask.sum() >= 1:
                # Les NaN coexistent avec une valeur très dominante = pattern suspect
                flagged_cols.append(col)
                total_n += int(missing_mask.sum())

    triggered = bool(flagged_cols)
    detail = (
        f"Valeurs manquantes non aléatoires dans : {flagged_cols}" if triggered
        else "Aucun pattern MNAR détecté."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T64(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T64 — Name-Email Semantic Mismatch
    Détecte les paires (nom, email) où le nom ne correspond pas à l'email.
    """
    name_kw  = ["customer_name", "client_name", "name", "nom", "full_name", "contact_name"]
    email_kw = ["email", "mail", "courriel", "e_mail"]

    name_cols  = [c for c in df.columns if any(kw in c.lower() for kw in name_kw)]
    email_cols = [c for c in df.columns if any(kw in c.lower() for kw in email_kw)]

    if not name_cols or not email_cols:
        return False, "Colonnes name et/ou email absentes — vérification impossible.", 0, []

    mismatches = []
    for nc in name_cols:
        for ec in email_cols:
            names  = df[nc].fillna("").astype(str).str.strip()
            emails = df[ec].fillna("").astype(str).str.strip()
            for idx in df.index:
                name  = names[idx].lower()
                email = emails[idx].lower().split("@")[0] if "@" in emails[idx] else ""
                if not name or not email:
                    continue
                # Extraire prénom/nom (premier et dernier mot)
                name_parts = re.findall(r"[a-z]+", name)
                email_parts = re.findall(r"[a-z]+", email)
                # Mismatch = aucun mot du nom présent dans la partie locale de l'email
                if name_parts and email_parts:
                    overlap = set(name_parts) & set(email_parts)
                    if not overlap and len(email_parts) > 0:
                        mismatches.append(idx)

    n = len(mismatches)
    triggered = _triggered(n / max(len(df), 1))
    detail = (
        f"{n} incompatibilité(s) nom/email." if triggered
        else "Cohérence nom/email vérifiée."
    )
    flagged_cols = name_cols + email_cols if triggered else []
    return triggered, detail, n, flagged_cols


def _detect_T65(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T65 — Invalid Email Format"""
    email_kw = ["email", "mail", "courriel", "e_mail"]
    email_cols = [c for c in df.columns if any(kw in c.lower() for kw in email_kw)]

    flagged_cols = []
    total_n = 0
    for col in email_cols:
        series = df[col].dropna().astype(str).str.strip()
        series = series[series != ""]
        invalid = series[~series.str.match(EMAIL_PATTERN)]
        n = len(invalid)
        if n > 0 and _triggered(n / max(len(series), 1)):
            flagged_cols.append(col)
            total_n += n

    triggered = bool(flagged_cols)
    detail = (
        f"{total_n} email(s) invalide(s) dans : {flagged_cols}" if triggered
        else "Formats email valides."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T66(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T66 — Type Mismatch: Numeric in Text Field
    Détecte des valeurs numériques dans des colonnes attendues comme catégorielles.
    Une colonne est suspecte si elle contient un MIX de valeurs numériques
    et non-numériques (ni purement numérique, ni purement textuelle).

    Correction : l'exclusion des mots-clés utilise une correspondance sur
    des TOKENS entiers (délimiteurs _ ou début/fin de chaîne) pour éviter
    d'exclure 'status' à cause du sous-mot 'at' de DATE_KEYWORDS.
    """
    flagged_cols = []
    total_n = 0
    exclude_kw = AMOUNT_KEYWORDS + DATE_KEYWORDS + ["id", "code", "ref", "key"]

    def _col_matches_kw(col_name: str, kws: list) -> bool:
        col_lower = col_name.lower()
        for kw in kws:
            # Correspondance sur token entier uniquement
            if re.search(rf"(^|_){re.escape(kw.lower())}($|_)", col_lower):
                return True
        # NOTE : Les colonnes comme created_at, updated_at, deleted_at
        # sont correctement classées comme dates via le pattern (^|_)at($|_)
        # Elles sont donc EXCLUES de l'analyse T66 (numeric in text field).
        # Comportement voulu — ne pas modifier.
        return False

    cat_cols = [
        c for c in _string_columns(df)
        if not _col_matches_kw(c, exclude_kw)
    ]
    for col in cat_cols:
        series = df[col].dropna().astype(str).str.strip()
        if series.empty:
            continue
        numeric_hits = series[series.str.match(NUMERIC_PATTERN)]
        n_num = len(numeric_hits)
        n_total = len(series)
        num_ratio = n_num / n_total if n_total > 0 else 0

        # Mismatch si : au moins 5% de numériques ET colonne non-majoritairement numérique
        if _triggered(num_ratio) and num_ratio < 0.85:
            flagged_cols.append(col)
            total_n += n_num
    triggered = bool(flagged_cols)
    detail = (
        f"{total_n} valeur(s) numérique(s) dans colonnes texte : {flagged_cols}" if triggered
        else "Aucun type mismatch numérique/texte détecté."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T67(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T67 — Type Mismatch Inverse: Text in Numeric Field"""
    flagged_cols = []
    total_n = 0
    for col in _amount_columns(df):
        series = df[col].dropna().astype(str).str.strip()
        numeric = _to_numeric(df[col])
        non_numeric = series[numeric.isna() & (series != "") & (series.str.lower() != "nan")]
        n = len(non_numeric)
        if n > 0 and _triggered(n / max(len(series), 1)):
            flagged_cols.append(col)
            total_n += n
    triggered = bool(flagged_cols)
    detail = (
        f"{total_n} valeur(s) texte dans colonnes numériques : {flagged_cols}" if triggered
        else "Aucun type mismatch texte/numérique détecté."
    )
    return triggered, detail, total_n, flagged_cols


def _detect_T69(df: pd.DataFrame) -> tuple[bool, str, int, list[str]]:
    """T69 — Statistical Outlier Detection
    Méthode primaire  : Z-score > 3 (standard statistique).
    Méthode secondaire: IQR — valeur < Q1 - 3×IQR ou > Q3 + 3×IQR.
    Les deux méthodes sont testées ; un seul déclenchement suffit.
    """
    flagged_cols = []
    total_n = 0
    for col in _amount_columns(df):
        numeric = _to_numeric(df[col]).dropna()
        if len(numeric) < 4:
            continue
        mean = numeric.mean()
        std  = numeric.std()

        # Méthode 1 : Z-score
        n_zscore = 0
        if std > 0:
            z_scores = (numeric - mean).abs() / std
            n_zscore = int((z_scores > 3).sum())

        # Méthode 2 : IQR
        q1, q3 = numeric.quantile(0.25), numeric.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            lower = q1 - 3 * iqr
            upper = q3 + 3 * iqr
            n_iqr = int(((numeric < lower) | (numeric > upper)).sum())
        else:
            n_iqr = 0

        n = max(n_zscore, n_iqr)
        if n > 0 and _triggered(n / max(len(numeric), 1)):
            flagged_cols.append(col)
            total_n += n

    triggered = bool(flagged_cols)
    detail = (
        f"{total_n} outlier(s) (Z>3 ou IQR×3) dans : {flagged_cols}" if triggered
        else "Aucun outlier statistique détecté."
    )
    return triggered, detail, total_n, flagged_cols


# ─────────────────────────────────────────────
# CALCUL DQS
# ─────────────────────────────────────────────

def _compute_dqs(
    triggered_traps: list[dict],
    total_rows: int,
    total_cols: int,
) -> tuple[float, list[dict]]:
    """
    DQS = max(0, 100 − Σ(penalty_i × severity_mult × coverage_factor))
    """
    breakdown = []
    total_deduction = 0.0

    for t in triggered_traps:
        penalty        = t["penalty"]
        occurrences    = t.get("occurrences", 0)
        flagged_cols   = t.get("flagged_cols", [])

        sev_mult   = _severity_mult(occurrences, total_rows)
        cov_factor = _coverage_factor(flagged_cols, total_cols)
        deduction  = round(penalty * sev_mult * cov_factor, 4)

        breakdown.append({
            "id":             t["id"],
            "label":          t.get("label", ""),
            "penalty":        penalty,
            "occurrences":    occurrences,
            "severity_mult":  round(sev_mult, 4),
            "coverage_factor": cov_factor,
            "deduction":      deduction,
        })
        total_deduction += deduction

    dqs = max(0.0, round(100.0 - total_deduction, 2))
    return dqs, breakdown


# ─────────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────────

def analyze(
    df: pd.DataFrame,
    yaml_path: str | Path = "data/traps_catalog.yaml",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Analyse un DataFrame à la recherche des 23 traps Finance (T01–T10 + T57–T69).

    Paramètres
    ----------
    df : pd.DataFrame
        Dataset à analyser.
    yaml_path : str | Path
        Chemin vers traps_catalog.yaml.

    Retourne
    --------
    (df, log_dict) :
        df       — DataFrame original inchangé.
        log_dict — dict structuré pour le Transparency Ledger.
    """
    traps_catalog = load_finance_traps(yaml_path)

    detectors = {
        "T01": _detect_T01,
        "T02": _detect_T02,
        "T03": _detect_T03,
        "T04": _detect_T04,
        "T05": _detect_T05,
        "T06": _detect_T06,
        "T07": _detect_T07,
        "T08": _detect_T08,
        "T09": _detect_T09,
        "T10": _detect_T10,
        "T68": _detect_T68,
        "T57": _detect_T57,
        "T58": _detect_T58,
        "T59": _detect_T59,
        "T60": _detect_T60,
        "T61": _detect_T61,
        "T62": _detect_T62,
        "T63": _detect_T63,
        "T64": _detect_T64,
        "T65": _detect_T65,
        "T66": _detect_T66,
        "T67": _detect_T67,
        "T69": _detect_T69,
    }

    all_results: dict[str, dict] = {}
    triggered_list: list[dict] = []

    for trap_id in FINANCE_TRAP_IDS:
        detector = detectors[trap_id]
        trap_meta = traps_catalog[trap_id]
        try:
            triggered, detail, occurrences, flagged_cols = detector(df)
            result = {
                "id":           trap_id,
                "label":        trap_meta["label"],
                "criticality":  trap_meta["criticality"],
                "penalty":      trap_meta["penalty"],
                "triggered":    triggered,
                "detail":       detail,
                "occurrences":  occurrences,
                "flagged_cols": flagged_cols,
                "mental_rule":  trap_meta.get("mental_rule", ""),
            }
        except Exception as exc:
            result = {
                "id":          trap_id,
                "label":       trap_meta.get("label", trap_id),
                "criticality": trap_meta.get("criticality", "?"),
                "penalty":     trap_meta.get("penalty", 0),
                "triggered":   False,
                "detail":      f"Erreur détecteur : {exc}",
                "occurrences": 0,
                "flagged_cols": [],
                "mental_rule": "",
            }

        all_results[trap_id] = result
        if result["triggered"]:
            triggered_list.append(result)

    dqs, dqs_breakdown = _compute_dqs(triggered_list, len(df), len(df.columns))

    log_entry: dict[str, Any] = {
        "analyzer":        "finance_analyzer",
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "total_rows":      len(df),
        "total_columns":   len(df.columns),
        "traps_checked":   len(FINANCE_TRAP_IDS),
        "traps_triggered": triggered_list,
        "traps_count":     len(triggered_list),
        "all_results":     all_results,
        "dqs":             dqs,
        "dqs_breakdown": {
            "base_score":     100,
            "deductions":     dqs_breakdown,
            "total_deduction": round(sum(b["deduction"] for b in dqs_breakdown), 4),
            "final_dqs":      dqs,
        },
    }

    return df, log_entry
