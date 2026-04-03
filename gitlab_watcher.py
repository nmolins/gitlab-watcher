#!/usr/bin/env python3
"""GitLab MR Watcher — Ubuntu system tray indicator for GitLab merge requests."""

import signal
import sys
import webbrowser
from pathlib import Path
from urllib.parse import quote as urlquote

import gi
import requests
import yaml

gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3

from gi.repository import GLib, Gtk, Notify

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

import os


def load_config():
    config_path = Path(__file__).parent / "config.yaml"
    cfg = {}
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}

    # Env overrides
    cfg["gitlab_url"] = os.environ.get("GITLAB_URL", cfg.get("gitlab_url", ""))
    cfg["private_token"] = os.environ.get("GITLAB_TOKEN", cfg.get("private_token", ""))
    cfg["project_ids"] = os.environ.get(
        "GITLAB_PROJECT_IDS",
        cfg.get("project_ids", cfg.get("project_id", "")),
    )
    cfg["poll_interval"] = int(
        os.environ.get("GITLAB_POLL_INTERVAL", cfg.get("poll_interval", 60))
    )
    cfg["mr_state"] = cfg.get("mr_state", "opened")
    cfg["per_page"] = int(cfg.get("per_page", 20))

    # Normalise project_ids to a list
    ids = cfg["project_ids"]
    if isinstance(ids, (int, str)):
        ids = str(ids)
        cfg["project_ids"] = [s.strip() for s in ids.split(",") if s.strip()]
    elif isinstance(ids, list):
        cfg["project_ids"] = [str(i).strip() for i in ids]

    for key in ("gitlab_url", "private_token", "project_ids"):
        if not cfg.get(key):
            print(f"Error: '{key}' manquant dans config.yaml ou env.", file=sys.stderr)
            sys.exit(1)

    return cfg


# ---------------------------------------------------------------------------
# GitLab client
# ---------------------------------------------------------------------------


class GitLabClient:
    def __init__(self, base_url: str, token: str, project_ids: list[str]):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.project_ids = project_ids
        self.session = requests.Session()
        self.session.headers["PRIVATE-TOKEN"] = token

    def fetch_merge_requests(self, state="opened", per_page=20) -> list[dict]:
        all_mrs: list[dict] = []
        for pid in self.project_ids:
            encoded = urlquote(str(pid), safe="")
            url = f"{self.base_url}/api/v4/projects/{encoded}/merge_requests"
            params = {
                "state": state,
                "per_page": per_page,
                "order_by": "updated_at",
                "sort": "desc",
            }
            try:
                resp = self.session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                all_mrs.extend(resp.json())
            except Exception as e:
                print(f"GitLab API error (project {pid}): {e}", file=sys.stderr)
        # Sort all by updated_at desc, keep top per_page
        all_mrs.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return all_mrs[:per_page]


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

STATE_LABELS = {
    "opened": "Open",
    "closed": "Closed",
    "merged": "Merged",
    "locked": "Locked",
}


class GitLabWatcherApp:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.client = GitLabClient(
            cfg["gitlab_url"], cfg["private_token"], cfg["project_ids"]
        )
        self.seen_iids: set[tuple] = set()  # (project_id, iid)
        self.first_run = True
        self.mrs: list[dict] = []

        Notify.init("GitLab Watcher")

        self.indicator = AppIndicator3.Indicator.new(
            "gitlab-watcher",
            "git-logo",
            AppIndicator3.IndicatorCategory.COMMUNICATIONS,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_attention_icon("mail-message-new")

        self._build_menu([])
        GLib.idle_add(self._poll)
        GLib.timeout_add_seconds(cfg["poll_interval"], self._poll)

    # -- Menu ---------------------------------------------------------------

    def _build_menu(self, mrs: list[dict]):
        menu = Gtk.Menu()

        if not mrs:
            item = Gtk.MenuItem(label="Aucune merge request")
            item.set_sensitive(False)
            menu.append(item)
        else:
            for mr in mrs:
                title = mr.get("title", "")
                if len(title) > 60:
                    title = title[:57] + "..."
                iid = mr.get("iid", "?")
                state = STATE_LABELS.get(mr.get("state", ""), mr.get("state", ""))
                author = mr.get("author", {}).get("name", "?")
                label = f"!{iid}  {title}  [{state}] — {author}"
                item = Gtk.MenuItem(label=label)
                url = mr.get("web_url", "")
                item.connect("activate", lambda w, u=url: webbrowser.open(u))
                menu.append(item)

        menu.append(Gtk.SeparatorMenuItem())

        refresh = Gtk.MenuItem(label="Rafraîchir")
        refresh.connect("activate", self._on_refresh)
        menu.append(refresh)

        mark_read = Gtk.MenuItem(label="Marquer comme lu")
        mark_read.connect("activate", self._mark_read)
        menu.append(mark_read)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quitter")
        quit_item.connect("activate", self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

    # -- Polling ------------------------------------------------------------

    def _poll(self) -> bool:
        mrs = self.client.fetch_merge_requests(
            state=self.cfg["mr_state"], per_page=self.cfg["per_page"]
        )
        self.mrs = mrs
        self._build_menu(mrs)

        current_keys = {(mr.get("project_id"), mr.get("iid")) for mr in mrs}
        new_keys = current_keys - self.seen_iids

        if not self.first_run and new_keys:
            new_mrs = [
                mr
                for mr in mrs
                if (mr.get("project_id"), mr.get("iid")) in new_keys
            ]
            self._send_notifications(new_mrs)
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ATTENTION)

        self.seen_iids = current_keys
        self.first_run = False
        return True  # keep the timeout

    # -- Notifications ------------------------------------------------------

    def _send_notifications(self, new_mrs: list[dict]):
        if len(new_mrs) > 3:
            n = Notify.Notification.new(
                "GitLab Watcher",
                f"{len(new_mrs)} nouvelles merge requests",
                "dialog-information",
            )
            n.show()
        else:
            for mr in new_mrs:
                title = f"Nouvelle MR !{mr.get('iid')}"
                body = f"{mr.get('title', '')}\npar {mr.get('author', {}).get('name', '?')}"
                n = Notify.Notification.new(title, body, "dialog-information")
                n.show()

    # -- Actions ------------------------------------------------------------

    def _on_refresh(self, _widget):
        self._poll()

    def _mark_read(self, _widget):
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    def _on_quit(self, _widget):
        Notify.uninit()
        Gtk.main_quit()

    def run(self):
        Gtk.main()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # allow Ctrl+C
    cfg = load_config()
    app = GitLabWatcherApp(cfg)
    app.run()
