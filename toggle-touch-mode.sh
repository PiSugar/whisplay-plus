#!/bin/bash
# Toggle labwc touch mode between mouse emulation and native touch
#
# Mouse emulation mode: touch acts as mouse (click & drag = select text)
# Native touch mode:    touch acts as touchscreen (swipe = scroll)
#
# Usage: ./toggle-touch-mode.sh [mouse|touch]
#   No argument: toggle between modes
#   mouse:       switch to mouse emulation mode
#   touch:       switch to native touch mode

set -e

LABWC_RC="$HOME/.config/labwc/rc.xml"
TOUCH_DEVICE="10-0038 generic ft5x06 (88)"
DSI_OUTPUT="DSI-1"

# Ensure labwc config directory exists
mkdir -p "$(dirname "$LABWC_RC")"

# Create config if it doesn't exist
if [ ! -f "$LABWC_RC" ]; then
    cat > "$LABWC_RC" << EOF
<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
        <touch deviceName="$TOUCH_DEVICE" mapToOutput="$DSI_OUTPUT" mouseEmulation="yes"/>
</openbox_config>
EOF
    echo "Created labwc config: $LABWC_RC"
fi

# Determine target mode
if [ -n "$1" ]; then
    case "$1" in
        mouse)  TARGET="yes" ;;
        touch)  TARGET="no"  ;;
        *)
            echo "Usage: $0 [mouse|touch]"
            echo "  mouse  - touch acts as mouse (click/drag to select text)"
            echo "  touch  - native touch mode (swipe to scroll)"
            echo "  (none) - toggle between modes"
            exit 1
            ;;
    esac
else
    # Toggle: read current value
    CURRENT=$(grep -oP 'mouseEmulation="\K[^"]+' "$LABWC_RC" 2>/dev/null || echo "yes")
    if [ "$CURRENT" = "yes" ]; then
        TARGET="no"
    else
        TARGET="yes"
    fi
fi

# Apply
sed -i "s/mouseEmulation=\"[^\"]*\"/mouseEmulation=\"$TARGET\"/" "$LABWC_RC"

# Reload labwc
if pgrep -x labwc > /dev/null 2>&1; then
    kill -HUP $(pgrep -x labwc) 2>/dev/null
fi

# Display result
if [ "$TARGET" = "yes" ]; then
    echo "Switched to MOUSE EMULATION mode (touch = click/drag)"
else
    echo "Switched to NATIVE TOUCH mode (swipe = scroll)"
fi
