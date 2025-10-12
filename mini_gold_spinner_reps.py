
# Mini Gold Spinner — all-gold text in cards, fixed backdrop, editable palette
from __future__ import annotations

import os, io, math, random, base64, datetime as dt
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
from shiny import App, ui, reactive, render

# ----------------------- Quick color palette (edit here) -----------------------
ACCENT   = "#ecc791"   # Gold: default text, buttons, outlines
TITLE_COL= "#eaebec"   # App title color only
MAJOR_BG = "#301c2d"   # Page background behind the backdrop
CARD_ALPHA = 0.52      # Card background opacity (0..1)

# ----------------------- Optional Google Sheets deps -----------------------
try:
    import gspread  # type: ignore
    from google.oauth2 import service_account  # type: ignore
except Exception:
    gspread = None  # type: ignore

APP_TITLE = "K&K Atelier — Mini Gold Spinner"

# ----------------------- Game constants -----------------------
WHEEL_MULTS = [0.8, 0.9, 1.0, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4, 1.45, 1.5]
TIER_NAMES = [
    "Dusting Dabbler","Curtain–Cord Wrangler","Tapestry Tender","Chandelier Charmer",
    "Parlour Perfectionist","Gilded Guilder","Salon Savant","Waterdhavian Tastemaker",
    "Noble–House Laureate","Master of Makeovers"
]
BASE_CAP   = 250
MIN_PAYOUT = 50

# ---------------- Google Sheets config ----------------
SPREADSHEET_ID = (os.getenv("GSHEETS_SPREADSHEET_ID") or os.getenv("SHEET_ID","")).strip()
WORKSHEET_NAME = (os.getenv("GSHEETS_WORKSHEET") or os.getenv("WORKSHEET_NAME","Sheet1")).strip()
SA_JSON_INLINE = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GCP_SA_JSON","")).strip()
SA_JSON_FILE   = os.getenv("GOOGLE_APPLICATION_CREDENTIALS","").strip()
SCOPES = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]

def clamp(v, lo, hi): return max(lo, min(hi, v))

def roll_to_base_gold(roll: int) -> float:
    roll = clamp(int(roll), 1, 30)
    return 50.0 + (roll - 1) * 100.0 / 29.0  # 50–150

def load_asset_b64(name: str) -> str:
    here = os.path.dirname(__file__)
    path = os.path.join(here, "assets", name)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    return ""

def draw_wheel(labels: list[str], size: int = 980):
    n = len(labels)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = size // 2, size // 2, size // 2 - 7
    palette = ["#3a2336", "#27151f", "#462a41", "#351f2e"]
    for i in range(n):
        start = 360 * i / n - 90
        end   = 360 * (i + 1) / n - 90
        d.pieslice([cx - r, cy - r, cx + r, cy + r], start, end,
                   fill=palette[i % len(palette)], outline="#2a1627")
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=6)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    except Exception:
        font = ImageFont.load_default()
    for i, lab in enumerate(labels):
        import math
        ang = math.radians(360 * (i + 0.5) / n - 90)
        tx = cx + int((r - 120) * math.cos(ang))
        ty = cy + int((r - 120) * math.sin(ang))
        d.text((tx, ty), lab, fill="#eaebec", font=font, anchor="mm")
    return img

def to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

HEADERS = ["timestamp_iso","note","roll","base_gold","wheel_multiplier","narrative_pct","raw_total","final_award_gp"]

def ensure_gspread_client():
    if gspread is None:
        return None, "gspread not installed in this environment."
    try:
        if SA_JSON_INLINE:
            info = service_account.Credentials.from_service_account_info(
                __import__("json").loads(SA_JSON_INLINE), scopes=SCOPES
            )
            return gspread.authorize(info), None
        if SA_JSON_FILE and os.path.exists(SA_JSON_FILE):
            creds = service_account.Credentials.from_service_account_file(SA_JSON_FILE, scopes=SCOPES)
            return gspread.authorize(creds), None
        return None, "No Google credentials provided."
    except Exception as e:
        return None, f"Credential error: {e}"

