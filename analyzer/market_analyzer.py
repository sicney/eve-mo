#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
EVE Online Market Analyzer (Jita Hub, Market Groups)

Funktionen:
- Ermittelt Typen (Items), deren Market-Group-Hierarchie unter folgenden
  Root-Market-Groups liegt:
    - Ship Equipment
    - Ship and Module Modifications (Rigs)
    - Ammunition & Charges (Consumables)
- Holt tägliche Market-Historie (Daily OHLC) aus der EVE ESI API (Region The Forge / Jita)
- Speichert Daten in SQLite
- Berechnet:
    - Rollierendes Mittel (True Value Approximation)
    - Rollierende Standardabweichung
    - z-Score (Abweichung vom Mittelwert)
    - Bollinger-Bänder
- Identifiziert:
    - Top 50 Items, deren aktueller Preis am stärksten UNTER dem Mean liegt

Outputs:
- SQLite-DB: eve_market.db
- CSV: jita_undervalued_top50.csv
- Konsolen-Ausgabe der Top 50

Nutzung:
    python market_analyzer.py
"""

import os
import time
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set

import requests
import pandas as pd
from sqlalchemy import create_engine

# ==============================
# KONFIGURATION
# ==============================

ESI_BASE_URL = "https://esi.evetech.net/latest"

# Optional: bitte anpassen (Char-Name / Kontakt)
USER_AGENT = "eve-mo-market-analyzer/0.1 (contact: your_name_or_mail)"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": USER_AGENT,
}

# SQLite-DB im Projektordner
DB_PATH = "eve_market.db"

# Jita-Hub: The Forge
JITA_REGION_ID = 10000002
REGION_IDS = [JITA_REGION_ID]

# Ziel-Market-Groups (Root-Level)
# -> bewusst NUR Ship Equipment, Rigs, Ammo/Charges
TARGET_MARKET_GROUP_ROOT_NAMES = [
    "Ship Equipment",
    "Ship and Module Modifications",  # Rigs
    "Ammunition & Charges",           # Consumables
]

# Maximalzahl an Typen, die wir pro Run berücksichtigen
# (um Laufzeit/ESI-Load zu begrenzen)
MAX_TYPES = 2000

# Rolling-Window für True-Value-Schätzung (in Tagen)
ROLLING_WINDOW_DAYS = 30

# z-Score-Schwelle für "unterbewertet" (negativ)
ZSCORE_THRESHOLD = 1.5

# Sleep zwischen Requests, um ESI nicht zu stressen (Sekunden)
REQUEST_SLEEP_SECONDS = 0.25

# Maximale Versuche pro Request bei temporären Fehlern
MAX_RETRIES = 3


# ==============================
# ESI HILFSFUNKTIONEN
# ==============================

def esi_get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[requests.Response]:
    """
    Wrapper für GET-Requests gegen ESI mit einfachem Retry und Rate-Limit-Handling.
    """
    url = f"{ESI_BASE_URL}{path}"
    params = params or {}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
        except Exception as e:
            print(f"[ERROR] Request-Exception {url}: {e} (Versuch {attempt}/{MAX_RETRIES})")
            time.sleep(2 * attempt)
            continue

        if resp.status_code in (420, 429, 500, 502, 503, 504):
            print(f"[WARN] HTTP {resp.status_code} für {url}, Versuche {attempt}/{MAX_RETRIES}")
            time.sleep(5 * attempt)
            continue

        if resp.status_code != 200:
            print(f"[WARN] HTTP {resp.status_code} für {url}, keine weiteren Versuche.")
            return None

        return resp

    print(f"[ERROR] Maximale Versuche überschritten für {url}")
    return None


# ==============================
# MARKET GROUP DISCOVERY
# ==============================

def fetch_all_market_group_ids() -> List[int]:
    """
    Holt alle Market-Group-IDs (paginierter Endpoint /markets/groups/).
    """
    ids: List[int] = []
    page = 1

    while True:
        resp = esi_get("/markets/groups/", params={"page": page})
        if resp is None:
            break
        chunk = resp.json()
        if not chunk:
            break
        ids.extend(chunk)

        x_pages = resp.headers.get("X-Pages")
        if not x_pages:
            break
        if page >= int(x_pages):
            break
        page += 1

    print(f"[INFO] Anzahl Market-Groups: {len(ids)}")
    return ids


def discover_type_ids_for_target_market_groups() -> List[int]:
    """
    Ermittelt alle Typ-IDs (Items), deren Market-Group-Hierarchie unter
    einer der TARGET_MARKET_GROUP_ROOT_NAMES liegt.
    """
    print("[INFO] Lade alle Market-Groups aus ESI ...")
    group_ids = fetch_all_market_group_ids()
    if not group_ids:
        print("[ERROR] Konnte Market-Group-IDs nicht laden.")
        return []

    groups: Dict[int, Dict[str, Any]] = {}
    count = 0

    for gid in group_ids:
        count += 1
        if count % 100 == 0:
            print(f"[INFO] Verarbeite Market-Group {count}/{len(group_ids)} ...")

        resp = esi_get(f"/markets/groups/{gid}/", params={"language": "en-us"})
        if resp is None:
            continue
        data = resp.json()

        name = data.get("name")
        parent_id = data.get("parent_group_id")
        types = data.get("types", []) or []

        groups[gid] = {
            "name": name,
            "parent_id": parent_id,
            "types": types,
        }

        time.sleep(REQUEST_SLEEP_SECONDS / 2)

    # Root-Gruppen anhand Name finden
    root_ids: Set[int] = set()
    for gid, info in groups.items():
        if info["name"] in TARGET_MARKET_GROUP_ROOT_NAMES:
            root_ids.add(gid)

    print(f"[INFO] Root-Market-Groups gefunden: {root_ids}")
    if not root_ids:
        print("[ERROR] Keine passenden Root-Market-Groups gefunden. Namen prüfen.")
        return []

    cache_is_target: Dict[int, bool] = {}

    def is_under_target_root(gid: int) -> bool:
        if gid in cache_is_target:
            return cache_is_target[gid]
        seen: Set[int] = set()
        current = gid
        while current is not None and current in groups and current not in seen:
            seen.add(current)
            if current in root_ids:
                cache_is_target[gid] = True
                return True
            parent = groups[current]["parent_id"]
            current = parent
        cache_is_target[gid] = False
        return False

    type_ids: Set[int] = set()

    for gid, info in groups.items():
        if not info["types"]:
            continue
        if not is_under_target_root(gid):
            continue
        type_ids.update(info["types"])

    type_id_list = sorted(list(type_ids))
    print(f"[INFO] Gefundene Typen unter Ziel-Market-Groups (ungefiltert): {len(type_id_list)}")

    # Hard-Cap auf MAX_TYPES (für Laufzeit)
    if len(type_id_list) > MAX_TYPES:
        type_id_list = type_id_list[:MAX_TYPES]
        print(f"[INFO] Typenliste auf MAX_TYPES={MAX_TYPES} gekürzt.")

    print(f"[INFO] Typen, die im Run berücksichtigt werden: {len(type_id_list)}")
    return type_id_list


# ==============================
# DATENBANK
# ==============================

def init_db(db_path: str = DB_PATH) -> None:
    """
    Initialisiert SQLite-Datenbank:
    - market_history: Zeitreihen
    - type_names: Mapping type_id -> Name
    """
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS type_names (
            type_id INTEGER PRIMARY KEY,
            name    TEXT NOT NULL
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
    Upsert über PRIMARY KEY (region_id, type_id, date).
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


def fetch_type_name_from_db(type_id: int, conn: sqlite3.Connection) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM type_names WHERE type_id = ?", (type_id,))
    row = cur.fetchone()
    return row[0] if row else None


def save_type_name_to_db(type_id: int, name: str, conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO type_names (type_id, name) VALUES (?, ?)",
        (type_id, name),
    )
    conn.commit()


def fetch_type_name_from_esi(type_id: int) -> Optional[str]:
    resp = esi_get(f"/universe/types/{type_id}/", params={"language": "en-us"})
    if resp is None:
        return None
    data = resp.json()
    return data.get("name")


def get_type_name(type_id: int, db_path: str = DB_PATH) -> str:
    """
    Holt den Typnamen aus der DB oder (falls nicht vorhanden) aus ESI und cached ihn.
    """
    conn = sqlite3.connect(db_path)
    name = fetch_type_name_from_db(type_id, conn)
    if name:
        conn.close()
        return name

    name = fetch_type_name_from_esi(type_id) or f"type_id_{type_id}"
    save_type_name_to_db(type_id, name, conn)
    conn.close()
    return name


# ==============================
# MARKET HISTORY ABFRAGE
# ==============================

def fetch_market_history(region_id: int, type_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    Holt die Market-Historie (Daily OHLC) für eine Region/Type-Kombination.
    Endpoint: /markets/{region_id}/history/?type_id=TYPE_ID
    """
    resp = esi_get(f"/markets/{region_id}/history/", params={"type_id": type_id})
    if resp is None:
        return None
    data = resp.json()
    if not isinstance(data, list):
        print(f"[WARN] Unerwartetes Format für Region {region_id}, Type {type_id}")
        return None
    return data


