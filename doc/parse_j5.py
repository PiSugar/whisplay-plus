#!/usr/bin/env python3
"""Parse KiCad schematic to extract J5 DSI0 connector pin mapping."""
import re

sch_path = "/Users/jdaie/repositories/pisugar/2.8inch_DSI_Driver/doc/RP-008099-DD-1-CM5 IO Board, revision 2, KiCAD files./CM5_HighSpeed.kicad_sch"

with open(sch_path, 'r') as f:
    content = f.read()

# J5 pin y-coordinates (placed at 156.21, 50.8)
sym_x, sym_y = 156.21, 50.8
pin_positions = {}
for i in range(1, 23):
    y = round(sym_y + (-25.4 + (i-1) * 2.54), 2)
    pin_positions[y] = i

# Find all labels with their coordinates
# Format: (label "NAME" (at x y angle) ...)
label_pattern = re.compile(r'\(label\s+"([^"]+)"\s*\n\s*\(at\s+([\d.]+)\s+([\d.]+)\s+\d+\)')
labels = label_pattern.findall(content)

# Find hierarchical labels
hlabel_pattern = re.compile(r'\(hierarchical_label\s+"([^"]+)"\s*\n\s*\(shape\s+\w+\)\s*\n\s*\(at\s+([\d.]+)\s+([\d.]+)\s+\d+\)')
hlabels = hlabel_pattern.findall(content)

# Find GND power symbols
gnd_pattern = re.compile(r'\(symbol\s*\n\s*\(lib_id\s+"power:GND"\)\s*\n\s*\(at\s+([\d.]+)\s+([\d.]+)\s+\d+\)')
gnds = gnd_pattern.findall(content)

# Find all wires
wire_pattern = re.compile(r'\(wire\s*\n\s*\(pts\s*\n\s*\(xy\s+([\d.]+)\s+([\d.]+)\)\s+\(xy\s+([\d.]+)\s+([\d.]+)\)')
wires = wire_pattern.findall(content)

print("=== Labels near J5 area (x=120-160, y=24-80) ===")
j5_labels = {}
for name, x, y in labels:
    x, y = float(x), float(y)
    if 120 <= x <= 160 and 24 <= y <= 80:
        pin = pin_positions.get(round(y, 2), '?')
        j5_labels[round(y, 2)] = name
        print(f"  Label '{name}' at ({x}, {y}) -> Pin {pin}")

print("\n=== Hierarchical labels near J5 area ===")
for name, x, y in hlabels:
    x, y = float(x), float(y)
    if 120 <= x <= 160 and 24 <= y <= 80:
        pin = pin_positions.get(round(y, 2), '?')
        j5_labels[round(y, 2)] = name
        print(f"  HLabel '{name}' at ({x}, {y}) -> Pin {pin}")

print("\n=== GND symbols near J5 area ===")
for x, y in gnds:
    x, y = float(x), float(y)
    if 120 <= x <= 160 and 24 <= y <= 85:
        print(f"  GND at ({x}, {y})")

print("\n=== Wires near J5 (x range 120-157) ===")
j5_wire_info = []
for x1, y1, x2, y2 in wires:
    x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
    if (120 <= x1 <= 157 or 120 <= x2 <= 157) and (24 <= y1 <= 82 or 24 <= y2 <= 82):
        j5_wire_info.append((x1, y1, x2, y2))

j5_wire_info.sort(key=lambda w: min(w[1], w[3]))
for x1, y1, x2, y2 in j5_wire_info:
    p1 = pin_positions.get(round(y1, 2), '')
    p2 = pin_positions.get(round(y2, 2), '')
    pin_info = f"Pin {p1}" if p1 else (f"Pin {p2}" if p2 else "")
    print(f"  Wire ({x1},{y1}) -> ({x2},{y2}) {pin_info}")

print("\n\n=== COMPLETE J5 PIN MAPPING ===")
for pin_num in range(1, 23):
    y = round(sym_y + (-25.4 + (pin_num-1) * 2.54), 2)
    label = j5_labels.get(y, "GND (inferred)")
    print(f"  Pin {pin_num:2d}: {label}")