def open_worksheet(gc):
    if not SPREADSHEET_ID:
        return None, "GSHEETS_SPREADSHEET_ID/SHEET_ID is not set."
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        try:
            ws = sh.worksheet(WORKSHEET_NAME)
        except Exception:
            ws = sh.add_worksheet(WORKSHEET_NAME, rows=2000, cols=20)
        return ws, None
    except Exception as e:
        return None, f"Open sheet error: {e}"

def ensure_headers(ws) -> None:
    try:
        first_row = ws.row_values(1)
        if not first_row:
            ws.update("A1:H1", [HEADERS])
    except Exception:
        pass

def append_result(ws, row_values: list) -> Optional[str]:
    try:
        ws.append_row(row_values, value_input_option="USER_ENTERED")
        return None
    except Exception as e:
        return str(e)

def fetch_stats(ws) -> Tuple[float,int,Optional[str]]:
    try:
        col = ws.col_values(8)[1:]
        total = 0.0
        jobs = 0
        for v in col:
            try:
                total += float(v)
                jobs += 1
            except Exception:
                continue
        return total, jobs, None
    except Exception as e:
        return 0.0, 0, str(e)

# ---------------- Shiny reactive state ----------------
selected_index = reactive.Value(None)   # type: ignore
last_angle     = reactive.Value(0.0)
spin_token     = reactive.Value(False)

gs_status_msg  = reactive.Value("Not connected")
agg_gold       = reactive.Value(0)
agg_jobs       = reactive.Value(0)
tier_idx       = reactive.Value(0)
show_tiers     = reactive.Value(False)

# ---------------- Assets ----------------
LOGO_B64 = load_asset_b64("Logo.png")
BG_B64   = load_asset_b64("backdrop.png")
LOGO_DATA_URI = ("data:image/png;base64," + LOGO_B64) if LOGO_B64 else ""
BG_DATA_URI   = ("data:image/png;base64," + BG_B64) if BG_B64 else ""

