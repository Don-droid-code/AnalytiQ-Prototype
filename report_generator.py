"""
report_generator.py — AnalytiQ Pro
=====================================
Génère un PDF de rapport d'analyse à partir des logs ACI, Finance et Temporal.

Support bilingue : FR (défaut) et EN via le paramètre lang.

Stack technique (immuable) :
    PDF     : WeasyPrint — HTML/CSS → PDF, support RTL
    Template: Jinja2     — injection données dans HTML
    Police  : Système    — Segoe UI / DejaVu Sans / Arial (fallback)
    Logo    : SVG inline — zéro dépendance fichier externe

Règles critiques de génération :
    1. CSS injecté avec {{ css_content | safe }} — jamais de <link> externe
    2. Logo SVG dans <div dir="ltr"> — isolé du RTL
    3. Barre de progression CSS — pas de conic-gradient (non supporté WeasyPrint)
    4. Police système uniquement — pas de téléchargement .ttf

Clés attendues des logs :
    aci_log      : aci, aci_band, components, deduplication_applied,
                   deduplication_details, ams_checklist, timestamp
    finance_log  : dqs, traps_triggered, total_rows, total_columns
                   Chaque trap : id, label, occurrences, flagged_cols,
                                 criticality, penalty
    temporal_log : tcs, traps_triggered, tcs_breakdown
                   Chaque trap : id, label, occurrences, penalty

Auteur  : Othmane Afif — othmane.afif@outlook.com
Projet  : AnalytiQ Pro — analytiq-pro.com
Version : 1.1 — 2025 (ajout support bilingue FR/EN)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from weasyprint import HTML
except ImportError:
    raise ImportError(
        "WeasyPrint non installé. "
        "Exécutez : pip install weasyprint"
    )

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    raise ImportError(
        "Jinja2 non installé. "
        "Exécutez : pip install jinja2"
    )

# ─────────────────────────────────────────────
# DICTIONNAIRE DE TRADUCTIONS (FR / EN)
# ─────────────────────────────────────────────

TRANSLATIONS: dict[str, dict[str, str]] = {
    "fr": {
        "report_title":          "Rapport d'analyse de données",
        "aci_title":             "Score ACI (AnalytiQ Confidence Index)",
        "components_title":      "Composantes de l'ACI",
        "recommendations_title": "Recommandations prioritaires",
        "finance_traps_title":   "Traps Finance déclenchés",
        "temporal_traps_title":  "Traps Temporels déclenchés",
        "ams_title":             "Checklist de maturité analytique (AMS)",
        "dedup_title":           "Déduplication appliquée",
        "no_finance_trap":       "Aucune anomalie détectée",
        "no_temporal_trap":      "Aucune anomalie temporelle",
        "col_id":                "ID",
        "col_label":             "Label",
        "col_occurrences":       "Occurrences",
        "col_columns":           "Colonnes touchées",
        "col_severity":          "Sévérité",
        "col_penalty":           "Pénalité",
        "col_weight":            "Poids",
        "col_contribution":      "Contribution",
        "col_item":              "Item",
        "col_status":            "Statut",
        "col_reason":            "Détail",
        "score_label":           "Score",
        "generated_on":          "Généré le",
        "dataset_info":          "lignes · colonnes",
        "ams_summary":           "items passés — Score",
        "tcs_rules_applied":     "Règles TCS appliquées",
        "dedup_removed":         "Trap retiré du calcul DQS",
        "dedup_original_dqs":    "DQS Finance original",
        "recalculated_without":  "recalculé sans",
        "confidential":          "Rapport confidentiel — usage interne uniquement",
    },
    "en": {
        "report_title":          "Data Analysis Report",
        "aci_title":             "ACI Score (AnalytiQ Confidence Index)",
        "components_title":      "ACI Components",
        "recommendations_title": "Priority Recommendations",
        "finance_traps_title":   "Finance Traps Triggered",
        "temporal_traps_title":  "Temporal Traps Triggered",
        "ams_title":             "Analytical Maturity Checklist (AMS)",
        "dedup_title":           "Deduplication Applied",
        "no_finance_trap":       "No anomaly detected",
        "no_temporal_trap":      "No temporal anomaly detected",
        "col_id":                "ID",
        "col_label":             "Label",
        "col_occurrences":       "Occurrences",
        "col_columns":           "Affected Columns",
        "col_severity":          "Severity",
        "col_penalty":           "Penalty",
        "col_weight":            "Weight",
        "col_contribution":      "Contribution",
        "col_item":              "Item",
        "col_status":            "Status",
        "col_reason":            "Detail",
        "score_label":           "Score",
        "generated_on":          "Generated on",
        "dataset_info":          "rows · columns",
        "ams_summary":           "items passed — Score",
        "tcs_rules_applied":     "TCS Rules Applied",
        "dedup_removed":         "Trap removed from DQS calculation",
        "dedup_original_dqs":    "Original Finance DQS",
        "recalculated_without":  "recalculated without",
        "confidential":          "Confidential report — internal use only",
    },
}


# ─────────────────────────────────────────────
# HELPERS PYTHON — PAS DANS LE TEMPLATE
# ─────────────────────────────────────────────

def _get_interpretation(aci_score: float, lang: str = "fr") -> str:
    """Retourne une phrase d'interprétation selon la bande ACI et la langue."""
    interpretations: dict[str, dict[int, str]] = {
        "fr": {
            85: (
                "Le dataset présente une qualité exceptionnelle. "
                "Les analyses peuvent être utilisées pour des "
                "décisions stratégiques en production."
            ),
            70: (
                "Le dataset est acceptable. Quelques améliorations "
                "mineures sont recommandées avant usage critique."
            ),
            55: (
                "Des lacunes significatives ont été identifiées. "
                "Les résultats doivent être interprétés avec prudence."
            ),
            40: (
                "Le dataset présente des problèmes matériels de "
                "fiabilité. Une analyse approfondie est nécessaire "
                "avant toute décision."
            ),
            0: (
                "Le dataset ne peut pas être utilisé pour orienter "
                "des décisions. Une correction préalable est obligatoire."
            ),
        },
        "en": {
            85: (
                "The dataset exhibits exceptional quality. "
                "Analytical outputs can be used to drive "
                "strategic decisions in production."
            ),
            70: (
                "The dataset is acceptable. Minor improvements "
                "are recommended before critical use."
            ),
            55: (
                "Significant gaps have been identified. "
                "Results should be interpreted with caution."
            ),
            40: (
                "The dataset presents material reliability issues. "
                "A thorough review is required before any decision."
            ),
            0: (
                "The dataset cannot be used to drive decisions. "
                "Prior correction is mandatory."
            ),
        },
    }
    lang = lang if lang in interpretations else "fr"
    thresholds = [85, 70, 55, 40, 0]
    texts = interpretations[lang]
    for t in thresholds:
        if aci_score >= t:
            return texts[t]
    return texts[0]


