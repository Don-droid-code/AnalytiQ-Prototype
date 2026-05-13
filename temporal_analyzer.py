"""
temporal_analyzer.py — AnalytiQ Pro
====================================
Détecte les 22 temporal traps (T70–T91) dans un DataFrame
et calcule le TCS (Temporal Confidence Score).

Formule TCS officielle (CDC V1.5 §11) :
    TCS = max(0, 100 − Σ pénalités − malus cas spéciaux)

Pénalités par criticité :
    Critical → −20 pts
    High     → −12 pts
    Medium   → −7 pts
    Low      → −3 pts

Cas spéciaux :
    T70 : pénalité doublée si colonne financière détectée
    T89 : pénalité additionnelle de −5 pts si triggered
    T91 : TCS plafonné à 30 si triggered

Signature obligatoire : analyze(df, yaml_path) → (df, log_entry_dict)

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

import pandas as pd
import yaml

warnings.filterwarnings("ignore", category=UserWarning)


# ─────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────

PENALTY_MAP = {
    "Critical": 20,
    "High": 12,
    "Medium": 7,
    "Low": 3,
}

# Mots-clés qui qualifient une colonne comme "financière" (pour T70 doublement)
FINANCIAL_COLUMN_KEYWORDS = [
    "amount", "revenue", "price", "cost", "salary", "wage", "payment",
    "balance", "invoice", "fee", "tax", "total", "profit", "loss",
    "montant", "salaire", "revenu", "prix", "solde", "facture",
]

# Epoch Unix (T72)
UNIX_EPOCH = pd.Timestamp("1970-01-01")

# MySQL zero-date string (T73)
MYSQL_ZERO_DATE = "0000-00-00"

# Seuil de retard en jours pour late-arriving data (T82)
LATE_ARRIVING_THRESHOLD_DAYS = 30

# Seuil d'écart en jours entre accounting_date et transaction_date (T83)
ACCT_TX_GAP_DAYS = 7

# Ratio minimal de valeurs "suspectes" pour déclencher un trap (5 %)
TRIGGER_RATIO = 0.05


# ─────────────────────────────────────────────
# CHARGEMENT DU CATALOGUE
# ─────────────────────────────────────────────

def load_temporal_traps(yaml_path: str | Path) -> dict[str, dict]:
    """
    Charge traps_catalog.yaml et retourne un dict {trap_id: trap_dict}
    pour les 22 temporal traps T70–T91 uniquement.
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        catalog = yaml.safe_load(f)

    traps = {}
    for trap in catalog.get("temporal_traps", []):
        traps[trap["id"]] = trap

    if len(traps) != 22:
        raise ValueError(
            f"traps_catalog.yaml : 22 temporal traps attendus, {len(traps)} trouvés."
        )
    return traps


# ─────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────

def _date_columns(df: pd.DataFrame) -> list[str]:
    """Retourne les colonnes dont le dtype est datetime ou dont le nom suggère une date."""
    date_kw = ["date", "time", "dt", "ts", "at", "on", "day", "created", "updated",
               "start", "end", "birth", "hire", "delivery", "order", "expir",
               "posted", "accounting", "transaction", "fiscal", "period", "snapshot",
               "event", "load", "ingested", "arrival"]
    cols = []
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            cols.append(col)
        elif any(kw in col.lower() for kw in date_kw):
            cols.append(col)
    return list(dict.fromkeys(cols))  # dédoublonnage en gardant l'ordre


def _is_financial_column(col_name: str) -> bool:
    return any(kw in col_name.lower() for kw in FINANCIAL_COLUMN_KEYWORDS)


def _ratio_above(series: pd.Series, condition: pd.Series) -> float:
    """Ratio de lignes remplissant condition sur total non-nul."""
    total = len(series.dropna())
    if total == 0:
        return 0.0
    return condition.sum() / total


