#!/usr/bin/env python3
import re
from collections import defaultdict

sch_file = "RP-008099-DD-1-CM5 IO Board, revision 2, KiCAD files./CM5_HighSpeed.kicad_sch"

with open(sch_file, 'r') as f:
    content = f.read()

# Conn_01x22_Socket pin Y offsets (local coords)
y_vals = [25.4, 22.86, 20.32, 17.78, 15.24, 12.7, 10.16, 7.62, 5.08, 2.54,
          0, -2.54, -5.08, -7.62, -10.16, -12.7, -15.24, -17.78, -20.32, -22.86, -25.4, -27.94]
pin_y_offsets = {i+1: y for i, y in enumerate(y_vals)}

# J5 at (156.21, 50.8), J16 at (156.21, 121.92)
connectors = {
    'J5': (156.21, 50.8),
    'J16': (156.21, 121.92)
}
pin_x_local = -5.08

# Parse all labels
labels = []
for m in re.finditer(r'\(label\s+"([^"]+)"\s*\n\s*\(at\s+([\d.-]+)\s+([\d.-]+)', content):
    labels.append((m.group(1), float(m.group(2)), float(m.group(3))))

for m in re.finditer(r'\(hierarchical_label\s+"([^"]+)"\s*\n\s*\(shape\s+\w+\)\s*\n\s*\(at\s+([\d.-]+)\s+([\d.-]+)', content):
    labels.append(('H:'+m.group(1), float(m.group(2)), float(m.group(3))))

# Parse GND power symbols
for m in re.finditer(r'\(lib_id\s+"power:GND"\)\s*\n\s*\(at\s+([\d.-]+)\s+([\d.-]+)', content):
    labels.append(('GND', float(m.group(1)), float(m.group(2))))

# Parse no_connect markers too
for m in re.finditer(r'\(no_connect\s*\(at\s+([\d.-]+)\s+([\d.-]+)', content):
    labels.append(('NC', float(m.group(1)), float(m.group(2))))

# Parse all wire segments
wires = []
for m in re.finditer(r'\(wire\s+\(pts\s+\(xy\s+([\d.-]+)\s+([\d.-]+)\)\s+\(xy\s+([\d.-]+)\s+([\d.-]+)\)\s*\)', content, re.DOTALL):
    wires.append(((float(m.group(1)), float(m.group(2))), (float(m.group(3)), float(m.group(4)))))

print(f"Parsed {len(labels)} labels, {len(wires)} wires")

def pt_key(x, y):
    return (round(x*10)/10, round(y*10)/10)

# Build graph
graph = defaultdict(set)
for (x1, y1), (x2, y2) in wires:
    k1 = pt_key(x1, y1)
    k2 = pt_key(x2, y2)
    graph[k1].add(k2)
    graph[k2].add(k1)

# Build label lookup
label_at = defaultdict(list)
for name, x, y in labels:
    label_at[pt_key(x, y)].append(name)

def trace_net(start):
    """BFS through wire graph, collect all labels"""
    visited = set()
    queue = [start]
    visited.add(start)
    found = list(label_at.get(start, []))
    while queue:
        cur = queue.pop(0)
        for nb in graph.get(cur, set()):
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)
                found.extend(label_at.get(nb, []))
    return found

print("\n" + "="*70)
for ref, (cx, cy) in connectors.items():
    print(f"\n{ref} Pin Mapping (22-pin FPC DSI/CSI Connector)")
    print("-"*60)
    for pin in range(1, 23):
        ax = cx + pin_x_local
        ay = cy - pin_y_offsets[pin]
        pk = pt_key(ax, ay)
        nets = trace_net(pk)
        net_str = ', '.join(nets) if nets else '???'
        print(f"  Pin {pin:2d}  y={ay:7.2f}  ->  {net_str}")

# Also check what SCL1/SDA1 connect to and trace the DPHY1 jumper area
print("\n" + "="*70)
print("ANALYSIS: SCL0/SDA0 vs SCL1/SDA1 routing")
print("-"*60)

for search in ['SCL0', 'SDA0', 'SCL1', 'SDA1', 'SDA0', 'SCL0']:
    for name, x, y in labels:
        clean = name.replace('H:', '')
        if clean == search:
            pk = pt_key(x, y)
            nets = trace_net(pk)
            unique_nets = list(set(nets))
            print(f"  {name:15s} at ({x:8.2f}, {y:8.2f}) -> connected to: {unique_nets}")

