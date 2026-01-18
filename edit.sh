#! /bin/bash

set -e

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <table> <slug>"
    exit 1
fi

TABLE="$1"
SLUG="$2"
DB="knowledge.db"
BUF="buf_${TABLE}_${SLUG}.html"

# ==========================================
# Create Entry on DB if Missing.
# ==========================================

# Check if entry already exists.
EXISTS=$(sqlite3 $DB "SELECT count(*) FROM \"$TABLE\" WHERE slug='$SLUG';")

if [ "$TABLE" == "authors" ] && [ "$EXISTS" -ne "0" ]; then
  # If it's an author and already exists, there's nothing to do.
  exit 0
fi

# For existing bookmarks, offer to append authors.
if [ "$TABLE" == "bookmarks" ] && [ "$EXISTS" -ne "0" ]; then
  read -p "Append new authors? [y/N]: " APPEND_AUTHORS
  if [[ "$APPEND_AUTHORS" =~ ^[Yy]$ ]]; then
    echo "Available Authors:"
    sqlite3 -header -column $DB "SELECT id, name FROM authors;"
    read -p "Enter Author ID(s) (comma separated): " AUTHORS_INPUT

    BM_ID=$(sqlite3 $DB "SELECT id FROM bookmarks WHERE slug='$SLUG';")
    IFS=',' read -ra ADDR <<< "$AUTHORS_INPUT"
    for AUTH_ID in "${ADDR[@]}"; do
      AUTH_ID=$(echo "$AUTH_ID" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
      if [ -z "$AUTH_ID" ]; then continue; fi
      sqlite3 $DB "INSERT INTO bookmark_authors (bookmark_id, author_id) VALUES ($BM_ID, $AUTH_ID);"
    done
  fi
fi

if [ "$EXISTS" -eq "0" ]; then
  echo "Entry '$SLUG' not found in '$TABLE'. Creating new..."

  # Handle Schema differences: 'authors' table uses 'name', others use 'title'.
  if [ "$TABLE" == "authors" ]; then
    read -p "Enter Name: " DISPLAY_VAL

    # Escape quotes
    DISPLAY_VAL="${DISPLAY_VAL//\'/''}"

    # Insert author onto db.
    sqlite3 $DB "INSERT INTO \"$TABLE\" (slug, name) VALUES ('$SLUG', '$DISPLAY_VAL');"

    exit 0 # There is nothing to edit in vim for authors.

  elif [ "$TABLE" == "snippets" ]; then
    # Ask for the text content
    read -p "Enter Snippet Text: " DISPLAY_VAL

    # Escape quotes
    DISPLAY_VAL="${DISPLAY_VAL//\'/''}"

    # List authors to get an ID
    echo "Available Authors:"
    sqlite3 -header -column $DB "SELECT id, name FROM authors;"
    read -p "Enter Author ID: " AUTH_ID

    # Insert new quote
    sqlite3 $DB "INSERT INTO \"$TABLE\" (slug, txt, author_id) VALUES ('$SLUG', '$DISPLAY_VAL', $AUTH_ID);"
    exit 0 # Nothing to edit in vim for snippets.

  elif [ "$TABLE" == "bookmarks" ]; then
    read -p "Enter Title: " DISPLAY_VAL

    # Escape quotes
    DISPLAY_VAL="${DISPLAY_VAL//\'/''}"

    # List authors to get an ID
    echo "Available Authors:"
    sqlite3 -header -column $DB "SELECT id, name FROM authors;"
    read -p "Enter Author ID(s) (comma separated): " AUTHORS_INPUT

    # Insert the new row with empty HTML.
    sqlite3 $DB "INSERT INTO \"$TABLE\" (slug, title, html) VALUES ('$SLUG', '$DISPLAY_VAL', '');"

    # Get the bookmark ID
    BM_ID=$(sqlite3 $DB "SELECT id FROM bookmarks WHERE slug='$SLUG';")

    # Process authors
    IFS=',' read -ra ADDR <<< "$AUTHORS_INPUT"
    for AUTH_ID in "${ADDR[@]}"; do
        # trim whitespace
        AUTH_ID=$(echo "$AUTH_ID" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
        if [ -z "$AUTH_ID" ]; then continue; fi

        # Link author to bookmark
        sqlite3 $DB "INSERT INTO bookmark_authors (bookmark_id, author_id) VALUES ($BM_ID, $AUTH_ID);"
    done

  elif [ "$TABLE" == "weblog" ]; then
    read -p "Enter Title: " DISPLAY_VAL

    # Escape quotes
    DISPLAY_VAL="${DISPLAY_VAL//\'/''}"

    CREATED_TIME=""
    LISTED="0"
    read -p "Override created_time? [y/N]: " OV_TIME
    if [[ "$OV_TIME" =~ ^[Yy]$ ]]; then
      read -p "Enter created_time (YYYY-MM-DD HH:MM:SS): " CREATED_TIME
    fi
    read -p "Listed? [y/N]: " OV_LISTED
    if [[ "$OV_LISTED" =~ ^[Yy]$ ]]; then
      LISTED="1"
    fi

    # Insert the new row with default HTML.
    DEFAULT_HTML='<span style="opacity: 0.8; font-size: 0.8rem;" id="dateage"></span>'
    if [ -n "$CREATED_TIME" ]; then
      sqlite3 $DB "INSERT INTO weblog (slug, title, html, created_time, listed) VALUES ('$SLUG', '$DISPLAY_VAL', '$DEFAULT_HTML', '$CREATED_TIME', $LISTED);"
    else
      sqlite3 $DB "INSERT INTO weblog (slug, title, html, listed) VALUES ('$SLUG', '$DISPLAY_VAL', '$DEFAULT_HTML', $LISTED);"
    fi

  else
    echo "Unknown table: $TABLE"
    exit 1
  fi
fi

# ==========================================
# Fault-tolerant Buffer File Management.
# ==========================================

# Check if buf.html exists and is not empty.
if [ -s "$BUF" ]; then
  echo "----------------------------------------------------"
  echo "WARNING: '$BUF' is not empty!"
  echo "This might contain unsaved work from a previous session."
  echo "----------------------------------------------------"
  read -p "Load from local buffer instead of DB? [Y/n]: " CHOICE

  # If choice is empty (default) or matches Y/y, load unsaved work;
  # else, load the html content from DB.
  if [[ -z "$CHOICE" || "$CHOICE" =~ ^[Yy]$ ]]; then
    echo "Using local '$BUF' content..."
  else
    echo "Overwriting '$BUF' with fresh content from DB..."
    sqlite3 $DB "SELECT html FROM \"$TABLE\" WHERE slug='$SLUG';" > "$BUF"
  fi
else
  # File is empty or missing, safe to pull from DB
  sqlite3 $DB "SELECT html FROM \"$TABLE\" WHERE slug='$SLUG';" > "$BUF"
fi

# ==========================================
#  Start watcher for live preview.
# ==========================================

# Start prev.py in watch mode in the background.
# We redirect stdout/stderr to /dev/null to prevent text from
# messing up the vim interface when changes are detected.
echo "Starting live preview at dist/$TABLE/$SLUG.html ..."
python3 prev.py --watch "$TABLE" "$SLUG" > /dev/null 2>&1 &
WATCH_PID=$!

# Register a trap to kill the watcher process when this script exits.
trap "kill $WATCH_PID 2>/dev/null" EXIT

# ==========================================
#  Editing.
# ==========================================

# Open vim for editing of the html content.
#
# 'backupcopy=yes' writes the actual original file at every ':w'
# so '--watch' mode can directly listen for changes on that file
# for the live preview feature (see README).
vi -c "set backupcopy=yes" "$BUF"

# ==========================================
# Save and Cleanup.
# ==========================================

# Attempt to write updated html content back to DB.
# Note: 'set -e' ensures script exits here if sqlite3 fails.
sqlite3 $DB "UPDATE \"$TABLE\" SET html=CAST(readfile('$BUF') as TEXT) WHERE slug='$SLUG';"

# If we reached this line, the DB update was successful.
# Clean the buffer so the next run pulls fresh from DB.
rm "$BUF"

echo "Database updated and buffer cleaned."

# ==========================================
# Final Rebuild.
# ==========================================

# Rebuild site
python3 prev.py