# ---------------- UI ----------------
# CSS via token replacement (no f-strings in CSS)
GLOBAL_CSS = """
<style>
:root {
  --major:MAJOR_TOKEN;
  --title:TITLE_TOKEN;
  --accent:ACCENT_TOKEN;
  --card-alpha:CARD_ALPHA_TOKEN;
  /* Overwrite Bootstrap text variables to prevent grey */
  --bs-body-color: var(--accent);
  --bs-card-color: var(--accent);
}
/* Default text is gold; the title uses --title */

/* Force all main containers to be transparent so the image shows through */
body, main, .container, .container-fluid, .row, .col, .navbar, header, nav, .shiny-plot-output, .shiny-html-output {
  background: transparent !important;
}

/* Make Bootstrap layout wrappers transparent as well */
#app, #shiny-body, #shiny-content, .bslib-page-fill, .page-wrapper {
  background: transparent !important;
}

/* Put app content above scrim/backdrop just in case */
.container, .grid, h2 { position: relative; z-index: 2; }

html,body { background: transparent !important; color: var(--accent); font-size: 18px; height: 100%; }
h2 { color: var(--title); }

/* Real backdrop layer */
#backdrop {
  position: fixed; inset: 0;
  background: url(BG_URI_TOKEN) center/cover no-repeat fixed;
  z-index: 0;
}
#scrim {
  position: fixed; inset: 0;
  background: linear-gradient(180deg, rgba(0,0,0,.35), rgba(0,0,0,.55));
  z-index: 1;
}

.container { max-width: 1760px !important; min-height: 100vh; padding-bottom: 32px; }

* { box-sizing: border-box; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, "Helvetica Neue", Arial; }
h2 { margin: 0 0 22px 0; font-size: 38px }
h3, h4 { margin: 0 0 14px 0; font-size: 20px; color: var(--accent); }

.card {
  background: rgba(34, 16, 30, var(--card-alpha));
  border: 1px solid var(--accent); border-radius: 22px; padding: 20px;
  color: var(--accent);
}
/* Belt-and-braces: force all descendants inside cards to gold */
.card * { color: var(--accent); }

.grid {
  display: grid; gap: 24px;
  grid-template-columns: 1.2fr 0.9fr 1fr;
  grid-template-rows: auto 1fr;
  grid-template-areas:
    "rep logo gold"
    "roll wheel payout";
  align-items: stretch;
  min-height: 760px;
}

#rep { grid-area: rep; display:flex; flex-direction:column; gap:12px; }
#logo { grid-area: logo; display:flex; align-items:center; justify-content:center; }
#logo .logo-box { position: relative; width:100%; height:100%; }
.logoimg { width:100%; height:100%; object-fit: contain; display:block; filter: drop-shadow(0 8px 18px rgba(0,0,0,.35)); }
#gold { grid-area: gold; }
#roll { grid-area: roll; }
#wheelcard { grid-area: wheel; position: relative; display:flex; align-items:center; justify-content:center; overflow:hidden; }
#payout { grid-area: payout; }

.kpi-title { font-size: 15px; letter-spacing: .3px }
.kpi-number { font-size: 30px; font-weight: 800; }
.kpi-sub { font-size: 14px; opacity: .98 }
.kpi-card {
  border:1px solid var(--accent); border-radius:18px; padding:16px 18px;
  background:linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.04));
  box-shadow:0 14px 40px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06);
  color: var(--accent);
}
.kpi-click { cursor: pointer; }

/* Inputs/readability */
label, .form-label, .form-check-label, .kpi, .kpi b { color: var(--accent); }
input, .form-control, .btn { color: var(--accent); background-color: rgba(0,0,0,.15); border-color: var(--accent); }
.form-check-input { border-color: var(--accent); }
.form-check-input:checked { background-color: var(--accent); }

/* Wheel contained */
#wheel-wrap { position: relative; width: 100%; max-width: 100%; aspect-ratio: 1 / 1; }
#wheel-img, #spin-target { width: 100%; height: 100%; border-radius: 50%; box-shadow: 0 14px 44px rgba(0,0,0,.55); }
#pointer {
  position: absolute; top: -10px; left: 50%; transform: translateX(-50%);
  width: 0; height: 0; border-left: 20px solid transparent; border-right: 20px solid transparent;
  border-bottom: 34px solid var(--accent); filter: drop-shadow(0 2px 3px rgba(0,0,0,.5));
}
.spin-btn {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%,-50%);
  min-width: 150px; height: 150px; border-radius: 80px; border: 1px solid var(--accent);
  background: linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.04));
  color: var(--accent); font-weight: 900; letter-spacing: .4px; font-size: 20px;
  box-shadow: 0 12px 36px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06);
}
@keyframes wheelspin { from { transform: rotate(0deg); } to { transform: rotate(var(--spin-deg, 1440deg)); } }
#spin-target.spinning { animation: wheelspin 3.0s cubic-bezier(.17,.67,.32,1.35); }

/* Tiers inside Reputation card */
#tiers-panel { margin-top: 4px; }
.tierlist { display: grid; grid-template-columns: 1fr; gap: 10px }
.tier { border:1px solid var(--accent); border-radius:14px; padding:12px 14px; background: rgba(255,255,255,.08); }
.tier.current { outline: 2px solid var(--accent); }
.tier .name { font-weight: 800; font-size: 16px; color: var(--accent); }
.tier .desc { font-size: 13px; color: var(--accent); opacity: 1; }

.total { font-size: 34px; font-weight: 900; color: var(--accent); }

@media (max-width: 1200px) {
  .grid {
    grid-template-columns: 1fr;
    grid-template-rows: auto auto auto auto auto auto;
    grid-template-areas: "logo" "gold" "rep" "wheel" "roll" "payout";
  }
  #wheel-wrap { width: min(80vw, 90vh); }
}
</style>
"""
GLOBAL_CSS = (GLOBAL_CSS
              .replace("MAJOR_TOKEN", MAJOR_BG)
              .replace("TITLE_TOKEN", TITLE_COL)
              .replace("ACCENT_TOKEN", ACCENT)
              .replace("CARD_ALPHA_TOKEN", f"{CARD_ALPHA:.2f}")
              .replace("BG_URI_TOKEN", BG_DATA_URI))

