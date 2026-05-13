"""
app.py — AnalytiQ Pro
=======================
Interface Streamlit complète bilingue EN/FR.
Upload → Département → Analyse → ACI → PDF.

Flux utilisateur :
    0. Sélecteur de langue (premier widget affiché)
    1. Upload dataset (CSV ou XLSX)
    2. Sélection département (Finance / HR / Temporal)
    3. Paramètres utilisateur (EXS)
    4. Analyse (Finance/HR + Temporal toujours)
    5. Affichage résultats (ACI, composantes, traps, AMS)
    6. Génération et téléchargement PDF

Règles architecturales :
    - Sélecteur langue = premier widget — avant l'upload
    - t = UI[lang] — toute l'interface utilise t[]
    - Temporal analyzer TOUJOURS exécuté en complément
    - Session state initialisé pour tous les logs
    - ingest() + map_columns() utilisés pour le chargement
    - generate_pdf() avec lang sélectionné

Auteur  : Othmane Afif — othmane.afif@outlook.com
Projet  : AnalytiQ Pro — analytiq-pro.com
Version : 1.1 — 2025 (interface bilingue EN/FR)
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from ingestion        import ingest
from mapper           import map_columns
from finance_analyzer import analyze as finance_analyze
from hr_analyzer      import HRAnalyzer
from temporal_analyzer import analyze as temporal_analyze
from aci_calculator   import calculate_aci
from report_generator import generate_pdf, _get_interpretation


# ─────────────────────────────────────────────
# DICTIONNAIRE DE TRADUCTIONS UI (FR / EN)
# ─────────────────────────────────────────────

UI = {
    "fr": {
        "app_caption":      "Audit data intelligent pour PMEs MENA",
        "step1_title":      "📁 Étape 1 — Chargement du dataset",
        "upload_label":     "Déposez votre fichier CSV ou XLSX",
        "upload_help":      "Formats acceptés : CSV (UTF-8, séparateur auto-détecté) et Excel (XLSX/XLS)",
        "upload_success":   "✅ Fichier chargé : **{name}** — {rows} lignes × {cols} colonnes",
        "upload_preview":   "👁️ Aperçu des 5 premières lignes",
        "upload_empty":     "Le fichier est vide ou non lisible.",
        "upload_error":     "Erreur lors du chargement : {error}",
        "step23_title":     "⚙️ Étape 2 & 3 — Département et paramètres",
        "dept_label":       "Département à analyser",
        "dept_finance":     "Finance (23 traps)",
        "dept_hr":          "HR — Ressources Humaines (20 traps)",
        "dept_temporal":    "Temporal — Analyse temporelle (22 traps)",
        "dept_help":        "Note : L analyse temporelle est toujours exécutée en complément pour calculer le TCS.",
        "exs_label":        "Executive Readiness Score (EXS)",
        "exs_help":         "Évaluez la qualité de communication de votre rapport analytique (0 = non préparé, 100 = rapport executive complet)",
        "lang_label":       "Langue du rapport / Report language",
        "btn_analyze":      "🔍 Analyser le dataset",
        "analyzing":        "Analyse en cours…",
        "no_file":          "Veuillez d abord charger un fichier.",
        "hr_missing":       "Colonnes HR non détectées dans ce dataset. Vérifiez le fichier ou choisissez Finance.",
        "analyze_error":    "Erreur lors de l analyse : {error}",
        "step5_title":      "📈 Étape 5 — Résultats de l analyse",
        "metric_aci":       "🎯 ACI",
        "metric_tcs":       "⏱️ TCS",
        "metric_dqs":       "💰 DQS Finance",
        "metric_ams":       "🏗️ AMS",
        "dedup_warning":    "⚠️ Déduplication appliquée : **{removed}** retiré car **{conflict}** détecté dans l analyse temporelle.",
        "components_title": "📊 Composantes ACI",
        "col_component":    "Composante",
        "col_description":  "Description",
        "col_score":        "Score",
        "col_weight":       "Poids",
        "col_contribution": "Contribution",
        "tcs_desc":         "Temporal Confidence Score",
        "dqs_desc":         "Data Quality Score Finance",
        "ams_desc":         "Analytical Maturity Score",
        "exs_desc":         "Executive Readiness Score",
        "finance_traps":    "💰 Traps Finance détectés ({n})",
        "no_finance":       "✅ Aucune anomalie Finance détectée.",
        "hr_traps":         "👥 Traps HR détectés ({n} / {total})",
        "no_hr":            "✅ Aucune anomalie HR détectée.",
        "temporal_traps":   "⏱️ Traps Temporels détectés ({n})",
        "no_temporal":      "✅ Aucune anomalie temporelle détectée.",
        "ams_checklist":    "🏗️ AMS Checklist ({n} items)",
        "ams_caption":      "AMS : {passed} / {total} items passés — Score : {score:.1f} / 100",
        "ams_unavailable":  "Checklist AMS non disponible.",
        "col_id":           "ID",
        "col_label":        "Label",
        "col_occurrences":  "Occurrences",
        "col_columns":      "Colonnes",
        "col_severity":     "Criticité",
        "col_penalty":      "Pénalité",
        "col_status":       "Statut",
        "col_item":         "Item",
        "col_detail":       "Détail",
        "step6_title":      "📄 Étape 6 — Rapport PDF",
        "lang_selected":    "Langue sélectionnée : {label}",
        "lang_fr_label":    "Français 🇫🇷",
        "lang_en_label":    "English 🇬🇧",
        "btn_pdf":          "📄 Générer le rapport PDF",
        "generating_pdf":   "Génération du PDF en cours…",
        "btn_download":     "⬇️ Télécharger le rapport (PDF)",
        "pdf_caption":      "PDF généré — {size:.0f} Ko",
        "pdf_error":        "Erreur lors de la génération PDF : {error}",
        "footer":           "AnalytiQ Pro™ — analytiq-pro.com — © Othmane Afif 2025",
        "lang_selector":    "🌐 Langue de l interface / Interface language",
    },
    "en": {
        "app_caption":      "Intelligent data audit for MENA SMEs",
        "step1_title":      "📁 Step 1 — Load Dataset",
        "upload_label":     "Drop your CSV or XLSX file here",
        "upload_help":      "Accepted formats: CSV (UTF-8, auto-detected separator) and Excel (XLSX/XLS)",
        "upload_success":   "✅ File loaded: **{name}** — {rows} rows × {cols} columns",
        "upload_preview":   "👁️ Preview — first 5 rows",
        "upload_empty":     "The file is empty or unreadable.",
        "upload_error":     "Error loading file: {error}",
        "step23_title":     "⚙️ Step 2 & 3 — Department and Parameters",
        "dept_label":       "Department to analyze",
        "dept_finance":     "Finance (23 traps)",
        "dept_hr":          "HR — Human Resources (20 traps)",
        "dept_temporal":    "Temporal — Time Analysis (22 traps)",
        "dept_help":        "Note: Temporal analysis is always run alongside to compute the TCS score.",
        "exs_label":        "Executive Readiness Score (EXS)",
        "exs_help":         "Rate the communication quality of your analytical report (0 = unprepared, 100 = full executive report)",
        "lang_label":       "Report language / Langue du rapport",
        "btn_analyze":      "🔍 Analyze Dataset",
        "analyzing":        "Analysis in progress…",
        "no_file":          "Please load a file first.",
        "hr_missing":       "HR columns not detected in this dataset. Check the file or select Finance.",
        "analyze_error":    "Analysis error: {error}",
        "step5_title":      "📈 Step 5 — Analysis Results",
        "metric_aci":       "🎯 ACI",
        "metric_tcs":       "⏱️ TCS",
        "metric_dqs":       "💰 DQS Finance",
        "metric_ams":       "🏗️ AMS",
        "dedup_warning":    "⚠️ Deduplication applied: **{removed}** removed because **{conflict}** detected in temporal analysis.",
        "components_title": "📊 ACI Components",
        "col_component":    "Component",
        "col_description":  "Description",
        "col_score":        "Score",
        "col_weight":       "Weight",
        "col_contribution": "Contribution",
        "tcs_desc":         "Temporal Confidence Score",
        "dqs_desc":         "Finance Data Quality Score",
        "ams_desc":         "Analytical Maturity Score",
        "exs_desc":         "Executive Readiness Score",
        "finance_traps":    "💰 Finance Traps Triggered ({n})",
        "no_finance":       "✅ No Finance anomaly detected.",
        "hr_traps":         "👥 HR Traps Triggered ({n} / {total})",
        "no_hr":            "✅ No HR anomaly detected.",
        "temporal_traps":   "⏱️ Temporal Traps Triggered ({n})",
        "no_temporal":      "✅ No temporal anomaly detected.",
        "ams_checklist":    "🏗️ AMS Checklist ({n} items)",
        "ams_caption":      "AMS: {passed} / {total} items passed — Score: {score:.1f} / 100",
        "ams_unavailable":  "AMS checklist not available.",
        "col_id":           "ID",
        "col_label":        "Label",
        "col_occurrences":  "Occurrences",
        "col_columns":      "Affected Columns",
        "col_severity":     "Severity",
        "col_penalty":      "Penalty",
        "col_status":       "Status",
        "col_item":         "Item",
        "col_detail":       "Detail",
        "step6_title":      "📄 Step 6 — PDF Report",
        "lang_selected":    "Selected language: {label}",
        "lang_fr_label":    "Français 🇫🇷",
        "lang_en_label":    "English 🇬🇧",
        "btn_pdf":          "📄 Generate PDF Report",
        "generating_pdf":   "Generating PDF…",
        "btn_download":     "⬇️ Download Report (PDF)",
        "pdf_caption":      "PDF generated — {size:.0f} KB",
        "pdf_error":        "PDF generation error: {error}",
        "footer":           "AnalytiQ Pro™ — analytiq-pro.com — © Othmane Afif 2025",
        "lang_selector":    "🌐 Interface language / Langue de l interface",
    },
}

# ─────────────────────────────────────────────
# CONFIGURATION PAGE
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="AnalytiQ Pro",
    page_icon="📊",
    layout="wide",
)

# ─────────────────────────────────────────────
# CHEMINS
# ─────────────────────────────────────────────

HERE         = Path(__file__).resolve().parent
YAML_PATH    = HERE / "traps_catalog.yaml"
TEMPLATE_DIR = HERE / "templates"
REPORTS_DIR  = HERE / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────

_STATE_DEFAULTS = {
    "analysis_done": False,
    "aci_log":       None,
    "finance_log":   None,
    "temporal_log":  None,
    "hr_log":        None,
    "df":            None,
    "department":    None,
    "lang":          "fr",
    "pdf_bytes":     None,
    "pdf_filename":  None,
}
for _key, _default in _STATE_DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default

# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────

st.title("📊 AnalytiQ Pro")
st.divider()

# ─────────────────────────────────────────────
# SÉLECTEUR DE LANGUE — PREMIER WIDGET AFFICHÉ
# ─────────────────────────────────────────────

lang_choice = st.radio(
    UI["fr"]["lang_selector"],
    options=["Français", "English"],
    horizontal=True,
    key="lang_selector",
)
lang = "fr" if lang_choice == "Français" else "en"
st.session_state.lang = lang
t = UI[lang]

st.caption(t["app_caption"])
st.divider()

# ─────────────────────────────────────────────
# ÉTAPE 1 — UPLOAD DATASET
# ─────────────────────────────────────────────

st.subheader(t["step1_title"])

uploaded_file = st.file_uploader(
    t["upload_label"],
    type=["csv", "xlsx", "xls"],
    help=t["upload_help"],
)

df_preview = None

if uploaded_file is not None:
    suffix = Path(uploaded_file.name).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = Path(tmp.name)

    try:
        df_raw, ingest_log = ingest(tmp_path)
    except Exception as e:
        st.error(t["upload_error"].format(error=e))
        st.stop()
    finally:
        tmp_path.unlink(missing_ok=True)

    if df_raw is None or df_raw.empty:
        st.warning(t["upload_empty"])
        st.stop()

    df_mapped, map_log = map_columns(df_raw, yaml_path=str(YAML_PATH))
    df_preview = df_mapped

    st.success(t["upload_success"].format(
        name=uploaded_file.name,
        rows=len(df_mapped),
        cols=len(df_mapped.columns),
    ))

    with st.expander(t["upload_preview"]):
        st.dataframe(df_mapped.head(5), use_container_width=True)

    st.session_state.df = df_mapped

# ─────────────────────────────────────────────
# ÉTAPE 2 & 3 — PARAMÈTRES
# ─────────────────────────────────────────────

st.divider()
st.subheader(t["step23_title"])

col1, col2 = st.columns(2)

with col1:
    department = st.radio(
        t["dept_label"],
        options=[t["dept_finance"], t["dept_hr"], t["dept_temporal"]],
        index=0,
        help=t["dept_help"],
    )

with col2:
    exs_score = st.slider(
        t["exs_label"],
        min_value=0,
        max_value=100,
        value=60,
        step=5,
        help=t["exs_help"],
    )

# ─────────────────────────────────────────────
# ÉTAPE 4 — BOUTON ANALYSER
# ─────────────────────────────────────────────

st.divider()

btn_analyze = st.button(
    t["btn_analyze"],
    type="primary",
    disabled=(st.session_state.df is None),
)

if btn_analyze:
    if st.session_state.df is None:
        st.warning(t["no_file"])
        st.stop()

    df = st.session_state.df

    with st.spinner(t["analyzing"]):
        try:
            _, temporal_log = temporal_analyze(df, yaml_path=str(YAML_PATH))
            st.session_state.temporal_log = temporal_log

            if department == t["dept_finance"]:
                _, finance_log = finance_analyze(df, yaml_path=str(YAML_PATH))
                st.session_state.finance_log = finance_log
                st.session_state.hr_log      = None
                st.session_state.department  = "Finance"

            elif department == t["dept_hr"]:
                hr_required = ["name", "salary", "hire_date", "job_title",
                               "department", "status", "employee"]
                cols_lower = [c.lower() for c in df.columns]
                has_hr_cols = any(
                    any(req in col for col in cols_lower)
                    for req in hr_required
                )
                if not has_hr_cols:
                    st.warning(t["hr_missing"])
                    st.stop()

                hr_analyzer = HRAnalyzer(df)
                hr_log = hr_analyzer.analyze()
                st.session_state.hr_log = hr_log

                finance_log = {
                    "analyzer":        "finance_analyzer",
                    "total_rows":      len(df),
                    "total_columns":   len(df.columns),
                    "traps_triggered": [],
                    "traps_count":     0,
                    "dqs":             100.0,
                }
                st.session_state.finance_log = finance_log
                st.session_state.department  = "HR"

            else:
                finance_log = {
                    "analyzer":        "finance_analyzer",
                    "total_rows":      len(df),
                    "total_columns":   len(df.columns),
                    "traps_triggered": [],
                    "traps_count":     0,
                    "dqs":             100.0,
                }
                st.session_state.finance_log = finance_log
                st.session_state.department  = "Temporal"

            aci_log = calculate_aci(
                temporal_log=st.session_state.temporal_log,
                finance_log=st.session_state.finance_log,
                df=df,
                yaml_path=str(YAML_PATH),
                exs_score=float(exs_score),
            )
            st.session_state.aci_log       = aci_log
            st.session_state.analysis_done = True
            st.session_state.pdf_bytes     = None
            st.session_state.pdf_filename  = None

        except Exception as e:
            st.error(t["analyze_error"].format(error=e))
            st.stop()

# ─────────────────────────────────────────────
# ÉTAPE 5 — AFFICHAGE DES RÉSULTATS
# ─────────────────────────────────────────────

if st.session_state.analysis_done and st.session_state.aci_log is not None:
    aci_log      = st.session_state.aci_log
    finance_log  = st.session_state.finance_log
    temporal_log = st.session_state.temporal_log
    hr_log       = st.session_state.hr_log

    aci      = aci_log["aci"]
    aci_band = aci_log["aci_band"]
    comps    = aci_log["components"]

    tcs = comps["tcs"]["score"]
    dqs = comps["dqs_finance"]["score"]
    ams = comps["ams"]["score"]
    exs = comps["exs"]["score"]

    st.divider()
    st.subheader(t["step5_title"])

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(t["metric_aci"], f"{aci:.1f} / 100", delta=aci_band)
    col2.metric(t["metric_tcs"], f"{tcs:.1f} / 100")
    col3.metric(t["metric_dqs"], f"{dqs:.1f} / 100")
    col4.metric(t["metric_ams"], f"{ams:.1f} / 100")

    st.progress(min(1.0, max(0.0, aci / 100)))

    interpretation = _get_interpretation(aci, lang)
    st.info(f"**{aci_band}** — {interpretation}")

    if aci_log.get("deduplication_applied"):
        for detail in aci_log.get("deduplication_details", []):
            st.warning(t["dedup_warning"].format(
                removed=detail["removed_trap"],
                conflict=detail["conflicts_found"],
            ))

    with st.expander(t["components_title"], expanded=True):
        components_data = {
            t["col_component"]:   ["TCS", "DQS Finance", "AMS", "EXS"],
            t["col_description"]: [t["tcs_desc"], t["dqs_desc"], t["ams_desc"], t["exs_desc"]],
            t["col_score"]:       [f"{tcs:.1f}", f"{dqs:.1f}", f"{ams:.1f}", f"{exs:.1f}"],
            t["col_weight"]:      ["35 %", "30 %", "25 %", "10 %"],
            t["col_contribution"]: [
                f"{comps['tcs']['weighted']:.2f}",
                f"{comps['dqs_finance']['weighted']:.2f}",
                f"{comps['ams']['weighted']:.2f}",
                f"{comps['exs']['weighted']:.2f}",
            ],
        }
        st.dataframe(pd.DataFrame(components_data), use_container_width=True, hide_index=True)

    finance_triggered = finance_log.get("traps_triggered", [])
    with st.expander(t["finance_traps"].format(n=len(finance_triggered)), expanded=len(finance_triggered) > 0):
        if finance_triggered:
            rows = []
            for trap in finance_triggered:
                fcols = trap.get("flagged_cols", [])
                rows.append({
                    t["col_id"]:          trap.get("id", "?"),
                    t["col_label"]:       trap.get("label", ""),
                    t["col_occurrences"]: trap.get("occurrences", 0),
                    t["col_columns"]:     ", ".join(str(c) for c in fcols[:3]) + ("…" if len(fcols) > 3 else ""),
                    t["col_severity"]:    trap.get("criticality", "—"),
                    t["col_penalty"]:     trap.get("penalty", 0),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success(t["no_finance"])

    if hr_log is not None:
        hr_triggered = [r for r in hr_log.get("results", []) if r.get("detected")]
        with st.expander(t["hr_traps"].format(n=len(hr_triggered), total=hr_log.get("total_traps", 20)), expanded=len(hr_triggered) > 0):
            if hr_triggered:
                rows = []
                for trap in hr_triggered:
                    hcols = trap.get("affected_columns", [])
                    rows.append({
                        t["col_id"]:          trap.get("trap_id", "?"),
                        t["col_label"]:       trap.get("label", ""),
                        t["col_occurrences"]: trap.get("occurrences", 0),
                        t["col_columns"]:     ", ".join(str(c) for c in hcols[:3]) + ("…" if len(hcols) > 3 else ""),
                        t["col_severity"]:    trap.get("severity", "—"),
                        t["col_penalty"]:     trap.get("penalty", 0),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.metric("DQS HR", f"{hr_log.get('dqs_score', 0):.1f} / 100")
            else:
                st.success(t["no_hr"])

    temporal_triggered = temporal_log.get("traps_triggered", [])
    with st.expander(t["temporal_traps"].format(n=len(temporal_triggered)), expanded=len(temporal_triggered) > 0):
        if temporal_triggered:
            rows = []
            for trap in temporal_triggered:
                detail_txt = trap.get("detail", "")
                rows.append({
                    t["col_id"]:      trap.get("id", "?"),
                    t["col_label"]:   trap.get("label", ""),
                    t["col_penalty"]: trap.get("penalty", 0),
                    t["col_detail"]:  detail_txt[:80] + ("…" if len(detail_txt) > 80 else ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success(t["no_temporal"])

    ams_checklist = aci_log.get("ams_checklist", [])
    with st.expander(t["ams_checklist"].format(n=len(ams_checklist)), expanded=False):
        if ams_checklist:
            rows = []
            for item in ams_checklist:
                rows.append({
                    t["col_status"]: "✓" if item.get("passed") else "✗",
                    t["col_item"]:   item.get("item", ""),
                    t["col_detail"]: item.get("reason", ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            passed = sum(1 for i in ams_checklist if i.get("passed"))
            st.caption(t["ams_caption"].format(passed=passed, total=len(ams_checklist), score=ams))
        else:
            st.info(t["ams_unavailable"])

    st.divider()
    st.subheader(t["step6_title"])

    lang_label = t["lang_fr_label"] if lang == "fr" else t["lang_en_label"]
    st.caption(t["lang_selected"].format(label=lang_label))

    btn_pdf = st.button(t["btn_pdf"], type="secondary")

    if btn_pdf:
        with st.spinner(t["generating_pdf"]):
            try:
                ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                pdf_filename = f"report_{ts}_{lang}.pdf"
                pdf_path     = REPORTS_DIR / pdf_filename

                generate_pdf(
                    aci_log=aci_log,
                    finance_log=finance_log,
                    temporal_log=temporal_log,
                    output_path=pdf_path,
                    template_dir=TEMPLATE_DIR,
                    lang=lang,
                )

                pdf_bytes = pdf_path.read_bytes()
                st.session_state.pdf_bytes    = pdf_bytes
                st.session_state.pdf_filename = pdf_filename

            except Exception as e:
                st.error(t["pdf_error"].format(error=e))
                st.stop()

    if st.session_state.pdf_bytes is not None:
        st.download_button(
            label=t["btn_download"],
            data=st.session_state.pdf_bytes,
            file_name=st.session_state.pdf_filename,
            mime="application/pdf",
        )
        size_kb = len(st.session_state.pdf_bytes) / 1024
        st.caption(t["pdf_caption"].format(size=size_kb))

# ─────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────

st.divider()
st.caption(t["footer"])
