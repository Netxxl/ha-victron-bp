# Victron BT Devices für Home Assistant

Eigene Integration, um **Victron-Geräte über Bluetooth** (VE.Direct Smart)
auszulesen **und ihren Ausgang zu schalten** – was die eingebaute BLE-Werbung
allein nicht kann.
Die Integration erkennt das Gerät an der **Produkt-ID** aus der Werbung und baut
die passenden Sensoren und den Schalter daraus auf, statt ein einzelnes Modell
hart zu verdrahten. Bestätigt an einem **Orion Smart 12/12-18** und einem
**Smart BatteryProtect**; weitere Geräte der Smart-Familie nutzen denselben
Transport.

Die Geräte sprechen BLE (nicht Classic); die PIN ist die 6-stellige
BLE-Pairing-PIN.

## Was sie kann

- **Lesen** (über GATT, nach einmaligem Koppeln): je nach Gerät z. B. Spannungen,
  Strom, Leistung, Temperatur, Ladezustand (Bulk / Absorption / Float) und Modus.
  Welche Register ein Gerät liefert, ergibt sich aus seiner Produkt-ID; nicht
  vorhandene Werte bleiben einfach leer.
- **Schalten** (über dieselbe GATT-Verbindung): Ausgang an/aus über das
  VE.Direct-Register `0x0200` (generisch: Aus = 4, Ein = der vom Gerät gemeldete
  Betriebswert, z. B. 3 beim BatteryProtect, 1 beim Orion).
- **Zweisprachig** (Deutsch/Englisch), Werte mit korrekten Einheiten und
  Nachkommastellen.

Optional lassen sich zusätzlich die laufenden Messwerte aus der passiven
„Instant Readout"-Werbung lesen; dafür wird der 16-Byte-Verschlüsselungsschlüssel
gebraucht. Für Lesen und Schalten über GATT reicht die PIN.

## Installation

**Über HACS (empfohlen):**

1. HACS → ⋮ (oben rechts) → **Benutzerdefinierte Repositories**.
2. Repository `https://github.com/Netxxl/ha-victron-bp`, Typ **Integration**,
   hinzufügen.
3. „Victron BT Devices" in HACS suchen, **Herunterladen**, Home Assistant neu
   starten.

**Manuell:** den Ordner `custom_components/victron_bp` aus dem
[neuesten Release](https://github.com/Netxxl/ha-victron-bp/releases) nach
`config/custom_components/` kopieren und Home Assistant neu starten.

Voraussetzung: Home Assistant 2025.1 oder neuer und ein **lokaler**
Bluetooth-Adapter (siehe „Einmal koppeln").

## Einrichten

*Einstellungen → Geräte & Dienste → Integration hinzufügen → „Victron BT Devices"*.
Abgefragt werden:

- **MAC-Adresse** (oft schon von Home Assistant erkannt),
- **PIN** (6 Ziffern, Standard `000000`),
- **Verschlüsselungsschlüssel** (32 Hex, optional; nur für die Werbungs-Auslesung;
  VictronConnect → Produktinfo → Instant Readout → Verschlüsselungsdaten).

Nichts davon steht im Code; alles wird erst beim Einrichten eingegeben.

## Einmal koppeln (PIN)

VE.Direct Smart sichert die GATT-Verbindung über ein **BLE-Bonding**: der
6-stellige PIN ist der Pairing-Passkey, die Verbindung ist verschlüsselt. Der
PIN wird **nur einmal** gebraucht – danach merkt sich BlueZ den Schlüssel und
das Gerät ist „trusted".

Da Home Assistant von sich aus keinen Passkey eingibt, macht das die Integration:
einmal den Button **„Mit PIN koppeln"** drücken. Sie registriert kurz einen
eigenen BlueZ-Pairing-Agenten, koppelt mit dem PIN und markiert das Gerät als
vertrauenswürdig; das Ergebnis erscheint als Benachrichtigung. Ab dann
verbindet sich die Integration ohne PIN.

Wichtig: Bonding liegt auf dem **lokalen** Bluetooth-Adapter von Home Assistant –
das Gerät muss in dessen Reichweite sein. **Ein ESPHome-Proxy kann nicht
bonden** und hilft hier nicht.

## Wie es funktioniert

Nach dem Koppeln läuft das VE.Direct-/CBOR-Protokoll im Klartext über den
verschlüsselten, gebondeten Link. Die Integration hält eine dauerhafte
GATT-Verbindung mit Keepalive, abonniert die Register des erkannten Geräts und
dekodiert den selbstbeschreibenden Datenstrom. Das Protokoll wurde ohne
Herstellerunterlagen aus frei verfügbaren Quellen (u. a. `birdie1/victron`)
nachvollzogen – Details in [PROTOCOL.md](PROTOCOL.md).

Debug-Log einschalten:

```yaml
logger:
  logs:
    custom_components.victron_bp: debug
```

## Tests

```
python tests/test_cbor.py
python tests/test_advertisement.py
```

## Hinweis

Dies ist ein privates, inoffizielles Projekt. Es steht **in keiner Verbindung
zu Victron Energy** und wird von Victron weder unterstützt noch geprüft oder
freigegeben. „Victron", „VictronConnect", „VE.Direct", „Smart BatteryProtect",
„Orion" und weitere Namen sind Marken ihrer jeweiligen Inhaber und werden hier
nur zur Beschreibung der Kompatibilität genannt.

Das Protokoll wurde ohne Herstellerunterlagen aus frei verfügbaren Quellen
nachvollzogen; mitgeliefert werden nur die dafür nötigen technischen Angaben
(Registernummern, Typen, Skalierung, Produkt-IDs), kein Text- oder Bildmaterial
des Herstellers. Nutzung auf **eigenes Risiko**: keine Gewähr für Richtigkeit
oder Eignung, keine Haftung für Schäden an Gerät, Anlage oder Daten. Prüfe vor
dem Einsatz an einer realen Anlage selbst, ob sich das Gerät wie erwartet verhält.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
