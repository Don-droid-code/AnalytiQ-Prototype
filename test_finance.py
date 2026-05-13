"""
test_finance.py — AnalytiQ Pro
================================
Test d'intégration pour finance_analyzer.py.

1. Charge finance_test_dataset.csv via ingestion.py
2. Passe dans mapper.py
3. Passe dans finance_analyzer.py
4. Affiche dans le terminal :
   - 23 traps vérifiés
   - Traps déclenchés avec pénalité + détail
   - Traps non déclenchés avec raison
   - DQS final + bande + breakdown

Auteur  : Othmane Afif — othmane.afif@outlook.com
Projet  : AnalytiQ Pro — analytiq-pro.com
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ingestion import ingest
from mapper import map_columns
from finance_analyzer import analyze

# ─────────────────────────────────────────────
# CHEMINS
# ─────────────────────────────────────────────

CSV_PATH  = HERE / "finance_test_dataset.csv"
YAML_PATH = HERE / "traps_catalog.yaml"

for path, label in [(CSV_PATH, "CSV"), (YAML_PATH, "YAML")]:
    if not path.exists():
        print(f"ERREUR : {label} introuvable → {path}")
        sys.exit(1)

# ─────────────────────────────────────────────
# ÉTAPE 1 — INGESTION
# ─────────────────────────────────────────────

print("=" * 70)
print("  AnalytiQ Pro — Finance Analyzer — Test Complet (23 Traps)")
print("=" * 70)
print(f"\n[ÉTAPE 1] Ingestion de : {CSV_PATH.name}")

df, ing_log = ingest(CSV_PATH)

if ing_log["status"] == "ERROR":
    print(f"  ERREUR INGESTION : {ing_log.get('error')}")
    sys.exit(1)

print(f"  Status   : {ing_log['status']}")
print(f"  Lignes   : {ing_log['rows']} | Colonnes : {ing_log['columns']}")
print(f"  Encodage : {ing_log['encoding_detected']} | Séparateur : {ing_log['separator_detected']}")
print(f"  NaN 100% : {ing_log['columns_all_null_count']} col(s) | Doublons : {ing_log['duplicate_rows_count']}")

# ─────────────────────────────────────────────
# ÉTAPE 2 — MAPPING
# ─────────────────────────────────────────────

print(f"\n[ÉTAPE 2] Mapping sémantique")

df_out, map_log = map_columns(df, yaml_path=YAML_PATH)

assert df_out is df, "ERREUR : mapper a retourné un DataFrame différent"
print(f"  Colonnes mappées : {map_log['total_cols']}")
print(f"  Résumé dépts     : { {k: v for k, v in map_log['summary'].items()} }")

# ─────────────────────────────────────────────
# ÉTAPE 3 — FINANCE ANALYZER
# ─────────────────────────────────────────────

print(f"\n[ÉTAPE 3] Finance Analyzer")

df_final, fin_log = analyze(df, yaml_path=YAML_PATH)

assert df_final is df, "ERREUR : finance_analyzer a retourné un DataFrame différent"

# ─────────────────────────────────────────────
# AFFICHAGE — TRAPS DÉCLENCHÉS
# ─────────────────────────────────────────────

triggered_list = fin_log["traps_triggered"]
all_results    = fin_log["all_results"]

print()
print("=" * 70)
print(f"  TRAPS DÉCLENCHÉS — {len(triggered_list)} / 23")
print("=" * 70)

CRIT_ICON = {"Critical": "[CRIT]", "Common": "[COMM]"}

for t in triggered_list:
    crit = t.get("criticality", "?")
    pen  = t.get("penalty", 0)
    occ  = t.get("occurrences", 0)
    cols = t.get("flagged_cols", [])
    icon = CRIT_ICON.get(crit, "[?]")
    print(f"\n  {icon}  [{t['id']}] {t.get('label', '')}")
    print(f"       Criticité  : {crit} | Pénalité base : {pen} pts")
    print(f"       Occurrences: {occ} | Colonnes : {cols}")
    print(f"       Détail     : {t.get('detail', '')}")
    print(f"       Mental     : {t.get('mental_rule', '')}")

# ─────────────────────────────────────────────
# AFFICHAGE — TRAPS NON DÉCLENCHÉS
# ─────────────────────────────────────────────

print()
print("=" * 70)
not_triggered = [v for v in all_results.values() if not v.get("triggered")]
print(f"  TRAPS NON DÉCLENCHÉS — {len(not_triggered)} / 23")
print("=" * 70)

for t in not_triggered:
    print(f"\n  [OK]  [{t['id']}] {t.get('label', '')}")
    print(f"        Raison : {t.get('detail', '')}")

# ─────────────────────────────────────────────
# DQS — BREAKDOWN
# ─────────────────────────────────────────────

print()
print("=" * 70)
print("  DQS — DATA QUALITY SCORE — BREAKDOWN")
print("=" * 70)

bd  = fin_log["dqs_breakdown"]
dqs = fin_log["dqs"]

print(f"\n  Base score        : {bd['base_score']}")
print(f"  Traps déclenchés  : {fin_log['traps_count']} / 23")
print()
print(f"  {'ID':<6} {'Pen':>4}  {'Occ':>5}  {'SevMult':>8}  {'CovFact':>8}  {'Déduction':>10}  Label")
print(f"  {'-'*6} {'-'*4}  {'-'*5}  {'-'*8}  {'-'*8}  {'-'*10}  {'-'*30}")
for b in bd["deductions"]:
    print(
        f"  {b['id']:<6} {b['penalty']:>4}  {b['occurrences']:>5}  "
        f"{b['severity_mult']:>8.4f}  {b['coverage_factor']:>8.2f}  "
        f"{b['deduction']:>10.4f}  {b['label'][:35]}"
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
  |   DQS FINAL  =  {dqs:6.2f} / 100           |
  |   Bande       :  {band:10s}  {icon:10s}  |
  +==========================================+""")

print(f"\n  Dataset analysé   : {fin_log['total_rows']} lignes × {fin_log['total_columns']} colonnes")
print(f"  Timestamp         : {fin_log['timestamp']}")
print()
print("=" * 70)
print(f"  FIN DU TEST — 23 traps Finance vérifiés")
print("=" * 70)
