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
| E2 | Österreichische Post: Live-Abfrage per Sendungsnummer, Post-Mails, Amazon-Pakete über die Post zuordnen | erledigt |
| E3 | DPD Österreich live, österreichische Shop-Mails | erledigt |
| E4 | Optional: Gmail per OAuth statt App-Passwort | offen |

## Hauptpostfach nur lesen (E1)

Einschalten unter Einstellungen → Geräte & Dienste → Paket Tracker → Konfigurieren → E-Mail-Import → „Hauptpostfach nur lesen“.

- Der Ordner wird mit EXAMINE geöffnet, Mails werden mit BODY.PEEK gelesen: nichts wird als gelesen markiert, verschoben, gelöscht oder angelegt. Das automatische Löschen ist in diesem Modus aus.
- Gelesen werden nur mögliche Versandmails. Der erste Abruf nach Installation oder Neustart ist eine Bestandsaufnahme der letzten 14 Tage (so alt darf eine Versandmail laut Original höchstens sein), danach jeweils die letzten 3 Tage. Bei Gmail sucht der Server selbst (`X-GM-RAW`, ohne Gesendet), bei anderen Servern über Datum plus Absender- und Betreff-Wörter (`mail/readonly.py`).
- Bereits gelesene Mails merkt sich die Integration bis zum Neustart (je UIDVALIDITY). Nach einem Neustart werden die Mails der letzten 14 Tage noch einmal abgerufen, aber nicht erneut verarbeitet: Die Message-IDs verarbeiteter Mails speichert die Integration dauerhaft.
- Für Gmail: IMAP-Server `imap.gmail.com`, Benutzer ist die Gmail-Adresse, Passwort ein App-Passwort.

## Österreichische Post (E2)

- **Live-Status** über die öffentliche Sendungsverfolgung der Post (`api.post.at`, dieselbe Abfrage wie die Post-App). Gesendet wird nur die Sendungsnummer, kein Konto, keine PLZ (`carriers/post_at.py`; Abfrage und Status-Vokabular nach ha-parcel-integrations/ha-oesterreichische-post, MIT).
- **Nummern:** 22 oder 24 Ziffern sowie internationale Nummern auf `…AT` werden automatisch der Post zugeordnet. In der Karte und im Dienst `add_parcel` ist „Österreichische Post“ (`post_at`) wählbar.
- **Post-Mails** (`mail/post_at.py`): „in Verteilung“ (mit Zustelltag), „in Zustellung“ (heute), „So stellen wir … zu“ und „Ihr Paket ist da!“ (Abholstation, Abholfrist, Abholcode nur mit eingeschaltetem Zustellcode). Name, Kundennummer und Gewicht werden nicht gelesen. Andere Mails von post.at (Belege, Rechnungen, Paketmarken) werden ignoriert.
- **Amazon über die Post:** „Ihre AMAZON-Sendung …“ wird der offenen Amazon-Bestellung mit passendem Tag zugeordnet. Die Bestellung übernimmt die Post-Nummer und wird ab dann live abgefragt.

## DPD Österreich und österreichische Shops (E3)

- **DPD live für Österreich:** my.dpd.de leitet österreichische Pakete nur an mydpd.at weiter. Der Fork fragt dann die öffentliche Paketsuche von mydpd.at ab (`carriers/dpd_at.py`), ohne Konto und nur mit der Paketnummer. Mit Land „Österreich“ wird mydpd.at zuerst gefragt. Die Seite hat einen Bot-Schutz; wird die Abfrage abgewiesen, gilt DPD als „nicht erreichbar“ und der Status kommt weiter aus den DPD-Mails.
- **Post-Nummern in jeder Mail:** Links zur Post-Sendungsverfolgung, Nummern hinter „Paket:“/„Sendungsnummer“ und internationale Nummern auf `…AT`.
- **Shop-Mails** (`mail/at_shops.py`): dm, Shop Apotheke, apotheke.at, ABOUT YOU, asambeauty, Pharmeo, apol. Nummer (Post, DPD, DHL), Shopname als Paketname, Status aus dem Betreff. Retouren werden ignoriert.

## Upstream einspielen

```
git remote add upstream https://github.com/SoerenKaiser99/parcel_tracker
git fetch upstream
git merge upstream/main
```

Konflikte entstehen erwartbar nur in `manifest.json`, `hacs.json`, `README.md` und `tests/test_readme.py`.
