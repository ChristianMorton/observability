#!/usr/bin/env python3
"""Mail Prometheus alerts via the host msmtp relay (no Alertmanager, no new creds).

Runs every minute from obs-alert-mailer.timer as root on hub. Polls
Prometheus (127.0.0.1:50090) for firing alerts, e-mails new ones, a reminder
every REPEAT_HOURS while still firing, and a RESOLVED mail when they clear.
Config: /etc/default/obs-alert-mailer (ALERT_TO, REPEAT_HOURS, PROM_URL).
"""
import json, os, subprocess, time, urllib.request
from email.message import EmailMessage

PROM = os.environ.get("PROM_URL", "http://127.0.0.1:9090")
TO = os.environ.get("ALERT_TO", "root")
REPEAT = float(os.environ.get("REPEAT_HOURS", "6")) * 3600
STATE = "/var/lib/obs-alert-mailer/state.json"
GRAFANA = os.environ.get("GRAFANA_URL", "http://localhost:3003")


def key(a):
    labels = a["labels"]
    return "|".join(f"{k}={labels[k]}" for k in sorted(labels))


def send(subject, body):
    m = EmailMessage()
    m["To"] = TO
    m["Subject"] = subject
    m.set_content(body)
    subprocess.run(["/usr/sbin/sendmail", "-t", "-i"], input=m.as_bytes(), check=True, timeout=60)


def save(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


def main():
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    try:
        with open(STATE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    try:
        with urllib.request.urlopen(PROM + "/api/v1/alerts", timeout=15) as r:
            alerts = [a for a in json.load(r)["data"]["alerts"] if a["state"] == "firing"]
        state.pop("__prom_down__", None)
    except Exception as e:
        last = state.get("__prom_down__", 0)
        if time.time() - last > REPEAT:
            send("[CRITICAL] hub: Prometheus unreachable, alerting is blind",
                 f"obs-alert-mailer could not query {PROM}: {e}")
            state["__prom_down__"] = time.time()
        save(state)
        return
    now = time.time()
    seen = set()
    for a in alerts:
        k = key(a)
        seen.add(k)
        labels, ann = a["labels"], a.get("annotations", {})
        sev = labels.get("severity", "warning").upper()
        summary = ann.get("summary", labels.get("alertname"))
        prev = state.get(k)
        if prev is None or now - prev["sent"] > REPEAT:
            body = (f"{summary}\n\n{ann.get('description', '')}\n\n"
                    f"Labels: {json.dumps(labels, indent=1)}\n"
                    f"Active since: {a.get('activeAt')}\n"
                    f"Grafana: {GRAFANA}/d/obs-alerts\n")
            send(f"[{sev}] {summary}", body)
            state[k] = {"sent": now, "summary": summary}
    for k in [k for k in state if not k.startswith("__") and k not in seen]:
        cleared = time.strftime("%Y-%m-%d %H:%M %Z")
        send(f"[RESOLVED] {state[k]['summary']}", f"Cleared at {cleared}.\n\n{k}")
        del state[k]
    save(state)


if __name__ == "__main__":
    main()
