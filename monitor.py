"""Monitor de trabajos pagos de Expensify/App.

Detecta issues que pasan a cumplir: abierta + etiquetas External y Help Wanted
+ precio en el título ([$250]). Avisa creando una issue en el repo de avisos.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
TARGET_REPO = os.environ.get("TARGET_REPO", "Expensify/App")
NOTIFY_REPO = os.environ.get("NOTIFY_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
NOTIFY_USER = os.environ.get("NOTIFY_USER", "LeandroAram")
SESSION_URL = os.environ.get("DEVIN_SESSION_URL", "")
SHUTDOWN_AT = dt.datetime.fromisoformat(os.environ.get("SHUTDOWN_AT", "2026-11-30T23:59:59-03:00"))
REQUIRED_LABELS = {"External", "Help Wanted"}
PRICE_RE = re.compile(r"\[\s*\$\s*([\d][\d,.]*)\s*\]")
OVERLAP = dt.timedelta(minutes=5)

QUALIFIED, UNQUALIFIED, CLOSED = "q", "u", "closed"


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def price_of(title):
    m = PRICE_RE.search(title or "")
    return m.group(1).replace(",", "") if m else None


def labels_of(issue):
    return {lab["name"] if isinstance(lab, dict) else lab for lab in issue.get("labels", [])}


def status_of(issue):
    if issue.get("state") != "open":
        return CLOSED
    if REQUIRED_LABELS <= labels_of(issue) and price_of(issue.get("title")):
        return QUALIFIED
    return UNQUALIFIED


def transition(prev, issue, reopened_since_cursor=False):
    """Devuelve 'nueva', 'reapertura' o None según el cambio de estado."""
    new = status_of(issue)
    if new != QUALIFIED:
        return None
    if prev == QUALIFIED:
        return "reapertura" if reopened_since_cursor else None
    if prev == CLOSED or (prev is None and reopened_since_cursor):
        return "reapertura"
    return "nueva"


class GitHub:
    def __init__(self, token):
        self.token = token
        self.calls = 0

    def request(self, method, path, body=None, params=None):
        url = path if path.startswith("http") else API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        self.calls += 1
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw else None), resp.headers

    def paginate(self, path, params, max_pages=30):
        params = dict(params, per_page=100)
        url, out = path, []
        for _ in range(max_pages):
            items, headers = self.request("GET", url, params=params)
            out.extend(items)
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link") or "")
            if not nxt:
                break
            url, params = nxt.group(1), None
        return out


def load_state(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save_state(path, state):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def baseline(gh, now):
    issues = gh.paginate(f"/repos/{TARGET_REPO}/issues",
                         {"state": "open", "labels": ",".join(sorted(REQUIRED_LABELS))})
    tracked = {}
    for it in issues:
        if "pull_request" in it:
            continue
        tracked[str(it["number"])] = {"s": status_of(it), "p": price_of(it["title"])}
    return {"cursor": iso(now), "baseline_at": iso(now), "tracked": tracked, "alerts": []}


def reopened_after(gh, number, since):
    events = gh.paginate(f"/repos/{TARGET_REPO}/issues/{number}/events", {}, max_pages=5)
    return any(e.get("event") == "reopened" and parse_iso(e["created_at"]) > since for e in events)


def detect(gh, state, now):
    since = parse_iso(state["cursor"]) - OVERLAP
    issues = gh.paginate(f"/repos/{TARGET_REPO}/issues",
                         {"state": "all", "sort": "updated", "direction": "asc", "since": iso(since)})
    tracked = state["tracked"]
    found = []
    for it in issues:
        if "pull_request" in it:
            continue
        key = str(it["number"])
        prev = tracked.get(key, {}).get("s")
        new = status_of(it)
        reopened = False
        if new == QUALIFIED and prev in (QUALIFIED, None) and it.get("state_reason") == "reopened":
            reopened = reopened_after(gh, it["number"], since)
        kind = transition(prev, it, reopened)
        if kind:
            found.append((kind, it))
        if new != UNQUALIFIED or key in tracked or REQUIRED_LABELS & labels_of(it):
            tracked[key] = {"s": new, "p": price_of(it["title"])}
    state["cursor"] = iso(now)
    return found


def ensure_label(gh, name, color):
    try:
        gh.request("POST", f"/repos/{NOTIFY_REPO}/labels", {"name": name, "color": color})
    except urllib.error.HTTPError as e:
        if e.code != 422:
            raise


def notify(gh, kind, issue, test=False):
    n, title = issue["number"], issue["title"]
    url = issue["html_url"]
    tag = "[PRUEBA] " if test else ""
    created = issue["created_at"][:10]
    labels = ", ".join(sorted(labels_of(issue)))
    resume = (f"Abrí {SESSION_URL} y escribí: `Analizá {url}`" if SESSION_URL
              else f"En tu sesión de Devin escribí: `Analizá {url}`")
    body = "\n".join([
        f"@{NOTIFY_USER} {'**PRUEBA DEL MONITOR — no es una oportunidad real.** ' if test else ''}"
        f"Trabajo pago {'reabierto' if kind == 'reapertura' else 'nuevo'} en Expensify/App.",
        "",
        f"- Issue: {url}",
        f"- Título: {title}",
        f"- Precio: ${price_of(title)}",
        f"- Tipo de detección: {kind}",
        f"- Creada: {created} · Etiquetas: {labels}",
        f"- Propuestas/comentarios al detectar: {issue.get('comments', 0)}",
        "",
        f"**Para reanudar a Devin (1 línea):** {resume}",
    ])
    ensure_label(gh, "oportunidad", "0e8a16")
    issue_labels = ["oportunidad", kind]
    if test:
        ensure_label(gh, "prueba", "fbca04")
        issue_labels.append("prueba")
    ensure_label(gh, kind, "1d76db")
    created_issue, _ = gh.request("POST", f"/repos/{NOTIFY_REPO}/issues", {
        "title": f"{tag}Oportunidad {kind}: Expensify/App#{n} {title}"[:250],
        "body": body,
        "labels": issue_labels,
    })
    return created_issue["html_url"]


def disable_workflow(gh):
    wf = os.environ.get("WORKFLOW_FILE", "monitor.yml")
    try:
        gh.request("PUT", f"/repos/{NOTIFY_REPO}/actions/workflows/{wf}/disable")
        print("Workflow deshabilitado.")
    except urllib.error.HTTPError as e:
        print(f"No se pudo deshabilitar el workflow: {e.code}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state/state.json")
    ap.add_argument("--simulate", type=int, help="Prueba: trata esta issue como recién publicada")
    ap.add_argument("--dry-run", action="store_true", help="No crea issues de aviso")
    args = ap.parse_args(argv)

    gh = GitHub(os.environ.get("GITHUB_TOKEN"))
    now = utcnow()
    if now >= SHUTDOWN_AT:
        print(f"Fecha de apagado alcanzada ({SHUTDOWN_AT.isoformat()}).")
        if not args.dry_run:
            disable_workflow(gh)
        return 0

    state = load_state(args.state)
    if args.simulate:
        if state is None:
            print("Sin línea base todavía.")
            return 1
        issue, _ = gh.request("GET", f"/repos/{TARGET_REPO}/issues/{args.simulate}")
        kind = transition(None, dict(issue, state_reason=None))
        print(f"Simulación #{args.simulate}: estado={status_of(issue)} detección={kind}")
        if kind and not args.dry_run:
            print("Aviso:", notify(gh, kind, issue, test=True))
        return 0 if kind else 1

    if state is None:
        state = baseline(gh, now)
        q = sum(1 for v in state["tracked"].values() if v["s"] == QUALIFIED)
        print(f"Línea base: {len(state['tracked'])} issues con etiquetas, {q} con precio. Sin avisos.")
        save_state(args.state, state)
        return 0

    found = detect(gh, state, now)
    for kind, issue in found:
        link = "(dry-run)" if args.dry_run else notify(gh, kind, issue)
        print(f"DETECCIÓN {kind}: #{issue['number']} {issue['title']} -> {link}")
        state["alerts"] = (state.get("alerts", []) + [
            {"n": issue["number"], "k": kind, "at": iso(now)}])[-200:]
    print(f"Ejecución OK: {len(found)} detecciones, {gh.calls} llamadas a la API, cursor {state['cursor']}.")
    save_state(args.state, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
