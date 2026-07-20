"""Reporter Module — Terminal & Interactive HTML Dashboard

Exports
-------
- print_terminal_report(analysis_result)   — colour-coded terminal output
- generate_html_report(analysis_result)    — self-contained interactive dashboard
- generate_short_summary(analysis_result)  — one-line TUI summary
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import colorama
    from colorama import Fore, Style

    colorama.init(autoreset=True)
except ImportError:
    class _FakeStyle:
        def __getattr__(self, name: str) -> str:
            return ""
    Fore = _FakeStyle()
    Style = _FakeStyle()
    colorama = None

# Project root — derived from this file's location
_REPORTER_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _REPORTER_DIR.parent

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

_HEADER = Fore.CYAN + Style.BRIGHT
_OK = Fore.GREEN
_WARN = Fore.YELLOW
_ERR = Fore.RED
_INFO = Fore.BLUE
_HILITE = Fore.MAGENTA + Style.BRIGHT


def _c(text: str, colour: str) -> str:
    return f"{colour}{text}{Style.RESET_ALL}" if colorama else text


def _section(title: str) -> None:
    print()
    print(_c("─" * 70, _HEADER))
    print(f"  {_c(title, _HEADER)} ")
    print(_c("─" * 70, _HEADER))


def _print_item(name: str, detail: str, icon: str = "•") -> None:
    print(f"  {icon} {_c(name, _HILITE)} — {detail}")


# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------


def generate_short_summary(analysis_result: dict[str, Any]) -> str:
    """One-line plain-text summary."""
    a = analysis_result
    total = a.get("total_items", 0)
    weapons = a.get("total_weapons", 0)
    armor = a.get("total_armor", 0)
    keep = a.get("keep_count", 0)
    dismantle = a.get("dismantle_count", 0)
    matches = len(a.get("god_roll_matches", []))
    recs = len(a.get("dismantle_recommendations", []))
    return (
        f"📦 {total} items ({weapons}W / {armor}A) "
        f"| ✅ Keep {keep} | ❌ Dismantle {dismantle} "
        f"| 🎯 {matches} god roll(s) | 🗑 {recs} dismantle reason(s)"
    )


def print_terminal_report(analysis_result: dict[str, Any]) -> None:
    """Print a comprehensive, colour-coded terminal report."""
    a = analysis_result

    print()
    print(_c("═" * 70, _HEADER))
    print(
        _c(
            f"  DESTINY VAULT ANALYSIS REPORT  "
            f"({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            _HEADER,
        )
    )
    print(_c("═" * 70, _HEADER))

    # Summary
    _section("Summary")
    print(f"  {_c('Total items:', Fore.CYAN)}  {a.get('total_items', 0)}")
    print(f"  {_c('Weapons:', Fore.CYAN)}     {a.get('total_weapons', 0)}")
    print(f"  {_c('Armour:', Fore.CYAN)}      {a.get('total_armor', 0)}")
    print(f"  {_c('Keep:', Fore.GREEN)}       {a.get('keep_count', 0)}")
    print(f"  {_c('Dismantle:', Fore.RED)}    {a.get('dismantle_count', 0)}")

    # God Roll Matches
    matches = a.get("god_roll_matches", [])
    _section(f"God Roll Matches ({len(matches)})")
    if not matches:
        print(f"  {_c('No god-roll matches found.', _WARN)}")
    else:
        for m in matches:
            pct = m.get("match_pct", 0)
            pct_str = _c(f"{pct:.0f}%", _OK if pct >= 80 else _WARN)
            verdict = _c(m.get("verdict", ""), _OK)
            print(
                f"  {_c(m.get('name', '?'), _HILITE)} "
                f"\u2192 {m.get('roll_name', '?')} "
                f"[{pct_str}]"
            )
            print(f"    Perks   : {m.get('perks', '')}")
            print(f"    Verdict : {verdict}")
            print()

    # Dismantle Recommendations
    recs = a.get("dismantle_recommendations", [])
    _section(f"Dismantle Recommendations ({len(recs)})")
    if not recs:
        print(f"  {_c('No items flagged for dismantle.', _OK)}")
    else:
        for r in recs:
            _print_item(
                r.get("name", "?"),
                _c(r.get("reason", ""), _WARN),
                icon=_c("✘", _ERR),
            )

    # Best Armour
    armor = a.get("best_armor", [])
    _section(f"Best Armour Picks ({len(armor)})")
    if not armor:
        print(f"  {_c('No armour data available.', _WARN)}")
    else:
        for p in armor:
            print(
                f"  {_c(p.get('name', '?'), _HILITE)}  "
                f"| Slot: {p.get('slot', '?')}  "
                f"| Total: {_c(str(p.get('stat_total', 0)), _OK)}  "
                f"| {p.get('distribution', '')}"
            )

    # Farming
    farming = a.get("farming_recommendations", [])
    _section("Farming Recommendations")
    if not farming:
        print(f"  {_c('No farming recommendations.', _INFO)}")
    else:
        for i, tip in enumerate(farming, 1):
            print(f"  {i}. {tip}")

    print()
    print(_c("═" * 70, _HEADER))
    print()


# ---------------------------------------------------------------------------
# Interactive HTML Dashboard
# ---------------------------------------------------------------------------


def generate_html_report(
    analysis_result: dict[str, Any],
    output_path: str | None = None,
) -> str:
    """Generate a self-contained interactive HTML dashboard.

    Features
    --------
    • Dark Destiny-themed design
    • Search / filter weapons by name
    • Sortable columns (click table headers)
    • Expandable rows showing you-have vs god-roll perk comparison
    • Colour-coded Keep / Dismantle / Farm badges
    • Visual stat bars for armour distribution
    • Crafting checklist
    • Responsive — works on phone too
    """
    a = analysis_result
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    if output_path is None:
        output_dir = _PROJECT_ROOT / "reports"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"vault_report_{timestamp}.html")

    # ── Serialise full result as JSON so JS can build everything ────────
    data_json = json.dumps(analysis_result, default=str, indent=None)

    # We'll also pull the raw weapon/armor items for the dashboard
    # The analysis_result already has all the flattened fields we need.

    report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Destiny Vault Dashboard</title>
<style>
  /* ── Reset & base ─────────────────────────────────── */
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{
    background:#0d1117; color:#c9d1d9;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.6; padding:1.5rem;
  }}
  .container{{max-width:1200px;margin:0 auto}}

  /* ── Header ────────────────────────────────────────── */
  h1{{color:#58a6ff;font-size:1.8rem;border-bottom:2px solid #21262d;padding-bottom:.4rem;display:flex;align-items:center;gap:.75rem;flex-wrap:wrap}}
  h1 small{{font-size:.8rem;color:#8b949e;font-weight:400}}
  .subtitle{{color:#8b949e;font-size:.85rem;margin-bottom:1.5rem}}

  /* ── Stat cards ────────────────────────────────────── */
  .summary-grid{{display:flex;flex-wrap:wrap;gap:.75rem;margin:1.25rem 0}}
  .stat-card{{
    background:#161b22; border:1px solid #21262d; border-radius:8px;
    padding:1rem 1.5rem; text-align:center; flex:1 1 120px; min-width:90px;
    transition:transform .15s;
  }}
  .stat-card:hover{{transform:translateY(-2px);border-color:#30363d}}
  .stat-card .val{{font-size:2rem;font-weight:700;color:#58a6ff}}
  .stat-card .lbl{{font-size:.7rem;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-top:2px}}
  .stat-card.keep .val{{color:#3fb950}}
  .stat-card.dismantle .val{{color:#f85149}}
  .stat-card.farm .val{{color:#d29922}}

  /* ── Toolbar ───────────────────────────────────────── */
  .toolbar{{display:flex;gap:.75rem;margin:1rem 0;flex-wrap:wrap;align-items:center}}
  .toolbar input{{
    background:#0d1117; border:1px solid #30363d; border-radius:6px;
    color:#c9d1d9; padding:.5rem .75rem; font-size:.9rem; flex:1; min-width:180px;
  }}
  .toolbar input:focus{{outline:none;border-color:#58a6ff}}
  .toolbar select{{
    background:#161b22; border:1px solid #30363d; border-radius:6px;
    color:#c9d1d9; padding:.5rem .75rem; font-size:.85rem;
  }}
  .btn{{
    background:#21262d; border:1px solid #30363d; border-radius:6px;
    color:#c9d1d9; padding:.5rem 1rem; font-size:.85rem; cursor:pointer;
    transition:background .15s;
  }}
  .btn:hover{{background:#30363d}}
  .btn.active{{background:#1f6feb;border-color:#1f6feb;color:#fff}}
  .badge{{
    display:inline-block; padding:.15rem .55rem; border-radius:12px;
    font-size:.7rem; font-weight:600; text-transform:uppercase;
    letter-spacing:.3px;
  }}
  .badge-keep{{background:#3fb95022;color:#3fb950;border:1px solid #3fb95044}}
  .badge-dim{{background:#f8514922;color:#f85149;border:1px solid #f8514944}}
  .badge-farm{{background:#d2992222;color:#d29922;border:1px solid #d2992244}}
  .badge-craft{{background:#58a6ff22;color:#58a6ff;border:1px solid #58a6ff44}}

  /* ── Tables ────────────────────────────────────────── */
  .table-wrap{{
    background:#161b22; border:1px solid #30363d; border-radius:8px;
    overflow:hidden; margin:1rem 0;
  }}
  table{{width:100%;border-collapse:collapse}}
  thead{{background:#21262d}}
  th{{
    padding:.6rem .85rem; text-align:left; font-weight:600;
    color:#8b949e; text-transform:uppercase; font-size:.7rem;
    letter-spacing:.5px; border-bottom:1px solid #30363d;
    cursor:pointer; user-select:none; white-space:nowrap;
  }}
  th:hover{{color:#c9d1d9}}
  th .sort-arrow{{margin-left:4px;opacity:.4;font-size:.65rem}}
  th.sorted .sort-arrow{{opacity:1;color:#58a6ff}}
  td{{padding:.55rem .85rem;border-bottom:1px solid #21262d;font-size:.85rem;vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}
  tr:hover{{background:#1c2128}}

  /* ── Expandable row ────────────────────────────────── */
  .weapon-name{{font-weight:600;cursor:pointer;color:#f0f6fc}}
  .weapon-name:hover{{color:#58a6ff}}
  .detail-row{{display:none}}
  .detail-row.open{{display:table-row}}
  .detail-cell{{padding:.75rem 1rem;background:#0d1117;border-bottom:1px solid #30363d}}
  .detail-grid{{display:flex;flex-wrap:wrap;gap:.5rem 1.5rem}}
  .detail-col{{min-width:160px;flex:1}}
  .detail-col h4{{color:#8b949e;font-size:.7rem;text-transform:uppercase;letter-spacing:.4px;margin-bottom:4px}}
  .detail-col .perk{{padding:2px 6px;margin:2px 0;border-radius:4px;font-size:.8rem;display:inline-block}}
  .perk-have{{background:#3fb95022;color:#3fb950;border:1px solid #3fb95044}}
  .perk-miss{{background:#f8514922;color:#f85149;border:1px solid #f8514944}}
  .perk-opt{{background:#1f6ebf22;color:#58a6ff;border:1px solid #1f6ebf44}}
  .mw-have{{color:#3fb950}}
  .mw-miss{{color:#8b949e}}

  /* ── Stat sections ─────────────────────────────────── */
  h2{{color:#f0f6fc;font-size:1.2rem;margin-top:2rem;margin-bottom:.75rem;border-left:4px solid #58a6ff;padding-left:.65rem}}
  .empty-state{{color:#8b949e;font-style:italic;padding:.75rem 0}}

  /* ── Armour stat bars ──────────────────────────────── */
  .stat-row{{display:flex;align-items:center;gap:6px;margin:2px 0;font-size:.75rem}}
  .stat-row .slbl{{width:60px;color:#8b949e;text-align:right;flex-shrink:0}}
  .stat-row .sbar{{height:8px;border-radius:4px;flex:1;min-width:40px;background:#21262d}}
  .stat-row .sbar-fill{{height:100%;border-radius:4px;transition:width .3s}}
  .stat-row .sval{{width:22px;text-align:right;color:#c9d1d9;font-weight:600;flex-shrink:0}}
  .clr-mob{{background:#58a6ff}}
  .clr-res{{background:#3fb950}}
  .clr-rec{{background:#d29922}}
  .clr-dis{{background:#f0883e}}
  .clr-int{{background:#bc8cff}}
  .clr-str{{background:#f85149}}
  .stat-total{{font-weight:700;color:#f0f6fc;font-size:.9rem;margin-top:3px}}

  /* ── Farming ───────────────────────────────────────── */
  .farming-list{{
    background:#161b22; border:1px solid #30363d; border-radius:8px;
    padding:1rem 1rem 1rem 2.5rem; margin:.5rem 0;
  }}
  .farming-list li{{padding:.3rem 0;color:#c9d1d9;font-size:.85rem}}
  .farming-list li::marker{{color:#58a6ff;font-weight:700}}

  /* ── Responsive ────────────────────────────────────── */
  @media(max-width:640px){{
    body{{padding:.75rem}}
    h1{{font-size:1.3rem}}
    .stat-card{{flex:1 1 80px;padding:.6rem}}
    .stat-card .val{{font-size:1.4rem}}
    td,th{{padding:.4rem .5rem;font-size:.75rem}}
    .detail-grid{{flex-direction:column}}
  }}

  /* ── Tabs ──────────────────────────────────────────── */
  .tabs{{display:flex;gap:0;margin:1rem 0;border-bottom:2px solid #21262d}}
  .tab{{
    padding:.5rem 1.25rem; cursor:pointer; color:#8b949e;
    border-bottom:2px solid transparent; margin-bottom:-2px;
    font-size:.85rem; font-weight:500; transition:all .15s;
  }}
  .tab:hover{{color:#c9d1d9}}
  .tab.active{{color:#58a6ff;border-bottom-color:#58a6ff}}
  .tab-section{{display:none}}
  .tab-section.active{{display:block}}
</style>
</head>
<body>
<div class="container" id="app">
  <h1>🛡️ D2 Vault Dashboard <small>{date_str}</small></h1>
  <div class="subtitle">All weapons checked against curated god rolls. Armour ranked by total stats.</div>

  <!-- Stat cards -->
  <div class="summary-grid" id="stats"></div>

  <!-- Tabs -->
  <div class="tabs">
    <div class="tab active" data-tab="weapons">Weapons</div>
    <div class="tab" data-tab="armor">Armour</div>
    <div class="tab" data-tab="farming">Farming Guide</div>
  </div>

  <!-- Weapons tab -->
  <div class="tab-section active" id="tab-weapons">
    <div class="toolbar">
      <input type="text" id="search" placeholder="🔍 Search weapons…" oninput="renderWeapons()">
      <select id="filterVerdict" onchange="renderWeapons()">
        <option value="all">All</option>
        <option value="KEEP">✅ Keep</option>
        <option value="DIMABLE">❌ Dismantle</option>
        <option value="FARM_FOR_BETTER">⚠️ Farm</option>
      </select>
    </div>
    <div class="table-wrap" id="weapons-table"></div>
  </div>

  <!-- Armour tab -->
  <div class="tab-section" id="tab-armor">
    <div class="table-wrap" id="armor-table"></div>
  </div>

  <!-- Farming tab -->
  <div class="tab-section" id="tab-farming">
    <ol class="farming-list" id="farming-list"></ol>
  </div>
</div>

<script>
// ── Data ──────────────────────────────────────────────────────────────
const DATA = {data_json};

// ── Tab switching ─────────────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {{
  t.addEventListener('click', () => {{
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tab-section').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('tab-' + t.dataset.tab).classList.add('active');
  }});
}});

// ── Stat cards ────────────────────────────────────────────────────────
function renderStats() {{
  const s = DATA;
  document.getElementById('stats').innerHTML = `
    <div class="stat-card"><div class="val">${{s.total_items||0}}</div><div class="lbl">Total Items</div></div>
    <div class="stat-card"><div class="val">${{s.total_weapons||0}}</div><div class="lbl">Weapons</div></div>
    <div class="stat-card"><div class="val">${{s.total_armor||0}}</div><div class="lbl">Armour</div></div>
    <div class="stat-card keep"><div class="val">${{s.keep_count||0}}</div><div class="lbl">Keep</div></div>
    <div class="stat-card dismantle"><div class="val">${{s.dismantle_count||0}}</div><div class="lbl">Dismantle</div></div>
  `;
}}

// ── Weapons table ─────────────────────────────────────────────────────
let weaponSortCol = -1;
let weaponSortDir = 1;

function getBadge(verdict) {{
  const map = {{'KEEP':'badge-keep','DIMABLE':'badge-dim','FARM_FOR_BETTER':'badge-farm'}};
  return `<span class="badge ${{map[verdict]||''}}">${{verdict||'?'}}</span>`;
}}

function renderWeapons() {{
  const q = document.getElementById('search').value.toLowerCase();
  const fv = document.getElementById('filterVerdict').value;
  const matches = DATA.god_roll_matches || [];
  const recs = DATA.dismantle_recommendations || [];

  // Build lookup from dismantle recs
  const dimLookup = {{}};
  recs.forEach(r => dimLookup[r.name] = r.reason);

  // Merge god-roll matches + dimmable items
  let items = matches.map(m => ({{
    name: m.name, roll: m.roll_name, pct: m.match_pct,
    perks: m.perks, verdict: m.verdict,
    reason: m.perks || '',
    isDim: false,
  }}));

  // Add dimmable items not in matches
  recs.forEach(r => {{
    if (!items.find(i => i.name === r.name)) {{
      items.push({{
        name: r.name, roll: '—', pct: 0,
        perks: '', verdict: 'DIMABLE',
        reason: r.reason || '',
        isDim: true,
      }});
    }}
  }});

  // Filter
  items = items.filter(i => {{
    if (q && !i.name.toLowerCase().includes(q)) return false;
    if (fv !== 'all' && i.verdict !== fv) return false;
    return true;
  }});

  // Sort
  if (weaponSortCol >= 0) {{
    const key = ['name','roll','pct','verdict'][weaponSortCol];
    items.sort((a,b) => {{
      let va = a[key], vb = b[key];
      if (typeof va === 'number') return (va - vb) * weaponSortDir;
      return (''+va).localeCompare(''+vb) * weaponSortDir;
    }});
  }}

  let html = `<table><thead><tr>
    <th onclick="sortWeapons(0)" class="${{weaponSortCol===0?'sorted':''}}">Name<span class="sort-arrow">${{weaponSortCol===0?(weaponSortDir>0?'▲':'▼'):'⇅'}}</span></th>
    <th onclick="sortWeapons(1)" class="${{weaponSortCol===1?'sorted':''}}">Roll<span class="sort-arrow">${{weaponSortCol===1?(weaponSortDir>0?'▲':'▼'):'⇅'}}</span></th>
    <th onclick="sortWeapons(2)" class="${{weaponSortCol===2?'sorted':''}}">Match<span class="sort-arrow">${{weaponSortCol===2?(weaponSortDir>0?'▲':'▼'):'⇅'}}</span></th>
    <th onclick="sortWeapons(3)" class="${{weaponSortCol===3?'sorted':''}}">Verdict<span class="sort-arrow">${{weaponSortCol===3?(weaponSortDir>0?'▲':'▼'):'⇅'}}</span></th>
    <th>Details</th>
  </tr></thead><tbody>`;

  if (items.length === 0) {{
    html += `<tr><td colspan="5" style="text-align:center;padding:2rem;color:#8b949e;font-style:italic">No weapons match your filters</td></tr>`;
  }}

  items.forEach((item, idx) => {{
    const pctClass = item.pct >= 80 ? '#3fb950' : item.pct >= 50 ? '#d29922' : '#8b949e';
    const detailId = `wdet-${{idx}}`;
    html += `<tr>
      <td><span class="weapon-name" onclick="toggleDetail('${{detailId}}')">${{item.name}}</span></td>
      <td>${{item.roll}}</td>
      <td style="color:${{pctClass}};font-weight:600">${{item.pct}}%</td>
      <td>${{getBadge(item.verdict)}}</td>
      <td><span style="color:#58a6ff;cursor:pointer;font-size:.75rem" onclick="toggleDetail('${{detailId}}')">▼ expand</span></td>
    </tr>
    <tr class="detail-row" id="${{detailId}}">
      <td colspan="5"><div class="detail-cell">
        <div class="detail-grid">
          <div class="detail-col">
            <h4>📋 Assessment</h4>
            <div style="font-size:.8rem;color:#c9d1d9">${{item.reason}}</div>
            ${{DATA.craftable_weapons && DATA.craftable_weapons.find(c => c.name === item.name) ? `<div style="margin-top:6px"><span class="badge badge-craft">🛠️ Craftable</span> — ${{DATA.craftable_weapons.find(c => c.name === item.name).deepsight_required}} Deepsight needed</div>` : ''}}
          </div>
        </div>
      </div></td>
    </tr>`;
  }});

  html += '</tbody></table>';
  document.getElementById('weapons-table').innerHTML = html;
}}

function toggleDetail(id) {{
  const el = document.getElementById(id);
  if (el) el.classList.toggle('open');
}}

function sortWeapons(col) {{
  if (weaponSortCol === col) weaponSortDir *= -1;
  else {{ weaponSortCol = col; weaponSortDir = 1; }}
  renderWeapons();
}}

// ── Armour ────────────────────────────────────────────────────────────
function renderArmor() {{
  const armor = DATA.best_armor || [];
  if (armor.length === 0) {{
    document.getElementById('armor-table').innerHTML = '<p class="empty-state">No armour data available.</p>';
    return;
  }}

  const statColors = {{
    'Mobility':'#58a6ff','Resilience':'#3fb950','Recovery':'#d29922',
    'Discipline':'#f0883e','Intellect':'#bc8cff','Strength':'#f85149'
  }};
  const statClasses = {{'Mobility':'clr-mob','Resilience':'clr-res','Recovery':'clr-rec','Discipline':'clr-dis','Intellect':'clr-int','Strength':'clr-str'}};
  const statOrder = ['Mobility','Resilience','Recovery','Discipline','Intellect','Strength'];

  // We don't have full stat breakdown in best_armor, so show what we have
  let html = `<table><thead><tr><th>Name</th><th>Slot</th><th>Total Stats</th><th>Distribution</th></tr></thead><tbody>`;
  armor.forEach(p => {{
    html += `<tr>
      <td style="font-weight:600">${{p.name||'?'}}</td>
      <td>${{p.slot||'?'}}</td>
      <td style="color:#58a6ff;font-weight:700">${{p.stat_total||0}}</td>
      <td><span class="badge badge-keep">${{p.distribution||'unknown'}}</span></td>
    </tr>`;
  }});
  html += '</tbody></table>';
  document.getElementById('armor-table').innerHTML = html;
}}

// ── Farming ───────────────────────────────────────────────────────────
function renderFarming() {{
  const tips = DATA.farming_recommendations || [];
  const list = document.getElementById('farming-list');
  if (tips.length === 0) {{
    list.innerHTML = '<li style="color:#8b949e;font-style:italic">No farming recommendations.</li>';
    return;
  }}
  list.innerHTML = tips.map(t => `<li>${{t}}</li>`).join('');
}}

// ── Init ──────────────────────────────────────────────────────────────
renderStats();
renderWeapons();
renderArmor();
renderFarming();
</script>
</body>
</html>"""

    output_path = os.path.abspath(output_path)
    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(report_html)

    return output_path