def _get_recommendations(
    finance_log: dict,
    temporal_log: dict,
    lang: str = "fr",
) -> list[str]:
    """Génère jusqu'à 5 recommandations prioritaires dans la langue choisie."""
    explicit_map: dict[str, dict[str, str]] = {
        "fr": {
            "T01": "Revoir les montants négatifs dans les colonnes financières.",
            "T02": "Dédupliquer les transactions identiques.",
            "T57": "Remplacer les valeurs placeholder (9999, N/A) par NULL.",
            "T60": "Corriger les inversions temporelles (end_date < start_date).",
            "T80": "Des dates futures ont été détectées — vérifier les saisies.",
        },
        "en": {
            "T01": "Review negative amounts in financial columns.",
            "T02": "Deduplicate identical transactions.",
            "T57": "Replace placeholder values (9999, N/A) with NULL.",
            "T60": "Fix temporal inversions (end_date < start_date).",
            "T80": "Future dates detected — verify data entry.",
        },
    }
    fallback: dict[str, str] = {
        "fr": "Corriger {} anomalie(s) critique(s) supplémentaire(s) détectée(s).",
        "en": "Fix {} additional critical anomaly/anomalies detected.",
    }
    no_issue: dict[str, str] = {
        "fr": "Aucune anomalie critique détectée. Le dataset est sain.",
        "en": "No critical anomaly detected. The dataset is clean.",
    }

    lang = lang if lang in explicit_map else "fr"
    recommendations: list[str] = []
    handled_ids: set[str] = set()

    triggered = {t["id"] for t in finance_log.get("traps_triggered", [])}
    temporal  = {t["id"] for t in temporal_log.get("traps_triggered", [])}

    for trap_id, message in explicit_map[lang].items():
        if trap_id in triggered or trap_id in temporal:
            recommendations.append(message)
            handled_ids.add(trap_id)

    critical_unhandled = [
        t for t in finance_log.get("traps_triggered", [])
        if t.get("criticality") == "Critical"
        and t["id"] not in handled_ids
    ]
    if critical_unhandled:
        recommendations.append(
            fallback[lang].format(len(critical_unhandled))
        )

    if not recommendations:
        recommendations.append(no_issue[lang])

    return recommendations[:5]