def kpi_gold_ui(total:int, cap:int, boost_pct:int):
    return ui.div(
        {"class":"kpi-card"},
        ui.HTML(
            f"<div class='kpi-title'>Total Gold Earned</div>"
            f"<div class='kpi-number'>{int(total)} gp</div>"
            f"<div class='kpi-sub'>Current cap: {cap} gp (+{boost_pct}% from reputation)</div>"
        )
    )

def kpi_rep_ui(jobs:int, tier:int, name:str):
    human = tier + 1
    return ui.input_action_button(
        "toggle_tiers",
        ui.HTML(
            f"<div class='kpi-title'>Reputation</div>"
            f"<div class='kpi-number'>Tier {human}/10 — {name}</div>"
            f"<div class='kpi-sub'>{jobs} jobs completed • +{tier*10}% max-cap bonus — click to view tiers</div>"
        ),
        class_="kpi-card kpi-click"
    )

app_ui = ui.page_fixed(
    ui.head_content(ui.HTML(GLOBAL_CSS)),
    ui.tags.div(id="backdrop"),
    ui.tags.div(id="scrim"),
    ui.h2(APP_TITLE),
    ui.div({"class":"grid"},
        ui.div({"id":"rep","class":"card"}, ui.h4("Reputation"),
               ui.output_ui("rep_kpi"),
               ui.output_ui("tier_panel")),
        ui.div({"id":"logo","class":"card"},
               ui.div({"class":"logo-box"},
                      ui.img(src=LOGO_DATA_URI or "", class_="logoimg")),
               ui.div({"class":"kpi-sub"}, "Tip: ensure ./assets/Logo.png exists in the repo.") if not LOGO_DATA_URI else None),
        ui.div({"id":"gold","class":"card"}, ui.output_ui("gold_kpi")),
        ui.div({"id":"roll","class":"card"},
            ui.h4("Rolls and Flair"),
            ui.input_slider("roll","Dice Result (1–30)",1,30,15),
            ui.input_checkbox("flair_pass","Passable narrative  (+10%)",False),
            ui.input_checkbox("flair_good","Good narrative      (+15%)",False),
            ui.input_checkbox("flair_ex","Excellent narrative (+25%)",False),
            ui.input_text("note","Note (optional)",""),
            ui.hr(),
            ui.input_action_button("save","Save result to Google Sheets"),
            ui.div({"class":"kpi-sub"},"Status: ", ui.output_text("gs_status_text"))
        ),
        ui.div({"id":"wheelcard","class":"card"},
            ui.h4("Wheel of fortune"), ui.output_ui("wheel_ui"), ui.output_ui("wheel_result")
        ),
        ui.div({"id":"payout","class":"card"},
            ui.h4("Payout Estimates"), ui.output_ui("payout_block")
        ),
    ),
    title=APP_TITLE
)

