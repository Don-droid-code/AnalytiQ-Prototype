"""
test_ingestion_mapper.py — AnalytiQ Pro
=========================================
Test d'intégration pour ingestion.py + mapper.py.

1. Charge test_temporal_complete.csv via ingestion.py
2. Passe le DataFrame dans mapper.py
3. Affiche dans le terminal :
   - ingestion_log complet
   - mapping colonne par colonne avec type + département
   - summary par département
   - Aucune erreur = test passé

Auteur  : Othmane Afif — othmane.afif@outlook.com
Projet  : AnalytiQ Pro — analytiq-pro.com
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from ingestion import ingest
from mapper import map_columns

# ─────────────────────────────────────────────
# CHEMINS
# ─────────────────────────────────────────────

CSV_PATH  = HERE / "test_temporal_complete.csv"
YAML_PATH = HERE / "traps_catalog.yaml"

for path, label in [(CSV_PATH, "CSV"), (YAML_PATH, "YAML")]:
    if not path.exists():
        print(f"ERREUR : {label} introuvable → {path}")
        sys.exit(1)

# ─────────────────────────────────────────────
# ÉTAPE 1 — INGESTION
# ─────────────────────────────────────────────

print("=" * 70)
print("  AnalytiQ Pro — Test ingestion.py + mapper.py")
print("=" * 70)
print(f"\n[ÉTAPE 1] Ingestion de : {CSV_PATH.name}\n")

df, ing_log = ingest(CSV_PATH)

# ── Affichage ingestion_log
print(f"  Status            : {ing_log['status']}")
print(f"  Fichier           : {ing_log['filename']}")
print(f"  Format            : {ing_log['format']}")
print(f"  Encodage détecté  : {ing_log['encoding_detected']}")
print(f"  Séparateur        : {ing_log['separator_detected']}")
print(f"  Lignes            : {ing_log['rows']}")
print(f"  Colonnes          : {ing_log['columns']}")
print(f"  Colonnes 100% NaN : {ing_log['columns_all_null_count']} → {ing_log.get('columns_all_null', [])}")
print(f"  Doublons lignes   : {ing_log['duplicate_rows_count']} ({ing_log['duplicate_rows_pct']}%)")
print(f"  Timestamp         : {ing_log['timestamp']}")
print()
print(f"  Colonnes du dataset :")
for i, col in enumerate(ing_log.get("column_names", []), 1):
    density = ing_log["column_density"].get(col, 0.0)
    print(f"    {i:2d}. {col:<35s} densité={density:.0%}")

if ing_log["status"] == "ERROR":
    print(f"\n  ERREUR INGESTION : {ing_log.get('error')}")
    sys.exit(1)

# ─────────────────────────────────────────────
# ÉTAPE 2 — MAPPING
# ─────────────────────────────────────────────

print()
print("=" * 70)
print("[ÉTAPE 2] Mapping sémantique des colonnes")
print("=" * 70)

df_out, map_log = map_columns(df, yaml_path=YAML_PATH)

# Vérification : le DataFrame n'a pas été modifié
assert df_out is df, "ERREUR CRITIQUE : mapper.py a retourné un DataFrame différent !"
assert list(df_out.columns) == list(df.columns), "ERREUR CRITIQUE : les colonnes ont été modifiées !"
assert len(df_out) == len(df), "ERREUR CRITIQUE : le nombre de lignes a changé !"

print(f"\n  YAML chargé       : {map_log['yaml_loaded']}")
print(f"  Colonnes analysées: {map_log['total_cols']}")
print(f"  Timestamp         : {map_log['timestamp']}")

# ── Mapping colonne par colonne
print()
print(f"  {'COLONNE':<35s} {'TYPE':<10s} {'CONF%':>6s}  {'DÉPARTEMENT':<20s} {'CONFIANCE'}")
print(f"  {'-'*35} {'-'*10} {'-'*6}  {'-'*20} {'-'*10}")

for col, info in map_log["mapping"].items():
    t      = info["detected_type"]
    t_conf = info["type_confidence_pct"]
    dept   = info["detected_department"]
    d_conf = info["dept_confidence"]

    type_icon = {
        "date":    "📅",
        "numeric": "🔢",
        "text":    "📝",
        "boolean": "☑️ ",
        "mixed":   "🔀",
        "empty":   "⬜",
    }.get(t, "❓")

    conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(d_conf, "⚪")

    print(f"  {col:<35s} {type_icon} {t:<8s} {t_conf:>5.1f}%  {dept:<20s} {conf_icon} {d_conf}")

# ── Summary par département
print()
print("=" * 70)
print("[RÉSUMÉ] Colonnes par département")
print("=" * 70)
print()
for dept, count in map_log["summary"].items():
    bar = "█" * count
    print(f"  {dept:<20s} : {count:2d} col(s)  {bar}")

# ── Summary par type
print()
print("[RÉSUMÉ] Colonnes par type détecté")
print("=" * 70)
print()
for t, count in map_log["type_summary"].items():
    bar = "█" * count
    print(f"  {t:<12s} : {count:2d} col(s)  {bar}")

# ── Colonnes inconnues
if map_log["unknown_cols"]:
    print()
    print(f"  [ATTENTION] {map_log['unknown_cols_count']} colonne(s) non classifiée(s) :")
    for col in map_log["unknown_cols"]:
        print(f"    - {col}")
else:
    print()
    print("  Aucune colonne non classifiée.")

# ─────────────────────────────────────────────
# RÉSULTAT FINAL
# ─────────────────────────────────────────────

print()
print("=" * 70)
errors = []
if ing_log["status"] != "OK":
    errors.append("Ingestion en erreur")
if df is None:
    errors.append("DataFrame None après ingestion")
if map_log["total_cols"] != ing_log["columns"]:
    errors.append(f"Colonnes mapper ({map_log['total_cols']}) != ingestion ({ing_log['columns']})")

if errors:
    print(f"  RÉSULTAT : ÉCHEC")
    for e in errors:
        print(f"    ❌ {e}")
    sys.exit(1)
else:
    print(f"  RÉSULTAT : PASS ✅")
    print(f"  ingestion.py  : OK — {ing_log['rows']} lignes × {ing_log['columns']} colonnes chargées")
    print(f"  mapper.py     : OK — {map_log['total_cols']} colonnes mappées, DataFrame inchangé")
print("=" * 70)
