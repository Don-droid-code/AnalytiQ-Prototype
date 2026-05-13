"""
test_hr_complete.py — AnalytiQ Pro
=====================================
Test d'intégration pour hr_analyzer.py.

1. Charge payroll_ma_300.csv
2. Instancie HRAnalyzer
3. Lance analyze()
4. Affiche terminal output complet :
   - 20/20 traps couverts
   - DETECTED / clean + occurrences
   - DQS final + bande + breakdown

Auteur  : Othmane Afif — othmane.afif@outlook.com
Projet  : AnalytiQ Pro — analytiq-pro.com
"""
# TODO V2 : remplacer pd.read_csv() par ingest()
# de ingestion.py pour cohérence avec le flux app.py

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from hr_analyzer import HRAnalyzer

# ─────────────────────────────────────────────
# CHEMINS
# ─────────────────────────────────────────────

CSV_PATH = HERE / "payroll_ma_300.csv"
if not CSV_PATH.exists():
    print(f"ERREUR : {CSV_PATH} introuvable.")
    sys.exit(1)

# ─────────────────────────────────────────────
# CHARGEMENT + ANALYSE
# ─────────────────────────────────────────────

df = pd.read_csv(CSV_PATH, dtype=str)

print("=" * 70)
print("  AnalytiQ Pro — HR Analyzer — Test Complet (20 Traps)")
print("=" * 70)
print(f"\nDataset : {CSV_PATH.name}")
print(f"Lignes  : {len(df)} | Colonnes : {len(df.columns)}")
print(f"Colonnes : {df.columns.tolist()}")

analyzer = HRAnalyzer(df)
results  = analyzer.analyze()

print(f"\nDepartment : {results['department']}")
print(f"Total traps : {results['total_traps']}")
print(f"Detected    : {results['traps_detected']}")
print(f"DQS Score   : {results['dqs_score']:.1f}/100")
print(f"Timestamp   : {results['timestamp']}")

# ─────────────────────────────────────────────
# TRAPS DÉCLENCHÉS
# ─────────────────────────────────────────────

detected_list = [r for r in results["results"] if r["detected"]]
clean_list    = [r for r in results["results"] if not r["detected"]]

print()
print("=" * 70)
print(f"  TRAPS DÉCLENCHÉS — {len(detected_list)} / 20")
print("=" * 70)

CRIT_ICON = {"Critical": "[CRIT]", "Common": "[COMM]"}

for r in detected_list:
    sev  = r["severity"]
    pen  = r["penalty"]
    occ  = r["occurrences"]
    cols = r["affected_columns"]
    icon = CRIT_ICON.get(sev, "[?]")
    print(f"\n  {icon}  [{r['trap_id']}] {r['label']}")
    print(f"       Sévérité   : {sev} | Pénalité base : {pen} pts")
    print(f"       Occurrences: {occ} | Colonnes : {cols}")
    print(f"       Détail     : {r['details']}")
    if r.get("mental_rule"):
        print(f"       Mental     : {r['mental_rule']}")

# ─────────────────────────────────────────────
# TRAPS PROPRES
# ─────────────────────────────────────────────

print()
print("=" * 70)
print(f"  TRAPS NON DÉCLENCHÉS — {len(clean_list)} / 20")
print("=" * 70)

for r in clean_list:
    print(f"\n  [OK]  [{r['trap_id']}] {r['label']}")
    print(f"        Raison : {r['details']}")

# ─────────────────────────────────────────────
# RÉSUMÉ COMPACT (format demandé)
# ─────────────────────────────────────────────

print()
print("=" * 70)
print("  RÉSUMÉ COMPACT")
print("=" * 70)
print()
for r in results["results"]:
    status = "DETECTED" if r["detected"] else "clean   "
    print(f"  {r['trap_id']} | {status} | occ={r['occurrences']:>4} | {r['label'][:40]}")

# ─────────────────────────────────────────────
# DQS BREAKDOWN
# ─────────────────────────────────────────────

print()
print("=" * 70)
print("  DQS — DATA QUALITY SCORE — BREAKDOWN")
print("=" * 70)

bd  = results["dqs_breakdown"]
dqs = results["dqs_score"]

print(f"\n  Base score        : {bd['base_score']}")
print(f"  Traps déclenchés  : {results['traps_detected']} / 20")
print()
print(f"  {'ID':<6} {'Pen':>4}  {'Occ':>5}  {'SevMult':>8}  {'CovFact':>8}  {'Déduction':>10}  Label")
print(f"  {'-'*6} {'-'*4}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*30}")

for b in bd["deductions"]:
    print(
        f"  {b['id']:<6} {b['penalty']:>4}  {b['occurrences']:>5}  "
        f"{b['severity_mult']:>8.4f}  {b['coverage_factor']:>8.2f}  "
        f"{b['deduction']:>10.4f}  "
    )

print(f"\n  Total déductions  : -{bd['total_deduction']:.4f} pts")

dqs_bands = [
    (85, "Elite",      "[ELITE]"),
    (70, "Acceptable", "[OK]"),
    (55, "Moderate",   "[WARN]"),
    (40, "Low",        "[LOW]"),
    ( 0, "Critical",   "[CRIT]"),
]
band, icon = next((b, i) for threshold, b, i in dqs_bands if dqs >= threshold)

print(f"""
  +==========================================+
  |   DQS FINAL  =  {dqs:6.1f} / 100           |
  |   Bande       :  {band:10s}  {icon:10s}  |
  +==========================================+""")

print()
print("=" * 70)
print(f"  FIN DU TEST — {results['total_traps']} traps HR vérifiés")
print("=" * 70)
