import os
import sys
import shutil
import select
from pathlib import Path
from contextlib import closing
from datetime import datetime, timezone
from email.utils import format_datetime

# Try to use pip-installed pysqlite3, e.g. on Linux with no system pysqlite;
# otherwise fall back to the standard library's sqlite3, e.g. on macOS.
try:
    __import__('pysqlite3')
    sys.modules['sqlite3'] = sys.modules.pop('pysqlite3')
except ImportError:
    pass
import sqlite3

# ========================= Meta Configuration =================================

DB_FILE = 'knowledge.db'
DIST_DIR = Path('dist')

# ISO 8601 date string for December 10, 2003 at 1:30 PM Rome time (CET, UTC+1)
AUTHOR_BIRTHDAY = "2003-12-10T13:30:00+01:00"

BASE_URL = "https://danielfalbo.com"

USAGE_STR = f'Usage: python3 {sys.argv[0]} [ --watch <table> <slug> ]'

KB14_URL = 'https://endtimes.dev/why-your-website-should-be-under-14kb-in-size'

# Tables for which we also generate RSS.
# Must have fields 'title', 'created_time', 'html'.
RSS_TABLES = {'weblog', 'bookmarks'}

# ==================== Relationships Context Configuration =====================

#
# The following relationships definitions will be used to include 'context' on
# entries pages, such as including the authors on the entry page of a bookmark.
#
# Format:
# 'this_table': [
#     ('junction_table', 'this_id_col', 'this_junction_id_col',
#       'other_table', 'other_id_col', 'other_junction_id_col'),
# ]
#
# Note: assumes 'other_tables' has a 'slug' column and gets compiled at
# /<table>/[slug].html
#
RELATIONSHIPS = {
    'bookmarks': [
        ('bookmark_authors', 'id', 'bookmark_id',
         'authors', 'id', 'author_id')
    ],
    'authors': [
        ('bookmark_authors', 'id', 'author_id',
         'bookmarks', 'id', 'bookmark_id')
    ]
}

# =============================== Utils ========================================

def die_with_honor(msg):
    """
    Prints the given 'msg' to stderr and exits with status code 1.
    """
    print(msg, file=sys.stderr)
    sys.exit(1)

def write_file(path, content):
    """
    Writes the given 'content' to file at given 'path'
    ensuring all parent dirs exits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    size_kb = path.stat().st_size / 1024
    print(f"[OK] Wrote {size_kb:.2f}kB to {path}")

    if size_kb >= 14.0:
        print(f"[WARN] {path} over 14kB")
        print(f'[WARN] See {KB14_URL}')

def get_db_tables(db):
    """
    Returns a set of user table names in the database.
    """

    q = """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        """
    return {row[0] for row in db.execute(q)}

def xml_escape(s):
    s = str(s)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s

# ======================== HTML Functional Components ==========================

# Usage: h('p', {'id': 'hello'}, 'Hello', h('span', {}, 'World'))
def h(tag, props, *children):
    attr_str = " ".join([f'{key}="{value}"' for key, value in props.items()])
    inner_html = "".join(children)
    return f"<{tag} {attr_str}>{inner_html}</{tag}>"

# ======================= HTML Templates and Components ========================

GLOBAL_CSS = """
:root { --default-font-size: 1rem; }

html { color-scheme: light dark; }

body {
  font-family:
    "SF Mono", "SFMono-Regular", ui-monospace, Menlo, Consolas,
    "Liberation Mono", monospace;

  max-width: 42rem;
  margin: 24px auto 24px auto;
  font-size: var(--default-font-size);
}

a { color: light-dark(black, white); }

a:hover { text-decoration: none; }

.section-title { padding-top: 2rem; font-weight: bold; }

.section-content { opacity: 0.8; font-size: 0.8rem }

.title-component {
    font-size: 3rem;
    font-weight: 700; margin: 0px;
    word-break: break-word;
    text-transform: uppercase;
}

.footer {
    margin-top: 3rem;
    padding-top: 1rem;
    border-top: 1px dashed;
    font-size: 0.8rem;
    opacity: 0.5;
    text-align: center;
}
"""

DOT = h('span', {}, ' · ')
NAVBAR = h('p', {},
    h('a', {'href': '/index.html'}, 'root'),
    # DOT, h('a', {'href': '/weblog/code.html'}, 'code'),
    # DOT, h('a', {'href': '/weblog/words.html'}, 'words')
)
FOOTER = h('p', {'class': 'footer'},
    "This entire website is built using 1 single file: ",
    h('a', {'href': 'https://github.com/danielfalbo/prev.py/blob/main/prev.py'},
        'prev.py'),
    "."
)

WAVING_HAND_CSS = """
@keyframes wave {
  0%   { transform: rotate( 0.0deg) }
  10%  { transform: rotate( 0.0deg) }
  20%  { transform: rotate( 0.0deg) }
  30%  { transform: rotate( 0.0deg) }
  40%  { transform: rotate( 0.0deg) }
  50%  { transform: rotate(14.0deg) }
  60%  { transform: rotate(-8.0deg) }
  70%  { transform: rotate(14.0deg) }
  80%  { transform: rotate(-4.0deg) }
  90%  { transform: rotate(10.0deg) }
  100% { transform: rotate( 0.0deg) }
}

