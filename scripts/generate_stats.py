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
ACCENT  = "#C23E6E"
GLOW    = "#F778BA"
DEEP    = "#7C2D4A"
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
        stargazerCount
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


def write(name, body):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body + "</svg>\n")
    print("wrote %s" % path)


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
        s += ('<rect x="%d" y="%d" width="%d" height="%d" rx="2.5" fill="%s" '
              'opacity="%.2f"/>' % (x, base - h, cw, h, ACCENT, op))
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
    s += '<text class="big" x="30" y="100">%d</text>' % cur
    s += '<text class="sm" x="31" y="121">current streak, days</text>'
    s += '<text class="sm" x="31" y="138">%s → %s</text>' % (esc(pretty(cr[0])), esc(pretty(cr[1])))
    s += '<line x1="22" y1="156" x2="%d" y2="156" stroke="%s"/>' % (W - 22, BORDER)
    s += '<text class="nm" x="30" y="182">%d</text>' % lon
    s += '<text class="sm" x="72" y="182">longest</text>'
    s += '<text class="sm" x="31" y="200">%s → %s</text>' % (esc(pretty(lr[0])), esc(pretty(lr[1])))
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
    for name, size in top:
        pct = size / grand * 100
        s += '<circle cx="32" cy="%d" r="4.5" fill="%s"/>' % (y - 4, colour[name])
        s += '<text class="md" x="46" y="%d">%s</text>' % (y, esc(name))
        s += ('<text class="sm" x="%d" y="%d" text-anchor="end">%.1f%%</text>'
              % (W - 26, y, pct))
        s += ('<rect x="46" y="%d" width="%d" height="5" rx="2.5" fill="%s" '
              'opacity=".22"/>' % (y + 8, W - 72, BORDER))
        s += ('<rect x="46" y="%d" width="%.1f" height="5" rx="2.5" fill="%s"/>'
              % (y + 8, (W - 72) * size / grand, ACCENT))
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
        s += ('<text y="%d" font-size="14" fill="%s" text-anchor="middle">%s</text>'
              % (y0 + r * ch, ACCENT, spans))

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
