# eve-mo
eve market opportunities
# EVE Market Opportunities (eve-mo)

Daily Market Analyzer für EVE Online (Jita / The Forge).

## Features

- Holt tägliche Market-History aus ESI (Region The Forge, Jita Hub)
- Speichert Daten in SQLite (`eve_market.db`)
- Berechnet:
  - Rolling Mean (True Value Approximation)
  - Rolling Std
  - z-Score
  - Bollinger-Bänder
- Erzeugt:
  - `jita_buy_candidates.csv`
  - `jita_sell_candidates.csv`

## Usage

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python market_analyzer.py
