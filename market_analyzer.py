#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
EVE Online Market Analyzer (Daily Data - Jita Hub Fokus)

Funktionen:
- Holt tägliche Market-Historie (OHLC) aus der EVE ESI API
- Speichert Daten in SQLite
- Berechnet:
    - Rollierendes Mittel (True Value Approximation)
    - Rollierende Standardabweichung
    - z-Score (Abweichung vom Mittelwert)
    - Bollinger-Bänder
- Identifiziert:
    - BUY-Kandidaten: Preis deutlich UNTER True Value (negativer z-Score)
    - SELL-Kandidaten: Preis deutlich ÜBER True Value (positiver z-Score)

Jita Hub:
- Region "The Forge" = 10000002

Nutzung:
    python market_analyzer.py
"""

import os
import time
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import requests
import pandas as pd
from sqlalchemy import create_engine

# ==============================
# KONFIGURATION
# ==============================

ESI_BASE_URL = "https://esi.evetech.net/latest"

# SQLite-DB im Projektordner
DB_PATH = "eve_market.db"

# Jita-Hub: The Forge
JITA_REGION_ID = 10000002
REGION_IDS = [JITA_REGION_ID]

# TODO: Hier deine Ziel-Items (Type-IDs) eintragen
# Platzhalter: 34, 35, 36 (Tritanium, Pyerite, Mexallon)
TYPE_IDS = [
    34,
    35,
    36,
    # weitere Type-IDs ...
]

# Rolling-Window für True-Value-Schätzung (in Tagen)
ROLLING_WINDOW_DAYS = 30

# z-Score-Schwelle für Kauf-/Verkaufssignale
ZSCORE_THRESHOLD = 2.0

# Sleep zwischen Requests, um ESI nicht zu stressen
REQUEST_SLEEP_SECONDS = 0.3


# ==============================
# HILFSFUNKTIONEN: API
# ==============================

def fetch_market_history(region_id: int, type_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    Holt die Market-Historie (Daily OHLC) für eine Region/Type-Kombination.
    Endpoint: /markets/{region_id}/history/?type_id=TYPE_ID

    Rückgabe: Liste von Dicts mit Keys:
        - date
        - order_count
        - volume
        - lowest
        - highest
        - average
    """
    url = f"{ESI_BASE_URL}/markets/{region_id}/history/"
    params = {"type_id": type_id}

    try:
        response = requests.get(url, params=params, timeout=15)
        if response.status_code != 200:
            print(f"[WARN] HTTP {response.status_code} für Region {region_id}, Type {type_id}")
            return None
        data = response.json()
        if not isinstance(data, list):
            print(f"[WARN] Unerwartetes Datenformat für Region {region_id}, Type {type_id}")
            return None
        return data
    except Exception as e:
        print(f"[ERROR] Fehler beim Abruf für Region {region_id}, Type {type_id}: {e}")
        return None


# ==============================
# HILFSFUNKTIONEN: DATENBANK
# ==============================