#waving-hand {
  /* Set the pivot point to the bottom-left corner (the wrist) */
  transform-origin: 70% 70%;

  /* Ensure the hand is treated as a block for the transform to work well */
  display: inline-block;

  /* Apply the animation: name | duration | timing | repetition */
  animation: wave 2.5s ease-in-out 1;
}
"""

LIVE_AGE_JS = """
const LiveAge = document.getElementById("live-age");

// Average length of a Gregorian year in milliseconds
const MS_PER_YEAR = 365.2425 * 24 * 60 * 60 * 1000;

// December 10, 2003 at 1:30 PM Rome time (CET, UTC+1)
const BIRTH_MS = new Date("2003-12-10T13:30:00+01:00").getTime();

// Get unix timestamp milliseconds for current datetime
const startTimestampMs = Date.now();

const tick = () => {
  const msSincePageStartedLoading = performance.now();
  const nowMs = startTimestampMs + msSincePageStartedLoading;
  const age = (nowMs - BIRTH_MS) / MS_PER_YEAR;

  LiveAge.textContent = age.toFixed(9);

  requestAnimationFrame(tick);
}

tick();
"""

def layout(title, css, body_content_list):
    return "<!doctype html>" + h('html', {},

        h('head', {},
            h('title', {}, f'{title} | danielfalbo'),
            h('style', {}, css),
            '<meta charset="UTF-8">',
            '<link rel="icon" type="image/x-icon" href="/favicon.ico">',
        ),

        h('body', {}, "".join([NAVBAR, *body_content_list, FOOTER]))
    )

def index(css, more_html_content):
    return layout("Home", css, [
        h('p', {'style': 'font-size: 3rem; font-weight: 700; margin: 0px'},
            "Hi, I'm Daniel ",
            h('span', {'id': 'waving-hand'}, '👋')
        ),
        h('p', {}, "🇬🇧 Software Engineer at ",
            h('a', {'href': 'https://wikipedia.org/wiki/Palantir_Technologies'},
                'PLTR'),
            " London"
        ),
        h('p', {},
            "🧮 Studying ",
            h('a', {'href': './weblog/learning-library.html'}, "Computers & AI")
        ),
        h('p', {},
            "🎂 ",
            h('span', {'id': 'live-age'}, ""),
            " years old"
        ),
        h('p', {}, "🕺 Dancer"),
        h('code', {}, ":wq↵"),

        more_html_content,

        h('script', {}, LIVE_AGE_JS),
        h('style', {}, WAVING_HAND_CSS)
    ])

def title_component(title_str):
    return h('p', {'class': "title-component"}, title_str)

def table_index_page(css, table, html):
    return layout(table, css, [
        title_component(table),
        html
    ])

def author_page(css, entry):
    return layout(entry['name'], css, "".join([
        title_component(entry['name']),
        entry['context'],
    ]))

def entry_page(css, entry):
    return layout(entry['title'], css, "".join([
        title_component(entry['title']),
        entry['context'],
        entry['html']
    ]))

TABLE_TO_BUILDER = {
    'authors': author_page,
    'weblog': entry_page,
    'bookmarks': entry_page,
}

NOT_FOUND_PAGE = """
<style>html { color-scheme: light dark; }</style>
<pre>404</pre>
<pre>go <a href='/'>home</></pre>
"""

def dateage_js(created_time):
    return " ".join([
        f"""
<script>
    const createdStr = "{created_time}";
    const birthStr = "{AUTHOR_BIRTHDAY}";
        """,

        """
const container = document.getElementById("dateage");

