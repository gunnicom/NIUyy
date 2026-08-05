import asyncio
from bleak import BleakClient, BleakScanner

CHAR_NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_WRITE_UUID  = "0000ffe2-0000-1000-8000-00805f9b34fb"

SLAVE = 0x01
KNOWN_PREFIX = "48:87:2D"

last_request = None
cell_count = None


# ---------------------------------------------------------------------------
# Modbus CRC16
# ---------------------------------------------------------------------------
def crc16_modbus(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, "little")


def build_read(start: int, count: int) -> bytes:
    frame = bytearray([SLAVE, 0x03])
    frame += start.to_bytes(2, "big")
    frame += count.to_bytes(2, "big")
    frame += crc16_modbus(frame)
    return bytes(frame)


# ---------------------------------------------------------------------------
# Decoder
# ---------------------------------------------------------------------------
def u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset+2], "little")


def u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset+4], "little")


def i32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset+4], "little", signed=True)


def decode_hw_type(data: bytes) -> str:
    text = data.split(b"\x00", 1)[0]
    return text.decode("ascii", errors="ignore").strip()


def decode_pack(data: bytes):
    reg75 = u16(data, 0)
    cells = reg75 & 0xFF

    voltage_v = u32(data, 2) / 1000.0
    current_a = i32(data, 6) / 100.0
    power_w = u16(data, 10)

    return cells, voltage_v, current_a, power_w


def decode_cells(data: bytes, count: int):
    values = []
    for i in range(count):
        mv = u16(data, i * 2)
        values.append(mv / 1000.0)
    return values


# ---------------------------------------------------------------------------
# Notification Handler
# ---------------------------------------------------------------------------
def handle_notify(sender, data: bytes):
    global last_request, cell_count
    
    print(f"[RX] {data.hex(' ')}")
    if len(data) < 5:
        return

    byte_count = data[2]
    payload = data[3:3+byte_count]

    try:
        if last_request == "hw":
            hw = decode_hw_type(payload)
            print("\n=== HARDWARETYP ===")
            print(f"hwTypeName : {hw}")
            return

        elif last_request == "pack":
            cells, voltage, current, power = decode_pack(payload)
            cell_count = cells

            direction = "Entladung" if current >= 0 else "Ladung"

            print("\n=== PACK ===")
            print(f"Zellen      : {cells}")
            print(f"Spannung    : {voltage:.3f} V")
            print(f"Strom       : {current:+.2f} A ({direction})")
            print(f"Leistung    : {power} W")
            return

        elif last_request == "cells":
            if cell_count is None:
                print("Zellzahl unbekannt")
                return

            cells = decode_cells(payload, cell_count)

            print("\n=== ZELLSPANNUNGEN ===")
            for i, v in enumerate(cells, start=1):
                print(f"Zelle {i:02d}: {v:.3f} V")

            print(f"Min         : {min(cells):.3f} V")
            print(f"Max         : {max(cells):.3f} V")
            print(f"Delta       : {(max(cells)-min(cells))*1000:.0f} mV")
            return

    except Exception as e:
        print(f"Fehler beim Dekodieren: {e}")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------
async def main():
    global last_request

    print("Suche nach BMS...")
    devices = await BleakScanner.discover(timeout=10)

    target = None
    for d in devices:
        name = d.name or ""
        print(f"Gefunden: {d.address}  {name}")

        if d.address.upper().startswith(KNOWN_PREFIX):
            target = d

    if not target:
        print("Kein passendes BMS gefunden.")
        return

    print(f"\nVerbinde mit {target.address} ({target.name})")

    async with BleakClient(target.address) as client:
        await client.start_notify(CHAR_NOTIFY_UUID, handle_notify)

        # Hardwaretyp lesen
        last_request = "hw"
        req = build_read(7, 13)
        print(f"[TX] {req.hex(' ')}")
        await client.write_gatt_char(CHAR_WRITE_UUID, req, response=True)
        await asyncio.sleep(1.0)

        while True:
            # Register 75-80 lesen
            last_request = "pack"
            req = build_read(75, 6)
            print(f"[TX] {req.hex(' ')}")
            await client.write_gatt_char(CHAR_WRITE_UUID, req, response=True)
            await asyncio.sleep(1.0)

            # Zellspannungen lesen (tatsächliche Zellzahl)
            if cell_count:
                last_request = "cells"
                req = build_read(81, cell_count)
                print(f"[TX] {req.hex(' ')}")
                await client.write_gatt_char(CHAR_WRITE_UUID, req, response=True)

            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