def init_db(db_path: str = DB_PATH) -> None:
    """
    Initialisiert SQLite-Datenbank mit einfacher Market-History-Tabelle.
    """
    # Ordner sicherstellen (falls DB_PATH Unterordner hätte)
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS market_history (
            region_id   INTEGER NOT NULL,
            type_id     INTEGER NOT NULL,
            date        TEXT    NOT NULL,
            order_count INTEGER,
            volume      INTEGER,
            highest     REAL,
            lowest      REAL,
            average     REAL,
            PRIMARY KEY (region_id, type_id, date)
        );
        """
    )

    conn.commit()
    conn.close()


def store_market_history(region_id: int, type_id: int,
                         history: List[Dict[str, Any]],
                         db_path: str = DB_PATH) -> None:
    """
    Speichert Market-History-Einträge in SQLite.
    Upsert-Verhalten über PRIMARY KEY (region_id, type_id, date).
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for entry in history:
        date = entry.get("date")
        order_count = entry.get("order_count")
        volume = entry.get("volume")
        highest = entry.get("highest")
        lowest = entry.get("lowest")
        average = entry.get("average")

        cur.execute(
            """
            INSERT OR REPLACE INTO market_history
            (region_id, type_id, date, order_count, volume, highest, lowest, average)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (region_id, type_id, date, order_count, volume, highest, lowest, average)
        )

    conn.commit()
    conn.close()


# ==============================
# ANALYSEFUNKTIONEN
# ==============================

def load_history_to_dataframe(db_path: str = DB_PATH) -> pd.DataFrame:
    """
    Lädt gesamte Market-Historie als pandas DataFrame.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    df = pd.read_sql("SELECT * FROM market_history;", con=engine)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df


def compute_time_series_features(df: pd.DataFrame,
                                 rolling_window: int = ROLLING_WINDOW_DAYS) -> pd.DataFrame:
    """
    Berechnet auf Basis der Markt-Historie:
    - rolling_mean (True Value Approx.)
    - rolling_std
    - z_score
    - Bollinger-Bänder (upper_band, lower_band)
    """
    df_sorted = df.sort_values(["region_id", "type_id", "date"]).copy()

    def _apply_group(group: pd.DataFrame) -> pd.DataFrame:
        group = group.copy()
        group["rolling_mean"] = group["average"].rolling(
            window=rolling_window, min_periods=5
        ).mean()
        group["rolling_std"] = group["average"].rolling(
            window=rolling_window, min_periods=5
        ).std()

        group["z_score"] = (group["average"] - group["rolling_mean"]) / group["rolling_std"]

        group["upper_band"] = group["rolling_mean"] + 2 * group["rolling_std"]
        group["lower_band"] = group["rolling_mean"] - 2 * group["rolling_std"]

        return group

    df_features = df_sorted.groupby(["region_id", "type_id"], group_keys=False).apply(_apply_group)
    return df_features


def split_buy_sell_opportunities(
    df_features: pd.DataFrame,
    zscore_threshold: float = ZSCORE_THRESHOLD
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Splittet in BUY- und SELL-Kandidaten anhand des letzten Tages pro Region/Type.

    BUY:
        z_score <= -zscore_threshold  => Preis deutlich UNTER True Value
    SELL:
        z_score >=  zscore_threshold  => Preis deutlich ÜBER True Value
    """
    df_latest = df_features.sort_values("date").groupby(
        ["region_id", "type_id"]
    ).tail(1).copy()
    df_latest = df_latest.dropna(subset=["rolling_mean", "rolling_std", "z_score"])

    df_buy = df_latest[df_latest["z_score"] <= -zscore_threshold].copy()
    df_sell = df_latest[df_latest["z_score"] >= zscore_threshold].copy()

    # Ranking: stärkste Abweichung + Volumen
    for d in (df_buy, df_sell):
        d["abs_z"] = d["z_score"].abs()
        d["score"] = d["abs_z"] * (d["volume"].fillna(0) ** 0.5)

    df_buy = df_buy.sort_values("score", ascending=False)
    df_sell = df_sell.sort_values("score", ascending=False)

    return df_buy, df_sell


def print_buy_sell_lists(df_buy: pd.DataFrame, df_sell: pd.DataFrame) -> None:
    """
    Gibt BUY- und SELL-Listen kompakt auf der Konsole aus.
    """
    print("\n=======================")
    print("  BUY-KANDIDATEN (JITA)")
    print("=======================\n")

    if df_buy.empty:
        print("Keine BUY-Kandidaten (kein Item deutlich unter Mean bei aktuellem Threshold).")
    else:
        print(
            "Interpretation:\n"
            "  z_score << 0  => Preis deutlich UNTER True Value (Mean-Reversion BUY-Setup)\n"
        )
        cols = [
            "region_id", "type_id", "date",
            "average", "rolling_mean", "rolling_std",
            "z_score", "lower_band", "volume"
        ]
        dfb = df_buy[cols].copy()
        for col in ["average", "rolling_mean", "rolling_std", "z_score", "lower_band"]:
            dfb[col] = dfb[col].astype(float).round(3)
        print(dfb.to_string(index=False))

    print("\n========================")
    print("  SELL-KANDIDATEN (JITA)")
    print("========================\n")

    if df_sell.empty:
        print("Keine SELL-Kandidaten (kein Item deutlich über Mean bei aktuellem Threshold).")
    else:
        print(
            "Interpretation:\n"
            "  z_score >> 0  => Preis deutlich ÜBER True Value (Mean-Reversion SELL-Setup)\n"
        )
        cols = [
            "region_id", "type_id", "date",
            "average", "rolling_mean", "rolling_std",
            "z_score", "upper_band", "volume"
        ]
        dfs = df_sell[cols].copy()
        for col in ["average", "rolling_mean", "rolling_std", "z_score", "upper_band"]:
            dfs[col] = dfs[col].astype(float).round(3)
        print(dfs.to_string(index=False))


# ==============================
# MAIN WORKFLOW
# ==============================

def collect_and_store_all_history(region_ids: List[int], type_ids: List[int]) -> None:
    """
    Holt für alle Region/Type-Kombinationen die Market-History und speichert sie in die DB.
    """
    total_requests = len(region_ids) * len(type_ids)
    print(f"Starte Datenabruf für {total_requests} Region/Type-Kombinationen ...\n")

    count = 0
    for region_id in region_ids:
        for type_id in type_ids:
            count += 1
            print(f"[{count}/{total_requests}] Hole History für Region {region_id}, Type {type_id} ...")
            history = fetch_market_history(region_id, type_id)
            if history:
                store_market_history(region_id, type_id, history)
                print(f"    -> {len(history)} Einträge gespeichert.")
            else:
                print("    -> Keine Daten oder Fehler beim Abruf.")
            time.sleep(REQUEST_SLEEP_SECONDS)

    print("\nDatenabruf abgeschlossen.")


def main() -> None:
    start_time = datetime.now()
    print(f"=== EVE Market Analyzer (Jita Fokus) gestartet: {start_time} ===\n")

    print("Initialisiere Datenbank ...")
    init_db(DB_PATH)
    print("Datenbank bereit.\n")

    collect_and_store_all_history(REGION_IDS, TYPE_IDS)

    print("\nLade Daten aus DB und berechne Zeitreihen-Features ...")
    df = load_history_to_dataframe(DB_PATH)
    if df.empty:
        print("Keine Daten in der Datenbank. Abbruch.")
        return

    df_features = compute_time_series_features(df, ROLLING_WINDOW_DAYS)
    print("Zeitreihen-Features berechnet.\n")

    df_buy, df_sell = split_buy_sell_opportunities(df_features,
