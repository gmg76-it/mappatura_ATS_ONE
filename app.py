import os
from typing import Any, Dict, Optional

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def error_response(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def geocode_query(query: str) -> Dict[str, Any]:
    if not API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY non configurata")

    resp = requests.get(
        GEOCODE_URL,
        params={"address": query, "key": API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status != "OK" or not data.get("results"):
        raise ValueError(f"Geocoding fallito: {status}")

    result = data["results"][0]
    loc = result["geometry"]["location"]
    return {
        "query": query,
        "formatted_address": result.get("formatted_address"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "location_type": result.get("geometry", {}).get("location_type"),
        "place_id": result.get("place_id"),
        "partial_match": result.get("partial_match", False),
    }


def parse_point(prefix: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    text = payload.get(f"{prefix}_text")
    lat = payload.get(f"{prefix}_lat")
    lng = payload.get(f"{prefix}_lng")

    if text:
        point = geocode_query(text)
        return {
            "label": point["formatted_address"],
            "lat": point["lat"],
            "lng": point["lng"],
            "source": "geocoded_text",
            "raw": point,
        }

    if lat is not None and lng is not None:
        return {
            "label": payload.get(f"{prefix}_label") or f"{prefix} coordinates",
            "lat": float(lat),
            "lng": float(lng),
            "source": "explicit_coordinates",
            "raw": None,
        }

    raise ValueError(
        f"Fornisci {prefix}_text oppure {prefix}_lat e {prefix}_lng"
    )


def route_mode_to_google(mode: str) -> str:
    mode = mode.lower().strip()
    if mode == "driving":
        return "DRIVE"
    if mode == "walking":
        return "WALK"
    raise ValueError("mode deve essere 'driving' oppure 'walking'")


@app.get("/")
def home():
    return jsonify({"ok": True, "service": "gpt-routing-backend"})


@app.get("/health")
def health():
    return jsonify({"ok": True, "has_api_key": bool(API_KEY)})


@app.post("/geocode")
def geocode_endpoint():
    try:
        payload = request.get_json(force=True, silent=False) or {}
        query = (payload.get("query") or "").strip()
        if not query:
            return error_response("Manca il campo 'query'")
        result = geocode_query(query)
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        return error_response(str(e), 500)


@app.post("/route")
def route_endpoint():
    try:
        if not API_KEY:
            return error_response("GOOGLE_MAPS_API_KEY non configurata", 500)

        payload = request.get_json(force=True, silent=False) or {}
        mode = payload.get("mode", "driving")
        google_mode = route_mode_to_google(mode)
        origin = parse_point("origin", payload)
        destination = parse_point("destination", payload)

        body = {
            "origin": {
                "location": {
                    "latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}
                }
            },
            "destination": {
                "location": {
                    "latLng": {
                        "latitude": destination["lat"],
                        "longitude": destination["lng"],
                    }
                }
            },
            "travelMode": google_mode,
            "routingPreference": "TRAFFIC_UNAWARE",
            "languageCode": "it-IT",
            "units": "METRIC",
        }

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "routes.distanceMeters,routes.duration,routes.legs"
        }

        resp = requests.post(ROUTES_URL, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        routes = data.get("routes", [])
        if not routes:
            return error_response("Nessuna rotta disponibile", 404)

        route = routes[0]
        distance_meters = route.get("distanceMeters")
        duration_raw = route.get("duration", "0s")
        duration_seconds = float(str(duration_raw).rstrip("s")) if duration_raw else 0.0

        return jsonify(
            {
                "ok": True,
                "result": {
                    "mode": mode.lower(),
                    "distance_meters": distance_meters,
                    "distance_km": round(distance_meters / 1000, 2) if distance_meters is not None else None,
                    "duration_seconds": int(duration_seconds),
                    "duration_minutes": round(duration_seconds / 60, 1),
                    "origin": origin,
                    "destination": destination,
                    "warning": None,
                },
            }
        )
    except Exception as e:
        return error_response(str(e), 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