def _try_parse_dates(series: pd.Series) -> pd.Series:
    """
    Tente de parser une série en datetime ; retourne NaT si impossible.
    infer_datetime_format est déprécié depuis pandas 2.0 — retiré.
    On essaie le parse standard, puis dayfirst=True en fallback si le
    premier taux de réussite est < 20% (séries avec formats slash mixtes).
    """
    try:
        parsed = pd.to_datetime(series, errors="coerce")
        total = len(series.dropna())
        if total > 0 and parsed.notna().sum() / total < 0.2:
            alt = pd.to_datetime(series, errors="coerce", dayfirst=True)
            if alt.notna().sum() > parsed.notna().sum():
                return alt
        return parsed
    except Exception:
        return pd.Series([pd.NaT] * len(series), index=series.index)


def _coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _triggered(ratio: float) -> bool:
    return ratio >= TRIGGER_RATIO


# ─────────────────────────────────────────────
# DÉTECTEURS — un par trap T70–T91
# ─────────────────────────────────────────────

def _detect_T70(df: pd.DataFrame) -> tuple[bool, str, bool]:
    """
    T70 — MDY vs DMY Ambiguous Format
    Détecte des dates slash-séparées où jour et mois sont tous deux ≤ 12
    (ambiguïté MM/DD vs DD/MM impossible à lever sans métadonnées).
    Retourne (triggered, detail, is_on_financial_col).
    """
    ambiguous_count = 0
    total_count = 0
    on_financial = False
    detail_cols = []

    slash_pattern = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")

    # Vérifier si le DataFrame contient au moins une colonne financière
    # (indépendamment des colonnes date) — c'est la condition du doublement T70
    on_financial = any(_is_financial_column(c) for c in df.columns)

    for col in df.columns:
        series = df[col].dropna().astype(str)
        slash_vals = series[series.str.match(slash_pattern)]
        if slash_vals.empty:
            continue
        total_count += len(slash_vals)
        # Ambiguïté : extraire MM et DD, vérifier que les deux ≤ 12
        def is_ambiguous(v):
            parts = v.split("/")
            if len(parts) < 2:
                return False
            try:
                a, b = int(parts[0]), int(parts[1])
                return a <= 12 and b <= 12
            except ValueError:
                return False
        amb = slash_vals[slash_vals.apply(is_ambiguous)]
        if not amb.empty:
            ambiguous_count += len(amb)
            detail_cols.append(col)

    if total_count == 0:
        return False, "Aucune date slash-séparée trouvée.", False

    ratio = ambiguous_count / total_count if total_count else 0
    triggered = _triggered(ratio)
    detail = (
        f"{ambiguous_count}/{total_count} valeurs date ambiguës MDY/DMY "
        f"dans colonnes : {detail_cols}" if triggered
        else f"Ratio ambiguïté {ratio:.1%} — sous le seuil."
    )
    return triggered, detail, on_financial


def _detect_T71(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T71 — Excel Date Serial Offset
    Détecte des entiers dans [1, 2958465] dans des colonnes date.
    """
    flagged_cols = []
    for col in _date_columns(df):
        series = df[col].dropna()
        if pd.api.types.is_integer_dtype(series) or pd.api.types.is_float_dtype(series):
            numeric = _coerce_numeric(series)
            excel_range = numeric[(numeric >= 1) & (numeric <= 2_958_465)]
            if _triggered(_ratio_above(series, numeric.between(1, 2_958_465))):
                flagged_cols.append(col)
        else:
            # Colonne string : chercher des entiers 5 chiffres
            str_series = series.astype(str)
            excel_like = str_series[str_series.str.match(r"^\d{5}$")]
            if _triggered(len(excel_like) / max(len(series), 1)):
                flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Sérials Excel détectés dans : {flagged_cols}" if triggered
        else "Aucun sériaux Excel détecté."
    )
    return triggered, detail


def _detect_T72(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T72 — Unix Epoch Sentinel (1970-01-01)
    Détecte des valeurs 1970-01-01 ou timestamp=0 dans des colonnes date.
    """
    flagged_cols = []
    for col in _date_columns(df):
        parsed = _try_parse_dates(df[col])
        epoch_mask = parsed == UNIX_EPOCH
        if _triggered(_ratio_above(parsed, epoch_mask)):
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Epoch Unix 1970-01-01 détectée dans : {flagged_cols}" if triggered
        else "Aucun sentinel epoch Unix détecté."
    )
    return triggered, detail


