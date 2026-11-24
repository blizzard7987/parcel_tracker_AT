# Paket Tracker AT – Fork

Fork von [SoerenKaiser99/parcel_tracker](https://github.com/SoerenKaiser99/parcel_tracker) (MIT). Ziel: Paketverfolgung für Österreich in Home Assistant, Pakete erscheinen von selbst aus dem Hauptpostfach, Live-Status direkt bei den Zustellern, keine Weiterleitung und kein Zwischendienst.

## Grundsätze

- Änderungen klein und in eigenen Dateien halten, damit Updates des Originals weiter eingespielt werden können.
- Die Integrations-Kennung bleibt `parcel_tracker` (Dienste, Entitäten, Speicher wie im Original). Deshalb nicht parallel zum Original installieren.
- Allgemein nützliche Teile (Österreichische Post, Nur-Lesen-Modus) können als Pull Request ans Original zurückgehen.

## Etappen

| Etappe | Inhalt | Stand |
|---|---|---|
| E0 | Fork-Identität: Name „Paket Tracker AT“, HACS-Installation direkt aus dem Repository (ohne Release-ZIP), Hinweise auf das Original | erledigt |
| E1 | Mail-Import „Hauptpostfach, nur lesen“: Ordner lesend öffnen, nichts markieren, nichts verschieben; Auswahl über Gmail-Suche bzw. Datum und Absender | erledigt |
| E2 | Österreichische Post: Live-Abfrage per Sendungsnummer, Post-Mails, Amazon-Pakete über die Post zuordnen | offen |
| E3 | DPD Österreich live, österreichische Shop-Mails | offen |
| E4 | Optional: Gmail per OAuth statt App-Passwort | offen |

## Hauptpostfach nur lesen (E1)

Einschalten unter Einstellungen → Geräte & Dienste → Paket Tracker → Konfigurieren → E-Mail-Import → „Hauptpostfach nur lesen“.

- Der Ordner wird mit EXAMINE geöffnet, Mails werden mit BODY.PEEK gelesen: nichts wird als gelesen markiert, verschoben, gelöscht oder angelegt. Das automatische Löschen ist in diesem Modus aus.
- Gelesen werden nur mögliche Versandmails. Der erste Abruf nach Installation oder Neustart ist eine Bestandsaufnahme der letzten 14 Tage (so alt darf eine Versandmail laut Original höchstens sein), danach jeweils die letzten 3 Tage. Bei Gmail sucht der Server selbst (`X-GM-RAW`, ohne Gesendet), bei anderen Servern über Datum plus Absender- und Betreff-Wörter (`mail/readonly.py`).
- Bereits gelesene Mails merkt sich die Integration bis zum Neustart (je UIDVALIDITY). Nach einem Neustart werden die Mails der letzten 14 Tage noch einmal abgerufen, aber nicht erneut verarbeitet: Die Message-IDs verarbeiteter Mails speichert die Integration dauerhaft.
- Für Gmail: IMAP-Server `imap.gmail.com`, Benutzer ist die Gmail-Adresse, Passwort ein App-Passwort.

## Upstream einspielen

```
git remote add upstream https://github.com/SoerenKaiser99/parcel_tracker
git fetch upstream
git merge upstream/main
```

Konflikte entstehen erwartbar nur in `manifest.json`, `hacs.json`, `README.md` und `tests/test_readme.py`.
