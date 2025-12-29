#! /bin/bash

# Get the current day of the week (0=Sunday ... 6=Saturday)
DOW=$(date +%w)

# Calculate days until next Sunday.
# If DOW is 0 (Sun), offset is 0. If DOW is 1 (Mon), offset is 6.
OFFSET=$(( (7 - DOW) % 7 ))

# Use BSD date syntax (-v) to adjust the date by the calculated offset
TARGET_DATE=$(date -v+"$OFFSET"d +%Y-%m-%d)

echo "Opening weekly log for Sunday: $TARGET_DATE"

./edit.sh weblog "$TARGET_DATE"
