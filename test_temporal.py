"""
test_temporal.py — AnalytiQ Pro
================================
Script de test pour temporal_analyzer.py.
Charge test_temporal_complete.csv — conçu pour déclencher
les 22 traps temporels (T70–T91).

Affiche :
  - Les 22 traps vérifiés
  - Ceux déclenchés : pénalité + détail + mental rule
  - Ceux non déclenchés : raison
  - TCS final + bande + breakdown complet
"""

import sys
from pathlib import Path

import pandas as pd

# --- Résolution des chemins ---
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from temporal_analyzer import analyze

# --- YAML path ---
for candidate in [
    HERE / "traps_catalog.yaml",
    HERE / "data" / "traps_catalog.yaml",
    HERE.parent / "data" / "traps_catalog.yaml",
]:
    if candidate.exists():
        YAML_PATH = candidate
        break
else:
    print("ERREUR : traps_catalog.yaml introuvable.")
    sys.exit(1)

# --- CSV path ---
CSV_PATH = HERE / "test_temporal_complete.csv"
if not CSV_PATH.exists():
    print(f"ERREUR : {CSV_PATH} introuvable.")
    sys.exit(1)

# ─────────────────────────────────────────────
# CHARGEMENT
# ─────────────────────────────────────────────

df = pd.read_csv(CSV_PATH, dtype=str)  # dtype=str pour préserver tous les formats bruts

print("=" * 70)
print("  AnalytiQ Pro — Temporal Analyzer — Test Complet (22 Traps)")
print("=" * 70)
print(f"\nDataset : {CSV_PATH.name}")
print(f"Lignes  : {len(df)} | Colonnes : {len(df.columns)}")
print(f"YAML    : {YAML_PATH}")
print(f"\nColonnes du dataset :")
for i, col in enumerate(df.columns, 1):
    print(f"  {i:2d}. {col}")
print()

# ─────────────────────────────────────────────
# ANALYSE
# ─────────────────────────────────────────────

df_out, log = analyze(df, yaml_path=YAML_PATH)

# ─────────────────────────────────────────────
# AFFICHAGE — TRAPS DÉCLENCHÉS
# ─────────────────────────────────────────────

triggered_list = log["traps_triggered"]
all_results    = log["all_results"]

print("=" * 70)
print(f"  TRAPS DÉCLENCHÉS — {len(triggered_list)} / 22")
print("=" * 70)

if not triggered_list:
    print("  Aucun trap temporel déclenché.")
else:
    for t in triggered_list:
        crit = t.get("criticality", "?")
        pen  = t.get("penalty", 0)
        icon = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}.get(crit, "⚪")

        # Cas spéciaux
        note = ""
        bd = log["tcs_breakdown"]
        if t["id"] == "T70" and bd["t70_doubled"]:
            note = f"  ⚡ DOUBLEMENT ACTIF (colonne financière) → pénalité effective : −{pen*2} pts"
        if t["id"] == "T89" and bd["t89_extra"]:
            note = "  ⚡ + 5 pts additionnels appliqués"
        if t["id"] == "T91" and bd["t91_cap_applied"]:
            note = "  ⚡ PLAFOND 30 pts appliqué au TCS"

        print(f"\n  {icon}  [{t['id']}] {t.get('label', '')}")
        print(f"       Criticité : {crit} | Pénalité de base : −{pen} pts")
        if note:
            print(f"      {note}")
        print(f"       Détail    : {t.get('detail', '')}")
        print(f"       Mental    : {t.get('mental_rule', '')}")

# ─────────────────────────────────────────────
# AFFICHAGE — TRAPS NON DÉCLENCHÉS
# ─────────────────────────────────────────────

print()
print("=" * 70)
print(f"  TRAPS NON DÉCLENCHÉS — {22 - len(triggered_list)} / 22")
print("=" * 70)

not_triggered = [v for v in all_results.values() if not v.get("triggered")]
for t in not_triggered:
    print(f"\n  ✅  [{t['id']}] {t.get('label', '')}")
    print(f"       Raison : {t.get('detail', '')}")

# ─────────────────────────────────────────────
# TCS — BREAKDOWN COMPLET
# ─────────────────────────────────────────────

print()
print("=" * 70)
print("  TCS — TEMPORAL CONFIDENCE SCORE — BREAKDOWN COMPLET")
print("=" * 70)

bd = log["tcs_breakdown"]
tcs = log["tcs"]

print(f"\n  Base score              :  {bd['base_score']}")
print(f"  Nombre de traps actifs :  {log['traps_count']} / 22")
print()
print("  Détail des pénalités appliquées :")
for p in bd["penalties"]:
    note_str = f"  <- {p['note']}" if p.get("note") else ""
    print(f"    [{p['id']}]  -{p['penalty']} pts{note_str}")
print(f"\n  Total pénalités         : -{bd['total_penalty']} pts")

if bd["t70_doubled"]:
    print("  T70 doublé              :  OUI (colonne financière présente)")
if bd["t89_extra"]:
    print("  T89 malus additionnel   :  OUI (-5 pts inclus ci-dessus)")
if bd["t91_cap_applied"]:
    print("  T91 plafond 30 pts      :  OUI (TCS ramené à 30 max)")

band_map = [
    (85, "Elite",      "TROPHY"),
    (70, "Acceptable", "OK"),
    (55, "Moderate",   "WARNING"),
    (40, "Low",        "ALERT"),
    ( 0, "Critical",   "CRITICAL"),
]
band, icon = next((b, i) for threshold, b, i in band_map if tcs >= threshold)

print(f"""
  +========================================+
  |   TCS FINAL  =  {tcs:6.2f} / 100          |
  |   Bande      :  {band:10s}  [{icon}]  |
  +========================================+""")

print(f"\n  Colonnes date identifiées : {log['date_columns']}")
print(f"  Timestamp analyse         : {log['timestamp']}")
print()
print("=" * 70)
print("  FIN DU TEST — 22 traps couverts")
print("=" * 70)