def collect_and_store_all_history(region_ids: List[int], type_ids: List[int]) -> None:
    """
    Holt für alle Region/Type-Kombinationen die Market-History und speichert sie in die DB.
    """
    total_requests = len(region_ids) * len(type_ids)
    print(f"[INFO] Starte Datenabruf für {total_requests} Region/Type-Kombinationen ...\n")

    count = 0
    for region_id in region_ids:
        for type_id in type_ids:
            count += 1
            print(f"[INFO] [{count}/{total_requests}] History für Region {region_id}, Type {type_id} ...")
            history = fetch_market_history(region_id, type_id)
            if history:
                store_market_history(region_id, type_id, history)
                print(f"    -> {len(history)} Einträge gespeichert.")
            else:
                print("    -> Keine Daten oder Fehler beim Abruf.")
            time.sleep(REQUEST_SLEEP_SECONDS)

    print("\n[INFO] Datenabruf abgeschlossen.")


# ==============================
# ANALYSEFUNKTIONEN
# ==============================

def compute_time_series_features(df: pd.DataFrame,
                                 rolling_window: int = ROLLING_WINDOW_DAYS) -> pd.DataFrame:
    """
    Berechnet:
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


def find_buy_opportunities(df_features: pd.DataFrame,
                           zscore_threshold: float = ZSCORE_THRESHOLD) -> pd.DataFrame:
    """
    Sucht nach BUY-Kandidaten (Preis deutlich UNTER Mean).

    BUY:
        z_score <= -zscore_threshold

    Es wird immer nur der letzte Tag pro Region/Type betrachtet.
    """
    df_latest = df_features.sort_values("date").groupby(
        ["region_id", "type_id"]
    ).tail(1).copy()

    df_latest = df_latest.dropna(subset=["rolling_mean", "rolling_std", "z_score"])

    df_buy = df_latest[df_latest["z_score"] <= -zscore_threshold].copy()
    if df_buy.empty:
        return df_buy

    # Ranking: primär stärkster negativer z-Score, sekundär Volumen
    df_buy = df_buy.sort_values(
        by=["z_score", "volume"],
        ascending=[True, False]
    )

    return df_buy


def attach_type_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fügt type_name-Spalte hinzu basierend auf type_id (via ESI + DB-Cache).
    """
    if df.empty:
        return df
    df = df.copy()
    df["type_name"] = df["type_id"].apply(lambda tid: get_type_name(int(tid)))
    return df


