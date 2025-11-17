import sqlite3
import pandas as pd
from config import DB_PATH

def get_connection():
    return sqlite3.connect(DB_PATH)

def load_latest_analyzed(min_volume=50, limit=50):
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM market_history", conn)
    conn.close()

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").groupby("type_id").tail(1)

    df = df[df["volume"] >= min_volume]

    df["rolling_mean"] = df["average"].rolling(30, min_periods=5).mean()
    df["rolling_std"] = df["average"].rolling(30, min_periods=5).std()
    df["z_score"] = (df["average"] - df["rolling_mean"]) / df["rolling_std"]
    df["pct_diff"] = (df["average"] - df["rolling_mean"]) / df["rolling_mean"]

    df = df[df["z_score"] < 0]
    df = df.sort_values("z_score")

    return df.head(limit).to_dict(orient="records")
