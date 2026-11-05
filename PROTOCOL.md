# Victron Smart (VE.Direct über BLE) – Bluetooth-Protokoll

Zusammengetragen aus dem Projekt `birdie1/victron`, den Standard-Registern von
VE.Direct und eigenen Tests am Gerät. Beschrieben sind hier nur technische Fakten (Register-
nummern, Opcodes, Frame-Aufbau, UUIDs) zur Interoperabilität; Hersteller-Texte
werden paraphrasiert, nicht wörtlich übernommen. Was durch einen Mitschnitt
belegt ist, ist als **bestätigt** markiert; der Rest ist **zu prüfen**.

## Überblick

Die Geräte sprechen über Bluetooth dasselbe **VE.Direct**-Protokoll wie über
Kabel: jeder Wert ist ein **VREG** (16-Bit-Registernummer), verpackt in **CBOR**
und über die Hersteller-GATT-Dienste übertragen. Es gibt zwei Wege:

| Weg | Richtung | Verschlüsselung | Zweck |
|---|---|---|---|
| Instant-Readout-Advertisement | nur lesen | AES-128-**CTR**, 16-Byte-Geräteschlüssel | laufende Messwerte |
| GATT „VE.Direct Smart" | lesen **und schreiben** | **BLE-Bond**, PIN als Passkey (Link verschlüsselt) | Steuern, alle Register |

## GATT-Dienste (bestätigt, aus birdie)

- `306b0001-b081-4037-83dc-e59fcc3cdfd0` – Vendor, Characteristics `…0002/0003/0004`
- `97580001-ddf1-48be-b73e-182664615d8e`
- `68c10001-b17f-4d3a-a290-34ad6499937c` – VE.Direct Smart

## Ausgang schalten (bestätigt)

Geschaltet wird über **VE_REG_MODE `0x0200`**:

| Wert | Bedeutung |
|---|---|
| 1 | Laden an |
| 2 | Entladen an |
| 3 | an |
| 4 | aus |

Also: Ausgang **aus** → `0x0200` = `4` schreiben; **an** → der Betriebswert des
Geräts (`3` beim BatteryProtect, `1` beim Orion – die Integration lernt ihn aus
dem gelesenen Modus). Geschrieben wird mit **Set-Opcode `0x06`**:
`[0x06, 0x00] + CBOR([vreg, value])`, z. B. `06 00 82 19 0200 41 04` für Aus.
Das deckt sich byte-genau mit einem mitgeschnittenen App-Write. Zum Kontrollieren
denselben VREG zurücklesen.

## Weitere Register

| dbus-Pfad | VREG | Richtung | Status |
|---|---|---|---|
| /Mode | 0x0200 | r/w | bestätigt |
| /State | 0x0201 | r | bestätigt (Standard-VE.Direct) |
| /DeviceOffReason | 0x0207 | r | bestätigt |
| /Settings/Relay/0/Mode | 0xEDD8 (RELAY_CONTROL) | r/w | zu prüfen |
| /Dc/0/Voltage, Ein-/Ausgang V/A/W | — | r | zu prüfen |
| /ErrorCode, /Uptime, /SmartBatteryProtect/InternalTemperature | — | r | zu prüfen |

Relais-Modi: 0 Alarm, 2 Manuell, 3 immer offen, 8 Laden. Relaiszustand 0/1.

Produkt-IDs Smart BatteryProtect: **0xA3B0–0xA3B3**.

## Advertisement-Format (zu prüfen am Gerät)

Siehe `custom_components/victron_bp/advertisement.py`. Victron-Hersteller-ID
`0x02E1`, Record-Typ `0x09`, AES-128-CTR mit dem 16-Byte-Geräteschlüssel. Nutzlast
LSB-first: device_state(8), output_state(8), error_code(8), alarm_reason(16),
warning_reason(16), input_voltage(16, signed, 0,01 V), output_voltage(16, 0,01 V),
off_reason(32).

## Zugang: BLE-Bonding mit dem PIN (bestätigt)

