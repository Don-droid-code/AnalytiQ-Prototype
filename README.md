# AnalytiQ Pro — Prototype

**Audit data intelligent pour PMEs MENA**

Prototype fonctionnel de la plateforme AnalytiQ Pro.
Détecte automatiquement 55 traps data sur 91,
calcule l'ACI (AnalytiQ Confidence Index),
et génère un rapport PDF professionnel bilingue FR/EN.

## Stack technique

- Python 3.11+
- Streamlit
- WeasyPrint + Jinja2
- pandas / pyyaml

## Lancer en local

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Démo en ligne

https://demo.analytiq-pro.com

## Corpus de détection

- 69 data traps sectoriels (T01-T69)
- 22 temporal traps (T70-T91)
- 91 traps total — 55 actifs en V1

## Auteur

Othmane Afif — othmane.afif@outlook.com  
https://analytiq-pro.com
