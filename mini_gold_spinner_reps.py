
# K&K Atelier — Mini Gold Spinner (Sheets + Logo + Reputation + Grid Layout)
from __future__ import annotations
import os, io, math, random, base64, json, datetime as dt
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shiny import App, ui, reactive, render

# Optional Google Sheets deps
try:
    import gspread
    from google.oauth2 import service_account
except Exception:
    gspread = None
    service_account = None

APP_TITLE = "K&K Atelier — Mini Gold Spinner"

# Colour scheme
MAJOR_BG = "#301c2d"   # major
TEXT_COL = "#eaebec"   # minor & text
ACCENT   = "#ecc791"   # accents & button borders

# Wheel multipliers
WHEEL_MULTS = [0.8, 0.9, 1.0, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4, 1.45, 1.5]

# Reputation tiers
TIER_NAMES = [
    "Dusting Dabbler","Curtain–Cord Wrangler","Tapestry Tender","Chandelier Charmer",
    "Parlour Perfectionist","Gilded Guilder","Salon Savant","Waterdhavian Tastemaker",
    "Noble–House Laureate","Master of Makeovers"
]

BASE_CAP = 250
MIN_PAYOUT = 50

# ---------------- Google Sheets config (supports legacy names) ----------------
SPREADSHEET_ID = (os.getenv("GSHEETS_SPREADSHEET_ID") or os.getenv("SHEET_ID","")).strip()
WORKSHEET_NAME = (os.getenv("GSHEETS_WORKSHEET") or os.getenv("WORKSHEET_NAME","Sheet1")).strip()
SA_JSON_INLINE = (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or os.getenv("GCP_SA_JSON","")).strip()
SA_JSON_FILE   = os.getenv("GOOGLE_APPLICATION_CREDENTIALS","").strip()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]

def clamp(v, lo, hi): return max(lo, min(hi, v))

def roll_to_base_gold(roll: int) -> float:
    roll = clamp(int(roll), 1, 30)
    return 50.0 + (roll - 1) * 100.0 / 29.0  # 50–150

def draw_wheel(labels: List[str], size: int = 560):
    n = len(labels)
    img = Image.new("RGBA",(size,size),(0,0,0,0))
    d = ImageDraw.Draw(img)
    cx, cy, r = size//2, size//2, size//2-6
    palette=["#3a2336","#27151f","#462a41","#351f2e"]
    for i in range(n):
        start = 360*i/n - 90; end = 360*(i+1)/n - 90
        d.pieslice([cx-r,cy-r,cx+r,cy+r], start, end, fill=palette[i%len(palette)], outline="#2a1627")
    d.ellipse([cx-r,cy-r,cx+r,cy+r], outline=ACCENT, width=5)
    try: font = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception: font = ImageFont.load_default()
    for i, lab in enumerate(labels):
        ang = math.radians(360*(i+0.5)/n - 90)
        tx = cx + int((r-64)*math.cos(ang)); ty = cy + int((r-64)*math.sin(ang))
        d.text((tx,ty), lab, fill=TEXT_COL, font=font, anchor="mm")
    return img

def to_b64(img): buf=io.BytesIO(); img.save(buf, format="PNG"); return base64.b64encode(buf.getvalue()).decode()

def ensure_gspread_client():
    if gspread is None or service_account is None:
        return None, "Missing dependencies: install `gspread` and `google-auth`."
    try:
        if SA_JSON_INLINE:
            info = json.loads(SA_JSON_INLINE)
            creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        elif SA_JSON_FILE and os.path.exists(SA_JSON_FILE):
            creds = service_account.Credentials.from_service_account_file(SA_JSON_FILE, scopes=SCOPES)
        else:
            return None, "No Google credentials found. Set GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_SERVICE_ACCOUNT_JSON."
        return gspread.authorize(creds), None
    except Exception as e:
        return None, f"Credential error: {{e}}"

def open_worksheet(gc):
    if not SPREADSHEET_ID: return None, "GSHEETS_SPREADSHEET_ID/SHEET_ID is not set."
    try:
        sh = gc.open_by_key(SPREADSHEET_ID)
        try: ws = sh.worksheet(WORKSHEET_NAME)
        except Exception: ws = sh.add_worksheet(WORKSHEET_NAME, rows=2000, cols=20)
        return ws, None
    except Exception as e:
        return None, f"Open sheet error: {{e}}"

