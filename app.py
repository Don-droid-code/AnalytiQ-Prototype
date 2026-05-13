"""
app.py — AnalytiQ Pro
=======================
Interface Streamlit complète : Upload → Département → Analyse → ACI → PDF.

Flux utilisateur :
    1. Upload dataset (CSV ou XLSX)
    2. Sélection département (Finance / HR / Temporal)
    3. Paramètres utilisateur (EXS, langue)
    4. Analyse (Finance/HR + Temporal toujours)
    5. Affichage résultats (ACI, composantes, traps, AMS)
    6. Génération et téléchargement PDF

Règles architecturales :
    - Temporal analyzer TOUJOURS exécuté en complément
    - Session state initialisé pour tous les logs
    - ingest() + map_columns() utilisés pour le chargement
    - generate_pdf() avec lang sélectionné

Auteur  : Othmane Afif — othmane.afif@outlook.com
Projet  : AnalytiQ Pro — analytiq-pro.com
Version : 1.0 — 2025
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

# Modules AnalytiQ Pro
from ingestion       import ingest
from mapper          import map_columns
from finance_analyzer import analyze as finance_analyze
from hr_analyzer     import HRAnalyzer
from temporal_analyzer import analyze as temporal_analyze
from aci_calculator  import calculate_aci
from report_generator import generate_pdf

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
# SESSION STATE — initialisation
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
for key, default in _STATE_DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────

st.title("📊 AnalytiQ Pro")
st.caption("Audit data intelligent pour PMEs MENA")
st.divider()

# ─────────────────────────────────────────────
# ÉTAPE 1 — UPLOAD DATASET
# ─────────────────────────────────────────────

st.subheader("📁 Étape 1 — Chargement du dataset")

uploaded_file = st.file_uploader(
    "Déposez votre fichier CSV ou XLSX",
    type=["csv", "xlsx", "xls"],
    help="Formats acceptés : CSV (UTF-8, séparateur auto-détecté) et Excel (XLSX/XLS)",
)

df_preview = None

if uploaded_file is not None:
    # Sauvegarder dans un fichier temporaire pour ingest()
    suffix = Path(uploaded_file.name).suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getvalue())
        tmp_path = Path(tmp.name)

    try:
        df_raw, ingest_log = ingest(tmp_path)
    except Exception as e:
        st.error(f"Erreur lors du chargement : {e}")
        st.stop()
    finally:
        tmp_path.unlink(missing_ok=True)

    if df_raw is None or df_raw.empty:
        st.warning("Le fichier est vide ou non lisible.")
        st.stop()

    # Mapping sémantique des colonnes
    df_mapped, map_log = map_columns(df_raw, yaml_path=str(YAML_PATH))
    df_preview = df_mapped

    st.success(
        f"✅ Fichier chargé : **{uploaded_file.name}** — "
        f"{len(df_mapped)} lignes × {len(df_mapped.columns)} colonnes"
    )

    with st.expander("👁️ Aperçu des 5 premières lignes"):
        st.dataframe(df_mapped.head(5), use_container_width=True)

    st.session_state.df = df_mapped

# ─────────────────────────────────────────────
# ÉTAPE 2 & 3 — PARAMÈTRES
# ─────────────────────────────────────────────

st.divider()
st.subheader("⚙️ Étape 2 & 3 — Département et paramètres")

col1, col2 = st.columns(2)

with col1:
    department = st.radio(
        "Département à analyser",
        options=[
            "Finance (23 traps)",
            "HR — Ressources Humaines (20 traps)",
            "Temporal — Analyse temporelle (22 traps)",
        ],
        index=0,
        help=(
            "Note : L'analyse temporelle est toujours exécutée "
            "en complément pour calculer le TCS de l'ACI."
        ),
    )

with col2:
    exs_score = st.slider(
        "Executive Readiness Score (EXS)",
        min_value=0,
        max_value=100,
        value=60,
        step=5,
        help=(
            "Évaluez la qualité de communication de votre rapport analytique "
            "(0 = non préparé, 100 = rapport executive complet)"
        ),
    )
    lang_choice = st.radio(
        "Langue du rapport / Report language",
        options=["Français", "English"],
        index=0,
        horizontal=True,
    )

lang = "fr" if lang_choice == "Français" else "en"
st.session_state.lang = lang

# ─────────────────────────────────────────────
# ÉTAPE 4 — BOUTON ANALYSER
# ─────────────────────────────────────────────

st.divider()

btn_analyze = st.button(
    "🔍 Analyser le dataset",
    type="primary",
    disabled=(st.session_state.df is None),
)

if btn_analyze:
    if st.session_state.df is None:
        st.warning("Veuillez d'abord charger un fichier.")
        st.stop()

    df = st.session_state.df

    with st.spinner("Analyse en cours…"):
        try:
            # ── Analyse Temporal (TOUJOURS) ──────────────────
            _, temporal_log = temporal_analyze(df, yaml_path=str(YAML_PATH))
            st.session_state.temporal_log = temporal_log

            # ── Analyse principale selon département ─────────
            if department.startswith("Finance"):
                _, finance_log = finance_analyze(df, yaml_path=str(YAML_PATH))
                st.session_state.finance_log = finance_log
                st.session_state.hr_log      = None
                st.session_state.department  = "Finance"

            elif department.startswith("HR"):
                # Vérification colonnes HR minimales
                hr_required = ["name", "salary", "hire_date", "job_title",
                               "department", "status", "employee"]
                cols_lower = [c.lower() for c in df.columns]
                has_hr_cols = any(
                    any(req in col for col in cols_lower)
                    for req in hr_required
                )
                if not has_hr_cols:
                    st.warning(
                        "Colonnes HR non détectées dans ce dataset. "
                        "Vérifiez le fichier ou choisissez Finance."
                    )
                    st.stop()

                hr_analyzer = HRAnalyzer(df)
                hr_log = hr_analyzer.analyze()
                st.session_state.hr_log = hr_log

                # HR n'est pas dans l'ACI V1 — construire un finance_log minimal
                # pour alimenter aci_calculator (traps_triggered vide, dqs=100)
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
                # Temporal uniquement — finance_log minimal
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

            # ── Calcul ACI ───────────────────────────────────
            aci_log = calculate_aci(
                temporal_log=st.session_state.temporal_log,
                finance_log=st.session_state.finance_log,
                df=df,
                yaml_path=str(YAML_PATH),
                exs_score=float(exs_score),
            )
            st.session_state.aci_log      = aci_log
            st.session_state.analysis_done = True
            st.session_state.pdf_bytes    = None   # reset PDF précédent
            st.session_state.pdf_filename = None

        except Exception as e:
            st.error(f"Erreur lors de l'analyse : {e}")
            st.stop()

# ─────────────────────────────────────────────
# ÉTAPE 5 — AFFICHAGE DES RÉSULTATS
# ─────────────────────────────────────────────

if st.session_state.analysis_done and st.session_state.aci_log is not None:
    aci_log      = st.session_state.aci_log
    finance_log  = st.session_state.finance_log
    temporal_log = st.session_state.temporal_log
    hr_log       = st.session_state.hr_log
    department   = st.session_state.department

    aci       = aci_log["aci"]
    aci_band  = aci_log["aci_band"]
    comps     = aci_log["components"]

    tcs = comps["tcs"]["score"]
    dqs = comps["dqs_finance"]["score"]
    ams = comps["ams"]["score"]
    exs = comps["exs"]["score"]

    st.divider()
    st.subheader("📈 Étape 5 — Résultats de l'analyse")

    # ── Métriques principales ────────────────────────────
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🎯 ACI",         f"{aci:.1f} / 100",  delta=aci_band)
    col2.metric("⏱️ TCS",         f"{tcs:.1f} / 100")
    col3.metric("💰 DQS Finance", f"{dqs:.1f} / 100")
    col4.metric("🏗️ AMS",         f"{ams:.1f} / 100")

    # Barre de progression ACI
    st.progress(min(1.0, max(0.0, aci / 100)))

    # Interprétation
    from report_generator import _get_interpretation
    interpretation = _get_interpretation(aci, lang)
    st.info(f"**{aci_band}** — {interpretation}")

    # ── Déduplication ────────────────────────────────────
    if aci_log.get("deduplication_applied"):
        for detail in aci_log.get("deduplication_details", []):
            st.warning(
                f"⚠️ Déduplication appliquée : **{detail['removed_trap']}** retiré "
                f"car **{detail['conflicts_found']}** détecté dans l'analyse temporelle."
            )

    # ── Composantes ACI ──────────────────────────────────
    with st.expander("📊 Composantes ACI", expanded=True):
        components_data = {
            "Composante": ["TCS", "DQS Finance", "AMS", "EXS"],
            "Description": [
                "Temporal Confidence Score",
                "Data Quality Score Finance",
                "Analytical Maturity Score",
                "Expert Score",
            ],
            "Score": [
                f"{tcs:.1f}",
                f"{dqs:.1f}",
                f"{ams:.1f}",
                f"{exs:.1f}",
            ],
            "Poids": ["35 %", "30 %", "25 %", "10 %"],
            "Contribution": [
                f"{comps['tcs']['weighted']:.2f}",
                f"{comps['dqs_finance']['weighted']:.2f}",
                f"{comps['ams']['weighted']:.2f}",
                f"{comps['exs']['weighted']:.2f}",
            ],
        }
        st.dataframe(pd.DataFrame(components_data), use_container_width=True, hide_index=True)

    # ── Traps Finance ─────────────────────────────────────
    finance_triggered = finance_log.get("traps_triggered", [])
    with st.expander(
        f"💰 Traps Finance détectés ({len(finance_triggered)})",
        expanded=len(finance_triggered) > 0,
    ):
        if finance_triggered:
            rows = []
            for t in finance_triggered:
                cols = t.get("flagged_cols", [])
                rows.append({
                    "ID":          t.get("id", "?"),
                    "Label":       t.get("label", ""),
                    "Occurrences": t.get("occurrences", 0),
                    "Colonnes":    ", ".join(str(c) for c in cols[:3]) + ("…" if len(cols) > 3 else ""),
                    "Criticité":   t.get("criticality", "—"),
                    "Pénalité":    t.get("penalty", 0),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success("✅ Aucune anomalie Finance détectée.")

    # ── Traps HR (si département HR) ──────────────────────
    if hr_log is not None:
        hr_triggered = [r for r in hr_log.get("results", []) if r.get("detected")]
        with st.expander(
            f"👥 Traps HR détectés ({len(hr_triggered)} / {hr_log.get('total_traps', 20)})",
            expanded=len(hr_triggered) > 0,
        ):
            if hr_triggered:
                rows = []
                for t in hr_triggered:
                    cols = t.get("affected_columns", [])
                    rows.append({
                        "ID":          t.get("trap_id", "?"),
                        "Label":       t.get("label", ""),
                        "Occurrences": t.get("occurrences", 0),
                        "Colonnes":    ", ".join(str(c) for c in cols[:3]) + ("…" if len(cols) > 3 else ""),
                        "Sévérité":    t.get("severity", "—"),
                        "Pénalité":    t.get("penalty", 0),
                    })
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.metric("DQS HR", f"{hr_log.get('dqs_score', 0):.1f} / 100")
            else:
                st.success("✅ Aucune anomalie HR détectée.")

    # ── Traps Temporels ───────────────────────────────────
    temporal_triggered = temporal_log.get("traps_triggered", [])
    with st.expander(
        f"⏱️ Traps Temporels détectés ({len(temporal_triggered)})",
        expanded=len(temporal_triggered) > 0,
    ):
        if temporal_triggered:
            rows = []
            for t in temporal_triggered:
                rows.append({
                    "ID":          t.get("id", "?"),
                    "Label":       t.get("label", ""),
                    "Pénalité":    t.get("penalty", 0),
                    "Détail":      t.get("detail", "")[:80] + ("…" if len(t.get("detail", "")) > 80 else ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.success("✅ Aucune anomalie temporelle détectée.")

    # ── AMS Checklist ─────────────────────────────────────
    ams_checklist = aci_log.get("ams_checklist", [])
    with st.expander(f"🏗️ AMS Checklist ({len(ams_checklist)} items)", expanded=False):
        if ams_checklist:
            rows = []
            for item in ams_checklist:
                rows.append({
                    "Statut": "✓" if item.get("passed") else "✗",
                    "Item":   item.get("item", ""),
                    "Détail": item.get("reason", ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            passed = sum(1 for i in ams_checklist if i.get("passed"))
            st.caption(f"AMS : {passed} / {len(ams_checklist)} items passés — Score : {ams:.1f} / 100")
        else:
            st.info("Checklist AMS non disponible.")

    # ─────────────────────────────────────────────
    # ÉTAPE 6 — GÉNÉRATION ET TÉLÉCHARGEMENT PDF
    # ─────────────────────────────────────────────

    st.divider()
    st.subheader("📄 Étape 6 — Rapport PDF")

    lang_label = "Français 🇫🇷" if lang == "fr" else "English 🇬🇧"
    st.caption(f"Langue sélectionnée : {lang_label}")

    btn_pdf = st.button("📄 Générer le rapport PDF", type="secondary")

    if btn_pdf:
        with st.spinner("Génération du PDF en cours…"):
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
                st.error(f"Erreur lors de la génération PDF : {e}")
                st.stop()

    # Bouton de téléchargement (visible dès que pdf_bytes est disponible)
    if st.session_state.pdf_bytes is not None:
        dl_label = (
            "⬇️ Télécharger le rapport (PDF)"
            if lang == "fr"
            else "⬇️ Download Report (PDF)"
        )
        st.download_button(
            label=dl_label,
            data=st.session_state.pdf_bytes,
            file_name=st.session_state.pdf_filename,
            mime="application/pdf",
        )
        size_kb = len(st.session_state.pdf_bytes) / 1024
        st.caption(f"PDF généré — {size_kb:.0f} Ko")

# ─────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────

st.divider()
st.caption("AnalytiQ Pro™ — analytiq-pro.com — © Othmane Afif 2025")