const formatDate = (dateString, birthString) => {
    const MS_PER_YEAR = 1000 * 60 * 60 * 24 * 365.2425;

    const targetDate = new Date(dateString);
    const birthDate = new Date(birthString);
    const now = new Date();

    // Calculate time difference relative to now (client-side)
    const timeDifference = Math.abs(now.getTime() - targetDate.getTime());
    const daysAgo = Math.floor(timeDifference / (1000 * 60 * 60 * 24));

    // Calculate age at the created_time of the entry
    const ageAtPostTime = (
        (targetDate.getTime() - birthDate.getTime()) / MS_PER_YEAR
    );
    const ageSuffix = ageAtPostTime >= 0
      ? `, ${ageAtPostTime.toFixed(2)} y.o.`
      : '';

    // Format the display date (e.g. "December 15, 2022")
    const fullDate = targetDate.toLocaleString('en-us', {
      month: 'long', day: 'numeric', year: 'numeric'
    });

    // Return the string based on "Ago" buckets
    if (daysAgo < 1) {
      return 'Today';
    } else if (daysAgo < 7) {
      return `${fullDate} (${daysAgo}d ago${ageSuffix})`;
    } else if (daysAgo < 30) {
      const weeksAgo = Math.floor(daysAgo / 7);
      return `${fullDate} (${weeksAgo}w ago${ageSuffix})`;
    } else if (daysAgo < 365) {
      const monthsAgo = Math.floor(daysAgo / 30);
      return `${fullDate} (${monthsAgo}mo ago${ageSuffix})`;
    } else {
      const yearsAgo = Math.floor(daysAgo / 365);
      return `${fullDate} (${yearsAgo}y ago${ageSuffix})`;
    }
};

