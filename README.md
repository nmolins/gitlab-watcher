# GitLab Watcher

Indicateur system tray Ubuntu qui affiche les merge requests GitLab récentes, avec notifications desktop pour les nouvelles MR.

## Fonctionnalités

- Liste des MR dans un menu déroulant (titre, statut, auteur)
- Notification desktop à chaque nouvelle MR détectée
- Badge sur l'icône quand il y a du nouveau
- Clic sur une MR → ouvre dans le navigateur
- Support multi-projets
- Configuration intégrée (dialogue GTK)

## Prérequis

```bash
sudo apt install gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 python3-gi python3-yaml python3-requests
```

## Utilisation

```bash
python3 gitlab_watcher.py
```

Au premier lancement, un dialogue de configuration s'ouvre pour renseigner l'URL GitLab, le token et les IDs de projets.

Le token se crée dans GitLab : **Settings > Access Tokens**, scope `read_api`.

## Autostart

```bash
cp gitlab-watcher.desktop.example gitlab-watcher.desktop
# Éditer le chemin Exec= dans gitlab-watcher.desktop
cp gitlab-watcher.desktop ~/.config/autostart/
```
