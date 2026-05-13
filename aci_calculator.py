"""
aci_calculator.py — AnalytiQ Pro
==================================
Agrège les scores des analyseurs et calcule l'ACI
(AnalytiQ Confidence Index).

Formule officielle ACI (immuable) :
    ACI = (TCS × 0.35) + (DQS_Finance × 0.30) + (AMS × 0.25) + (EXS × 0.10)

    - TCS         : retourné par temporal_analyzer.analyze()  → clé "tcs"
    - DQS_Finance : retourné par finance_analyzer.analyze()   → clé "dqs"
    - AMS         : calculé ici sur 8 items automatiques
    - EXS         : paramètre d'entrée (slider app.py, défaut 60.0)

Déduplication :
    T60 est retiré si T79, T80 ou T81 est présent dans les traps temporels.
    Le DQS Finance est alors recalculé sans T60.
    La déduplication utilise la clé "id" — cohérent avec les analyseurs.

HR non inclus dans ACI V1.
# V2 : inclure DQS_HR avec un poids à définir (ex. 0.15) en ajustant les
# poids existants ou en ajoutant une 5e composante.

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

ACI_WEIGHTS = {
    "tcs":         0.35,
    "dqs_finance": 0.30,
    "ams":         0.25,
    "exs":         0.10,
}

ACI_BANDS = [
    (85, "Elite"),
    (70, "Acceptable"),
    (55, "Moderate"),
    (40, "Low"),
    (0,  "Critical"),
]

# Règles de déduplication inter-analyseurs (figées)
# T60 est retiré si T79, T80 ou T81 est présent dans les traps temporels.
CONFLICT_RULES: dict[str, list[str]] = {
    "T60": ["T79", "T80", "T81"],
}

# ─────────────────────────────────────────────
# UTILITAIRES INTERNES
# ─────────────────────────────────────────────

def _aci_band(score: float) -> str:
    """Retourne la bande ACI pour un score donné."""
    for threshold, band in ACI_BANDS:
        if score >= threshold:
            return band
    return "Critical"


def _severity_mult(occurrences: int, total_rows: int) -> float:
    """occurrences / total_rows, plafonné à 1.0."""
    if total_rows == 0:
        return 0.0
    return min(1.0, occurrences / total_rows)


def _coverage_factor(flagged_cols: list[str], total_cols: int) -> float:
    """1.0 si > 25 % colonnes touchées, 0.5 sinon."""
    if total_cols == 0:
        return 0.5
    return 1.0 if (len(flagged_cols) / total_cols) > 0.25 else 0.5


def _cols_containing(df: pd.DataFrame, keywords: list[str]) -> list[str]:
    """Colonnes dont le nom contient au moins un mot-clé (sous-chaîne)."""
    result = []
    for col in df.columns:
        col_lower = col.lower()
        for kw in keywords:
            if kw.lower() in col_lower:
                result.append(col)
                break
    return result


# ─────────────────────────────────────────────
# DÉDUPLICATION
# ─────────────────────────────────────────────

def apply_deduplication(
    finance_traps: list[dict],
    temporal_traps: list[dict],
) -> tuple[list[dict], bool, list[dict]]:
    """
    Applique les règles de déduplication inter-analyseurs.

    Règle figée : retire T60 des traps Finance si T79, T80 ou T81
    est présent dans les traps Temporels.

    Paramètres
    ----------
    finance_traps  : liste des traps déclenchés par finance_analyzer
                     (chaque dict utilise la clé "id")
    temporal_traps : liste des traps déclenchés par temporal_analyzer
                     (chaque dict utilise la clé "id")

    Retourne
    --------
    (finance_traps_dedupliqués, deduplication_applied, deduplication_details)
    """
    temporal_ids = {t["id"] for t in temporal_traps}
    dedup_details: list[dict] = []
    traps_to_remove: set[str] = set()

    for base_trap, conflicting_traps in CONFLICT_RULES.items():
        present_conflicts = [c for c in conflicting_traps if c in temporal_ids]
        if present_conflicts:
            # Vérifier que le trap de base est bien déclenché dans Finance
            base_in_finance = any(t["id"] == base_trap for t in finance_traps)
            if base_in_finance:
                traps_to_remove.add(base_trap)
                dedup_details.append({
                    "removed_trap": base_trap,
                    "because":      f"{present_conflicts[0]} detected in temporal analysis",
                    "conflicts_found": present_conflicts,
                })

    if not traps_to_remove:
        return finance_traps, False, []

    filtered = [t for t in finance_traps if t["id"] not in traps_to_remove]
    return filtered, True, dedup_details


# ─────────────────────────────────────────────
# RECALCUL DQS APRÈS DÉDUPLICATION
# ─────────────────────────────────────────────

def _recompute_dqs_after_deduplication(
    original_finance_log: dict,
    traps_to_remove: list[str],
) -> float:
    """
    Recalcule le DQS Finance sans les traps supprimés par la déduplication.

    Même formule que finance_analyzer :
        DQS = max(0, 100 - Σ(penalty × severity_mult × coverage_factor))
        severity_mult   = min(occurrences / total_rows, 1.0)
        coverage_factor = 1.0 si colonnes_touchées > 25% total, 0.5 sinon

    Utilise les occurrences et flagged_cols originaux.
    """
    total_rows = original_finance_log.get("total_rows", 1)
    total_cols = original_finance_log.get("total_columns", 1)

    filtered = [
        t for t in original_finance_log["traps_triggered"]
        if t["id"] not in traps_to_remove
    ]

    total_deduction = 0.0
    for t in filtered:
        penalty      = t.get("penalty", 0)
        occurrences  = t.get("occurrences", 0)
        flagged_cols = t.get("flagged_cols", [])
        sev_mult     = _severity_mult(occurrences, total_rows)
        cov_factor   = _coverage_factor(flagged_cols, total_cols)
        total_deduction += round(penalty * sev_mult * cov_factor, 4)

    return max(0.0, round(100.0 - total_deduction, 2))


# ─────────────────────────────────────────────
# AMS — 8 ITEMS AUTOMATIQUES
# ─────────────────────────────────────────────

def _check_utc_dates(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Item 1 — UTC dates.
    Colonnes date parsées avec timezone UTC.
    # NOTE V2 : Échoue sur CSV sans timezone explicite.
    # À enrichir avec ingestion standardisée.
    """
    date_kw = ["date", "time", "ts", "at", "created", "updated",
               "posted", "accounting", "transaction"]
    date_cols = _cols_containing(df, date_kw)
    if not date_cols:
        return False, "Aucune colonne date trouvée."

    utc_count = 0
    for col in date_cols:
        parsed = pd.to_datetime(df[col], errors="coerce", utc=True)
        if parsed.notna().sum() > 0 and parsed.dt.tz is not None:
            utc_count += 1

    if utc_count > 0:
        return True, f"{utc_count}/{len(date_cols)} colonne(s) date avec timezone UTC."
    return False, "Aucune colonne date avec timezone UTC détectée."