// Render
container.innerText = `written on ${formatDate(createdStr, birthStr)}`
</script>
        """
    ])

# =========================== Site Generation ==================================

def gen_tmpl_values(db, table):
    """
    Returns a dict from slug to a key-value object with the values to
    be used as arguments when rendering pages via builder functions.
    """

    # Fetch entries from the given table
    rows = db.execute(f'SELECT * FROM {table}').fetchall()

    # Construct a map from slug to key-value replacements
    tmpl_values_by_slug = {r['slug']: {**dict(r), 'context': ''} for r in rows}

    # Append DATEAGE JS <script> to 'html' content
    for slug in tmpl_values_by_slug:
        html = tmpl_values_by_slug[slug].get('html', None)
        created_time = tmpl_values_by_slug[slug].get('created_time', None)
        if html is None or created_time is None: continue

        tmpl_values_by_slug[slug]['html'] += dateage_js(created_time)

    # Construct the 'context' value to contain hyperlinks to all pages related
    # to the one with the given slug, based on the defined 'RELATIONSHIPS'.
    if table not in RELATIONSHIPS:
        return tmpl_values_by_slug

    a = table
    for j, a_id, j_a_id, b, b_id, j_b_id in RELATIONSHIPS[table]:
        # Query the database for the given relationship
        q = f"""
            SELECT a.slug as a_slug, b.slug as b_slug
            FROM {b} b
            JOIN {j} j ON b.id = j.{j_b_id}
            JOIN {a} a ON a.id = j.{j_a_id}
        """
        relations_map = {}
        for rel in db.execute(q):
            src_slug = rel['a_slug']
            if src_slug not in relations_map: relations_map[src_slug] = []
            relations_map[src_slug].append(rel['b_slug'])

        # Add links to the results at `tmpl_values_by_slug[row_slug]['context']`
        for row_slug, rel_slugs in relations_map.items():
            if row_slug in tmpl_values_by_slug:
                links = "".join([
                    h('p', {},
                        h('a', {'href': f'../{b}/{s}.html'}, f'/{b}/{s}')
                    )
                    for s in rel_slugs
                ])

                tmpl_values_by_slug[row_slug]['context'] += links

    return tmpl_values_by_slug

def generate_section(db, css, table, builder):
    """
    Generate html page for 'table' index at 'DIST_DIR/<table>.html' and page for
    all entries of given 'table' within 'db' at 'DIST_DIR/<table>/[slug].html'.
    """
    values = gen_tmpl_values(db, table)
    index_content_html = ''

    for slug, row in values.items():
        # Generate entry page
        html = builder(css, values[slug])
        path = DIST_DIR / table / f"{slug}.html"
        write_file(path, html)

        # Append page href row to table index html accumulator,
        # unless marked as unlisted on db.
        if row.get('listed', 1):
            # try to get title, fallback on name, fallback on slug
            label = row.get('title', row.get('name', slug))

            index_content_html += (f'''<p>
                <a href="./{table}/{slug}.html">{label}</a>
            </p>''')

    # Generate table index
    index_path = DIST_DIR / f"{table}.html"
    index_html = table_index_page(css, table, index_content_html)
    write_file(index_path, index_html)

def generate_rss(db, table):
    """
    Generates the RSS 2.0 XML feed for the given table at 'dist/<table>/rss'.
    """
    title_col = 'title'

    # Fetch all entries ordered by creation date (newest first)
    q = f"SELECT slug, {title_col}, created_time, html FROM {table} ORDER BY created_time DESC"
    rows = db.execute(q).fetchall()

    items_xml = ""
    for r in rows:
        link = f"{BASE_URL}/{table}/{r['slug']}"

        # XML Escape the title
        title = xml_escape(r[title_col])

        # Convert SQLite date string to RSS 2.0 RFC 822 format.
        #
        # SQLite assumes UTC and formats as YYYY-MM-DD HH:MM:SS
        # but some dates may have been updated manually to be just YYYY-MM-DD.
        #
        # Run
        #   'SELECT created_time FROM weblog' or
        #   'SELECT created_time FROM weblog'
        # to see what does the DB look like in practice.
        dt = r['created_time'].split(' ')[0]
        dt = datetime.strptime(dt, "%Y-%m-%d")
        dt = dt.replace(tzinfo=timezone.utc)
        dt = format_datetime(dt)

        items_xml += f"""<item>
          <title>{title}</title>
          <guid>{link}</guid>
          <link>{link}</link>
          <pubDate>{dt}</pubDate>
          <description>
            <![CDATA[ {r['html']} ]]>
          </description>
        </item>"""

        rss_content = f"""<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
          <channel>
            <title>{table} | danielfalbo</title>
            <link>{BASE_URL}/{table}</link>
            <description>Latest entries from {table}</description>
            {items_xml}
          </channel>
        </rss>"""

    write_file(DIST_DIR / table / "rss", rss_content)

def generate_all(db):
    # Clean output dir
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir()

    # Copy favicon
    shutil.copy("favicon.ico", DIST_DIR / 'favicon.ico')
    print(f"[OK] Copied favicon.ico to {DIST_DIR / 'favicon.ico'}")

    css = GLOBAL_CSS

    tables = get_db_tables(db)

    # write index.html from index() function with blocks from db
    INDEX_PATH = DIST_DIR / f'index.html'
    db_blocks = [
        db.execute(f"SELECT html FROM weblog WHERE slug='{slug}'").fetchone()
        for slug in ['words', 'code']
    ]
    db_blocks_html = " ".join([row['html'] for row in db_blocks])
    write_file(INDEX_PATH, index(css, db_blocks_html))

    # Write 404.html page
    INDEX_PATH = DIST_DIR / f'404.html'
    write_file(INDEX_PATH, NOT_FOUND_PAGE)

    # For each available table entry page builder, find its database table and
    # generate a section with one html page per entry, replacing any placeholder
    # value from the template with its value on the given entry row.
    for table, builder in TABLE_TO_BUILDER.items():
        assert table in tables
        generate_section(db, css, table, builder)

    # Generate RSS feeds for RSS_TABLES.
    for table in RSS_TABLES:
        generate_rss(db, table)

# =========================== Live Watch Mode ==================================

def watch_buffer(table, slug):
    buf_file = f'buf_{table}_{slug}.html'

    # Get entry title from database given 'table' and 'slug'.
    with closing(sqlite3.connect(DB_FILE)) as db:
        db.row_factory = sqlite3.Row # Access columns by name
        cursor = db.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE slug='{slug}'")
        row = cursor.fetchone()

    if not row:
        die_with_honor(f"Slug '{slug}' not found in table '{table}'")

    print(f"Watching {buf_file} as html content of {table}/{slug}...")

    fd = os.open(buf_file, os.O_RDONLY | os.O_CREAT, 0o644)
    kq = select.kqueue()
    kevent = select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                           flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                           fflags=select.KQ_NOTE_WRITE)

    # Register filter, wait for 0 events just yet
    kq.control([kevent], 0)

    builder = TABLE_TO_BUILDER[table]
    css = GLOBAL_CSS

    try:
        while True:
            kq.control(None, 1) # Wait for 1 event
            print('>> Change detected, rebuilding...')

            # Read html buffer size
            size = os.lseek(fd, 0, os.SEEK_END)

            # Rewind and read full file
            os.lseek(fd, 0, os.SEEK_SET)
            html = os.read(fd, size).decode('utf-8')

            # Inject dateage js
            if 'created_time' in row:
                html += dateage_js(row['created_time'])

            # Generate just this file
            content = builder(css, {**row, 'html': html, 'context': ''})

            out_path = DIST_DIR / table / f'{slug}.html'
            write_file(out_path, content)

    except KeyboardInterrupt:
        print("\nStopping...")
        os.close(fd)

# ================================ Main ========================================

def main():
    if len(sys.argv) == 1:
        with closing(sqlite3.connect(DB_FILE)) as db:
            db.row_factory = sqlite3.Row # Access columns by name
            generate_all(db)
    elif len(sys.argv) == 4 and sys.argv[1] == '--watch':
        table, slug = sys.argv[2], sys.argv[3]
        watch_buffer(table, slug)
    else:
        die_with_honor(USAGE_STR)

if __name__ == '__main__':
    main()
