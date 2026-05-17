import json
import os
import tempfile
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_file
from fontTools.ttLib import TTFont

app = Flask(__name__, static_folder=".", static_url_path="")

CONFIG_PATH = Path.home() / ".config" / "type-scale-generator" / "config.json"

_fonts_cache = None
_fonts_cache_time = 0
_fonts_cache_key = None


# ── Config ────────────────────────────────────────────────────────────────────

def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def save_config(data):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


# ── Google Fonts ───────────────────────────────────────────────────────────────

def get_fonts_list(api_key):
    global _fonts_cache, _fonts_cache_time, _fonts_cache_key
    if _fonts_cache and _fonts_cache_key == api_key and (time.time() - _fonts_cache_time) < 3600:
        return _fonts_cache
    resp = requests.get(
        "https://www.googleapis.com/webfonts/v1/webfonts",
        params={"key": api_key, "sort": "popularity"},
        timeout=10,
    )
    resp.raise_for_status()
    _fonts_cache = resp.json().get("items", [])
    _fonts_cache_time = time.time()
    _fonts_cache_key = api_key
    return _fonts_cache


# ── Font Metrics ───────────────────────────────────────────────────────────────

def cap_height_from_glyph(font):
    """Estimate cap-height from H glyph bounding box when OS/2.sCapHeight is 0."""
    try:
        cmap = font.getBestCmap()
        if not cmap:
            return None
        h_name = cmap.get(ord("H"))
        if not h_name:
            return None

        if "glyf" in font:
            glyph = font["glyf"][h_name]
            if hasattr(glyph, "yMax") and glyph.yMax:
                return glyph.yMax
        elif "CFF " in font:
            from fontTools.pens.boundsPen import BoundsPen
            cs = font["CFF "].cff.topDictIndex[0].CharStrings
            if h_name in cs:
                pen = BoundsPen(None)
                cs[h_name].draw(pen)
                if pen.bounds:
                    return int(pen.bounds[3])
    except Exception:
        pass
    return None