def _is_rtl_needed(finance_log: dict) -> bool:
    """
    Détecte si des colonnes arabes sont présentes.
    En V1 : retourne False par défaut.
    # TODO V2 : détecter les caractères arabes dans df.
    """
    return False


def _format_timestamp(ts: str, lang: str = "fr") -> str:
    """Reformate un timestamp ISO en format lisible selon la langue."""
    try:
        dt = datetime.fromisoformat(ts)
        if lang == "en":
            return dt.strftime("%Y-%m-%d at %H:%M UTC")
        return dt.strftime("%d/%m/%Y à %H:%M UTC")
    except Exception:
        return ts


def _format_cols(flagged_cols: Any) -> str:
    """Formate la liste des colonnes pour l'affichage."""
    if not flagged_cols:
        return "—"
    if isinstance(flagged_cols, list):
        return ", ".join(str(c) for c in flagged_cols[:4]) + (
            "…" if len(flagged_cols) > 4 else ""
        )
    return str(flagged_cols)


def _tcs_breakdown_flags(temporal_log: dict) -> dict:
    """Extrait les flags spéciaux du TCS breakdown."""
    breakdown = temporal_log.get("tcs_breakdown", {})
    return {
        "t70_doubled":     breakdown.get("t70_doubled", False),
        "t89_extra":       breakdown.get("t89_extra", False),
        "t91_cap_applied": breakdown.get("t91_cap_applied", False),
    }


# ─────────────────────────────────────────────
# CONSTRUCTION DU CONTEXTE TEMPLATE
# ─────────────────────────────────────────────