def _detect_T73(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T73 — MySQL Zero-Date (0000-00-00)
    """
    flagged_cols = []
    for col in _date_columns(df):
        series = df[col].dropna().astype(str)
        zero_mask = series.str.startswith("0000")
        if _triggered(_ratio_above(series, zero_mask)):
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"MySQL zero-date 0000-00-00 dans : {flagged_cols}" if triggered
        else "Aucune zero-date MySQL détectée."
    )
    return triggered, detail


def _detect_T74(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T74 — ISO 8601 Non-Compliance (Week / Ordinal)
    Détecte des formats semaine ISO (2024-W03-2) ou ordinaux (2024-019).
    """
    week_pattern = re.compile(r"^\d{4}-W\d{2}-\d$")
    ordinal_pattern = re.compile(r"^\d{4}-\d{3}$")
    flagged_cols = []
    for col in df.columns:
        series = df[col].dropna().astype(str)
        hits = series[series.str.match(week_pattern) | series.str.match(ordinal_pattern)]
        if _triggered(len(hits) / max(len(series), 1)):
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Formats semaine/ordinaux ISO dans : {flagged_cols}" if triggered
        else "Aucun format semaine ou ordinal ISO détecté."
    )
    return triggered, detail


def _detect_T75(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T75 — Two-Digit Year Ambiguity (Y2K Legacy)
    Détecte des années à 2 chiffres dans les dates.

    Règle : détecteur de FORMAT string → scan de toutes les colonnes
    object/string du DataFrame, pas uniquement _date_columns().
    Une colonne nommée 'two_digit_date' ou 'birth_year' contient des
    formats XX/XX/XX même si son nom ne contient pas de mot-clé date.
    """
    two_digit_pattern = re.compile(r"^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2}$")
    flagged_cols = []
    string_cols = df.select_dtypes(include=["object"]).columns
    for col in string_cols:
        series = df[col].dropna().astype(str)
        hits = series[series.str.match(two_digit_pattern)]
        if _triggered(len(hits) / max(len(series), 1)):
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Années à 2 chiffres dans : {flagged_cols}" if triggered
        else "Aucune année à 2 chiffres détectée."
    )
    return triggered, detail


def _detect_T76(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T76 — Timezone-Naive Timestamp Storage
    Détecte des colonnes datetime sans timezone info.
    """
    flagged_cols = []
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            if df[col].dt.tz is None:
                flagged_cols.append(col)
        else:
            # Chercher des timestamps avec 'T' et sans offset (+HH:MM ou Z)
            series = df[col].dropna().astype(str)
            has_T = series.str.contains("T", regex=False)
            has_tz = series.str.contains(r"[Zz]|[+\-]\d{2}:\d{2}", regex=True)
            naive = has_T & ~has_tz
            if _triggered(_ratio_above(series, naive)):
                flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Timestamps sans timezone dans : {flagged_cols}" if triggered
        else "Aucun timestamp naive détecté."
    )
    return triggered, detail