def extract_metrics_from_path(font_path, family_name):
    font = TTFont(font_path)
    os2 = font["OS/2"]
    cap_h = os2.sCapHeight
    x_h = os2.sxHeight

    if cap_h == 0:
        cap_h = cap_height_from_glyph(font)

    if not cap_h:
        raise ValueError("Could not determine cap-height for this font.")
    if not x_h:
        raise ValueError("x-height not defined in this font.")

    ratio = round(cap_h / x_h, 4)
    return {"family": family_name, "cap_height": cap_h, "x_height": x_h, "ratio": ratio}


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(Path(__file__).parent / "index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    config = load_config()
    return jsonify({"configured": bool(config.get("api_key"))})


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.json
    api_key = (data.get("api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "API key is required"}), 400
    config = load_config()
    config["api_key"] = api_key
    save_config(config)
    return jsonify({"ok": True})


@app.route("/api/search")
def search_fonts():
    q = (request.args.get("q") or "").strip().lower()
    if not q:
        return jsonify({"results": []})

    config = load_config()
    api_key = config.get("api_key")
    if not api_key:
        return jsonify({"error": "API key not configured"}), 401

    try:
        fonts = get_fonts_list(api_key)
    except requests.RequestException as e:
        return jsonify({"error": f"Google Fonts API error: {e}"}), 502

    matches = [f for f in fonts if q in f["family"].lower()][:10]
    results = [
        {"family": f["family"], "category": f["category"], "variants": list(f["files"].keys())}
        for f in matches
    ]
    return jsonify({"results": results})


@app.route("/api/metrics", methods=["POST"])
def get_metrics():
    tmpdir = tempfile.mkdtemp()

    if "file" in request.files:
        f = request.files["file"]
        ext = Path(f.filename).suffix.lower()
        if ext not in (".ttf", ".otf"):
            return jsonify({"error": "Only .ttf and .otf files are supported"}), 400
        font_path = os.path.join(tmpdir, f"font{ext}")
        f.save(font_path)
        family = Path(f.filename).stem
    else:
        data = request.json
        family = (data or {}).get("family", "").strip()
        if not family:
            return jsonify({"error": "family name required"}), 400

        config = load_config()
        api_key = config.get("api_key")
        if not api_key:
            return jsonify({"error": "API key not configured"}), 401

        try:
            fonts = get_fonts_list(api_key)
        except requests.RequestException as e:
            return jsonify({"error": f"Google Fonts API error: {e}"}), 502

        font_data = next((f for f in fonts if f["family"] == family), None)
        if not font_data:
            return jsonify({"error": f"Font '{family}' not found"}), 404

        files = font_data["files"]
        url = files.get("regular") or list(files.values())[0]
        url = url.replace("http://", "https://")

        try:
            font_resp = requests.get(url, timeout=15)
            font_resp.raise_for_status()
        except requests.RequestException as e:
            return jsonify({"error": f"Failed to download font: {e}"}), 502

        ext = ".ttf" if url.endswith(".ttf") else ".otf"
        font_path = os.path.join(tmpdir, f"font{ext}")
        with open(font_path, "wb") as fh:
            fh.write(font_resp.content)

    try:
        result = extract_metrics_from_path(font_path, family)
    except Exception as e:
        return jsonify({"error": str(e)}), 422

    return jsonify(result)


@app.route("/api/scale", methods=["POST"])
def generate_scale():
    data = request.json or {}
    try:
        ratio = float(data["ratio"])
        base = float(data.get("base", 16))
        steps_up = int(data.get("steps_up", 5))
        steps_down = int(data.get("steps_down", 2))
        base_tracking = float(data.get("base_tracking", 0.0))
        top_tracking = float(data.get("top_tracking", 0.0))
        bottom_tracking = float(data.get("bottom_tracking", 0.0))
    except (KeyError, ValueError, TypeError) as e:
        return jsonify({"error": f"Invalid parameters: {e}"}), 400

    family = str(data.get("family", ""))
    curve = str(data.get("curve", "exponential"))

    def ease(t, i, n):
        """Map normalized t ∈ [0,1] through the selected curve. i and n are
        the raw step index and endpoint index, needed for the exponential case."""
        if curve == "linear":
            return t
        elif curve == "ease-in":
            return t * t
        elif curve == "ease-out":
            return 1 - (1 - t) ** 2
        elif curve == "ease-in-out":
            return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2
        else:  # exponential — mirrors the scale's own geometric progression
            denom = ratio ** n - 1
            return (ratio ** i - 1) / denom if denom else t

    def tracking_at(i):
        if i >= 0:
            if steps_up == 0:
                return round(base_tracking, 4)
            t = ease(i / steps_up, i, steps_up)
            return round(base_tracking + (top_tracking - base_tracking) * t, 4)
        else:
            if steps_down == 0:
                return round(base_tracking, 4)
            t = ease(-i / steps_down, i, -steps_down)
            return round(base_tracking + (bottom_tracking - base_tracking) * t, 4)

    label_pool = ["5xs", "4xs", "3xs", "2xs", "xs", "sm", "base", "md", "lg", "xl", "2xl", "3xl", "4xl", "5xl", "6xl"]
    base_idx = label_pool.index("base")

    steps = []
    for i in range(-steps_down, steps_up + 1):
        size = base * (ratio ** i)
        label_idx = base_idx + i
        label = label_pool[label_idx] if 0 <= label_idx < len(label_pool) else f"step{i:+d}"
        size_rounded = round(size, 2)
        css_val = f"{int(size_rounded)}px" if size_rounded == int(size_rounded) else f"{size_rounded:.2f}px"
        t = tracking_at(i)
        # Format: strip trailing zeros, keep sign, e.g. "0em", "-0.025em", "+0.01em"
        t_str = f"{t:.4f}".rstrip("0").rstrip(".")
        if t > 0:
            t_str = "+" + t_str
        tracking_css = t_str + "em"
        steps.append({"label": label, "size": size_rounded, "css_value": css_val,
                       "tracking": t, "tracking_css": tracking_css})

    max_label_len = max(len(s["label"]) for s in steps)

    comment = f'  /* Type Scale — {family} — ratio: {ratio:.3f} */' if family else f'  /* Type Scale — ratio: {ratio:.3f} */'
    lines = [":root {", comment]
    for s in steps:
        prop = f"  --font-size-{s['label']}:"
        pad_prop = " " * (max_label_len - len(s["label"]) + 1)
        lines.append(f"{prop}{pad_prop}{s['css_value']};")
    lines.append("}")

    track_comment_detail = f"smallest: {bottom_tracking:+g}em  →  base: {base_tracking:+g}em  →  largest: {top_tracking:+g}em"
    track_comment = f'  /* Tracking — {family} — {track_comment_detail} */' if family else f'  /* Tracking — {track_comment_detail} */'
    max_tracking_len = max(len(s["tracking_css"]) for s in steps)
    lines += ["", ":root {", track_comment]
    for s in steps:
        prop = f"  --letter-spacing-{s['label']}:"
        pad_prop = " " * (max_label_len - len(s["label"]) + 1)
        lines.append(f"{prop}{pad_prop}{s['tracking_css']};")
    lines.append("}")

    return jsonify({"css": "\n".join(lines), "steps": steps})


if __name__ == "__main__":
    print("Type Scale Generator running at http://localhost:5000")
    app.run(debug=True, port=5000)
