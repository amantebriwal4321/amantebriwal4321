#!/usr/bin/env python3
"""Draw the profile graphics from the GitHub GraphQL API.

Standard library only - urllib for the API, nothing to break in CI.
Writes assets/*.svg. Run by .github/workflows/stats.yml.

Determinism matters here: the workflow commits whatever this produces, so
any nondeterminism becomes a stream of meaningless nightly commits.
Two guards:
  1. the contribution window is pinned to whole UTC days
  2. repositories are filtered to public only, so a personal token and the
     workflow token see the same set
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

# ── palette ───────────────────────────────────────────────────────────────
BG      = "#0D1117"
BORDER  = "#30363D"
ACCENT  = "#10B981"
GLOW    = "#6EE7B7"
DEEP    = "#047857"
TEXT    = "#C9D1D9"
MUTED   = "#8B949E"

MONO = "ui-monospace, SFMono-Regular, 'JetBrains Mono', Menlo, Consolas, monospace"

# 10-level ramp, darkest to densest. One fill colour, varying weight -
# per-character rainbow is what makes these look like static.
RAMP = " .:-=+*#%@"

OUT_DIR = "assets"
API = "https://api.github.com/graphql"

QUERY = """
query($login:String!, $from:DateTime!, $to:DateTime!) {
  user(login:$login) {
    followers { totalCount }
    contributionsCollection(from:$from, to:$to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(first:100, privacy:PUBLIC, isFork:false,
                 ownerAffiliations:[OWNER],
                 orderBy:{field:PUSHED_AT, direction:DESC}) {
      totalCount
      nodes {
        name
        pushedAt
        stargazerCount
        primaryLanguage { name color }
        languages(first:10, orderBy:{field:SIZE, direction:DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


# ── helpers ───────────────────────────────────────────────────────────────

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def fetch(login, token):
    today = datetime.now(timezone.utc).date()
    frm = datetime.combine(today - timedelta(days=364), datetime.min.time())
    to = datetime.combine(today, datetime.max.time().replace(microsecond=0))
    body = json.dumps({
        "query": QUERY,
        "variables": {
            "login": login,
            "from": frm.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }).encode()
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": "bearer " + token,
        "Content-Type": "application/json",
        "User-Agent": "profile-stats-generator",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    if "errors" in payload:
        raise SystemExit("GraphQL error: %s" % payload["errors"])
    return payload["data"]["user"]


def streaks(days):
    """(current, longest, current_range, longest_range) over ascending days.

    Today counting zero does not break the current streak - the day is not
    over yet. Any earlier zero does.
    """
    longest = cur = 0
    l_range = c_range = ("", "")
    run, start = 0, None
    for d in days:
        if d["contributionCount"] > 0:
            run += 1
            start = start or d["date"]
            if run > longest:
                longest, l_range = run, (start, d["date"])
        else:
            run, start = 0, None
    # walk backwards for the live streak
    run, end, start = 0, None, None
    for d in reversed(days):
        if d["contributionCount"] > 0:
            run += 1
            end = end or d["date"]
            start = d["date"]
        elif d is days[-1]:
            continue          # today may legitimately be empty
        else:
            break
    cur, c_range = run, (start or "", end or "")
    return cur, longest, c_range, l_range


def pretty(iso):
    if not iso:
        return "-"
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y")


def human(n):
    return "{:,}".format(n)


def frame(w, h, label):
    """Card chrome: background, hairline border, lowercase mono label."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        'viewBox="0 0 {w} {h}" role="img">'
        '<style>text{{font-family:{mono};}}'
        '.lb{{fill:{muted};font-size:11px;letter-spacing:.14em;}}'
        '.big{{fill:{acc};font-size:44px;font-weight:700;}}'
        '.md{{fill:{txt};font-size:15px;}}'
        '.sm{{fill:{muted};font-size:11.5px;}}'
        '.nm{{fill:{txt};font-size:19px;font-weight:700;}}</style>'
        '<rect x=".5" y=".5" width="{w1}" height="{h1}" rx="11" fill="{bg}" '
        'stroke="{bd}"/>'
        '<text class="lb" x="22" y="28">{label}</text>'
        '<line x1="22" y1="40" x2="{lx}" y2="40" stroke="{bd}"/>'
    ).format(w=w, h=h, w1=w - 1, h1=h - 1, bg=BG, bd=BORDER, mono=MONO,
             muted=MUTED, acc=ACCENT, txt=TEXT, label=esc(label), lx=w - 22)


def anim(attr, to, begin=0.0, dur=0.7, frm="0", tag="animate", extra=""):
    """A one-shot SMIL animation that is ALREADY ACTIVE at t=0.

    A delayed begin="" is the obvious way to stagger, and it is wrong here:
    before the animation starts the attribute falls back to its static value,
    which is the finished state (kept that way so non-SMIL renderers still
    show a complete card). The element therefore flashes fully drawn, blanks,
    then animates in. Encoding the delay as a flat leading segment in
    keyTimes keeps the value pinned from the first frame instead.
    """
    total = begin + dur
    if begin <= 0.001:
        vals, keys = "%s;%s" % (frm, to), "0;1"
    else:
        vals = "%s;%s;%s" % (frm, frm, to)
        keys = "0;%.4f;1" % (begin / total)
    return ('<%s attributeName="%s" values="%s" keyTimes="%s" dur="%gs" '
            'fill="freeze"%s/>' % (tag, attr, vals, keys, total, extra))


def write(name, body):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body + "</svg>\n")
    print("wrote %s" % path)


# ── 0. hero: the name draws itself ────────────────────────────────────────

def hero(name, tagline):
    """Stroke-drawn name over a drifting grid.

    Scripts are stripped from anything GitHub renders, so all motion here is
    SMIL inside the file. The one-shot parts use fill="freeze" so the page
    settles instead of looping forever; only the grid drift and the scanline
    keep moving, which is what stops it looking like a static image.

    Every static attribute holds the *finished* value, so a renderer that
    ignores SMIL still shows a correct, complete card.
    """
    W, H = 900, 232
    s = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
         'viewBox="0 0 %d %d" role="img">' % (W, H, W, H))
    s += ('<defs><clipPath id="panel"><rect width="%d" height="%d" rx="14"/>'
          '</clipPath></defs>' % (W, H))
    s += '<style>text{font-family:%s;}</style>' % MONO
    s += ('<rect x=".5" y=".5" width="%d" height="%d" rx="14" fill="%s" '
          'stroke="%s"/>' % (W - 1, H - 1, BG, BORDER))

    s += '<g clip-path="url(#panel)">'
    s += '<g stroke="%s" stroke-width=".6" opacity=".13">' % ACCENT
    for x in range(-40, W + 60, 34):
        s += '<line x1="%d" y1="0" x2="%d" y2="%d"/>' % (x, x, H)
    for y in range(0, H + 34, 34):
        s += '<line x1="-40" y1="%d" x2="%d" y2="%d"/>' % (y, W + 60, y)
    s += ('<animateTransform attributeName="transform" type="translate" '
          'values="0 0;34 0" dur="7s" repeatCount="indefinite"/></g>')
    s += ('<rect width="%d" height="2" fill="%s" opacity=".22">'
          '<animate attributeName="y" values="-4;%d" dur="5s" '
          'repeatCount="indefinite"/></rect>' % (W, GLOW, H + 4))
    s += '</g>'

    s += ('<text x="44" y="54" font-size="12" fill="%s" letter-spacing=".16em">'
          '$ whoami</text>' % MUTED)

    # Glyph outlines take the dash, so the name literally writes itself.
    s += ('<text x="42" y="134" font-size="52" font-weight="700" '
          'letter-spacing="1.5" fill="%s" stroke="%s" stroke-width=".9" '
          'stroke-opacity="0" stroke-dasharray="2000">%s'
          '<animate attributeName="stroke-opacity" values="1;1;0" '
          'keyTimes="0;.62;1" dur="4s" fill="freeze"/>'
          '<animate attributeName="stroke-dashoffset" from="2000" to="0" '
          'dur="2.6s" fill="freeze"/>'
          # Must be active from t=0: with a delayed begin the attribute falls
          # back to its static value (fill-opacity defaults to 1), which fills
          # the glyphs immediately and hides the whole stroke-draw.
          '<animate attributeName="fill-opacity" values="0;0;1" '
          'keyTimes="0;.52;1" dur="4s" fill="freeze"/></text>'
          % (ACCENT, ACCENT, esc(name)))

    L = len(tagline) * 8.4
    s += ('<clipPath id="tag"><rect x="44" y="152" height="26" width="%.1f">%s'
          '</rect></clipPath>'
          '<g clip-path="url(#tag)"><text x="44" y="171" font-size="14" '
          'fill="%s" textLength="%.1f" lengthAdjust="spacingAndGlyphs">%s'
          '</text></g>'
          % (L, anim("width", "%.1f" % L, 2.9, 1.3), TEXT, L, esc(tagline)))
    s += ('<rect x="%.1f" y="156" width="8" height="19" fill="%s">'
          '<animate attributeName="opacity" values="0;0;1;0;1;0;1" '
          'keyTimes="0;.72;.76;.84;.88;.96;1" dur="6s" '
          'repeatCount="indefinite"/></rect>' % (48 + L, GLOW))

    s += ('<line x1="44" y1="200" x2="%d" y2="200" stroke="%s"/>' % (W - 44, BORDER))
    s += ('<rect x="-170" y="199" width="170" height="1.6" fill="%s" opacity=".85">'
          '<animate attributeName="x" values="-170;%d" dur="4.5s" '
          'repeatCount="indefinite"/></rect>' % (GLOW, W))
    return s


# ── 1. headline: SMIL typing animation ────────────────────────────────────

def headline(lines):
    """Self-drawn replacement for third-party typing-SVG services.

    textLength pins each line's width in user units, so the animation lands
    identically regardless of which monospace font the visitor happens to
    have. That is the cheap fix for the cross-platform advance-width problem
    (the alternative is embedding a subsetted font in every file).
    """
    W, H, FS = 900, 132, 23
    CW = FS * 0.6                       # monospace advance we pin text to
    slot, type_t, hold_t = 3.6, 1.8, 1.2
    T = slot * len(lines)
    s = frame(W, H, "~ whoami")
    for i, ln in enumerate(lines):
        L = len(ln) * CW
        a = i * slot
        pts = [(0.0, 0.0), (a / T, 0.0), ((a + type_t) / T, L),
               ((a + type_t + hold_t) / T, L), ((a + slot) / T, 0.0), (1.0, 0.0)]
        kt, vals = [], []
        for k, v in pts:                # drop duplicate keyTimes
            k = round(min(max(k, 0.0), 1.0), 5)
            if kt and abs(k - kt[-1]) < 1e-9:
                vals[-1] = v
                continue
            kt.append(k)
            vals.append(v)
        ks = ";".join("%g" % k for k in kt)
        ws = ";".join("%g" % v for v in vals)
        s += (
            '<clipPath id="c{i}"><rect x="34" y="60" height="34" width="0">'
            '<animate attributeName="width" dur="{T}s" repeatCount="indefinite" '
            'keyTimes="{ks}" values="{ws}"/></rect></clipPath>'
            '<g clip-path="url(#c{i})"><text x="34" y="86" font-size="{fs}" '
            'fill="{acc}" font-weight="600" textLength="{L:.2f}" '
            'lengthAdjust="spacingAndGlyphs">{t}</text></g>'
            '<rect y="62" width="9" height="30" fill="{glow}" opacity="0">'
            '<animate attributeName="x" dur="{T}s" repeatCount="indefinite" '
            'keyTimes="{ks}" values="{xs}"/>'
            '<animate attributeName="opacity" dur="{T}s" repeatCount="indefinite" '
            'keyTimes="{ks}" values="{os_}"/></rect>'
        ).format(i=i, T=T, ks=ks, ws=ws, fs=FS, acc=ACCENT, glow=GLOW,
                 L=L, t=esc(ln),
                 xs=";".join("%g" % (34 + v) for v in vals),
                 os_=";".join("0" if v == 0 else "1" for v in vals))
    return s


# ── 2. activity: hero total + weekly columns ──────────────────────────────

def activity(cc, repos, stars, followers):
    W, H = 900, 214
    cal = cc["contributionCalendar"]
    s = frame(W, H, "~ activity  ·  last 365 days")

    s += '<text class="big" x="30" y="106">%s</text>' % human(cal["totalContributions"])
    s += '<text class="sm" x="31" y="128">contributions</text>'

    # Columns, not a line. Daily contributions are sparse and discrete - a
    # line through 0,0,11,0 draws values that never existed. Weekly sums are
    # continuous enough to aggregate, but columns stay honest about zeroes.
    weeks = cal["weeks"][-26:]
    sums = [sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks]
    peak = max(sums) or 1
    x0, base, cw, gap, maxh = 300, 150, 14, 8, 74
    for i, v in enumerate(sums):
        h = max(2, round(v / peak * maxh))
        x = x0 + i * (cw + gap)
        op = 0.42 + 0.58 * (v / peak)
        # Static attributes carry the finished bar; SMIL replays the growth.
        s += ('<rect x="%d" y="%d" width="%d" height="%d" rx="2.5" fill="%s" '
              'opacity="%.2f">%s%s</rect>'
              % (x, base - h, cw, h, ACCENT, op,
                 anim("height", h, i * 0.035, 0.65),
                 anim("y", base - h, i * 0.035, 0.65, frm=base)))
    s += ('<line x1="300" y1="%d" x2="%d" y2="%d" stroke="%s"/>'
          % (base + 4, x0 + 26 * (cw + gap) - gap, base + 4, BORDER))
    s += ('<text class="sm" x="300" y="%d">26 weeks  ·  peak %d</text>'
          % (base + 22, peak))

    cells = [("commits", cc["totalCommitContributions"]),
             ("pull requests", cc["totalPullRequestContributions"]),
             ("issues", cc["totalIssueContributions"]),
             ("reviews", cc["totalPullRequestReviewContributions"]),
             ("public repos", repos),
             ("stars", stars),
             ("followers", followers)]
    s += '<line x1="22" y1="168" x2="%d" y2="168" stroke="%s"/>' % (W - 22, BORDER)
    step = (W - 60) / len(cells)
    for i, (k, v) in enumerate(cells):
        x = 30 + i * step
        s += '<text class="nm" x="%.0f" y="192">%s</text>' % (x, human(v))
        s += '<text class="sm" x="%.0f" y="206">%s</text>' % (x, esc(k))
    return s


# ── 3. streak ─────────────────────────────────────────────────────────────

def streak_card(days):
    W, H = 442, 214
    cur, lon, cr, lr = streaks(days)
    s = frame(W, H, "~ streak")
    s += ('<g opacity="1">%s%s<text class="big" x="30" y="100">%d</text></g>'
          % (anim("opacity", 1, 0.15, 0.6),
             anim("transform", "0 0", 0.15, 0.6, frm="0 10",
                  tag="animateTransform", extra=' type="translate"'),
             cur))
    s += '<text class="sm" x="31" y="121">current streak, days</text>'
    s += '<text class="sm" x="31" y="138">%s → %s</text>' % (esc(pretty(cr[0])), esc(pretty(cr[1])))
    # a rule that draws itself across the card
    s += ('<rect x="22" y="155.2" width="%d" height="1.6" fill="%s">%s</rect>'
          % (W - 44, ACCENT, anim("width", W - 44, 0.5, 0.9)))
    s += ('<g opacity="1">%s'
          '<text class="nm" x="30" y="182">%d</text>'
          '<text class="sm" x="72" y="182">longest</text>'
          '<text class="sm" x="31" y="200">%s → %s</text></g>'
          % (anim("opacity", 1, 0.8, 0.6), lon,
             esc(pretty(lr[0])), esc(pretty(lr[1]))))
    return s


# ── 4. languages ──────────────────────────────────────────────────────────

def langs_card(nodes):
    W, H = 442, 214
    tot = {}
    colour = {}
    for repo in nodes:
        for e in (repo.get("languages") or {}).get("edges") or []:
            n = e["node"]["name"]
            tot[n] = tot.get(n, 0) + e["size"]
            colour[n] = e["node"]["color"] or ACCENT
    top = sorted(tot.items(), key=lambda kv: -kv[1])[:5]
    grand = sum(tot.values()) or 1
    s = frame(W, H, "~ languages  ·  by bytes")
    y = 66
    for i, (name, size) in enumerate(top):
        pct = size / grand * 100
        w = (W - 72) * size / grand
        s += '<circle cx="32" cy="%d" r="4.5" fill="%s"/>' % (y - 4, colour[name])
        s += '<text class="md" x="46" y="%d">%s</text>' % (y, esc(name))
        s += ('<text class="sm" x="%d" y="%d" text-anchor="end">%.1f%%</text>'
              % (W - 26, y, pct))
        s += ('<rect x="46" y="%d" width="%d" height="5" rx="2.5" fill="%s" '
              'opacity=".22"/>' % (y + 8, W - 72, BORDER))
        s += ('<rect x="46" y="%d" width="%.1f" height="5" rx="2.5" fill="%s">'
              '%s</rect>'
              % (y + 8, w, ACCENT, anim("width", "%.1f" % w, 0.15 + i * 0.11, 0.8)))
        y += 30
    return s


# ── 5. the year, one character per day ────────────────────────────────────

def year_card(weeks):
    W, H = 900, 226
    cw, ch, x0, y0 = 16, 15, 30, 74
    peak = max((d["contributionCount"]
                for w in weeks for d in w["contributionDays"]), default=0) or 1
    s = frame(W, H, "~ the year  ·  one character per day")

    grid = [[None] * len(weeks) for _ in range(7)]
    for wi, w in enumerate(weeks):
        for d in w["contributionDays"]:
            row = datetime.strptime(d["date"], "%Y-%m-%d").weekday()
            grid[(row + 1) % 7][wi] = d
    for r in range(7):
        spans = ""
        for c in range(len(weeks)):
            d = grid[r][c]
            if d is None:
                continue
            n = d["contributionCount"]
            lvl = 0 if n == 0 else max(1, min(len(RAMP) - 1,
                                              round(n / peak * (len(RAMP) - 1))))
            ch_ = RAMP[lvl]
            if ch_ == " ":
                ch_ = "."
                op = 0.16
            else:
                op = 0.34 + 0.66 * (lvl / (len(RAMP) - 1))
            spans += ('<tspan x="%d" fill-opacity="%.2f">%s</tspan>'
                      % (x0 + c * cw, op, esc(ch_)))
        # One animate per row rather than per cell - 7 elements instead of
        # 365, and it reads as a wave running down the grid.
        s += ('<text y="%d" font-size="14" fill="%s" text-anchor="middle">%s%s'
              '</text>'
              % (y0 + r * ch, ACCENT, spans,
                 anim("opacity", 1, 0.2 + r * 0.09, 0.5)))

    seen, lx = set(), -99
    for wi, w in enumerate(weeks):
        d0 = w["contributionDays"][0]
        dt = datetime.strptime(d0["date"], "%Y-%m-%d")
        key = (dt.year, dt.month)
        x = x0 + wi * cw
        if key not in seen and x - lx > 52:
            seen.add(key)
            lx = x
            s += ('<text class="sm" x="%d" y="54" text-anchor="middle">%s</text>'
                  % (x, dt.strftime("%b").lower()))
    s += '<text class="sm" x="30" y="200">less</text>'
    for i in range(1, len(RAMP)):
        s += ('<text x="%d" y="200" font-size="14" fill="%s" fill-opacity="%.2f" '
              'text-anchor="middle">%s</text>'
              % (74 + i * 15, ACCENT, 0.34 + 0.66 * (i / (len(RAMP) - 1)),
                 esc(RAMP[i])))
    s += '<text class="sm" x="%d" y="200">more</text>' % (74 + len(RAMP) * 15)
    return s


# ── 6. recently shipped ───────────────────────────────────────────────────

def ago(iso, now):
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    d = (now - dt).days
    if d <= 0:
        return "today"
    if d == 1:
        return "yesterday"
    if d < 7:
        return "%dd ago" % d
    if d < 30:
        return "%dw ago" % (d // 7)
    if d < 365:
        return "%dmo ago" % (d // 30)
    return "%dy ago" % (d // 365)


def recent(nodes, now):
    """The six most recently pushed public repos - so the profile ages well
    on its own instead of pointing at whatever was current the day it was
    written."""
    W, H = 900, 214
    items = sorted((n for n in nodes if n.get("pushedAt")),
                   key=lambda n: n["pushedAt"], reverse=True)[:6]
    s = frame(W, H, "~ recently shipped")
    cols, rows = (30, 470), (80, 126, 172)
    for i, n in enumerate(items):
        cx, cy = cols[i % 2], rows[i // 2]
        right = cx + 380
        lang = n.get("primaryLanguage") or {}
        name = n["name"]
        if len(name) > 30:
            name = name[:29] + "…"
        begin = 0.2 + i * 0.09
        s += ('<g opacity="1">%s%s'
              % (anim("opacity", 1, begin, 0.5),
                 anim("transform", "0 0", begin, 0.5, frm="0 9",
                      tag="animateTransform", extra=' type="translate"')))
        s += '<circle cx="%d" cy="%d" r="4.5" fill="%s"/>' % (
            cx + 6, cy - 5, lang.get("color") or ACCENT)
        s += '<text class="md" x="%d" y="%d">%s</text>' % (cx + 20, cy, esc(name))
        s += '<text class="sm" x="%d" y="%d">%s</text>' % (
            cx + 20, cy + 16, esc(lang.get("name") or "—"))
        s += ('<text class="sm" x="%d" y="%d" text-anchor="end">%s</text>'
              % (right, cy, esc(ago(n["pushedAt"], now))))
        s += '</g>'
    return s


# ── 7. footer ─────────────────────────────────────────────────────────────

def footer(quote):
    W, H = 900, 92
    s = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
         'viewBox="0 0 %d %d" role="img">' % (W, H, W, H))
    s += '<style>text{font-family:%s;}</style>' % MONO
    s += ('<rect x=".5" y=".5" width="%d" height="%d" rx="14" fill="%s" '
          'stroke="%s"/>' % (W - 1, H - 1, BG, BORDER))
    s += '<line x1="44" y1="34" x2="%d" y2="34" stroke="%s"/>' % (W - 44, BORDER)
    s += ('<rect x="-190" y="33" width="190" height="1.6" fill="%s" opacity=".9">'
          '<animate attributeName="x" values="-190;%d" dur="5s" '
          'repeatCount="indefinite"/></rect>' % (ACCENT, W))
    s += ('<text x="%d" y="66" font-size="14" fill="%s" text-anchor="middle">%s'
          '</text>' % (W // 2, MUTED, esc(quote)))
    return s


# ── main ──────────────────────────────────────────────────────────────────

def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    login = os.environ.get("GH_LOGIN", "").strip()
    if not token or not login:
        sys.exit("GITHUB_TOKEN and GH_LOGIN must both be set")

    os.makedirs(OUT_DIR, exist_ok=True)
    user = fetch(login, token)
    cc = user["contributionsCollection"]
    weeks = cc["contributionCalendar"]["weeks"]
    days = [d for w in weeks for d in w["contributionDays"]]
    nodes = user["repositories"]["nodes"]
    stars = sum(r["stargazerCount"] for r in nodes)

    write("hero.svg", hero("AMAN TEBRIWAL",
                           "I build cool stuff that has potential."))
    write("recent.svg", recent(nodes, datetime.now(timezone.utc)))
    write("footer.svg", footer("ship it, measure it, then make it beautiful"))
    write("headline.svg", headline([
        "CS undergrad  ·  Bengaluru, India",
        "AI  ·  automation  ·  full-stack",
        "computer vision, agents, real-time data",
        "learning fast, building faster",
    ]))
    write("activity.svg", activity(cc, user["repositories"]["totalCount"],
                                   stars, user["followers"]["totalCount"]))
    write("streak.svg", streak_card(days))
    write("langs.svg", langs_card(nodes))
    write("year.svg", year_card(weeks))


if __name__ == "__main__":
    main()