def server(input, output, session):
    labels = [f"×{m:g}" for m in WHEEL_MULTS]
    wheel_b64 = to_b64(draw_wheel(labels, size=980))

    def refresh_stats():
        if gspread is None:
            gs_status_msg.set("gspread not installed")
            return None, "gspread missing"
        gc, err = ensure_gspread_client()
        if err:
            gs_status_msg.set(f"❌ {err}")
            return None, err
        ws, err = open_worksheet(gc)
        if err:
            gs_status_msg.set(f"❌ {err}")
            return None, err
        ensure_headers(ws)
        total, jobs, ferr = fetch_stats(ws)
        if ferr:
            gs_status_msg.set(f"❌ Fetch failed: {ferr}")
        else:
            gs_status_msg.set("✅ Connected to Google Sheets")
            agg_gold.set(int(round(total))); agg_jobs.set(int(jobs))
            tier = min(jobs // 5, 9); tier_idx.set(int(tier))
        return ws, None

    try:
        refresh_stats()
    except Exception:
        pass

    @output
    @render.ui
    def rep_kpi():
        return kpi_rep_ui(agg_jobs.get(), tier_idx.get(), TIER_NAMES[tier_idx.get()])

    @output
    @render.ui
    def wheel_ui():
        angle = last_angle.get()
        spinning = "spinning" if spin_token.get() else ""
        return ui.div({"id":"wheel-wrap"},
            ui.div({"id":"pointer"}),
            ui.img(id="wheel-img", src=f"data:image/png;base64,{wheel_b64}"),
            ui.div(
                ui.img(
                    id="spin-target",
                    src=f"data:image/png;base64,{wheel_b64}",
                    style=f"--spin-deg:{angle}deg;position:absolute;inset:0;border-radius:50%;",
                    class_=spinning
                ),
                ui.input_action_button("spin","SPIN!", class_="spin-btn"),
                style="position:absolute;inset:0;"
            )
        )

    @reactive.Effect
    @reactive.event(input.spin)
    def _spin():
        n = len(WHEEL_MULTS)
        import random
        idx = random.randrange(n)
        selected_index.set(idx)
        seg = 360 / n
        spin_token.set(False)
        last_angle.set(random.randint(4, 7) * 360 + (idx + 0.5) * seg)
        spin_token.set(True)

    def narrative_bonus_pct() -> float:
        bonuses = []
        if input.flair_pass():
            bonuses.append(0.10)
        if input.flair_good():
            bonuses.append(0.15)
        if input.flair_ex():
            bonuses.append(0.25)
        return max(bonuses) if bonuses else 0.0

    def compute_payout():
        roll = int(input.roll())
        base = roll_to_base_gold(roll)
        idx  = selected_index.get() if selected_index.get() is not None else 2  # default 1.0x
        mult = WHEEL_MULTS[int(idx)]
        flair = narrative_bonus_pct()
        raw = base * mult * (1.0 + flair)
        tier = int(tier_idx.get())
        cap = int(round(BASE_CAP * (1.0 + 0.10 * tier)))
        total = int(round(clamp(raw, MIN_PAYOUT, cap)))
        return roll, base, mult, flair, raw, total, cap

    @output
    @render.ui
    def payout_block():
        roll, base, mult, flair, raw, total, cap = compute_payout()
        tier = int(tier_idx.get())
        return ui.div(
            ui.div({"class":"kpi"}, f"Base from roll {roll}:  ", ui.tags.b(f"{base:.0f} gp")),
            ui.div({"class":"kpi"}, "Wheel multiplier:       ", ui.tags.b(f"×{mult:g}")),
            ui.div({"class":"kpi"}, "Narrative bonus:        ", ui.tags.b(f"+{int(flair*100)}%")),
            ui.div({"class":"kpi"}, f"Current cap (Tier {tier+1}): ", ui.tags.b(f"{cap} gp")),
            ui.hr(),
            ui.div({"class":"total"}, f"Final award: {total} gp"),
        )

    @output
    @render.ui
    def gold_kpi():
        tier = int(tier_idx.get())
        cap  = int(round(BASE_CAP * (1.0 + 0.10 * tier)))
        return kpi_gold_ui(agg_gold.get(), cap, tier * 10)

    @output
    @render.text
    def gs_status_text():
        return gs_status_msg.get()

    @reactive.Effect
    @reactive.event(input.toggle_tiers)
    def _toggle():
        show_tiers.set(not show_tiers.get())

    @output
    @render.ui
    def tier_panel():
        if not show_tiers.get():
            return ui.HTML("")
        jobs = agg_jobs.get()
        curr = tier_idx.get()
        items = []
        for i, name in enumerate(TIER_NAMES):
            needed = i * 5
            desc = "Unlocked" if jobs >= needed else f"Reach {needed} jobs"
            klass = "tier current" if i == curr else "tier"
            items.append(ui.div({"class": klass},
                ui.div({"class":"name"}, f"Tier {i+1} — {name}"),
                ui.div({"class":"desc"}, f"+{i*10}% cap • {desc}")
            ))
        return ui.div({"id":"tiers-panel"}, ui.div({"class":"tierlist"}, *items))

app = App(app_ui, server)