HEADERS = ["timestamp_iso","note","roll","base_gold","wheel_multiplier","narrative_pct","raw_total","final_award_gp"]
def ensure_headers(ws):
    try:
        if not ws.row_values(1): ws.update("A1:H1",[HEADERS])
    except Exception:
        pass
def append_result(ws, row_values: List):
    try:
        ws.append_row(row_values, value_input_option="USER_ENTERED"); return None
    except Exception as e:
        return str(e)
def fetch_stats(ws):
    try:
        col = ws.col_values(8)[1:]; total=0.0; jobs=0
        for v in col:
            try: total += float(v); jobs += 1
            except Exception: pass
        return total, jobs, None
    except Exception as e: return 0.0, 0, str(e)

# -------- Reactive state --------
selected_index = reactive.Value(None)
spin_token = reactive.Value(None)
last_angle = reactive.Value(0.0)
gs_status_msg = reactive.Value("Not connected")
agg_gold = reactive.Value(0)
agg_jobs = reactive.Value(0)
tier_idx = reactive.Value(0)
show_tiers = reactive.Value(False)

# -------- UI --------
LOGO_DATA_URI = "data:image/png;base64,"
BG_DATA_URI = "data:image/png;base64,"

GLOBAL_CSS = f"""
<style>
:root{{ --major:{MAJOR_BG}; --text:{TEXT_COL}; --accent:{ACCENT}; }}
html,body{{background:var(--major);color:var(--text);}}
body::before{{ content:""; position:fixed; inset:0;
  background: url({{BG_DATA_URI}}) center/cover no-repeat fixed; opacity:.18; z-index:-1;}}

.card{{background:rgba(255,255,255,0.06);border:1px solid var(--accent);border-radius:18px; padding:14px;}}

.grid{{display:grid; gap:16px;
  grid-template-columns: 1.1fr 0.8fr 1.5fr;
  grid-template-areas:
    "rep logo gold"
    "roll wheel payout";
  align-items:stretch;}}
#rep{{grid-area:rep; position:relative;}}
#logo{{grid-area:logo;}}
#gold{{grid-area:gold;}}
#roll{{grid-area:roll;}}
#wheelcard{{grid-area:wheel;}}
#payout{{grid-area:payout;}}

.kpi-title{{font-size:12px;opacity:.85;letter-spacing:.3px}}
.kpi-number{{font-size:22px;font-weight:800;color:var(--accent);}}
.kpi-sub{{font-size:12px;opacity:.85}}
.kpi-card{{border:1px solid var(--accent);border-radius:14px;padding:12px 14px;
  background:linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.04));
  box-shadow:0 10px 30px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06);}}
.kpi-click{{cursor:pointer;}}
.logoimg{{height:96px;width:auto;display:block;margin:0 auto;filter: drop-shadow(0 6px 14px rgba(0,0,0,.35));}}

#wheel-wrap{{position:relative; width:100%; max-width:640px; margin:0 auto; aspect-ratio:1/1;}}
#wheel-img, #spin-target{{width:100%; height:100%; border-radius:50%; box-shadow:0 12px 36px rgba(0,0,0,.55);}}
#pointer{{position:absolute;top:-8px;left:50%;transform:translateX(-50%);
  width:0;height:0;border-left:16px solid transparent;border-right:16px solid transparent;
  border-bottom:26px solid var(--accent);filter: drop-shadow(0 2px 3px rgba(0,0,0,.5));}}
.spin-btn{{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
  min-width:120px;height:120px;border-radius:60px;border:1px solid var(--accent);
  background:linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,.04));
  color:var(--text);font-weight:700;letter-spacing:.4px;
  box-shadow:0 10px 30px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.06);}}
@keyframes wheelspin{{from{{transform:rotate(0deg);}}to{{transform:rotate(var(--spin-deg,1440deg));}}}}
#spin-target.spinning{{animation:wheelspin 3.0s cubic-bezier(.17,.67,.32,1.35);}}

#tiers-overlay{{position:absolute; top:12px; right:-12px; width:280px; z-index:3; display:none;}}
#tiers-overlay.show{{display:block;}}
.tierlist{{display:grid;grid-template-columns:1fr;gap:8px}}
.tier{{border:1px solid var(--accent);border-radius:12px;padding:10px 12px;background:rgba(255,255,255,.08);}}
.tier.current{{outline:2px solid var(--accent);}}
.tier .name{{font-weight:700;color:var(--accent)}}
.tier .desc{{font-size:12px;opacity:.9}}

.kpi b{{color:var(--accent)}} .total{{font-size:28px;font-weight:800;color:var(--accent);}}

@media (max-width: 1100px) {{
  .grid{{grid-template-columns: 1fr; grid-template-areas:
    "logo" "gold" "rep" "wheel" "roll" "payout"; }}
  #tiers-overlay{{position:static; width:auto; display:block; margin-top:10px;}}
}}
</style>
"""