def _detect_T77(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T77 — DST Clock-Change Hour Duplication & Gap
    Détecte des timestamps locaux dans la plage horaire DST ambiguë
    (01:00–03:00) sans offset de timezone explicite.
    """
    dst_pattern = re.compile(r"T0[12]:.*(?![\+\-Z])")
    flagged_cols = []
    for col in _date_columns(df):
        series = df[col].dropna().astype(str)
        hits = series[series.str.contains(dst_pattern)]
        if _triggered(len(hits) / max(len(series), 1)):
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Timestamps DST ambigus dans : {flagged_cols}" if triggered
        else "Aucune ambiguïté DST détectée."
    )
    return triggered, detail


def _detect_T78(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T78 — Leap Year February 29 Handling
    Détecte des calculs de durée basés sur timedelta(365) plutôt que
    relativedelta. Proxy : chercher des durées de 365j exactement dans
    des colonnes de durée/ancienneté, ou des 1er mars dans des années
    bissextiles là où on attendrait un 29 fév.
    """
    flagged_cols = []
    for col in _date_columns(df):
        parsed = _try_parse_dates(df[col])
        valid = parsed.dropna()
        if valid.empty:
            continue
        # Proxy : dates = 29 fév dans années non-bissextiles
        feb29 = valid[(valid.dt.month == 2) & (valid.dt.day == 29)]
        invalid_leap = feb29[~feb29.dt.is_leap_year]
        if not invalid_leap.empty:
            flagged_cols.append(col)

    # Second proxy : colonnes numériques avec valeur exacte 365
    for col in df.columns:
        if col in flagged_cols:
            continue
        numeric = _coerce_numeric(df[col])
        if numeric.dropna().empty:
            continue
        exact_365 = (numeric == 365)
        if _triggered(_ratio_above(numeric, exact_365)):
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Problèmes potentiels année bissextile dans : {flagged_cols}" if triggered
        else "Aucun problème année bissextile détecté."
    )
    return triggered, detail


