"""
hr_analyzer.py — AnalytiQ Pro
================================
Détecte les 20 traps HR dans un DataFrame :
  - 8 traps HR         : T11–T18
  - 12 traps CrossSector : T57–T67 + T69

Architecture miroir de finance_analyzer.py.
Signature : HRAnalyzer(df).analyze() → dict

Formule DQS :
    DQS = max(0, 100 − Σ(penalty_i × severity_mult × coverage_factor))
    severity_mult   = min(occurrences / total_rows, 1.0)
    coverage_factor = 1.0 si colonnes_touchées > 25% total, 0.5 sinon

Règle architecturale :
    T60 détecté normalement — pas de déduplication ici.
    La déduplication inter-analyseurs est gérée par aci_calculator.py.

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

HR_TRAP_IDS = [
    "T11", "T12", "T13", "T14", "T15", "T16", "T17", "T18",   # 8 HR
    "T57", "T58", "T59", "T60", "T61", "T62", "T63",           # 12 CrossSector
    "T64", "T65", "T66", "T67", "T69",
]

TRIGGER_RATIO = 0.05

# Mots-clés colonnes salaire
SALARY_KEYWORDS = [
    "salary", "salaire", "wage", "compensation", "base_pay",
    "gross_salary", "net_salary", "monthly_salary", "remuneration",
    "pay", "earnings",
]

# Mots-clés colonnes âge / date naissance
AGE_KEYWORDS   = ["age", "dob", "birth_date", "date_of_birth", "birthdate",
                   "birth", "naissance", "date_naissance"]

# Mots-clés colonnes poste
TITLE_KEYWORDS = ["job_title", "title", "poste", "fonction", "position",
                  "role", "job_role", "designation", "intitule"]

# Mots-clés colonnes statut employé
STATUS_KEYWORDS = ["status", "employee_status", "statut", "etat", "active",
                   "employment_status"]

# Mots-clés colonnes date de fin / départ
TERMDATE_KEYWORDS = ["termination_date", "end_date", "date_fin",
                     "departure_date", "date_depart", "exit_date",
                     "offboarding_date"]

# Mots-clés ancienneté / date embauche
SENIORITY_KEYWORDS = ["seniority", "anciennete", "years_of_service",
                       "tenure", "experience_years", "annees_service"]
HIREDATE_KEYWORDS  = ["hire_date", "start_date", "date_embauche",
                       "joining_date", "date_entree", "employment_date"]

# Mots-clés historisation
MODIFIED_KEYWORDS = ["updated_at", "modified_date", "last_modified",
                      "date_modification", "changed_at", "updated_date"]

# Mots-clés colonnes catégorielles RH sensibles (pour T16)
CATEGORY_KEYS = ["department", "gender", "grade", "region", "contract_type",
                  "sexe", "categorie", "niveau"]

# Colonnes montant RH (pour CrossSector)
AMOUNT_KEYWORDS = SALARY_KEYWORDS + [
    "bonus", "prime", "allowance", "indemnite", "total_comp",
    "amount", "revenue", "cost",
]

# Colonnes date génériques (pour CrossSector)
DATE_KEYWORDS = [
    "date", "time", "ts", "at", "created", "updated", "posted",
    "accounting", "transaction", "start", "end", "effective", "hire",
    "birth", "termination", "departure", "joining",
]

# Valeurs placeholder
PLACEHOLDER_VALUES = {
    "9999", "99999", "999999", "-1", "-999", "0000", "n/a", "na",
    "null", "none", "undefined", "unknown", "missing", "#n/a",
    "9999.0", "99999.0", "-1.0",
}

# Pattern email valide
EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# Pattern numérique pur
NUMERIC_PATTERN = re.compile(r"^-?\d+([.,]\d+)?$")

# Patterns données sensibles (T14)
# Note : le pattern téléphone 06/07 est retiré car il matche tous les numéros normaux
# dans une colonne dédiée. Seuls les identifiants structurés sont détectés.
SENSITIVE_PATTERNS = [
    re.compile(r"\b[A-Z]{1,2}[0-9]{5,6}\b"),                  # CIN/CNIE marocain
    re.compile(r"\b[A-Z]{2}[0-9]{7}\b"),                       # Passeport
    re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{10,}\b"),         # IBAN complet (≥ 14 chars)
    re.compile(r"\b(?:\+212|00212)\s?[0-9]{9}\b"),            # Téléphone MA international
]


# ─────────────────────────────────────────────
# UTILITAIRES
# ─────────────────────────────────────────────

def _cols_matching(df: pd.DataFrame, keywords: list[str]) -> list[str]:
    """Colonnes dont le nom contient au moins un mot-clé (token entier)."""
    result = []
    for col in df.columns:
        col_lower = col.lower()
        for kw in keywords:
            if re.search(rf"(^|_){re.escape(kw.lower())}($|_|$)", col_lower) or \
               col_lower == kw.lower():
                result.append(col)
                break
    return result


def _string_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns
            if pd.api.types.is_object_dtype(df[c]) or
               pd.api.types.is_string_dtype(df[c])]


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", ".", regex=False).str.strip(),
        errors="coerce",
    )


def _to_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def _triggered(ratio: float) -> bool:
    return ratio >= TRIGGER_RATIO


def _severity_mult(occurrences: int, total_rows: int) -> float:
    if total_rows == 0:
        return 0.0
    return min(1.0, occurrences / total_rows)


def _coverage_factor(flagged_cols: list[str], total_cols: int) -> float:
    if total_cols == 0:
        return 0.5
    return 1.0 if (len(flagged_cols) / total_cols) > 0.25 else 0.5


def _normalize_missing(series: pd.Series) -> pd.Series:
    return series.replace(
        ["", "nan", "NaN", "N/A", "None", "null", "NULL", "none"],
        pd.NA
    )


def _col_matches_kw(col_name: str, kws: list[str]) -> bool:
    col_lower = col_name.lower()
    for kw in kws:
        if re.search(rf"(^|_){re.escape(kw.lower())}($|_)", col_lower):
            return True
    # NOTE : Les colonnes comme created_at, updated_at, deleted_at
    # sont correctement classées comme dates via le pattern (^|_)at($|_)
    # Elles sont donc EXCLUES de l'analyse T66 (numeric in text field).
    # Comportement voulu — ne pas modifier.
    return False


def _make_result(
    trap_id: str,
    label: str,
    detected: bool,
    occurrences: int,
    affected_columns: list[str],
    severity: str,
    penalty: int,
    details: str,
    mental_rule: str = "",
) -> dict[str, Any]:
    """Construit le dict résultat normalisé pour un trap."""
    return {
        "trap_id":          trap_id,
        "label":            label,
        "detected":         detected,
        "occurrences":      occurrences,
        "affected_columns": affected_columns,
        "severity":         severity,
        "penalty":          penalty,
        "details":          details,
        "mental_rule":      mental_rule,
    }


# ─────────────────────────────────────────────
# CLASSE PRINCIPALE
# ─────────────────────────────────────────────

class HRAnalyzer:
    """
    Analyseur HR — détecte les 20 traps (T11–T18 + T57–T67 + T69).

    Usage :
        analyzer = HRAnalyzer(df)
        results  = analyzer.analyze()
    """

    def __init__(self, df: pd.DataFrame, config: dict | None = None):
        self.df      = df
        self.config  = config or {}
        self._n_rows = len(df)
        self._n_cols = len(df.columns)

    # ─────────────────────────────────────────
    # MÉTHODE PRINCIPALE
    # ─────────────────────────────────────────

    def analyze(self) -> dict[str, Any]:
        """
        Lance les 20 détecteurs et retourne le rapport complet.

        Retourne
        --------
        {
            "department"    : "HR",
            "total_traps"   : 20,
            "traps_detected": int,
            "dqs_score"     : float,
            "timestamp"     : str,
            "results"       : [liste des 20 dicts détecteurs],
            "dqs_breakdown" : {...},
        }
        """
        detectors = [
            self._detect_T11,
            self._detect_T12,
            self._detect_T13,
            self._detect_T14,
            self._detect_T15,
            self._detect_T16,
            self._detect_T17,
            self._detect_T18,
            self._detect_T57,
            self._detect_T58,
            self._detect_T59,
            self._detect_T60,
            self._detect_T61,
            self._detect_T62,
            self._detect_T63,
            self._detect_T64,
            self._detect_T65,
            self._detect_T66,
            self._detect_T67,
            self._detect_T69,
        ]

        results = []
        for det in detectors:
            try:
                r = det()
            except Exception as exc:
                r = _make_result(
                    det.__name__.replace("_detect_", "").upper(),
                    "Erreur détecteur",
                    False, 0, [], "?", 0,
                    f"Exception : {exc}",
                )
            results.append(r)

        # Calcul DQS
        detected     = [r for r in results if r["detected"]]
        dqs, breakdown = self._compute_dqs(detected)

        return {
            "department":     "HR",
            "total_traps":    len(results),
            "traps_detected": len(detected),
            "dqs_score":      dqs,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "results":        results,
            "dqs_breakdown":  breakdown,
        }

    # ─────────────────────────────────────────
    # CALCUL DQS
    # ─────────────────────────────────────────

    def _compute_dqs(
        self, triggered: list[dict]
    ) -> tuple[float, dict]:
        deductions = []
        total_ded  = 0.0

        for r in triggered:
            pen      = r["penalty"]
            occ      = r["occurrences"]
            cols     = r["affected_columns"]
            sev_mult = _severity_mult(occ, self._n_rows)
            cov_fac  = _coverage_factor(cols, self._n_cols)
            ded      = round(pen * sev_mult * cov_fac, 4)
            deductions.append({
                "id":             r["trap_id"],
                "penalty":        pen,
                "occurrences":    occ,
                "severity_mult":  round(sev_mult, 4),
                "coverage_factor": cov_fac,
                "deduction":      ded,
            })
            total_ded += ded

        dqs = max(0.0, round(100.0 - total_ded, 2))
        breakdown = {
            "base_score":      100,
            "deductions":      deductions,
            "total_deduction": round(total_ded, 4),
            "final_dqs":       dqs,
        }
        return dqs, breakdown

    # ─────────────────────────────────────────
    # DÉTECTEURS HR — T11 à T18
    # ─────────────────────────────────────────

    def _detect_T11(self) -> dict:
        """T11 — Inconsistent or Impossible Age Values [Critical, 20]"""
        df = self.df
        flagged_cols = []
        total_n = 0

        # Colonnes âge numérique
        for col in _cols_matching(df, ["age"]):
            numeric = _to_numeric(df[col])
            impossible = numeric[(numeric < 16) | (numeric > 80)]
            n = int(impossible.count())
            if n > 0:
                flagged_cols.append(col)
                total_n += n

        # Colonnes date de naissance → calculer âge
        today = pd.Timestamp.now(tz=None).normalize()
        for col in _cols_matching(df, ["birth_date", "date_of_birth", "dob",
                                        "birthdate", "date_naissance", "birth"]):
            if col in flagged_cols:
                continue
            parsed = _to_date(df[col])
            ages = ((today - parsed).dt.days / 365.25).dropna()
            impossible = ages[(ages < 16) | (ages > 80)]
            n = int(impossible.count())
            if n > 0:
                flagged_cols.append(col)
                total_n += n

        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} âge(s) impossible(s) (<16 ou >80) dans : {flagged_cols}"
            if triggered else "Aucun âge impossible détecté."
        )
        return _make_result(
            "T11", "Inconsistent or Impossible Age Values",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "An age below 16 or above 80 in an employment context is a red flag — "
            "always validate against official HR records.",
        )

    def _detect_T12(self) -> dict:
        """T12 — Salary = 0 or Placeholder Value [Critical, 20]"""
        df = self.df
        flagged_cols = []
        total_n = 0
        placeholder_vals = {0, 9999, 99999, 999999, -1}

        for col in _cols_matching(df, SALARY_KEYWORDS):
            numeric = _to_numeric(df[col])
            mask = numeric.isin(placeholder_vals) | (numeric == 0)
            n = int(mask.sum())
            if n > 0 and _triggered(n / max(len(numeric.dropna()), 1)):
                flagged_cols.append(col)
                total_n += n

        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} salaire(s) nul(s) ou placeholder dans : {flagged_cols}"
            if triggered else "Aucun salaire nul ou placeholder détecté."
        )
        return _make_result(
            "T12", "Salary = 0 or Placeholder Value",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "A zero salary is either a data entry error or a placeholder. "
            "Never confuse it with an unpaid leave period.",
        )

    def _detect_T13(self) -> dict:
        """T13 — Job Title Category Explosion [Critical, 20]"""
        df = self.df
        flagged_cols = []
        total_n = 0

        for col in _cols_matching(df, TITLE_KEYWORDS):
            vals = df[col].dropna().astype(str).str.strip()
            n_unique = vals.nunique()
            n_rows   = len(vals)
            if n_rows == 0:
                continue
            ratio = n_unique / n_rows
            if ratio > 0.25 and n_unique > 20:
                flagged_cols.append(col)
                total_n += n_unique

        triggered = bool(flagged_cols)
        detail = (
            f"Explosion de titres : {total_n} valeurs uniques dans : {flagged_cols}"
            if triggered else "Aucune explosion de catégorie de poste détectée."
        )
        return _make_result(
            "T13", "Job Title Category Explosion",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "More than 30% unique job titles = an uncontrolled free-text field "
            "masquerading as a category.",
        )

    def _detect_T14(self) -> dict:
        """T14 — Sensitive Data Not Properly Masked [Critical, 20]"""
        df = self.df
        flagged_cols = []
        total_n = 0

        for col in _string_columns(df):
            series = df[col].dropna().astype(str)
            hits = 0
            for val in series:
                for pat in SENSITIVE_PATTERNS:
                    if pat.search(val.strip()):
                        hits += 1
                        break   # un seul hit par cellule
            if hits > 0:
                flagged_cols.append(col)
                total_n += hits

        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} valeur(s) sensible(s) non masquée(s) dans : {flagged_cols}"
            if triggered else "Aucune donnée sensible non masquée détectée."
        )
        return _make_result(
            "T14", "Sensitive Data Not Properly Masked",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "HR data is human, legal, and regulated — never just technical.",
        )

    def _detect_T15(self) -> dict:
        """T15 — Active Employee With Termination Date Populated [Critical, 20]"""
        df = self.df
        status_cols  = _cols_matching(df, STATUS_KEYWORDS)
        termdat_cols = _cols_matching(df, TERMDATE_KEYWORDS)

        if not status_cols or not termdat_cols:
            return _make_result(
                "T15", "Active Employee With Termination Date Populated",
                False, 0, [],
                "Critical", 20,
                "Colonnes status et/ou termination_date absentes.",
            )

        total_n = 0
        flagged_cols = status_cols + termdat_cols

        for sc in status_cols:
            for tc in termdat_cols:
                status_vals = df[sc].astype(str).str.strip().str.lower()
                term_vals   = _normalize_missing(df[tc].astype(str))
                active_mask  = status_vals.isin(["active", "actif", "1", "true", "oui"])
                term_filled  = term_vals.notna() & (term_vals != "nan") & (term_vals != "")
                contradictions = active_mask & term_filled
                total_n += int(contradictions.sum())

        triggered = total_n > 0
        detail = (
            f"{total_n} employé(s) actif(s) avec date de départ renseignée."
            if triggered else "Aucune contradiction statut/date de départ détectée."
        )
        return _make_result(
            "T15", "Active Employee With Termination Date Populated",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "Active status + termination date = a contradiction that must be "
            "resolved immediately.",
        )

    def _detect_T16(self) -> dict:
        """T16 — Non-Random Missing Data (Bias) [Common, 8]"""
        df = self.df
        flagged_cols = []

        # Identifier la colonne catégorielle clé la plus présente
        cat_col = None
        for kw in CATEGORY_KEYS:
            matches = _cols_matching(df, [kw])
            if matches:
                cat_col = matches[0]
                break

        if cat_col is None:
            return _make_result(
                "T16", "Non-Random Missing Data (Bias)",
                False, 0, [],
                "Common", 8,
                "Aucune colonne catégorielle clé (gender, department, grade) trouvée.",
            )

        cat_vals = df[cat_col].dropna().astype(str)
        groups   = cat_vals.unique()
        if len(groups) < 2:
            return _make_result(
                "T16", "Non-Random Missing Data (Bias)",
                False, 0, [],
                "Common", 8,
                f"Colonne '{cat_col}' contient moins de 2 modalités — analyse impossible.",
            )

        for col in df.columns:
            if col == cat_col:
                continue
            overall_missing = _normalize_missing(df[col]).isna().sum() / max(self._n_rows, 1)
            if overall_missing < TRIGGER_RATIO:
                continue
            # Calculer taux de missing par groupe
            rates = []
            for grp in groups:
                mask = df[cat_col].astype(str) == grp
                sub  = df.loc[mask, col]
                rate = _normalize_missing(sub).isna().sum() / max(len(sub), 1)
                rates.append(rate)
            if not rates:
                continue
            spread = max(rates) - min(rates)
            if spread > 0.20:   # > 20 points d'écart
                flagged_cols.append(col)

        n = len(flagged_cols)
        triggered = bool(flagged_cols)
        detail = (
            f"{n} colonne(s) avec MNAR suspecté (spread > 20pp par '{cat_col}') : "
            f"{flagged_cols}" if triggered
            else f"Aucun MNAR détecté par rapport à '{cat_col}'."
        )
        # TODO V2 : analyser toutes les colonnes catégorielles,
        # pas uniquement la première trouvée.
        return _make_result(
            "T16", "Non-Random Missing Data (Bias)",
            triggered, n, flagged_cols,
            "Common", 8, detail,
            "Missing values are never innocent. Always analyze why they are missing.",
        )

    def _detect_T17(self) -> dict:
        """T17 — Incorrectly Calculated Seniority [Common, 8]"""
        df = self.df
        seniority_cols = _cols_matching(df, SENIORITY_KEYWORDS)
        hiredate_cols  = _cols_matching(df, HIREDATE_KEYWORDS)

        if not seniority_cols or not hiredate_cols:
            return _make_result(
                "T17", "Incorrectly Calculated Seniority",
                False, 0, [],
                "Common", 8,
                "Colonnes ancienneté et/ou date d'embauche absentes — vérification impossible.",
            )

        today = pd.Timestamp.now(tz=None).normalize()
        total_n = 0
        flagged_cols = []

        for sc in seniority_cols:
            for hc in hiredate_cols:
                declared = _to_numeric(df[sc])
                hire_dt  = _to_date(df[hc])
                expected = ((today - hire_dt).dt.days / 365.25)
                common   = declared.notna() & expected.notna()
                if common.sum() == 0:
                    continue
                diff = (declared[common] - expected[common]).abs()
                incorrect = diff > 1.0   # tolérance ±1 an
                n = int(incorrect.sum())
                if n > 0 and _triggered(n / max(common.sum(), 1)):
                    flagged_cols.extend([sc, hc])
                    total_n += n

        flagged_cols = list(dict.fromkeys(flagged_cols))
        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} ancienneté(s) incohérente(s) (tolérance ±1 an) dans : "
            f"{flagged_cols}" if triggered
            else "Anciennetés cohérentes avec les dates d'embauche."
        )
        return _make_result(
            "T17", "Incorrectly Calculated Seniority",
            triggered, total_n, flagged_cols,
            "Common", 8, detail,
            "Seniority has a legal definition. Always verify the calculation rule.",
        )

    def _detect_T18(self) -> dict:
        """T18 — Status Changes Not Historized [Common, 8]"""
        df = self.df
        status_cols   = _cols_matching(df, STATUS_KEYWORDS)
        modified_cols = _cols_matching(df, MODIFIED_KEYWORDS)

        if not status_cols:
            return _make_result(
                "T18", "Status Changes Not Historized",
                False, 0, [],
                "Common", 8,
                "Aucune colonne statut trouvée.",
            )

        flagged = False
        details_parts = []

        for col in status_cols:
            n_unique = df[col].dropna().nunique()
            has_hist = bool(modified_cols)
            if not has_hist:
                flagged = True
                details_parts.append(
                    f"'{col}' présente sans colonne date de modification"
                )
            elif n_unique > 2:
                flagged = True
                details_parts.append(
                    f"'{col}' avec {n_unique} modalités (transitions probables) "
                    f"sans historisation SCD"
                )

        triggered = flagged
        detail = (
            " | ".join(details_parts) if triggered
            else "Historisation des statuts présente ou non requise."
        )
        # occurrences = 1 (structurel) si déclenché
        occ = 1 if triggered else 0
        cols = status_cols + modified_cols if triggered else []
        return _make_result(
            "T18", "Status Changes Not Historized",
            triggered, occ, cols,
            "Common", 8, detail,
            "In HR, history is as important as the present.",
        )

    # ─────────────────────────────────────────
    # DÉTECTEURS CROSSSECTOR — T57 à T69
    # (Logique identique à finance_analyzer.py)
    # ─────────────────────────────────────────

    def _detect_T57(self) -> dict:
        """T57 — Hidden Default / Placeholder Values [Critical, 20]"""
        df = self.df
        placeholder_extended = PLACEHOLDER_VALUES | {
            "9999.0", "9999.00", "99999.0", "99999.00",
            "-1.0", "-1.00", "999999.0", "999999.00",
        }
        flagged_cols = []
        total_n = 0
        for col in df.columns:
            series = df[col].dropna().astype(str).str.strip().str.lower()
            hits   = series[series.isin(placeholder_extended)]
            n = len(hits)
            if n > 0 and _triggered(n / max(len(series), 1)):
                flagged_cols.append(col)
                total_n += n
        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} valeur(s) placeholder dans : {flagged_cols}" if triggered
            else "Aucune valeur placeholder détectée."
        )
        return _make_result(
            "T57", "Hidden Default / Placeholder Values",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "Identify and document every placeholder value before any analysis.",
        )

    def _detect_T58(self) -> dict:
        """T58 — Logical Duplicates [Critical, 20]"""
        df = self.df
        flagged_cols = []
        total_n = 0
        for col in _string_columns(df):
            series = df[col].dropna().astype(str)
            normalized = (
                series.str.lower().str.strip()
                      .str.replace(r"[^\w\s]", "", regex=True)
                      .str.replace(r"\s+", " ", regex=True)
            )
            norm_dups = normalized.duplicated(keep=False)
            orig_dups = series.duplicated(keep=False)
            logical   = norm_dups & ~orig_dups
            n = int(logical.sum())
            if n > 0 and _triggered(n / max(len(series), 1)):
                flagged_cols.append(col)
                total_n += n
        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} doublon(s) logique(s) dans : {flagged_cols}" if triggered
            else "Aucun doublon logique détecté."
        )
        return _make_result(
            "T58", "Logical Duplicates",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "Deduplication = normalize first, compare second.",
        )

    def _detect_T59(self) -> dict:
        """T59 — Over-Cleaning / Data Made Too Clean [Critical, 20]"""
        df = self.df
        flagged_cols = []
        total_n = 0
        for col in _cols_matching(df, AMOUNT_KEYWORDS):
            s_raw   = df[col].dropna().astype(str).str.strip()
            numeric = _to_numeric(df[col]).dropna()
            n_rows  = len(numeric)
            if n_rows < 3:
                continue
            if numeric.nunique() / n_rows < 0.20 and n_rows >= 5:
                flagged_cols.append(col)
                total_n += n_rows
                continue
            if numeric.std() == 0:
                flagged_cols.append(col)
                total_n += n_rows
                continue
            vc = s_raw.value_counts(normalize=True)
            if len(vc) > 0 and vc.iloc[0] > 0.60:
                flagged_cols.append(col)
                total_n += n_rows
        triggered = bool(flagged_cols)
        detail = (
            f"Over-cleaning probable dans : {flagged_cols}" if triggered
            else "Aucun over-cleaning détecté."
        )
        return _make_result(
            "T59", "Over-Cleaning / Data Made Too Clean",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "Cleaning = control, document, and own your decisions. Not delete.",
        )

    def _detect_T60(self) -> dict:
        """T60 — Temporal Logic Violated [Critical, 20]
        Note : détecté normalement — pas de déduplication avec T79.
        La déduplication est gérée par aci_calculator.py.
        """
        df = self.df
        date_cols = _cols_matching(df, DATE_KEYWORDS)
        start_kw  = ["start", "begin", "hire", "created", "open", "joining",
                      "contract_start", "date_entree", "date_embauche"]
        end_kw    = ["end", "close", "termination", "departure", "exit",
                      "contract_end", "date_fin", "date_depart"]

        starts = [c for c in date_cols if any(kw in c.lower() for kw in start_kw)]
        ends   = [c for c in date_cols if any(kw in c.lower() for kw in end_kw)]

        inversions = []
        total_n    = 0
        for sc in starts:
            for ec in ends:
                if sc == ec:
                    continue
                s_s = _to_date(df[sc])
                s_e = _to_date(df[ec])
                common = s_s.notna() & s_e.notna()
                inv    = common & (s_e < s_s)
                n = int(inv.sum())
                if n > 0 and _triggered(n / max(common.sum(), 1)):
                    inversions.append(f"{ec} < {sc}")
                    total_n += n

        triggered = bool(inversions)
        detail = (
            f"Inversions temporelles : {inversions}" if triggered
            else "Aucune inversion temporelle détectée."
        )
        flagged_cols = list({c for pair in inversions
                             for c in pair.replace(" < ", " ").split()})
        return _make_result(
            "T60", "Temporal Logic Violated",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "Time only moves forward. Your data should too.",
        )

    def _detect_T61(self) -> dict:
        """T61 — Business Rule Evolution Not Tracked [Common, 8]"""
        df = self.df
        rule_kw = ["rule_version", "version", "standard", "policy",
                    "referentiel", "norm_version", "contract_type_version"]
        eff_kw  = ["effective_date", "valid_from", "applicable_from"]

        rule_cols = _cols_matching(df, rule_kw)
        eff_cols  = _cols_matching(df, eff_kw)

        flagged_cols = []
        total_n = 0
        for col in rule_cols:
            vals = df[col].dropna().astype(str)
            if vals.nunique() > 1 and not eff_cols:
                flagged_cols.append(col)
                total_n += vals.nunique()

        if not flagged_cols:
            date_cols = _cols_matching(df, DATE_KEYWORDS)
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
            f"Règles métier potentiellement non versionnées : {flagged_cols}" if triggered
            else "Aucun changement de règle métier non tracé détecté."
        )
        return _make_result(
            "T61", "Business Rule Evolution Not Tracked",
            triggered, total_n, flagged_cols,
            "Common", 8, detail,
            "Data inherits the rules of the past. Document every one of them.",
        )

    def _detect_T62(self) -> dict:
        """T62 — Uncontrolled Free-Text Fields [Common, 8]"""
        df = self.df
        exclude_kw = AMOUNT_KEYWORDS + DATE_KEYWORDS + [
            "id", "description", "note", "comment", "email",
            "address", "adresse",
        ]
        flagged_cols = []
        total_n = 0
        for col in _string_columns(df):
            if _col_matches_kw(col, exclude_kw):
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
            f"Champs texte libre non contrôlés : {flagged_cols}" if triggered
            else "Aucun champ texte libre non contrôlé détecté."
        )
        return _make_result(
            "T62", "Uncontrolled Free-Text Fields",
            triggered, total_n, flagged_cols,
            "Common", 8, detail,
            "A free-text field is an analytical time bomb.",
        )

    def _detect_T63(self) -> dict:
        """T63 — Missing Not At Random (MNAR) [Common, 8]"""
        df = self.df
        flagged_cols = []
        total_n = 0

        null_rates = {
            col: _normalize_missing(df[col]).isna().sum() / max(self._n_rows, 1)
            for col in df.columns
        }
        avg_null = sum(null_rates.values()) / max(len(null_rates), 1)

        for col, null_ratio in null_rates.items():
            if null_ratio >= TRIGGER_RATIO and null_ratio < 1.0:
                if null_ratio > max(2 * avg_null + TRIGGER_RATIO, TRIGGER_RATIO):
                    flagged_cols.append(col)
                    total_n += int(_normalize_missing(df[col]).isna().sum())

        if not flagged_cols:
            for col in _string_columns(df):
                vals = _normalize_missing(df[col]).fillna("__MISSING__").astype(str)
                missing_mask  = vals == "__MISSING__"
                if missing_mask.sum() == 0:
                    continue
                non_null = vals[~missing_mask]
                if non_null.empty:
                    continue
                dominant_ratio = non_null.value_counts(normalize=True).iloc[0]
                if dominant_ratio > 0.70 and missing_mask.sum() >= 1:
                    flagged_cols.append(col)
                    total_n += int(missing_mask.sum())

        triggered = bool(flagged_cols)
        detail = (
            f"Valeurs manquantes non aléatoires dans : {flagged_cols}" if triggered
            else "Aucun pattern MNAR détecté."
        )
        return _make_result(
            "T63", "Missing Not At Random (MNAR)",
            triggered, total_n, flagged_cols,
            "Common", 8, detail,
            "A missing value is never innocent. Always analyze why it is missing.",
        )

    def _detect_T64(self) -> dict:
        """T64 — Name-Email Semantic Mismatch [Critical, 20]"""
        df = self.df
        name_kw  = ["name", "nom", "full_name", "employee_name",
                     "contact_name", "prenom"]
        email_kw = ["email", "mail", "courriel"]

        name_cols  = _cols_matching(df, name_kw)
        email_cols = _cols_matching(df, email_kw)

        if not name_cols or not email_cols:
            return _make_result(
                "T64", "Name-Email Semantic Mismatch",
                False, 0, [],
                "Critical", 20,
                "Colonnes name et/ou email absentes.",
            )

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
                    name_parts  = re.findall(r"[a-z]+", name)
                    email_parts = re.findall(r"[a-z]+", email)
                    if name_parts and email_parts:
                        overlap = set(name_parts) & set(email_parts)
                        if not overlap:
                            mismatches.append(idx)

        n = len(mismatches)
        triggered = _triggered(n / max(self._n_rows, 1))
        detail = (
            f"{n} incompatibilité(s) nom/email." if triggered
            else "Cohérence nom/email vérifiée."
        )
        flagged_cols = name_cols + email_cols if triggered else []
        return _make_result(
            "T64", "Name-Email Semantic Mismatch",
            triggered, n, flagged_cols,
            "Critical", 20, detail,
            "A name that doesn't match an email is a broken employee identity.",
        )

    def _detect_T65(self) -> dict:
        """T65 — Invalid Email Format [Critical, 20]"""
        df = self.df
        email_kw   = ["email", "mail", "courriel"]
        email_cols = _cols_matching(df, email_kw)

        flagged_cols = []
        total_n = 0
        for col in email_cols:
            series  = df[col].dropna().astype(str).str.strip()
            series  = series[series != ""]
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
        return _make_result(
            "T65", "Invalid Email Format",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "An invalid email = a broken communication channel. Validate before you store.",
        )

    def _detect_T66(self) -> dict:
        """T66 — Type Mismatch: Numeric in Text Field [Common, 8]"""
        df = self.df
        exclude_kw = AMOUNT_KEYWORDS + DATE_KEYWORDS + [
            "id", "code", "ref", "key", "employee_id",
        ]
        flagged_cols = []
        total_n = 0

        cat_cols = [
            c for c in _string_columns(df)
            if not _col_matches_kw(c, exclude_kw)
        ]
        for col in cat_cols:
            series  = df[col].dropna().astype(str).str.strip()
            if series.empty:
                continue
            num_hits  = series[series.str.match(NUMERIC_PATTERN)]
            n_num     = len(num_hits)
            n_total   = len(series)
            num_ratio = n_num / n_total if n_total > 0 else 0
            if _triggered(num_ratio) and num_ratio < 0.85:
                flagged_cols.append(col)
                total_n += n_num

        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} valeur(s) numérique(s) dans colonnes texte : {flagged_cols}"
            if triggered else "Aucun type mismatch numérique/texte détecté."
        )
        return _make_result(
            "T66", "Type Mismatch – Numeric in Text Field",
            triggered, total_n, flagged_cols,
            "Common", 8, detail,
            "A number in a text field is a code without a dictionary. Map it or flag it.",
        )

    def _detect_T67(self) -> dict:
        """T67 — Type Mismatch Inverse: Text in Numeric Field [Critical, 20]"""
        df = self.df
        flagged_cols = []
        total_n = 0
        for col in _cols_matching(df, AMOUNT_KEYWORDS):
            series      = df[col].dropna().astype(str).str.strip()
            numeric     = _to_numeric(df[col])
            non_numeric = series[
                numeric.isna() &
                (series != "") &
                (series.str.lower() != "nan")
            ]
            n = len(non_numeric)
            if n > 0 and _triggered(n / max(len(series), 1)):
                flagged_cols.append(col)
                total_n += n
        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} valeur(s) texte dans colonnes numériques : {flagged_cols}"
            if triggered else "Aucun type mismatch texte/numérique détecté."
        )
        return _make_result(
            "T67", "Type Mismatch Inverse – Text in Numeric Field",
            triggered, total_n, flagged_cols,
            "Critical", 20, detail,
            "A letter in a number field is a red flag. Investigate before any calculation.",
        )

    def _detect_T69(self) -> dict:
        """T69 — Statistical Outlier Detection [Common, 8]"""
        df = self.df
        flagged_cols = []
        total_n = 0
        for col in _cols_matching(df, AMOUNT_KEYWORDS):
            numeric = _to_numeric(df[col]).dropna()
            if len(numeric) < 4:
                continue
            mean = numeric.mean()
            std  = numeric.std()

            n_zscore = 0
            if std > 0:
                z = (numeric - mean).abs() / std
                n_zscore = int((z > 3).sum())

            q1, q3 = numeric.quantile(0.25), numeric.quantile(0.75)
            iqr = q3 - q1
            n_iqr = 0
            if iqr > 0:
                # Méthode IQR×3 (standard — règle figée, cohérente avec finance_analyzer.py)
                lower = q1 - 3 * iqr
                upper = q3 + 3 * iqr
                n_iqr = int(((numeric < lower) | (numeric > upper)).sum())

            n = max(n_zscore, n_iqr)
            if n > 0 and _triggered(n / max(len(numeric), 1)):
                flagged_cols.append(col)
                total_n += n

        triggered = bool(flagged_cols)
        detail = (
            f"{total_n} outlier(s) (Z>3 ou IQR×3) dans : {flagged_cols}" if triggered
            else "Aucun outlier statistique détecté."
        )
        return _make_result(
            "T69", "Statistical Outlier Detection",
            triggered, total_n, flagged_cols,
            "Common", 8, detail,
            "An outlier is either an error, a fraud, or your best employee. "
            "Investigate before you delete.",
        )
