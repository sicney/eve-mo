from flask import Flask, request, jsonify
from flask_cors import CORS
from database import load_latest_analyzed

app = Flask(__name__)
CORS(app)

@app.route("/api/undervalued")
def undervalued():
    min_volume = int(request.args.get("min_volume", 50))
    limit = int(request.args.get("limit", 50))

    data = load_latest_analyzed(min_volume=min_volume, limit=limit)
    return jsonify(data)

@app.route("/api/ping")
def ping():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