def kpi_gold_ui(total:int, cap:int, boost_pct:int):
    return ui.input_action_button(
        "toggle_tiers",
        ui.HTML(f"<div class='kpi-title'>Total Gold Earned</div>"
                f"<div class='kpi-number'>{int(total)} gp</div>"
                f"<div class='kpi-sub'>Current cap: {cap} gp (+{boost_pct}% from reputation) — click to view tiers</div>"),
        class_="kpi-card kpi-click"
    )

def kpi_rep_ui(jobs:int, tier:int, name:str):
    human = tier + 1
    return ui.div({{"class":"kpi-card"}}, ui.HTML(
        f"<div class='kpi-title'>Reputation</div>"
        f"<div class='kpi-number'>Tier {human}/10 — {name}</div>"
        f"<div class='kpi-sub'>{jobs} jobs completed • +{tier*10}% max-cap bonus</div>"
    ))

app_ui = ui.page_fixed(
    ui.head_content(ui.HTML(GLOBAL_CSS)),
    ui.h2(APP_TITLE),
    ui.div({"class":"grid"},
        ui.div({"id":"rep","class":"card"}, ui.h4("Reputation"), ui.output_ui("rep_kpi"),
               ui.div({"id":"tiers-overlay","class":"card"}, ui.output_ui("tier_panel"))),
        ui.div({"id":"logo","class":"card"}, ui.img(src=f"data:image/png;base64,", class_="logoimg")),
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
    labels = [f"×{{m:g}}" for m in WHEEL_MULTS]
    wheel_b64 = to_b64(draw_wheel(labels, size=560))

    def refresh_stats():
        gc, err = ensure_gspread_client()
        if err: gs_status_msg.set(f"❌ {{err}}"); return None, err
        ws, err = open_worksheet(gc)
        if err: gs_status_msg.set(f"❌ {{err}}"); return None, err
        ensure_headers(ws)
        total, jobs, ferr = fetch_stats(ws)
        if ferr: gs_status_msg.set(f"❌ Fetch failed: {{ferr}}")
        else:
            gs_status_msg.set("✅ Connected to Google Sheets")
            agg_gold.set(int(round(total))); agg_jobs.set(int(jobs))
            tier = min(jobs // 5, 9); tier_idx.set(int(tier))
        return ws, None

    ws_cache, _ = refresh_stats()

    @output
    @render.text
    def gs_status_text():
        return gs_status_msg.get()

    @output
    @render.ui
    def gold_kpi():
        tier = tier_idx.get(); cap = int(round(BASE_CAP*(1.0+0.10*tier))); boost=tier*10
        return kpi_gold_ui(agg_gold.get(), cap, boost)

    @output
    @render.ui
    def rep_kpi():
        return kpi_rep_ui(agg_jobs.get(), tier_idx.get(), TIER_NAMES[tier_idx.get()])

    @output
    @render.ui
    def wheel_ui():
        angle = last_angle.get(); spinning = "spinning" if spin_token.get() else ""
        return ui.div({{"id":"wheel-wrap"}},
            ui.div({{"id":"pointer"}}),
            ui.img(id="wheel-img", src=f"data:image/png;base64,{{wheel_b64}}"),
            ui.div(
                ui.img(id="spin-target", src=f"data:image/png;base64,{{wheel_b64}}",
                    style=f"--spin-deg:{{angle}}deg;position:absolute;inset:0;border-radius:50%;",
                    class_=spinning),
                ui.input_action_button("spin","SPIN!", class_="spin-btn"),
                style="position:absolute;inset:0;"
            )
        )

    @reactive.Effect
    @reactive.event(input.spin)
    def _spin():
        n=len(WHEEL_MULTS); idx=random.randrange(n); selected_index.set(idx); seg=360/n
        spin_token.set(None); last_angle.set(random.randint(4,7)*360 + (idx+0.5)*seg); spin_token.set(True)

    def narrative_bonus_pct()->float:
        b=0.0
        if input.flair_pass(): b+=0.10
        if input.flair_good(): b+=0.15
        if input.flair_ex(): b+=0.25
        return b
    def current_multiplier()->float:
        idx=selected_index.get(); return WHEEL_MULTS[idx] if idx is not None else 1.0
    def dynamic_cap()->int:
        tier=tier_idx.get(); return int(round(BASE_CAP*(1.0+0.10*tier)))
    def compute_payout():
        roll=int(input.roll()); base=roll_to_base_gold(roll); mult=current_multiplier(); flair=narrative_bonus_pct()
        raw=base*mult*(1.0+flair); cap=dynamic_cap(); total=clamp(round(raw), MIN_PAYOUT, cap)
        return roll, base, mult, flair, raw, total, cap

    @output
    @render.ui
    def payout_block():
        roll, base, mult, flair, raw, total, cap = compute_payout(); tier=tier_idx.get()
        return ui.div(
            ui.div({{"class":"kpi"}}, f"Base from roll {{roll}}:  ", ui.tags.b(f"{{base:.0f}} gp")),
            ui.div({{"class":"kpi"}}, f"Wheel multiplier:       ", ui.tags.b(f"×{{mult:g}}")),
            ui.div({{"class":"kpi"}}, f"Narrative bonus:        ", ui.tags.b(f"+{{int(flair*100)}}%")),
            ui.div({{"class":"kpi"}}, f"Current cap (Tier {{tier+1}}): ", ui.tags.b(f"{{cap}} gp")),
            ui.hr(), ui.div({{"class":"total"}}, f"Final award: {{total}} gp"),
        )

    @reactive.Effect
    @reactive.event(input.toggle_tiers)
    def _toggle():
        show_tiers.set(not show_tiers.get())

    @output
    @render.ui
    def tier_panel():
        jobs=agg_jobs.get(); curr=tier_idx.get()
        items=[]
        for i,name in enumerate(TIER_NAMES):
            needed=i*5; desc="Unlocked" if jobs>=needed else f"Reach {{needed}} jobs"
            klass="tier current" if i==curr else "tier"
            items.append(ui.div({{"class":klass}},
                ui.div({{"class":"name"}}, f"Tier {{i+1}} — {{name}}"),
                ui.div({{"class":"desc"}}, f"+{{i*10}}% cap • {{desc}}")
            ))
        display = "block" if show_tiers.get() else "none"
        return ui.div({{"style": f"display:{display};"}}, ui.div({{"class":"tierlist"}}, *items))

    @reactive.Effect
    @reactive.event(input.save)
    def _save_to_sheets():
        if selected_index.get() is None:
            ui.notification_show("Spin the wheel before saving.", type="warning", duration=4); return
        gc, err = ensure_gspread_client()
        if err: gs_status_msg.set(f"❌ {{err}}"); ui.notification_show(f"Google Sheets not ready: {{err}}", type="error", duration=6); return
        ws, err = open_worksheet(gc)
        if err: gs_status_msg.set(f"❌ {{err}}"); ui.notification_show(f"Google Sheet open error: {{err}}", type="error", duration=6); return
        ensure_headers(ws)
        roll, base, mult, flair, raw, total, cap = compute_payout()
        note=(input.note() or "").strip(); now_iso=dt.datetime.now().isoformat(timespec="seconds")
        row=[now_iso, note, roll, round(base,2), mult, round(flair,4), round(raw,2), total]
        err = append_result(ws, row)
        if err: gs_status_msg.set(f"❌ Append failed: {{err}}"); ui.notification_show(f"Append failed: {{err}}", type="error", duration=6)
        else:
            gs_status_msg.set("✅ Saved to Google Sheets"); ui.notification_show("Saved to Google Sheets.", type="message", duration=4)
            total_gold, jobs, ferr = fetch_stats(ws)
            if not ferr:
                agg_gold.set(int(round(total_gold))); agg_jobs.set(int(jobs)); tier=min(jobs//5,9); tier_idx.set(int(tier))

app = App(app_ui, server)