def _detect_T79(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T79 — End Date Before Start Date (Temporal Inversion)
    Cherche toutes les paires (start*, end*) et vérifie end >= start.
    """
    date_cols = _date_columns(df)
    parsed_cache = {col: _try_parse_dates(df[col]) for col in date_cols}

    start_kw = ["start", "begin", "from", "created", "hire", "order", "open"]
    end_kw   = ["end", "close", "expir", "delivery", "terminate", "finish"]

    starts = [c for c in date_cols if any(kw in c.lower() for kw in start_kw)]
    ends   = [c for c in date_cols if any(kw in c.lower() for kw in end_kw)]

    inversions = []
    for sc in starts:
        for ec in ends:
            s = parsed_cache[sc]
            e = parsed_cache[ec]
            common = s.notna() & e.notna()
            inv = common & (e < s)
            if _triggered(_ratio_above(s[common], inv[common])):
                inversions.append(f"{sc} > {ec}")

    triggered = bool(inversions)
    detail = (
        f"Inversions temporelles détectées : {inversions}" if triggered
        else "Aucune inversion temporelle (end < start) détectée."
    )
    return triggered, detail


def _detect_T80(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T80 — Future-Dated Records in Historical Datasets
    Détecte des dates postérieures à aujourd'hui dans des colonnes transactionnelles.

    Correction bug : le ratio est calculé sur les valeurs PARSÉES avec succès
    uniquement (dropna() après parse), pas sur l'ensemble de la colonne.
    Sans ça, une colonne mixte string/date produit beaucoup de NaT, dilue
    le ratio et fait rater le seuil même quand des dates futures sont présentes.
    """
    today = pd.Timestamp.now(tz=None).normalize()
    flagged_cols = []
    future_counts = {}

    for col in _date_columns(df):
        parsed = _try_parse_dates(df[col])
        # Ne travailler que sur les valeurs effectivement parsées
        parsed_valid = parsed.dropna()
        if parsed_valid.empty:
            continue
        future_mask = parsed_valid > today
        future_n = int(future_mask.sum())
        total_n  = len(parsed_valid)
        ratio    = future_n / total_n
        if _triggered(ratio):
            flagged_cols.append(col)
            future_counts[col] = f"{future_n}/{total_n} ({ratio:.0%})"

    triggered = bool(flagged_cols)
    detail = (
        f"Dates futures détectées dans : { {c: future_counts[c] for c in flagged_cols} }" if triggered
        else "Aucune date future détectée."
    )
    return triggered, detail


def _detect_T81(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T81 — Impossible Calendar Dates (ex. 30 fév, 31 avril)
    """
    flagged_cols = []
    for col in _date_columns(df):
        series = df[col].dropna()
        str_series = series.astype(str)
        parsed = _try_parse_dates(series)
        nat_mask = parsed.isna()
        # Les valeurs qui n'ont pas pu être parsées dans une colonne date = dates impossibles
        non_empty = str_series[str_series.str.match(r"\d")]
        if not non_empty.empty:
            nat_ratio = nat_mask[non_empty.index].sum() / len(non_empty)
            if _triggered(nat_ratio):
                flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Dates calendaires impossibles dans : {flagged_cols}" if triggered
        else "Aucune date calendaire impossible détectée."
    )
    return triggered, detail


def _detect_T82(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T82 — Late-Arriving Data / Backdated Records
    Cherche des paires (event_date, load_date) où l'écart > seuil.
    """
    date_cols = _date_columns(df)
    event_kw = ["event", "transaction", "sale", "order", "created", "occurred"]
    load_kw  = ["load", "ingested", "imported", "received", "processed", "inserted"]

    events = [c for c in date_cols if any(kw in c.lower() for kw in event_kw)]
    loads  = [c for c in date_cols if any(kw in c.lower() for kw in load_kw)]

    late_pairs = []
    for ec in events:
        for lc in loads:
            s_e = _try_parse_dates(df[ec])
            s_l = _try_parse_dates(df[lc])
            common = s_e.notna() & s_l.notna()
            if common.sum() == 0:
                continue
            gap = (s_l[common] - s_e[common]).dt.days
            late = gap > LATE_ARRIVING_THRESHOLD_DAYS
            if _triggered(_ratio_above(gap, late)):
                late_pairs.append(f"{ec} → {lc}")

    triggered = bool(late_pairs)
    detail = (
        f"Late-arriving data (>{LATE_ARRIVING_THRESHOLD_DAYS}j) : {late_pairs}" if triggered
        else "Aucun late-arriving data détecté."
    )
    return triggered, detail


def _detect_T83(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T83 — Accounting Date vs Transaction Date Mismatch
    Cherche des colonnes accounting_date et transaction_date avec écart > seuil.
    """
    date_cols = _date_columns(df)
    acct_kw = ["accounting", "posting", "posted", "ledger", "book"]
    tx_kw   = ["transaction", "sale_date", "trade", "effective"]

    accts = [c for c in date_cols if any(kw in c.lower() for kw in acct_kw)]
    txs   = [c for c in date_cols if any(kw in c.lower() for kw in tx_kw)]

    mismatches = []
    for ac in accts:
        for tc in txs:
            s_a = _try_parse_dates(df[ac])
            s_t = _try_parse_dates(df[tc])
            common = s_a.notna() & s_t.notna()
            if common.sum() == 0:
                continue
            gap = (s_a[common] - s_t[common]).dt.days.abs()
            mismatch = gap > ACCT_TX_GAP_DAYS
            if _triggered(_ratio_above(gap, mismatch)):
                mismatches.append(f"{ac} ↔ {tc}")

    triggered = bool(mismatches)
    detail = (
        f"Écarts accounting/transaction date (>{ACCT_TX_GAP_DAYS}j) : {mismatches}" if triggered
        else "Aucun écart accounting/transaction date détecté."
    )
    return triggered, detail


def _detect_T84(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T84 — Impossible Logistics Dates
    delivery_date < order_date ou shipment_date > receipt_date.
    """
    date_cols = _date_columns(df)
    order_kw    = ["order_date", "order_at", "ordered"]
    delivery_kw = ["delivery", "delivered", "receipt", "received", "arrival"]

    orders    = [c for c in date_cols if any(kw in c.lower() for kw in order_kw)]
    deliveries= [c for c in date_cols if any(kw in c.lower() for kw in delivery_kw)]

    impossible = []
    for oc in orders:
        for dc in deliveries:
            s_o = _try_parse_dates(df[oc])
            s_d = _try_parse_dates(df[dc])
            common = s_o.notna() & s_d.notna()
            if common.sum() == 0:
                continue
            inv = s_d[common] < s_o[common]
            if _triggered(_ratio_above(s_o[common], inv)):
                impossible.append(f"delivery({dc}) < order({oc})")

    triggered = bool(impossible)
    detail = (
        f"Dates logistiques impossibles : {impossible}" if triggered
        else "Aucune date logistique impossible détectée."
    )
    return triggered, detail


def _detect_T85(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T85 — Fiscal Year vs Calendar Year Offset
    Détecte des colonnes contenant des labels "FY" ou "fiscal" sans
    documentation explicite du mois de début.
    """
    fy_pattern = re.compile(r"\bFY\d{2,4}\b", re.IGNORECASE)
    flagged_cols = []
    for col in df.columns:
        series = df[col].dropna().astype(str)
        hits = series[series.str.contains(fy_pattern)]
        if _triggered(len(hits) / max(len(series), 1)):
            flagged_cols.append(col)
        elif "fiscal" in col.lower() or "fy" in col.lower():
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Labels fiscal year non documentés dans : {flagged_cols}" if triggered
        else "Aucun label FY ambigu détecté."
    )
    return triggered, detail


def _detect_T86(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T86 — Inconsistent Granularity in Date Column
    Détecte des colonnes mêlant daily / monthly / quarterly.
    """
    flagged_cols = []
    for col in _date_columns(df):
        series = df[col].dropna().astype(str)
        if series.empty:
            continue
        lengths = series.str.len()
        unique_lengths = lengths.unique()
        # Longueurs typiques : 10=daily (YYYY-MM-DD), 7=monthly, 4=yearly, 7=Q1 2024
        if len(unique_lengths) >= 2:
            q_pattern = series.str.match(r"^Q[1-4]\s+\d{4}$")
            monthly   = series.str.match(r"^\d{4}-\d{2}$")
            daily     = series.str.match(r"^\d{4}-\d{2}-\d{2}$")
            mixes = int(q_pattern.any()) + int(monthly.any()) + int(daily.any())
            if mixes >= 2:
                flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Granularités mixtes (jour/mois/trimestre) dans : {flagged_cols}" if triggered
        else "Aucune granularité mixte détectée."
    )
    return triggered, detail


def _detect_T87(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T87 — Periodicity Drift (5-Week Month)
    Détecte des comparaisons MoM sans normalisation par nombre de jours.
    Proxy : colonnes 'month' ou 'period' sans colonne 'num_days' ou 'trading_days'.
    """
    has_period_col = any(
        kw in col.lower()
        for col in df.columns
        for kw in ["month", "periode", "period", "mois"]
    )
    has_norm_col = any(
        kw in col.lower()
        for col in df.columns
        for kw in ["num_days", "trading_days", "business_days", "nb_jours"]
    )
    triggered = has_period_col and not has_norm_col
    detail = (
        "Colonne de période présente sans normalisation par nombre de jours." if triggered
        else "Normalisation par jours présente ou aucune colonne de période détectée."
    )
    return triggered, detail


def _detect_T88(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T88 — Reporting Period End-Date Labeling Convention
    Détecte des colonnes de période dont les valeurs sont parfois le
    premier jour du mois, parfois le dernier (convention incohérente).
    """
    flagged_cols = []
    for col in _date_columns(df):
        parsed = _try_parse_dates(df[col])
        valid = parsed.dropna()
        if valid.empty:
            continue
        is_first = (valid.dt.day == 1)
        is_last  = valid.dt.day == valid.dt.days_in_month
        if is_first.any() and is_last.any():
            # Deux conventions dans la même colonne
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Convention de labeling de période incohérente dans : {flagged_cols}" if triggered
        else "Aucune incohérence de labeling de période détectée."
    )
    return triggered, detail


def _detect_T89(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T89 — Holiday Calendar & Business Day Miscounting
    Proxy : colonnes de durée/SLA exprimées en jours calendaires
    sans colonne holiday_calendar ni business_day référencée.
    """
    has_sla_col = any(
        kw in col.lower()
        for col in df.columns
        for kw in ["sla", "due_date", "deadline", "payment_due", "echeance",
                   "processing_days", "lead_time", "delai"]
    )
    has_holiday_ref = any(
        kw in col.lower()
        for col in df.columns
        for kw in ["holiday", "business_day", "working_day", "jour_ouvre",
                   "jour_ferie", "calendrier"]
    )
    triggered = has_sla_col and not has_holiday_ref
    detail = (
        "Colonne SLA/délai présente sans référence à un calendrier de jours ouvrés." if triggered
        else "Aucun problème de calendrier jours ouvrés détecté."
    )
    return triggered, detail


def _detect_T90(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T90 — Snapshot vs Event Timestamp Confusion
    Détecte des colonnes nommées 'as_of_date' ou 'balance_date' (snapshot)
    joinées directement sur des colonnes event_ts sans range join.
    Proxy : présence simultanée de colonnes snapshot et event sans
    colonne effective_from/effective_to.
    """
    snapshot_kw = ["as_of", "balance_date", "snapshot", "as_at", "position_date"]
    event_kw    = ["event_ts", "transaction_date", "event_date", "occurred_at"]
    scd_kw      = ["effective_from", "effective_to", "valid_from", "valid_to"]

    has_snapshot = any(any(kw in c.lower() for kw in snapshot_kw) for c in df.columns)
    has_event    = any(any(kw in c.lower() for kw in event_kw)    for c in df.columns)
    has_scd      = any(any(kw in c.lower() for kw in scd_kw)      for c in df.columns)

    triggered = has_snapshot and has_event and not has_scd
    detail = (
        "Colonnes snapshot et event présentes sans colonnes SCD Type 2 (effective_from/to)." if triggered
        else "Aucune confusion snapshot/event détectée."
    )
    return triggered, detail


def _detect_T91(df: pd.DataFrame) -> tuple[bool, str]:
    """
    T91 — Aggregation Cutoff & Partial Period Bias
    Détecte si le mois/trimestre courant est inclus dans les données
    sans flag is_complete_period.
    """
    today = pd.Timestamp.now(tz=None)
    current_month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    flagged_cols = []

    has_complete_flag = any(
        kw in col.lower()
        for col in df.columns
        for kw in ["is_complete", "complete_period", "period_complete", "is_full"]
    )

    for col in _date_columns(df):
        parsed = _try_parse_dates(df[col])
        current_period = parsed >= current_month_start
        if current_period.any() and not has_complete_flag:
            flagged_cols.append(col)

    triggered = bool(flagged_cols)
    detail = (
        f"Période courante non terminée dans les données sans flag is_complete_period : {flagged_cols}" if triggered
        else "Aucune période partielle non flaggée détectée."
    )
    return triggered, detail


# ─────────────────────────────────────────────
# CALCUL DU TCS
# ─────────────────────────────────────────────

def _compute_tcs(
    triggered_traps: list[dict],
    t70_on_financial: bool,
    t89_triggered: bool,
    t91_triggered: bool,
) -> float:
    """
    Applique la formule TCS officielle avec cas spéciaux.

    TCS = max(0, 100 − Σ pénalités − malus cas spéciaux)
    Cas spéciaux :
        T70 sur colonne financière → pénalité × 2
        T89 → pénalité additionnelle de −5
        T91 → TCS plafonné à 30
    """
    total_penalty = 0.0

    for trap in triggered_traps:
        p = trap["penalty"]
        if trap["id"] == "T70" and t70_on_financial:
            p = p * 2  # doublement si colonne financière
        total_penalty += p

    if t89_triggered:
        total_penalty += 5  # pénalité additionnelle T89

    tcs = max(0.0, 100.0 - total_penalty)

    if t91_triggered:
        tcs = min(tcs, 30.0)  # plafond T91

    return round(tcs, 2)


# ─────────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────────

def analyze(
    df: pd.DataFrame,
    yaml_path: str | Path = "data/traps_catalog.yaml",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Analyse un DataFrame à la recherche des 22 temporal traps (T70–T91).

    Paramètres
    ----------
    df : pd.DataFrame
        Dataset à analyser.
    yaml_path : str | Path
        Chemin vers traps_catalog.yaml.

    Retourne
    --------
    (df, log_entry_dict) :
        df               — DataFrame original inchangé
        log_entry_dict   — dict structuré pour le Transparency Ledger :
            {
                "analyzer"         : "temporal_analyzer",
                "timestamp"        : "...",
                "total_rows"       : int,
                "total_columns"    : int,
                "date_columns"     : [...],
                "traps_triggered"  : [...],  # liste de dicts
                "traps_count"      : int,
                "tcs"              : float,
                "tcs_breakdown"    : {...},
            }
    """
    traps_catalog = load_temporal_traps(yaml_path)

    # --- Exécution de chaque détecteur ---
    detectors = {
        "T70": _detect_T70,
        "T71": _detect_T71,
        "T72": _detect_T72,
        "T73": _detect_T73,
        "T74": _detect_T74,
        "T75": _detect_T75,
        "T76": _detect_T76,
        "T77": _detect_T77,
        "T78": _detect_T78,
        "T79": _detect_T79,
        "T80": _detect_T80,
        "T81": _detect_T81,
        "T82": _detect_T82,
        "T83": _detect_T83,
        "T84": _detect_T84,
        "T85": _detect_T85,
        "T86": _detect_T86,
        "T87": _detect_T87,
        "T88": _detect_T88,
        "T89": _detect_T89,
        "T90": _detect_T90,
        "T91": _detect_T91,
    }

    results: dict[str, dict] = {}
    t70_on_financial = False
    t89_triggered = False
    t91_triggered = False

    for trap_id, detector in detectors.items():
        try:
            if trap_id == "T70":
                triggered, detail, on_fin = detector(df)
                t70_on_financial = on_fin
            else:
                triggered, detail = detector(df)

            trap_meta = traps_catalog[trap_id]
            results[trap_id] = {
                "id":          trap_id,
                "label":       trap_meta["label"],
                "category":    trap_meta.get("category", "Temporal"),
                "criticality": trap_meta["criticality"],
                "penalty":     trap_meta["penalty"],
                "triggered":   triggered,
                "detail":      detail,
                "mental_rule": trap_meta.get("mental_rule", ""),
            }

            if trap_id == "T89" and triggered:
                t89_triggered = True
            if trap_id == "T91" and triggered:
                t91_triggered = True

        except Exception as exc:
            results[trap_id] = {
                "id":        trap_id,
                "triggered": False,
                "detail":    f"Erreur détecteur : {exc}",
                "penalty":   traps_catalog[trap_id]["penalty"],
            }

    triggered_list = [v for v in results.values() if v.get("triggered")]

    tcs = _compute_tcs(
        triggered_list,
        t70_on_financial=t70_on_financial,
        t89_triggered=t89_triggered,
        t91_triggered=t91_triggered,
    )

    # Détail du breakdown pour transparence
    penalty_breakdown = []
    for t in triggered_list:
        p = t["penalty"]
        note = ""
        if t["id"] == "T70" and t70_on_financial:
            p = p * 2
            note = "× 2 (colonne financière)"
        if t["id"] == "T89":
            note = "+ 5 pts additionnels"
        penalty_breakdown.append({
            "id":        t["id"],
            "label":     t.get("label", ""),
            "penalty":   p,
            "note":      note,
        })
    if t89_triggered:
        penalty_breakdown.append({"id": "T89_extra", "label": "Pénalité additionnelle T89", "penalty": 5, "note": ""})

    log_entry = {
        "analyzer":        "temporal_analyzer",
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "total_rows":      len(df),
        "total_columns":   len(df.columns),
        "date_columns":    _date_columns(df),
        "traps_triggered": triggered_list,
        "traps_count":     len(triggered_list),
        "tcs":             tcs,
        "tcs_breakdown": {
            "base_score":       100,
            "penalties":        penalty_breakdown,
            "total_penalty":    sum(p["penalty"] for p in penalty_breakdown),
            "t91_cap_applied":  t91_triggered,
            "t70_doubled":      t70_on_financial,
            "t89_extra":        t89_triggered,
            "final_tcs":        tcs,
        },
        "all_results":     results,
    }

    return df, log_entry