def _build_template_context(
    aci_log: dict,
    finance_log: dict,
    temporal_log: dict,
    lang: str = "fr",
) -> dict[str, Any]:
    """
    Construit le dictionnaire de contexte injecté dans le template Jinja2.
    Toutes les transformations de données sont faites ici — pas dans le template.
    """
    lang = lang if lang in TRANSLATIONS else "fr"

    aci_score  = float(aci_log.get("aci", 0.0))
    aci_band   = str(aci_log.get("aci_band", "Critical"))
    components = aci_log.get("components", {})

    tcs_comp = components.get("tcs",         {})
    dqs_comp = components.get("dqs_finance", {})
    ams_comp = components.get("ams",         {})
    exs_comp = components.get("exs",         {})

    finance_traps = []
    for t in finance_log.get("traps_triggered", []):
        finance_traps.append({
            "id":               t.get("id", "?"),
            "label":            t.get("label", ""),
            "occurrences":      t.get("occurrences", 0),
            "flagged_cols_fmt": _format_cols(t.get("flagged_cols", [])),
            "criticality":      t.get("criticality", "—"),
            "penalty":          t.get("penalty", 0),
        })

    tcs_flags = _tcs_breakdown_flags(temporal_log)
    temporal_traps = []
    for t in temporal_log.get("traps_triggered", []):
        note = ""
        if t.get("id") == "T70" and tcs_flags["t70_doubled"]:
            note = "× 2 (financial column)" if lang == "en" else "× 2 (colonne financière)"
        elif t.get("id") == "T89" and tcs_flags["t89_extra"]:
            note = "+ 5 additional pts" if lang == "en" else "+ 5 pts additionnels"
        temporal_traps.append({
            "id":          t.get("id", "?"),
            "label":       t.get("label", ""),
            "occurrences": t.get("occurrences", 0),
            "penalty":     t.get("penalty", 0),
            "note":        note,
        })

    ams_checklist = aci_log.get("ams_checklist", [])
    dedup_applied = bool(aci_log.get("deduplication_applied", False))
    dedup_details = aci_log.get("deduplication_details", [])
    total_rows    = finance_log.get("total_rows", 0)
    total_cols    = finance_log.get("total_columns", 0)
    dqs_original  = finance_log.get("dqs", 0.0)

    return {
        "lang":            lang,
        "t":               TRANSLATIONS[lang],
        "timestamp_fmt":   _format_timestamp(aci_log.get("timestamp", ""), lang),
        "total_rows":      total_rows,
        "total_columns":   total_cols,
        "rtl":             _is_rtl_needed(finance_log),

        "aci_score":       round(aci_score, 1),
        "aci_score_pct":   min(100, max(0, round(aci_score, 1))),
        "aci_band":        aci_band,
        "aci_band_lower":  aci_band.lower(),
        "interpretation":  _get_interpretation(aci_score, lang),

        "tcs_score":    round(tcs_comp.get("score",    0.0), 1),
        "tcs_weight":   tcs_comp.get("weight",   0.35),
        "tcs_weighted": round(tcs_comp.get("weighted", 0.0), 2),

        "dqs_score":    round(dqs_comp.get("score",    0.0), 1),
        "dqs_weight":   dqs_comp.get("weight",   0.30),
        "dqs_weighted": round(dqs_comp.get("weighted", 0.0), 2),

        "ams_score":    round(ams_comp.get("score",    0.0), 1),
        "ams_weight":   ams_comp.get("weight",   0.25),
        "ams_weighted": round(ams_comp.get("weighted", 0.0), 2),

        "exs_score":    round(exs_comp.get("score",    0.0), 1),
        "exs_weight":   exs_comp.get("weight",   0.10),
        "exs_weighted": round(exs_comp.get("weighted", 0.0), 2),

        "recommendations": _get_recommendations(finance_log, temporal_log, lang),

        "finance_traps":  finance_traps,
        "temporal_traps": temporal_traps,
        "tcs_flags":      tcs_flags,

        "ams_checklist": ams_checklist,
        "ams_passed":    sum(1 for i in ams_checklist if i.get("passed")),
        "ams_total":     len(ams_checklist),

        "dedup_applied":  dedup_applied,
        "dedup_details":  dedup_details,
        "dqs_original":   round(dqs_original, 1),
    }


# ─────────────────────────────────────────────
# FONCTION PRINCIPALE
# ─────────────────────────────────────────────

def generate_pdf(
    aci_log: dict,
    finance_log: dict,
    temporal_log: dict,
    output_path: str | Path,
    template_dir: str | Path = "templates",
    lang: str = "fr",
) -> str:
    """
    Génère un PDF à partir des logs d'analyse.

    Paramètres
    ----------
    aci_log : dict
        Retourné par aci_calculator.calculate_aci()
    finance_log : dict
        Retourné par finance_analyzer.analyze()
    temporal_log : dict
        Retourné par temporal_analyzer.analyze()
    output_path : str | Path
        Chemin de sortie (ex: "reports/report_20250101.pdf")
    template_dir : str | Path
        Dossier contenant report.html et report.css
    lang : str
        Langue du rapport : "fr" (défaut) ou "en"

    Retourne
    --------
    output_path sous forme de string
    """
    lang = lang if lang in TRANSLATIONS else "fr"

    template_dir = Path(template_dir).resolve()
    output_path  = Path(output_path)

    html_path = template_dir / "report.html"
    css_path  = template_dir / "report.css"
    for p in [html_path, css_path]:
        if not p.exists():
            raise FileNotFoundError(f"Template introuvable : {p}")

    css_content = css_path.read_text(encoding="utf-8")

    context = _build_template_context(aci_log, finance_log, temporal_log, lang)
    context["css_content"] = css_content

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html")
    html_content = template.render(**context)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(
        string=html_content,
        base_url=str(template_dir),
    ).write_pdf(str(output_path))

    return str(output_path)
