"""
mapper.py — AnalytiQ Pro
==========================
Analyse les colonnes d'un DataFrame et les classe sémantiquement
pour orienter les analyseurs (Finance, HR, Temporal, Cross, etc.).

Le mapper :
  - Lit traps_catalog.yaml pour extraire les mots-clés par département
  - Détecte le type de données de chaque colonne (date / numeric /
    text / boolean / mixed)
  - Détecte le département sémantique probable de chaque colonne
  - Calcule un niveau de confiance (high / medium / low)
  - Ne modifie JAMAIS le DataFrame

Signature obligatoire : map_columns(df, yaml_path) -> (df, log_dict)

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
# MOTS-CLÉS SÉMANTIQUES PAR DÉPARTEMENT
# (complétés dynamiquement depuis traps_catalog.yaml)
# ─────────────────────────────────────────────

# Mots-clés de base — indépendants du catalogue
BASE_KEYWORDS: dict[str, list[str]] = {
    "Finance": [
        "amount", "revenue", "price", "cost", "salary", "wage", "payment",
        "balance", "invoice", "fee", "tax", "total", "profit", "loss",
        "credit", "debit", "currency", "exchange", "rate", "margin",
        "budget", "forecast", "expense", "income", "billing", "receipt",
        "montant", "salaire", "revenu", "prix", "solde", "facture",
        "taux", "devise", "charge", "produit", "resultat",
    ],
    "HR": [
        "employee", "staff", "worker", "hire", "hire_date", "birth",
        "age", "tenure", "seniority", "department", "job", "title",
        "role", "position", "salary", "headcount", "termination",
        "contract", "payroll", "performance", "appraisal", "leave",
        "absence", "gender", "cin", "national_id", "employe",
        "anciennete", "poste", "contrat", "conge",
    ],
    "Temporal": [
        "date", "time", "timestamp", "datetime", "ts", "dt", "at",
        "created", "updated", "modified", "posted", "effective",
        "start", "end", "begin", "expir", "fiscal", "period", "month",
        "quarter", "year", "week", "day", "hour", "snapshot",
        "accounting", "transaction_date", "delivery", "order_date",
        "load", "ingested", "event", "arrival", "as_of",
        "fy", "fiscal_year", "annee", "exercice",
    ],
    "Operations": [
        "stock", "inventory", "quantity", "shipment", "delivery",
        "warehouse", "logistics", "supply", "order", "tracking",
        "sku", "product", "batch", "lead_time", "transit",
        "reception", "dispatch", "entrepot", "livraison",
    ],
    "Marketing": [
        "customer", "client", "lead", "campaign", "channel", "source",
        "utm", "cac", "ltv", "roas", "conversion", "segment",
        "email", "phone", "address", "contact", "crm", "prospect",
        "acquisition", "churn", "retention", "coupon", "promo",
        "client", "prospect", "canal", "campagne",
    ],
    "ML_DataScience": [
        "feature", "label", "target", "prediction", "score", "model",
        "train", "test", "split", "accuracy", "precision", "recall",
        "embedding", "vector", "cluster", "class", "probability",
        "weight", "bias", "gradient", "epoch", "batch_size",
    ],
    "Healthcare": [
        "patient", "diagnosis", "treatment", "medication", "drug",
        "dose", "icd", "atc", "clinical", "medical", "hospital",
        "lab", "blood", "glucose", "pressure", "temperature",
        "symptom", "prescription", "pharmacy", "dossier",
    ],
    "IT": [
        "log", "event_id", "session", "request", "response", "error",
        "status_code", "ip", "server", "endpoint", "api", "database",
        "schema", "table", "index", "query", "latency", "uptime",
        "incident", "ticket", "deployment", "version", "commit",
    ],
    "EcommerceRetail": [
        "cart", "basket", "checkout", "return", "refund", "order_id",
        "product_id", "sku", "category", "brand", "marketplace",
        "seller", "buyer", "rating", "review", "discount", "coupon",
        "shipping", "tracking", "fulfillment", "store", "channel",
        "panier", "commande", "retour", "remboursement",
    ],
    "Cross": [
        "id", "key", "code", "reference", "ref", "flag", "type",
        "category", "status", "is_", "has_", "country", "region",
        "city", "zip", "iso", "currency_code", "language",
        "created_by", "updated_by", "source", "origin",
    ],
}

# Patterns regex pour la détection de type
BOOL_VALUES   = {"true", "false", "yes", "no", "oui", "non", "1", "0", "t", "f", "y", "n"}
DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}"),           # ISO YYYY-MM-DD
    re.compile(r"^\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}"),  # DD/MM/YYYY ou MM/DD/YYYY
    re.compile(r"^\d{4}-W\d{2}"),                  # ISO week
    re.compile(r"^\d{4}-\d{3}$"),                  # ISO ordinal
    re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"), # ISO datetime
    re.compile(r"^\d{5}$"),                         # Excel serial (5 chiffres)
    re.compile(r"^FY\d{2,4}$", re.IGNORECASE),    # Fiscal year label
    re.compile(r"^Q[1-4]\s+\d{4}$"),               # Quarter label
    re.compile(r"^\d{4}-\d{2}$"),                  # YYYY-MM monthly
]
NUMERIC_PATTERN = re.compile(r"^-?\d+([.,]\d+)?$")


# ─────────────────────────────────────────────
# CHARGEMENT DU CATALOGUE
# ─────────────────────────────────────────────

def _load_department_keywords(yaml_path: str | Path) -> dict[str, list[str]]:
    """
    Lit traps_catalog.yaml et extrait des mots-clés supplémentaires
    depuis les labels et descriptions de traps, par département.
    Fusionne avec BASE_KEYWORDS.
    """
    keywords = {k: list(v) for k, v in BASE_KEYWORDS.items()}

    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            catalog = yaml.safe_load(f)
    except Exception:
        return keywords  # retour des keywords de base si le yaml échoue

    dept_map = {
        "Finance":         "Finance",
        "HR":              "HR",
        "Temporal":        "Temporal",
        "Operations":      "Operations",
        "Marketing":       "Marketing",
        "ML_DataScience":  "ML_DataScience",
        "Healthcare":      "Healthcare",
        "IT":              "IT",
        "EcommerceRetail": "EcommerceRetail",
        "CrossSector":     "Cross",
    }

    for section in ["data_traps", "temporal_traps"]:
        for trap in catalog.get(section, []):
            dept_raw = trap.get("department", "")
            dept = dept_map.get(dept_raw, "Cross")

            # Extraire mots du label (mots >= 4 chars, alpha uniquement)
            label = trap.get("label", "")
            words = re.findall(r"\b[a-zA-Z]{4,}\b", label.lower())
            for w in words:
                if w not in keywords.get(dept, []):
                    keywords.setdefault(dept, []).append(w)

    return keywords


# ─────────────────────────────────────────────
# DÉTECTION DU TYPE DE DONNÉES
# ─────────────────────────────────────────────

def _detect_type(series: pd.Series) -> tuple[str, float]:
    """
    Détecte le type dominant d'une colonne parmi :
    date / numeric / boolean / text / mixed.

    Retourne (type_label, confidence_ratio).
    """
    clean = series.dropna().astype(str)
    clean = clean[clean.str.strip() != ""]
    n = len(clean)
    if n == 0:
        return "empty", 1.0

    # --- Boolean
    bool_hits = clean[clean.str.lower().isin(BOOL_VALUES)]
    bool_ratio = len(bool_hits) / n

    # --- Date
    date_hits = 0
    for val in clean:
        if any(p.search(val.strip()) for p in DATE_PATTERNS):
            date_hits += 1
    date_ratio = date_hits / n

    # --- Numeric
    # Accepter formats avec virgule comme séparateur décimal (FR)
    numeric_clean = clean.str.replace(",", ".", regex=False).str.replace(" ", "", regex=False)
    num_hits = numeric_clean[numeric_clean.str.match(NUMERIC_PATTERN)]
    numeric_ratio = len(num_hits) / n

    # Sélection du type dominant
    ratios = {
        "date":    date_ratio,
        "numeric": numeric_ratio,
        "boolean": bool_ratio,
    }
    best_type, best_ratio = max(ratios.items(), key=lambda x: x[1])

    # Si aucun type n'atteint 50% → mixed ou text
    if best_ratio < 0.50:
        # Vérifier si c'est du texte pur (peu de numériques, peu de dates)
        if numeric_ratio < 0.10 and date_ratio < 0.10:
            return "text", 1.0 - best_ratio
        return "mixed", best_ratio

    return best_type, best_ratio


# ─────────────────────────────────────────────
# DÉTECTION DU DÉPARTEMENT SÉMANTIQUE
# ─────────────────────────────────────────────

def _detect_department(
    col_name: str,
    series: pd.Series,
    keywords: dict[str, list[str]],
    detected_type: str,
) -> tuple[str, str]:
    """
    Classe une colonne dans un département sémantique.

    Stratégie :
    1. Correspondance sur le NOM de la colonne (score fort)
    2. Correspondance sur les VALEURS de la colonne (score faible)
    3. Fallback par type détecté (Temporal si date, Cross sinon)

    Retourne (department, confidence).
    """
    col_lower = col_name.lower()
    scores: dict[str, int] = {dept: 0 for dept in keywords}

    # ── Scoring sur le nom de la colonne
    for dept, kws in keywords.items():
        for kw in kws:
            kw_l = kw.lower()
            # Correspondance exacte de mot (délimiteurs : _, espace, début/fin)
            if re.search(rf"(^|_|\.){re.escape(kw_l)}($|_|\.)", col_lower):
                scores[dept] += 3   # score fort : mot entier dans le nom
            elif kw_l in col_lower:
                scores[dept] += 1   # score faible : sous-chaîne

    # ── Scoring sur les valeurs (échantillon 20 premières lignes)
    sample_vals = series.dropna().astype(str).head(20).str.lower().tolist()
    for dept, kws in keywords.items():
        for kw in kws:
            for val in sample_vals:
                if kw.lower() in val:
                    scores[dept] += 1
                    break  # un seul point par mot-clé même s'il apparaît plusieurs fois

    # ── Fallback type-based
    if detected_type == "date":
        scores["Temporal"] = scores.get("Temporal", 0) + 2

    # ── Sélection du gagnant
    best_dept = max(scores, key=scores.get)
    best_score = scores[best_dept]
    total_score = sum(scores.values())

    if best_score == 0:
        # Aucune correspondance trouvée
        dept_out = "Temporal" if detected_type == "date" else "Unknown"
        return dept_out, "low"

    # Confiance
    if total_score == 0:
        return "Unknown", "low"

    dominance = best_score / total_score
    if dominance >= 0.60 and best_score >= 3:
        confidence = "high"
    elif dominance >= 0.35 or best_score >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    return best_dept, confidence


# ─────────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────────

def map_columns(
    df: pd.DataFrame,
    yaml_path: str | Path = "data/traps_catalog.yaml",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Analyse les colonnes du DataFrame et les classe sémantiquement.

    Paramètres
    ----------
    df : pd.DataFrame
        Dataset à analyser (non modifié).
    yaml_path : str | Path
        Chemin vers traps_catalog.yaml.

    Retourne
    --------
    (df, log_dict) :
        df       — DataFrame original INCHANGÉ.
        log_dict — dict structuré pour le Transparency Ledger :
            {
                "analyzer"   : "mapper",
                "timestamp"  : "...",
                "total_cols" : int,
                "mapping"    : {
                    col_name: {
                        "detected_type"       : str,
                        "type_confidence_pct" : float,
                        "detected_department" : str,
                        "dept_confidence"     : str,
                    },
                    ...
                },
                "summary": {
                    dept: int,   # nb colonnes par département
                    ...
                },
                "type_summary": {
                    type: int,   # nb colonnes par type
                    ...
                },
                "unknown_cols": [...],  # colonnes non classifiées
            }
    """
    yaml_path = Path(yaml_path)
    keywords = _load_department_keywords(yaml_path)

    mapping: dict[str, dict] = {}
    dept_summary: dict[str, int] = {}
    type_summary: dict[str, int] = {}
    unknown_cols: list[str] = []

    for col in df.columns:
        series = df[col]

        # Détection du type
        col_type, type_conf = _detect_type(series)

        # Détection du département
        dept, dept_conf = _detect_department(col, series, keywords, col_type)

        mapping[col] = {
            "detected_type":        col_type,
            "type_confidence_pct":  round(type_conf * 100, 1),
            "detected_department":  dept,
            "dept_confidence":      dept_conf,
        }

        # Accumulateurs summary
        dept_summary[dept] = dept_summary.get(dept, 0) + 1
        type_summary[col_type] = type_summary.get(col_type, 0) + 1

        if dept == "Unknown":
            unknown_cols.append(col)

    log: dict[str, Any] = {
        "analyzer":    "mapper",
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "yaml_loaded": str(yaml_path),
        "total_cols":  len(df.columns),
        "mapping":     mapping,
        "summary":     dict(sorted(dept_summary.items(), key=lambda x: -x[1])),
        "type_summary": dict(sorted(type_summary.items(), key=lambda x: -x[1])),
        "unknown_cols": unknown_cols,
        "unknown_cols_count": len(unknown_cols),
    }

    return df, log
