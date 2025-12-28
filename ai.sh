#!/bin/bash

FILES=(
  "ai.sh"
  "README"
  "prev.py"
  "edit.sh"
  "vercel.json"
  "requirements.txt"
  ".gitattributes"
)

TEMP_FILE=$(mktemp)
trap "rm -f '$TEMP_FILE'" EXIT

{
  for FILE_PATH in "${FILES[@]}"; do
    echo "$FILE_PATH:"
    cat "$FILE_PATH"
    echo "--- End of $FILE_PATH ---"
  done

  echo "Database schema:"
  sqlite3 knowledge.db '.schema'
  echo "--- End of DB schema ---"
} > "$TEMP_FILE"

cat "$TEMP_FILE" | pbcopy
echo "Success: Content of files copied to clipboard."