def _has_version_column(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Item 2 — Colonne version.
    Chercher : version, rule_version, norm.
    """
    kws = ["version", "rule_version", "norm"]
    cols = _cols_containing(df, kws)
    if cols:
        return True, f"Colonne(s) version présente(s) : {cols}."
    return False, "Aucune colonne version/rule_version/norm trouvée."


def _check_no_t57(finance_log: dict) -> tuple[bool, str]:
    """
    Item 3 — Absence de placeholders.
    T57 non déclenché dans finance_log["traps_triggered"].
    """
    triggered_ids = {t["id"] for t in finance_log.get("traps_triggered", [])}
    if "T57" not in triggered_ids:
        return True, "T57 non déclenché — aucun placeholder détecté."
    return False, "T57 déclenché — des valeurs placeholder sont présentes."


def _has_primary_key(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Item 4 — Clé primaire.
    Chercher : id, _id, key, code dans noms de colonnes.
    """
    kws = ["id", "_id", "key", "code"]
    cols = _cols_containing(df, kws)
    if cols:
        return True, f"Clé primaire probable : {cols[0]}."
    return False, "Aucune colonne id/key/code trouvée."


def _has_audit_columns(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Item 5 — Colonnes de traçabilité.
    Chercher : created_at, updated_at, modified_at.
    """
    kws = ["created_at", "updated_at", "modified_at"]
    cols = _cols_containing(df, kws)
    if cols:
        return True, f"Colonne(s) audit présente(s) : {cols}."
    return False, "Aucune colonne created_at/updated_at/modified_at trouvée."


def _has_transaction_type(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Item 6 — Documentation des négatifs.
    Chercher : transaction_type, type, nature.
    """
    kws = ["transaction_type", "type", "nature"]
    cols = _cols_containing(df, kws)
    if cols:
        return True, f"Colonne(s) type de transaction présente(s) : {cols}."
    return False, "Aucune colonne transaction_type/type/nature trouvée."


def _has_retroactive_flag(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Item 7 — Flag retroactive.
    Chercher : retroactive, correction, adjustment.
    """
    kws = ["retroactive", "correction", "adjustment"]
    cols = _cols_containing(df, kws)
    if cols:
        return True, f"Flag rétroactif présent : {cols}."
    return False, "Aucun flag retroactive/correction/adjustment trouvé."


def _has_complete_period_flag(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Item 8 — Flag période complète.
    Chercher : is_complete_period, period_complete.
    """
    kws = ["is_complete_period", "period_complete"]
    cols = _cols_containing(df, kws)
    if cols:
        return True, f"Flag période complète présent : {cols}."
    return False, "Aucun flag is_complete_period/period_complete trouvé."


def _compute_ams(
    df: pd.DataFrame,
    finance_log: dict,
    yaml_path: str,
) -> tuple[float, list[dict]]:
    """
    Calcule l'AMS (Analytical Maturity Score) sur 8 items.

    AMS = (items_passed / 8) × 100

    RÈGLE ABSOLUE : items[] contient exactement 8 tuples — jamais 9.

    Paramètres
    ----------
    df           : DataFrame analysé
    finance_log  : log retourné par finance_analyzer.analyze()
    yaml_path    : chemin vers traps_catalog.yaml (non utilisé en V1,
                   réservé V2 pour items basés sur le catalog)

    Retourne
    --------
    (ams_score: float, checklist: list[dict])
    """
    # LISTE DES 8 ITEMS — NE PAS MODIFIER L'ORDRE NI LE NOMBRE
    items: list[tuple[str, tuple[bool, str]]] = [
        ("UTC dates",              _check_utc_dates(df)),
        ("Colonne version",        _has_version_column(df)),
        ("Absence T57",            _check_no_t57(finance_log)),
        ("Clé primaire",           _has_primary_key(df)),
        ("Colonnes traçabilité",   _has_audit_columns(df)),
        ("Documentation négatifs", _has_transaction_type(df)),
        ("Flag retroactive",       _has_retroactive_flag(df)),
        ("Flag période complète",  _has_complete_period_flag(df)),
    ]
    # Assertion de garde — garantit que la liste ne dérive jamais
    assert len(items) == 8, (
        f"ERREUR CRITIQUE : AMS attend exactement 8 items, {len(items)} trouvés."
    )

    checklist: list[dict] = []
    passed = 0
    for item_name, (result, reason) in items:
        checklist.append({
            "item":   item_name,
            "passed": result,
            "reason": reason,
        })
        if result:
            passed += 1

    ams_score = round((passed / 8) * 100, 2)
    return ams_score, checklist


# ─────────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────────

def calculate_aci(
    temporal_log: dict,
    finance_log: dict,
    df: pd.DataFrame,
    yaml_path: str = "data/traps_catalog.yaml",
    exs_score: float = 60.0,
    ams_override: float | None = None,
) -> dict[str, Any]:
    """
    Calcule l'ACI (AnalytiQ Confidence Index).

    Formule officielle (immuable) :
        ACI = (TCS × 0.35) + (DQS_Finance × 0.30) + (AMS × 0.25) + (EXS × 0.10)

    Paramètres
    ----------
    temporal_log  : log retourné par temporal_analyzer.analyze()
                    Clé attendue : "tcs", "traps_triggered"
    finance_log   : log retourné par finance_analyzer.analyze()
                    Clé attendue : "dqs", "traps_triggered"
    df            : DataFrame analysé (pour AMS)
    yaml_path     : chemin vers traps_catalog.yaml
    exs_score     : EXS paramètre d'entrée [0-100], défaut 60.0
                    Correspond au slider dans app.py.
    ams_override  : si fourni, remplace le calcul automatique de l'AMS.
                    Utile pour tests unitaires.

    Retourne
    --------
    {
        "aci":                    float,
        "aci_band":               str,
        "components": {
            "tcs":         {"score": float, "weight": 0.35, "weighted": float},
            "dqs_finance": {"score": float, "weight": 0.30, "weighted": float},
            "ams":         {"score": float, "weight": 0.25, "weighted": float},
            "exs":         {"score": float, "weight": 0.10, "weighted": float},
        },
        "deduplication_applied":  bool,
        "deduplication_details":  list[dict],
        "ams_checklist":          list[dict],
        "timestamp":              str,
    }

    Note V2 : HR non inclus dans ACI V1.
    # V2 : ajouter dqs_hr avec un poids à définir.
    """
    # ── 1. Extraire TCS ──────────────────────────────────────────────────
    tcs_score = float(temporal_log.get("tcs", 0.0))

    # ── 2. Extraire DQS Finance + appliquer déduplication ────────────────
    original_dqs_finance = float(finance_log.get("dqs", 0.0))
    finance_traps  = list(finance_log.get("traps_triggered", []))
    temporal_traps = list(temporal_log.get("traps_triggered", []))

    deduplicated_finance_traps, dedup_applied, dedup_details = apply_deduplication(
        finance_traps, temporal_traps
    )

    if dedup_applied:
        traps_removed = [d["removed_trap"] for d in dedup_details]
        dqs_finance = _recompute_dqs_after_deduplication(finance_log, traps_removed)
    else:
        dqs_finance = original_dqs_finance

    # ── 3. Calculer AMS ──────────────────────────────────────────────────
    if ams_override is not None:
        ams_score   = float(ams_override)
        ams_checklist = [{
            "item":   "AMS override",
            "passed": True,
            "reason": f"Valeur injectée manuellement : {ams_score}",
        }]
    else:
        ams_score, ams_checklist = _compute_ams(df, finance_log, yaml_path)

    # ── 4. EXS (paramètre d'entrée) ──────────────────────────────────────
    exs = float(exs_score)

    # ── 5. Calcul ACI ────────────────────────────────────────────────────
    tcs_weighted = round(tcs_score  * ACI_WEIGHTS["tcs"],         4)
    dqs_weighted = round(dqs_finance * ACI_WEIGHTS["dqs_finance"], 4)
    ams_weighted = round(ams_score   * ACI_WEIGHTS["ams"],         4)
    exs_weighted = round(exs         * ACI_WEIGHTS["exs"],         4)

    aci = round(tcs_weighted + dqs_weighted + ams_weighted + exs_weighted, 2)
    aci = max(0.0, min(100.0, aci))   # borner [0, 100]

    return {
        "aci":       aci,
        "aci_band":  _aci_band(aci),
        "components": {
            "tcs": {
                "score":    round(tcs_score, 2),
                "weight":   ACI_WEIGHTS["tcs"],
                "weighted": tcs_weighted,
            },
            "dqs_finance": {
                "score":    round(dqs_finance, 2),
                "weight":   ACI_WEIGHTS["dqs_finance"],
                "weighted": dqs_weighted,
            },
            "ams": {
                "score":    round(ams_score, 2),
                "weight":   ACI_WEIGHTS["ams"],
                "weighted": ams_weighted,
            },
            "exs": {
                "score":    round(exs, 2),
                "weight":   ACI_WEIGHTS["exs"],
                "weighted": exs_weighted,
            },
        },
        "deduplication_applied":  dedup_applied,
        "deduplication_details":  dedup_details,
        "ams_checklist":          ams_checklist,
        "timestamp":              datetime.now(timezone.utc).isoformat(),
    }