# Find the "Jumpers to be fitted" text area and any components nearby
print("\n" + "="*70)
print("DPHY1 JUMPER AREA (near 'Jumpers to be fitted if DPHY1 is used')")
print("-"*60)

# Search for resistor/jumper components in the schematic
for m in re.finditer(r'\(property "Reference" "(R\d+|J\d+)"\s*\n\s*\(at\s+([\d.-]+)\s+([\d.-]+)', content):
    ref_name = m.group(1)
    rx, ry = float(m.group(2)), float(m.group(3))
    # Look at components in the DPHY1 area (around y=119-150, x=100-160)
    if 90 < ry < 160 and 90 < rx < 170:
        print(f"  Component {ref_name} at ({rx}, {ry})")

# Debug: Show all wires that touch the J16 pin 20 and 21 positions
print("\n" + "="*70)
print("DEBUG: Wires near J16 pins 17,20,21")
print("-"*60)
for pin_num in [17, 20, 21]:
    py = 121.92 - pin_y_offsets[pin_num]
    print(f"\n  J16 Pin {pin_num} at y={py:.2f}:")
    for (x1,y1),(x2,y2) in wires:
        if abs(y1 - py) < 0.1 or abs(y2 - py) < 0.1:
            print(f"    Wire: ({x1:.2f},{y1:.2f}) -> ({x2:.2f},{y2:.2f})")

# Find ALL symbols (components) placed in the J16 I2C area (y=135-155)
print("\n" + "="*70)
print("ALL symbol placements near J16 I2C area (y=130-155)")
print("-"*60)
for m in re.finditer(r'\(symbol\s+\(lib_id\s+"([^"]+)"\)\s+\(at\s+([\d.-]+)\s+([\d.-]+)', content):
    lib_id = m.group(1)
    sx, sy = float(m.group(2)), float(m.group(3))
    if 130 < sy < 155 and 90 < sx < 170:
        # Find the reference
        ref_match = re.search(r'\(property "Reference" "([^"]+)"', content[m.start():m.start()+2000])
        ref_str = ref_match.group(1) if ref_match else "?"
        val_match = re.search(r'\(property "Value" "([^"]+)"', content[m.start():m.start()+2000])
        val_str = val_match.group(1) if val_match else "?"
        print(f"  {ref_str} ({val_str}) [{lib_id}] at ({sx:.2f}, {sy:.2f})")

# More broadly: find ALL symbols near J16 (y=92-155)
print("\n" + "="*70)
print("ALL symbol placements near J16 (y=92-155, x=100-230)")
print("-"*60)
for m in re.finditer(r'\(symbol\s+\(lib_id\s+"([^"]+)"\)\s+\(at\s+([\d.-]+)\s+([\d.-]+)', content):
    lib_id = m.group(1)
    sx, sy = float(m.group(2)), float(m.group(3))
    if 92 < sy < 155 and 100 < sx < 230:
        ref_match = re.search(r'\(property "Reference" "([^"]+)"', content[m.start():m.start()+2000])
        ref_str = ref_match.group(1) if ref_match else "?"
        val_match = re.search(r'\(property "Value" "([^"]+)"', content[m.start():m.start()+2000])
        val_str = val_match.group(1) if val_match else "?"
        print(f"  {ref_str} ({val_str}) [{lib_id}] at ({sx:.2f}, {sy:.2f})")

# Check for no_connects in the J16 area
print("\n" + "="*70)
print("No-connects near J16")
print("-"*60)
for m in re.finditer(r'\(no_connect\s*\(at\s+([\d.-]+)\s+([\d.-]+)\)', content):
    nx, ny = float(m.group(1)), float(m.group(2))
    if 90 < ny < 155 and 100 < nx < 165:
        print(f"  No-connect at ({nx:.2f}, {ny:.2f})")

# Check wires connecting to pin x=151.13 in the J16 area
print("\n" + "="*70)
print("Wires ending at x=151.13 (J16 pin x) for y in J16 range")
print("-"*60)
for (x1,y1),(x2,y2) in wires:
    if (abs(x1 - 151.13) < 0.1 and 92 < y1 < 155) or (abs(x2 - 151.13) < 0.1 and 92 < y2 < 155):
        print(f"  Wire: ({x1:.2f},{y1:.2f}) -> ({x2:.2f},{y2:.2f})")
