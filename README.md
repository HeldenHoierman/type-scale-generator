# Type Scale Generator

A local web app that derives a geometric CSS type scale from a font's intrinsic typographic metrics (cap-height / x-height ratio), with a perceptually-calibrated letter-spacing curve.

## How it works

1. **Select a typeface** — search Google Fonts or drop in a local `.ttf` / `.otf` file
2. **Inspect metrics** — cap-height, x-height, and their ratio are extracted directly from the font's `OS/2` table
3. **Configure the scale** — set base size, steps above/below, and tracking anchors for the smallest, base, and largest steps
4. **Generate** — outputs a `:root {}` CSS block with `--font-size-*` and `--letter-spacing-*` custom properties

## Tracking curve

Letter-spacing is interpolated across the scale using two independent exponential curves (one for sizes above base, one for below), each anchored at the base value. Five interpolation modes are available: Exponential, Linear, Ease In, Ease Out, Ease In-Out.

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Then open [http://localhost:5000](http://localhost:5000).

A Google Fonts API key is required for font search. Get one free at [console.cloud.google.com](https://console.cloud.google.com) (enable the Web Fonts Developer API). The key is stored locally at `~/.config/type-scale-generator/config.json`. Local font files work without a key.
