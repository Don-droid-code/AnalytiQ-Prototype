# GROUND TRUTH — payroll_ma_300.csv
## AnalytiQ Pro — Étape 5 HR Analyzer

**Dataset :** payroll_ma_300.csv — 300 lignes × 16 colonnes  
**Générateur :** seed=42 (reproductible)  
**Date :** 2025

---

## Anomalies injectées par trap

| Trap | Label | Lignes concernées | Colonne(s) | Détail |
|---|---|---|---|---|
| **T11** | Impossible Age Values | 18 | `age` | Lignes 0–17 : âges 10, 11, 12, 90, 95, 100 |
| **T12** | Salary = 0 or Placeholder | 25 | `salary` | Lignes 20–34 : salary=0 / Lignes 35–39 : salary=9999 |
| **T13** | Job Title Category Explosion | 100 | `job_title` | Lignes 50–149 : 100 titres libres distincts type "Senior Lead AI Strategist" |
| **T14** | Sensitive Data Not Masked | 40 | `national_id`, `phone` | Lignes 0–29 : CIN marocain (B######) / Lignes 200–209 : IBAN-like dans phone |
| **T15** | Active + Termination Date | 15 | `status`, `termination_date` | Lignes 150–164 : status=Active ET termination_date non vide |
| **T16** | Non-Random Missing (MNAR) | ~30 | `termination_date` | Toutes les femmes (gender=F) Inactive ont termination_date effacée |
| **T17** | Incorrect Seniority | 25 | `seniority`, `hire_date` | Lignes 170–194 : ancienneté augmentée de +3 à +8 ans |
| **T18** | Status Not Historized | 50 | `status`, `updated_at` | Lignes 250–299 : updated_at vide → pas d'historisation |
| **T57** | Placeholder Values | 5 | `salary` | Lignes 195–199 : salary=9999 |
| **T58** | Logical Duplicates | 2 | `name` | Lignes 40–41 : "alice martin" / "ALICE MARTIN" → doublon logique |
| **T59** | Over-Cleaning | 30 | `salary` | Lignes 220–249 : salary=15000.0 (valeur identique) |
| **T60** | Temporal Logic Violated | 10 | `hire_date`, `termination_date` | Lignes 260–269 : hire_date=2025-06-01 > termination_date=2024-01-01 |
| **T61** | Business Rule Not Tracked | proxy | `hire_date` | Données sur 16 ans (2010–2026) sans colonne rule_version ni effective_date |
| **T62** | Uncontrolled Free-Text | 100+ | `job_title`, `phone` | job_title explosé (T13) + phone = ratio unique > 50% |
| **T63** | MNAR | 270 | `national_id` | 270/300 = 90% de national_id vides → taux bien au-dessus de la moyenne |
| **T64** | Name-Email Mismatch | 15 | `name`, `email` | Lignes 280–294 : name="Zara El Idrissi", email=x###@external.net |
| **T65** | Invalid Email Format | 10 | `email` | Lignes 270–279 : emails sans @, sans TLD, doubles @ |
| **T66** | Numeric in Text Field | 20 | `grade` | Lignes 100–119 : grade="1"/"2"/"3"/"4" mélangé à "A"/"B"/"C" |
| **T67** | Text in Numeric Field | 10 | `salary` | Lignes 205–214 : salary="N/A"/"TBD"/"NC"/"Voir RH" |
| **T69** | Statistical Outliers | 3 | `salary` | Lignes 295–297 : salary=999000 / 998000 / 997500 (Z >> 3) |

---

## Récapitulatif

| Type | Traps | Total lignes anomales (approximatif) |
|---|---|---|
| HR spécifiques | T11–T18 | ~175 lignes |
| CrossSector | T57–T69 | ~180 lignes (dont overlap) |

**Note :** certaines lignes cumulent plusieurs anomalies (ex : ligne 195 = T12 + T57).

---

*Fichier de référence pour validation du test 20/20 traps.*  
*AnalytiQ Pro — analytiq-pro.com*