def print_top_undervalued(df_buy_top: pd.DataFrame) -> None:
    """
    Druckt eine kompakte Tabelle der Top-50 Unterbewerteten Items.
    """
    print("\n==============================")
    print("  TOP 50 UNDERVALUED (JITA)")
    print("==============================\n")

    if df_buy_top.empty:
        print("Keine BUY-Kandidaten gefunden (z-Score Threshold evtl. zu streng).")
        return

    print(
        "Interpretation:\n"
        "  z_score << 0  => Preis deutlich UNTER True Value (Mean-Reversion BUY-Setup)\n"
    )

    cols = [
        "region_id",
        "type_id",
        "type_name",
        "date",
        "average",
        "rolling_mean",
        "rolling_std",
        "z_score",
        "lower_band",
        "volume",
    ]

    df_disp = df_buy_top[cols].copy()
    for col in ["average", "rolling_mean", "rolling_std", "z_score", "lower_band"]:
        df_disp[col] = df_disp[col].astype(float).round(3)

    print(df_disp.to_string(index=False))


# ==============================
# MAIN WORKFLOW
# ==============================

def main() -> None:
    start_time = datetime.now()
    print(f"=== EVE Market Analyzer (Jita, Market Groups) gestartet: {start_time} ===\n")

    # DB initialisieren
    print("[INFO] Initialisiere Datenbank ...")
    init_db(DB_PATH)
    print("[INFO] Datenbank bereit.\n")

    # Typen ermitteln
    print("[INFO] Ermittele Typen für Ziel-Market-Groups ...")
    type_ids = discover_type_ids_for_target_market_groups()
    if not type_ids:
        print("[ERROR] Keine Typen für Ziel-Market-Groups gefunden. Abbruch.")
        return

    print(f"[INFO] Anzahl Typen im Run: {len(type_ids)}\n")

    # Market-History abrufen & speichern
    collect_and_store_all_history(REGION_IDS, type_ids)

    # Daten laden
    print("\n[INFO] Lade Daten aus DB und berechne Zeitreihen-Features ...")
    df = load_history_to_dataframe(DB_PATH)
    if df.empty:
        print("[ERROR] Keine Daten in der Datenbank. Abbruch.")
        return

    df_features = compute_time_series_features(df, ROLLING_WINDOW_DAYS)
    print("[INFO] Zeitreihen-Features berechnet.\n")

    # BUY-Kandidaten finden
    df_buy = find_buy_opportunities(df_features, ZSCORE_THRESHOLD)
    if df_buy.empty:
        print("[INFO] Keine BUY-Kandidaten gefunden.")
        return

    # Top 50 Unterbewertete (nach z_score & Volumen)
    df_buy_top = df_buy.head(50)
    df_buy_top = attach_type_names(df_buy_top)

    output_csv = "jita_undervalued_top50.csv"
    df_buy_top.to_csv(output_csv, index=False)
    print(f"[INFO] Top 50 Unterbewertete gespeichert in: {output_csv}")

    # Ausgabe
    print_top_undervalued(df_buy_top)

    end_time = datetime.now()
    print(f"\n=== Fertig. Laufzeit: {end_time - start_time} ===")


if __name__ == "__main__":
    main()