Der Zugang zum GATT-Kanal ist echtes **BLE-Bonding** mit verschlüsselter
Verbindung. Der **6-stellige Bluetooth-PIN** (Standard `000000`) ist der
Pairing-Passkey; am Gerät mit BlueZ bestätigt. Beim ersten Verbinden wird
gebondet (Schlüssel beidseitig gespeichert), danach reicht der gespeicherte
Schlüssel – der PIN wird nicht erneut gebraucht. Über diesen **verschlüsselten,
gebondeten Link** läuft das VE.Direct-/CBOR-Protokoll im **Klartext**.

Der 16-Byte-Geräteschlüssel betrifft nur die **Advertisement-Auslesung**,
nicht den gebondeten GATT-Stream.

Praktische Folge für Home Assistant: HA gibt von sich aus keinen Passkey ein.
Die Integration registriert deshalb einmalig einen eigenen BlueZ-Pairing-Agenten
(`custom_components/victron_bp/pairing.py`), ruft `Device1.Pair()` auf derselben
D-Bus-Verbindung auf (BlueZ fragt dann genau diesen Agenten nach dem Passkey) und
setzt `Trusted`. Das Bonding liegt auf dem **lokalen** Adapter; ein ESPHome-Proxy
kann nicht bonden.

## Registertabelle

`custom_components/victron_bp/catalog/vregs.json` enthält die VE.Direct-Register
als reine Protokoll-Fakten (id, name, type, scale, writable) – ohne
Beschreibungstexte. Zusammen mit `products.json` (Produkt-ID → Modell) ist das
die Grundlage des generischen, produkt-ID-gesteuerten Treibers (alle Geräte der
Smart-Familie, nicht nur BatteryProtect). Welche Register die Integration
tatsächlich liest und ihre Einheiten sind im Code deklariert.

Beispiel bestätigter BatteryProtect-Leseregister (Adresse, Typ, Skalierung):

| VREG | Name | Typ | /Skala | Einheit |
|---|---|---|---|---|
| 0x0201 | DEVICE_STATE | un8 | 1 | — |
| 0x0207 | DEVICE_OFF_REASON_2 | un32 | 1 | Bitfeld |
| 0xEDBB | DC_INPUT_VOLTAGE | un16 | 100 | V |
| 0xEDBD | DC_INPUT_CURRENT | un16 | 10 | A |
| 0xEDBC | DC_INPUT_POWER | un32 | 100 | W |
| 0xEDA9 | DC_OUTPUT_VOLTAGE | un16 | 100 | V |
| 0xEDAD | DC_OUTPUT_CURRENT | un16 | 10 | A |
| 0xEDDB | CHR_TEMPERATURE | sn16 | 100 | °C |
| 0x0200 | DEVICE_MODE | un8 | 1 | zum Schalten (Set-Opcode 0x06) |

## Lesen über GATT

Abonnieren eines Registers: Schreibe auf die Control-Charakteristik
`[0x05, 0x00] + CBOR([vreg])`, z. B. `05 00 81 19 ED BB`. Das deckt sich exakt
mit birdies beobachteten Frames. Danach streamt das Gerät die Werte als
Notifications. Vor dem Stream verlangt das Gerät Fluss­kontrolle auf der
`…0002`-Charakteristik (Chunk-Größe `FA 80 FF`, Credits `F9 …`), und der Stream
bricht ab, wenn er nicht per Keepalive (Credits + Ping alle paar Sekunden)
wachgehalten wird.

Jeder Wert-Frame ist **selbstbeschreibend** (am Gerät bestätigt):

```
08  ff  19 HH LL   TN   <data little-endian>
│   │   └─ CBOR-uint16 = VREG (19 ED BB -> 0xEDBB)
│   └───── Flag (03 bei BMV/SmartSolar, 00 bei Orion) – egal
└───────── 08 = var-len Wert (09 = fixed-len)
                    └─ TN: Low-Nibble = Datenlänge; dann `length` Bytes LE
```

`consumed = 6 + length`. Weil jeder Frame seine VREG mitführt, genügt EIN Parser
für alle Produkte; Typ und Skalierung kommen aus `vregs.json`.
