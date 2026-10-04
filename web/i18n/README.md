# Sprachen der Oberfläche

Die Texte im Quelltext sind deutsch und zugleich die Schlüssel der Dateien `<Sprache>.json` (`exact`: deutscher Text → Übersetzung).
`{1}`, `{2}` sind eingefügte Werte und müssen in der Übersetzung stehen. `web/i18n.js` übersetzt die Seite beim Laden.
Fehlt ein Text, gilt Englisch, dann Deutsch.

**Alle Übersetzungen außer Deutsch sind maschinell erstellt und nicht von Muttersprachlern geprüft.** Verbesserungen sind willkommen
(Issue oder Pull Request).

**Text verbessern:** in `web/i18n/<Sprache>.json` den Wert ändern, Schlüssel und `{1}` unverändert lassen.

**Neue Sprache:**
1. `python3 tools/i18n_extract.py --skeleton xx` legt `web/i18n/xx.json` mit allen Schlüsseln und leeren Werten an.
2. Werte übersetzen (leere bleiben englisch).
3. In `web/i18n/languages.json` eintragen: `code`, `name` (in der Sprache selbst), `label` (zwei bis drei Buchstaben).
4. In `web/i18n.js` unter `LOCALES` das Gebietsschema für Datum und Uhrzeit ergänzen (z. B. `sv: "sv-SE"`).
5. `python3 -m unittest tools.test_i18n` ausführen.

**Neuer deutscher Text im Quelltext:** `python3 tools/i18n_extract.py --keys` zeigt alle Schlüssel. Ganze Sätze mit `${…}` bauen, nicht aus
Bruchstücken zusammensetzen. Kurze Einzelwörter (z. B. „aus“, „verbunden“) stehen in `tools/i18n_extra_keys.json`.
