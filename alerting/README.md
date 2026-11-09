# Alerting on hub

Rules live in `prometheus-rules/*.yml` (deployed with the stack). There is no
Alertmanager: `obs-alert-mailer` (root systemd timer on hub, every 60s) polls
Prometheus and e-mails firing/resolved alerts through the host msmtp relay
(`/etc/msmtprc`, smtp.gmail.com:587 — Hetzner blocks 25/465). No new
credentials are needed.

Install / update (needs sudo, not done by CI):

    sudo install -m755 alerting/obs-alert-mailer.py /usr/local/bin/obs-alert-mailer
    sudo install -m644 alerting/obs-alert-mailer.{service,timer} /etc/systemd/system/
    echo "ALERT_TO=you@example.com" | sudo tee /etc/default/obs-alert-mailer
    sudo systemctl daemon-reload && sudo systemctl enable --now obs-alert-mailer.timer
