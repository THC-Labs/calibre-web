# THC Labs / Calibre-Web — Project Synthesis

> **Document Objective:** Provide a complete, self-contained architectural, structural, and operational description of the `calibre-web` project to a Large Language Model (LLM). This allows any downstream AI agent or engineering system to fully comprehend the system architecture, dual-database paradigm, module breakdown, technology stack, dependency graph, distribution protocols, and implementation status without access to the physical source files and **without embedding any source code**.
>
> **Synthesis Name:** `project_synthesis`  
> **Primary Repository:** `/home/carlos/THC/THCLabs/calibre-web`  
> **Project Upstream:** `janeczku/calibre-web` (Maintained by `@OzzieIsaacs`)  
> **Active Branch:** `master`  
> **Software License:** GNU General Public License v3.0 (GPLv3)  
> **Operational Status:** Production-Grade / Stable Ecosystem  

---

## 1. Executive Summary & Project Identity

`calibre-web` is an enterprise-grade, open-source web application designed to browse, read, organize, edit, and distribute digital e-books from an existing Calibre e-book database. It transforms a static, local Calibre library into a multi-user, multi-protocol cloud e-book platform.

Unlike Calibre's native content server or generic media servers, Calibre-Web employs a distinctive **Dual-Database Architectural Model**:
1. **Calibre Database (`metadata.db`):** An external SQLite database managed by Calibre, treated as the authoritative catalog of book metadata, authors, series, tags, publishers, and physical e-book files.
2. **Application Database (`app.db`):** An internal SQLite database isolated to Calibre-Web that manages user accounts, password hashes, role-based access control (RBAC), custom reading shelves, bookmarks, download histories, reading progress, and server configuration settings.

This separation guarantees that Calibre-Web never alters or corrupts Calibre's proprietary schema, enabling Calibre desktop applications and Calibre-Web to operate concurrently against the same physical library on disk or network storage.

Beyond web browsing, Calibre-Web serves as a multi-protocol content distribution hub:
- **Web Interface:** Responsive Bootstrap-based interface supporting 25+ languages, dark/light themes, and in-browser reading.
- **OPDS Catalog Feed:** Open Publication Distribution System XML feeds for mobile e-reader apps (e.g. Moon+ Reader, FBReader, KOReader).
- **Kobo Store API Emulation:** Direct wireless e-reader synchronization for physical Kobo hardware, synchronizing reading progress, collections, and e-books without cables.
- **Send-to-Kindle & E-Reader Delivery:** Asynchronous e-mail delivery pipeline via SMTP or Google Mail OAuth API.
- **On-the-Fly Conversion:** Asynchronous conversion of e-book formats via external Calibre binaries (`ebook-convert`) or `kepubify`.

---

## 2. Technology Choices & Stack Specification

Calibre-Web is built in Python, leveraging Flask and an asynchronous WSGI server layer, intentionally avoiding heavy full-stack frameworks like Django to retain modularity and lightweight embedded execution.

| Layer / Concern | Technology Selection | Version Constraints | Operational Role & Architectural Rationale |
|---|---|---|---|
| **Programming Language** | Python 3 | Python 3.8 through 3.14 (Python 2 dropped) | High portability across Linux, macOS, Windows, Docker, and NAS appliances (Synology, QNAP). |
| **Web Application Framework** | Flask | `>=1.0.2, <3.2.0` | Lightweight micro-framework orchestrating application routing, request lifecycles, and blueprint modularization. |
| **WSGI Server Layer** | Gevent or Tornado | Gevent `>=20.6.0` or Tornado `>=6.4.2` | Calibre-Web embeds its own production WSGI server. It dynamically selects Gevent if available; otherwise falls back to Tornado HTTPServer. |
| **ORM & Database Engine** | SQLAlchemy | `>=1.3.0, <2.1.0` (SQLite Backend) | Manages two distinct scoped sessions: one mapped to Calibre's `metadata.db` and another mapped to Calibre-Web's `app.db`. |
| **Authentication & Sessions** | Custom `cw_login` (Flask-Login fork) | Internal implementation | Handles session cookies, persistent remember-me tokens, anonymous browsing mode, and session hijacking prevention. |
| **Authorization & RBAC** | Flask-Principal & Bitmask Roles | `>=0.3.2, <0.5.1` | Granular per-user permissions stored as integer bitmasks (Admin, Download, Upload, Edit, Passwd, Shelf, Delete, Viewer). |
| **Security & SSRF Defense** | Advocate (`cw_advocate`) | Internal integration | Custom validating HTTP adapter that intercepts outgoing network requests, preventing Server-Side Request Forgery against private IP subnets. |
| **HTML Sanitization** | `nh3` | `>=0.3.0, <0.4.0` | Rust-based HTML sanitizer cleaning book descriptions, comments, and uploaded SVGs to eliminate XSS vectors. |
| **Secrets Encryption** | `cryptography` (Fernet) | `>=39.0.0, <48.0.0` | Symmetrically encrypts sensitive credentials (SMTP passwords, LDAP bind passwords) inside the SQLite settings table. |
| **Rate Limiting & CSRF** | Flask-Limiter & Flask-WTF | Limiter `<4.2.0`, WTF `<1.4.0` | Enforces request rate throttling on OPDS and Kobo endpoints; protects web mutations against CSRF attacks. |
| **Internationalization (i18n)** | Flask-Babel & Babel | `>=3.0.0, <4.1.0` | Provides runtime translation for 25+ languages, locale formatting of dates, times, and relative timestamps. |
| **Background Task Scheduling** | APScheduler | `>=3.6.3, <3.12.0` | Schedules recurring maintenance jobs, metadata backups, and thumbnail cleanup routines. |
| **Task Execution Engine** | Internal `WorkerThread` | Multi-threaded Python worker | Threaded background task queue for long-running operations (e-book conversion, thumbnail generation, email delivery). |
| **Metadata Extraction** | PyPDF, lxml, comicapi, Mutagen, rarfile | Various stable | Ingests metadata and covers from PDF, EPUB, MOBI/AZW, Comic Archives (CBR, CBZ, CBT, CB7), and Audiobooks. |
| **External E-Book Tools** | Calibre binaries, Kepubify, unrar | Optional external CLI | Optional binaries invoked via subprocess wrappers for format conversion and comic extraction. |

---

## 3. Core Architectural Paradigms & Design Patterns

### 3.1 The Dual-Database Isolation Pattern
The core architectural pillar of Calibre-Web is the strict separation between user management and library data:
- **The Library Database (`metadata.db` via `cps/db.py`):**
  - Represents Calibre's schema. Contains book records, authors, series, tags, ratings, publishers, languages, formats (`data` table), and comments.
  - Calibre-Web respects Calibre's schema invariants. When writing metadata or uploading new books, it mimics Calibre's naming conventions (e.g. `Author/Title (ID)/Title - Author.format`) and updates link tables accordingly.
  - Supports SQLite memory-mapping and custom SQLite collation functions (`title_sort`).
- **The User & Application Database (`app.db` via `cps/ub.py`):**
  - Manages Calibre-Web application state: user authentication, password hashes, individual reading progress, custom shelves, downloaded book logs, archived books, and server settings.
  - Crucially, user activity (such as bookmarking a page, marking a book as read, or organizing a custom shelf) never writes to Calibre's `metadata.db`.

### 3.2 Blueprint-Based Modular Monolith
Calibre-Web organizes its routing and presentation layers into discrete Flask Blueprints, dynamically registered during initialization:
- `web`: Primary book catalog browsing, book detail views, in-browser reading, and file downloading.
- `basic`: Authentication workflows (login, logout, registration, password resets, user profile updates).
- `admin`: System-wide settings, user administration, library path configuration, email/SMTP settings, and external tool configuration.
- `opds`: Atom-compliant OPDS catalog generation for external e-reader client applications.
- `kobo` & `kobo_auth`: Emulated Kobo Store API endpoints handling synchronization for Kobo hardware devices.
- `editbooks`: Book metadata editing, book uploads, format conversions, and deletions.
- `search` & `meta`: Advanced catalog searching, custom column filtering, and external metadata retrieval.
- `shelf`: Personal and public bookshelf creation and curation.
- `tasks`: Real-time monitoring and management of background workers.
- `remotelogin`: Magic link and QR-code mobile authentication.
- `gdrive`: Cloud-backed Calibre library streaming from Google Drive.
- `oauth`: OAuth 2.0 social login integration via Flask-Dance.

### 3.3 Dynamic Binary & Dependency Resilience
Calibre-Web is engineered to boot and run in minimal environments without failing if optional dependencies are absent:
- If `Flask-WTF` or `Flask-Limiter` are not installed, the application runs with degraded security wrappers rather than crashing.
- If external tools like `ebook-convert`, `kepubify`, or `unrar` are missing from the operating system PATH, conversion and RAR extraction features are automatically hidden from the UI while core reading and browsing remain functional.
- If optional cloud services (Google Drive, Gmail OAuth, LDAP) lack necessary libraries, their corresponding administration panels and blueprints gracefully deactivate.

### 3.4 In-Browser Reading Pipeline
Calibre-Web embeds customized open-source web readers directly into the interface:
- **EPUB & KEPUB:** Rendered via `epub.js`, supporting pagination, font sizing, themes, and bookmark persistence back to `app.db`.
- **PDF:** Rendered via `PDF.js` with full zoom, thumbnail navigation, and text search.
- **Comic Books (CBR/CBZ/CBT/CB7):** Rendered via an internal comic viewer reading uncompressed image archives.
- **Audiobooks (M4A/M4B/MP3/FLAC/OGG):** Streamed via an in-browser HTML5 audio player supporting playback speed adjustment and position tracking.
- **Plain Text & DJVU:** Rendered via specialized text and canvas viewers.

### 3.5 Asynchronous Worker Queue Pattern
Operations that exceed standard HTTP request timeouts are offloaded to an internal background worker system (`cps/services/worker.py` and `cps/tasks/`):
- When a user requests a format conversion or sends an e-book to an e-reader email address, Calibre-Web immediately generates a task entry in the worker queue and returns an HTTP response.
- Worker threads pull tasks sequentially or concurrently, updating task progress percentages and status messages in memory.
- The user can track conversion and delivery status in real-time via the Task Status dashboard (`/tasks`).

### 3.6 Reverse-Proxy Aware Architecture
Calibre-Web frequently runs behind reverse proxies (Nginx, Traefik, Apache, Caddy, Cloudflare):
- Includes custom `ReverseProxied` WSGI middleware that automatically rewrites `SCRIPT_NAME`, request schemes, and client IP addresses based on `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Prefix` headers.
- Supports path-prefix routing (hosting Calibre-Web under a sub-path such as `/calibre`) without breaking internal links or static asset references.
- Features `ScriptNameSessionInterface` to ensure session and remember-me cookies are restricted to the exact configured sub-path.

---

## 4. Codebase Directory & File Inventory

```
calibre-web/
├── .editorconfig                     # Code editor style rules
├── .eslintrc                         # ESLint rules for front-end JS
├── .gitignore                        # Git exclusion rules
├── CONTRIBUTING.md                   # Contribution guidelines
├── LICENSE                           # GNU GPLv3 License terms
├── MANIFEST.in                       # Packaging manifest rules
├── README.md                         # Main documentation and quick start
├── SECURITY.md                       # Security reporting policy
├── babel.cfg                         # Babel extraction configuration for i18n
├── cps.py                            # Standalone execution entrypoint wrapper
├── exclude.txt                       # Packaging exclusion patterns
├── messages.pot                      # Gettext translation template
├── optional-requirements.txt         # Optional dependencies (GDrive, LDAP, OAuth, etc.)
├── pyproject.toml                    # Modern PEP 517/621 build configuration
├── requirements.txt                  # Core mandatory runtime dependencies
├── library/                          # Default / sample Calibre library
│   └── metadata.db                   # Sample SQLite Calibre database
├── test/                             # Functional tests and test suite runner
└── cps/                              # Core Application Package
    ├── __init__.py                   # Flask app factory, logging, and dependency checks
    ├── main.py                       # CLI execution entrypoint and blueprint registrar
    ├── server.py                     # WebServer wrapper (Gevent / Tornado lifecycle)
    ├── tornado_wsgi.py               # Tornado WSGI container implementation
    ├── gevent_wsgi.py                # Gevent WSGI handler implementation
    ├── constants.py                  # Global constants, roles, bitmasks, format lists
    ├── cli.py                        # Command-line argument parsing (-p, -g, -c, etc.)
    ├── config_sql.py                 # Configuration persistence & Fernet encryption
    ├── db.py                         # Calibre SQLite database models and querying
    ├── ub.py                         # Calibre-Web SQLite user database models
    ├── web.py                        # Primary catalog browsing, reading, downloading
    ├── basic.py                      # Authentication views (login, logout, register, profile)
    ├── admin.py                      # Administration views and system settings
    ├── opds.py                       # OPDS XML feed generation
    ├── kobo.py                       # Kobo Store API sync emulation endpoints
    ├── kobo_auth.py                  # Kobo hardware authentication tokens
    ├── kobo_sync_status.py           # Kobo sync state tracker and shelf reconciliation
    ├── editbooks.py                  # Book editing, uploading, format conversion, deletion
    ├── search.py                     # Catalog search routing and query builders
    ├── search_metadata.py            # External metadata provider aggregation
    ├── shelf.py                      # Bookshelf creation, editing, and sharing
    ├── tasks_status.py               # Background task status viewing and management
    ├── remotelogin.py                # Magic link and QR-code authentication
    ├── helper.py                     # Formatting, email dispatch, covers, and conversions
    ├── binary_helper.py              # Resolvers for Calibre, Kepubify, and unrar binaries
    ├── converter.py                  # E-book format conversion orchestrator
    ├── uploader.py                   # File upload validation and ingestion
    ├── clean_html.py                 # HTML sanitization wrappers using nh3
    ├── cw_babel.py                   # Internationalization and locale selectors
    ├── reverseproxy.py               # WSGI reverse proxy header handling middleware
    ├── reverse_proxy_auth.py         # Reverse proxy header-based single sign-on (SSO)
    ├── subproc_wrapper.py            # Safe subprocess execution wrapper
    ├── updater.py                    # In-app updater checking GitHub releases
    ├── gdrive.py                     # Google Drive library management blueprint
    ├── gdriveutils.py                # Google Drive API and PyDrive2 client routines
    ├── oauth.py                      # OAuth authentication routing
    ├── oauth_bb.py                   # OAuth helper functions and database bindings
    ├── cw_advocate/                  # SSRF prevention network adapter package
    ├── cw_login/                     # Custom lightweight session and login manager
    ├── metadata_provider/            # Pluggable external metadata scraping providers
    │   ├── google.py                 # Google Books API scraper
    │   ├── amazon.py                 # Amazon book metadata scraper
    │   ├── comicvine.py              # ComicVine comic metadata scraper
    │   ├── scholar.py                # Google Scholar metadata provider
    │   ├── douban.py                 # Douban Chinese metadata provider
    │   └── lubimyczytac.py           # Lubimyczytac Polish metadata provider
    ├── services/                     # Background services and integration modules
    │   ├── worker.py                 # Multi-threaded task execution queue
    │   ├── background_scheduler.py   # APScheduler periodic task runner
    │   ├── Metadata.py               # Metadata provider coordinator
    │   ├── SyncToken.py              # Device synchronization token management
    │   ├── gmail.py                  # Google OAuth2 Gmail sending integration
    │   ├── goodreads_support.py      # Goodreads integration
    │   └── simpleldap.py             # LDAP / Active Directory authentication client
    ├── tasks/                        # Background task worker implementations
    │   ├── convert.py                # Asynchronous e-book format conversion task
    │   ├── mail.py                   # Asynchronous e-mail sending task (Kindle)
    │   ├── thumbnail.py              # Asynchronous cover thumbnail generation
    │   ├── metadata_backup.py        # Asynchronous OPF metadata file synchronization
    │   ├── upload.py                 # Asynchronous book archive upload processor
    │   ├── clean.py                  # Cache and temporary file cleanup task
    │   └── database.py               # Database maintenance and reconnect task
    ├── static/                       # Static web assets (CSS, JS, Fonts, Images)
    ├── templates/                    # Jinja2 HTML templates and XML feeds
    ├── themes/                       # Visual themes (Standard, Simple, Caliblur)
    └── translations/                 # 25+ language translation directories (.mo / .po)
```

---

## 5. Component Specifications & Module Responsibilities

### 5.1 Bootstrapping & Server Infrastructure
- **[cps.py](file:///home/carlos/THC/THCLabs/calibre-web/cps.py) & [cps/main.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/main.py):** Top-level execution entrypoints. Sets up system paths, manages Windows console hiding, parses CLI parameters, creates the Flask application instance, registers all blueprints, and starts the server.
- **[cps/__init__.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/__init__.py):** Central application factory. Configures cookie security, initializes LoginManager, sets up SQLAlchemy engines, checks dependency versions, initializes Babel locale selection, and wraps the WSGI application with reverse proxy handlers.
- **[cps/server.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/server.py):** Embedded WSGI server abstraction. Manages graceful shutdown signals (SIGINT, SIGTERM) and automatically chooses between Gevent WSGIServer and Tornado HTTPServer.
- **[cps/cli.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/cli.py):** Argument parsing engine supporting custom paths for settings database (`-p`), Google Drive database (`-g`), SSL certificates (`-c`, `-k`), listening interfaces (`-i`), and debug flags.

### 5.2 Database & Configuration Layers
- **[cps/db.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/db.py) (`CalibreDB`):** Maps SQLAlchemy models to Calibre's `metadata.db`. Provides functions to query books, filter by authors, series, tags, or custom columns, extract file formats, and write updated metadata back into Calibre tables.
- **[cps/ub.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/ub.py):** Maps SQLAlchemy models to Calibre-Web's `app.db`. Defines schemas for `User`, `Shelf`, `BookShelf`, `Downloads`, `Bookmark`, `ArchivedBook`, `ReadBook`, `RemoteAuthToken`, and `KoboSyncedBooks`.
- **[cps/config_sql.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/config_sql.py):** Configuration storage engine. Reads and writes server configuration in `app.db`, managing Fernet symmetric encryption keys to protect stored secrets (SMTP passwords, LDAP credentials).

### 5.3 Core Routing & User Interfaces
- **[cps/web.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/web.py):** Core web controller. Handles catalog listing pages, book detail views, cover image streaming, in-browser reading routers, download endpoints, and mark-as-read toggles.
- **[cps/basic.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/basic.py):** User session management. Manages login forms, user registrations, password resets, user profile updates (changing interface language, Kindle email, theme preference), and session termination.
- **[cps/admin.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/admin.py):** Comprehensive administrative portal. Handles user creation and role assignment, library directory path settings, SMTP server setup, external binary configuration, UI sidebar feature visibility, and system reboot/shutdown triggers.
- **[cps/editbooks.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/editbooks.py):** Catalog modification interface. Allows editing titles, authors, series, identifiers (ISBN, Google, Goodreads), uploading replacement book covers, uploading new book formats, converting formats, and deleting books.

### 5.4 Distribution & Synchronization Protocols
- **[cps/opds.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/opds.py):** Implements OPDS catalog specification (Atom XML). Serves categorized feeds (recent, popular, authors, series, tags, shelves) enabling e-reader apps to browse and download books over HTTP Basic Authentication.
- **[cps/kobo.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/kobo.py) & [cps/kobo_sync_status.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/kobo_sync_status.py):** Replicates the proprietary Kobo Store API (`storeapi.kobo.com`). Handles device initialization, user profile checks, synchronization tokens, reading position updates, and delivers e-books (specifically KEPUB) directly to Kobo e-readers over Wi-Fi.
- **[cps/remotelogin.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/remotelogin.py):** Provides passwordless login via mobile devices using time-limited tokens and QR codes.

### 5.5 Task Execution & Services
- **[cps/services/worker.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/services/worker.py):** Background thread worker executing non-blocking tasks from a queue.
- **[cps/tasks/convert.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/tasks/convert.py):** Invokes Calibre's `ebook-convert` or `kepubify` to convert books between supported formats (e.g. EPUB to MOBI, EPUB to KEPUB).
- **[cps/tasks/mail.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/tasks/mail.py):** Packages e-books into email attachments and delivers them to e-reader addresses (e.g. `@kindle.com`) via SMTP or Google Mail OAuth.
- **[cps/tasks/thumbnail.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/tasks/thumbnail.py):** Generates and caches cover thumbnails in multiple sizes to speed up UI rendering.

### 5.6 Security & Hardening Modules
- **[cps/cw_advocate/](file:///home/carlos/THC/THCLabs/calibre-web/cps/cw_advocate):** Outbound request validator. Validates IP addresses before HTTP connection establishment to block SSRF attempts targeting private IP ranges, loopback addresses, or cloud metadata endpoints.
- **[cps/reverse_proxy_auth.py](file:///home/carlos/THC/THCLabs/calibre-web/cps/reverse_proxy_auth.py):** Inspects incoming HTTP headers (e.g., `Remote-User`) to authenticate users via external single-sign-on gateways (Authelia, Authentik, Cloudflare Access).

---

## 6. Dependency Graph & Data Flow Architecture

### 6.1 Internal System Dependency Graph

```
[cps.py] / [CLI: cps]
       │
       ▼
[cps/main.py] ───► [cps/__init__.py] ───► [cps/server.py] (Gevent / Tornado)
       │                    │
       │                    ├───► [cps/db.py] ───────► (Calibre: metadata.db)
       │                    ├───► [cps/ub.py] ───────► (App: app.db)
       │                    ├───► [cps/config_sql.py] ─► (Fernet Secrets in app.db)
       │                    └───► [cps/cw_login/] ───► (User Sessions & RBAC)
       │
       ├───► Registers Web Blueprints:
       │     ├── [cps/web.py] (Catalog, Readers, Downloads)
       │     ├── [cps/basic.py] (Authentication, Profile)
       │     ├── [cps/admin.py] (System Settings, User Admin)
       │     ├── [cps/editbooks.py] (Metadata Edit, Upload, Delete)
       │     ├── [cps/shelf.py] (Custom Bookshelves)
       │     └── [cps/search.py] (Search Queries & Filters)
       │
       ├───► Registers Protocol Blueprints:
       │     ├── [cps/opds.py] (Atom XML Feeds for e-readers)
       │     ├── [cps/kobo.py] & [cps/kobo_auth.py] (Kobo Store API Sync)
       │     └── [cps/remotelogin.py] (QR Code / Magic Link Auth)
       │
       └───► Integrates Background Execution:
             ├── [cps/services/background_scheduler.py] (APScheduler)
             └── [cps/services/worker.py] (WorkerThread Queue)
                   ├── [cps/tasks/convert.py] ──► (Calibre / Kepubify Binaries)
                   ├── [cps/tasks/mail.py] ─────► (SMTP Server / Gmail API)
                   ├── [cps/tasks/thumbnail.py]─► (Cover Thumbnail Cache)
                   └── [cps/tasks/upload.py] ───► (Metadata Ingestion)
```

### 6.2 External Communication & Network Topography

```
+───────────────────────────────────────────────────────────────────────────+
|                              CLIENT CHANNELS                              |
|                                                                           |
|   Web Browsers        Mobile OPDS Apps       Kobo e-Readers       Kindle  |
|  (HTML5 Readers)    (Moon+, KOReader)       (Wireless Sync)      Devices  |
+─────────┬───────────────────┬──────────────────────┬─────────────────▲────+
          │                   │                      │                 │
          ▼                   ▼                      ▼                 │
+──────────────────────────────────────────────────────────────────────┼────+
|                    REVERSE PROXY / INGRESS LAYER                     │    |
|               (Nginx / Traefik / Apache / Cloudflare)                │    |
+─────────────────────────────────┬────────────────────────────────────┼────+
                                  │                                    │
                                  ▼                                    │
+──────────────────────────────────────────────────────────────────────┼────+
|                       CALIBRE-WEB APPLICATION                        │    |
|                                                                      │    |
|   +──────────────────────────────────────────────────────────────+   │    |
|   |                  WSGI Server (Gevent / Tornado)              |   │    |
|   +─────────────────────────────┬────────────────────────────────+   │    |
|                                 │                                    │    |
|   +─────────────────────────────▼────────────────────────────────+   │    |
|   |                     Flask Blueprint Router                   |   │    |
|   +───────┬──────────────┬──────────────┬──────────────┬─────────+   │    |
|           │              │              │              │             │    |
|           ▼              ▼              ▼              ▼             │    |
|       [Web UI]       [OPDS Feed]   [Kobo Sync]   [Admin API]         │    |
|           │              │              │              │             │    |
|   +───────┴──────────────┴──────────────┴──────────────┴─────────+   │    |
|   |                    Data Layer & Worker Engine                |   │    |
|   |         (Dual Scoped Sessions: CalibreDB & UserDB)           |   │    |
|   +───────┬──────────────────────┬─────────────────────┬─────────+   │    |
+───────────┼──────────────────────┼─────────────────────┼─────────────┼────+
            │                      │                     │             │
            ▼                      ▼                     ▼             │
   +─────────────────+    +─────────────────+   +─────────────────+    │
   |   metadata.db   |    |     app.db      |   | Background Queue|    │
   | (Calibre Books, |    | (Users, Shelves,|   | (Workers & Tasks)───►+
   | Authors, Files) |    | Roles, Settings)|   +────────┬────────+
   +─────────────────+    +─────────────────+            │
                                                         ▼
                                                +─────────────────+
                                                | External Tools  |
                                                | (ebook-convert, |
                                                |  kepubify, etc.)|
                                                +─────────────────+
```

### 6.3 End-to-End Operation Traces

#### Trace A: In-Browser Book Reading
1. User clicks read on an EPUB book detail page.
2. `cps/web.py` receives request and verifies user download/read permissions.
3. Controller determines book format and renders the EPUB reader HTML view containing `epub.js`.
4. Browser requests the book content stream; `web.py` streams file chunks from the book directory.
5. While reading, client JavaScript posts reading progress (progress percentage, CFI bookmark string) back to `/bookmark`.
6. Calibre-Web persists progress into the `Bookmark` table in `app.db` without modifying Calibre's `metadata.db`.

#### Trace B: Wireless Kobo Device Sync
1. Kobo e-reader initiates synchronization over Wi-Fi, calling the emulated initialization endpoint `/v1/initialization`.
2. `cps/kobo_auth.py` validates the Kobo authentication token stored in device headers.
3. Kobo queries `/v1/library/sync`; `cps/kobo.py` checks user's library and shelf configurations in `app.db`.
4. Controller identifies new or changed books, resolving files from `metadata.db`.
5. Device requests e-book files; Calibre-Web serves the file, converting to KEPUB on-the-fly or pulling pre-converted KEPUB assets.
6. Reading status and percentage read are bidirectional: Kobo updates Calibre-Web's `app.db`, and Calibre-Web updates Kobo.

#### Trace C: Send-to-Kindle Delivery
1. User clicks Send to Kindle on a book details page.
2. `cps/web.py` validates that the user has a configured Kindle email address and checks if the book exists in MOBI or EPUB format.
3. If conversion is needed (e.g. from EPUB to MOBI), a conversion task is queued in `cps/services/worker.py`.
4. A background mail task (`cps/tasks/mail.py`) is queued.
5. The worker thread picks up the mail task, retrieves SMTP or Google Mail credentials (decrypted via Fernet from `app.db`), attaches the e-book file, and dispatches the email to Amazon's Whispernet service.
6. User observes delivery status in real-time on the `/tasks` dashboard.

---

## 7. Configuration, Storage, & Secrets Management Matrix

Calibre-Web configuration is stored hierarchically across CLI parameters, environment variables, and the `app.db` configuration table.

### 7.1 Command-Line Interface Parameters
| Flag | Parameter | Description |
|---|---|---|
| `-p` | `settings_path` | Absolute path and filename for the settings database (default: `app.db` in config directory) |
| `-g` | `gd_path` | Absolute path and filename for Google Drive database (default: `gdrive.db`) |
| `-c` | `certfilepath` | Path to SSL certificate file for native HTTPS termination |
| `-k` | `keyfilepath` | Path to SSL private key file for native HTTPS termination |
| `-i` | `ip_address` | Listening IP address (default: `0.0.0.0` or `::`) |
| `-s` | `user_credentials` | Emergency password reset: sets credentials for user directly from CLI |

### 7.2 Core Environment Variables
| Variable Name | Default Value | Operational Purpose |
|---|---|---|
| `CALIBRE_DBPATH` | Working directory or `~/.calibre-web` | Directory containing `app.db` and configuration keys |
| `CALIBRE_PORT` | `8083` | Default TCP listening port |
| `CACHE_DIRECTORY` | `cps/cache` | Directory for cached cover thumbnails and transient conversion files |
| `SECRET_KEY` | Generated from `app.db` | Secret key for signing Flask session cookies |
| `COOKIE_PREFIX` | Empty string | Custom prefix for session and remember-me cookie names |
| `FLASK_DEBUG` | Disabled (`0`) | Enables debug mode and cache busting for static assets |

### 7.3 Role-Based Access Control (RBAC) Bitmask Table
User authorizations are computed using integer bitwise operators:
- `ROLE_USER`: Standard user baseline (value 0).
- `ROLE_ADMIN`: Full administrative privileges (bit 0).
- `ROLE_DOWNLOAD`: Allowed to download e-book files (bit 1).
- `ROLE_UPLOAD`: Allowed to upload new e-books (bit 2).
- `ROLE_EDIT`: Allowed to edit book metadata (bit 3).
- `ROLE_PASSWD`: Allowed to change own password (bit 4).
- `ROLE_ANONYMOUS`: Applied when public unauthenticated access is enabled (bit 5).
- `ROLE_EDIT_SHELFS`: Allowed to create and modify bookshelves (bit 6).
- `ROLE_DELETE_BOOKS`: Allowed to delete books from the library (bit 7).
- `ROLE_VIEWER`: Allowed to use in-browser book readers (bit 8).

---

## 8. Multi-Protocol Ingestion & Distribution Channels

Calibre-Web provides diverse communication channels tailored for different client types:

| Channel / Interface | Protocol / Format | Target Clients | Authentication Model | Key Capabilities |
|---|---|---|---|---|
| **Web UI** | HTTPS / HTML5 / CSS / JS | Desktop & mobile web browsers | Session Cookie / Remember Token / Reverse Proxy Header | Full catalog browsing, in-browser reading, metadata editing, user admin, theme switching. |
| **OPDS Catalog** | Atom XML / OPDS 1.2 | Dedicated e-reader apps (Moon+, FBReader, Aldiko, KOReader) | HTTP Basic Authentication / Rate-limited | Categorized catalog navigation, faceted search, direct download of e-book files. |
| **Kobo Store API** | HTTPS / JSON REST API | Physical Kobo e-readers | Kobo Auth Token (`SyncToken`) | Wirelessly syncs books, collections, reading progress, and KEPUB assets directly to hardware. |
| **Send-to-Kindle** | SMTP / Google Mail API | Amazon Kindle hardware & apps | Internal system credentials | Asynchronously emails e-books to Kindle personal document addresses (`@kindle.com`). |
| **Magic Link / QR** | HTTPS / JSON Token | Mobile smartphone browsers | Short-lived signed remote login tokens | Passwordless login for mobile devices scanning a QR code from a logged-in desktop screen. |
| **Google Drive** | Google Drive v3 REST API | Cloud-hosted Calibre libraries | OAuth 2.0 / Service Account | Reads and streams library databases and e-book binaries hosted in Google Cloud storage. |

---

## 9. Current Implementation Status & Capabilities

### 9.1 Implemented & Production-Ready Features
- **Catalog Navigation:** Browsing by title, author, series, tag, publisher, language, rating, formats, recently added, and random picks.
- **Dual-Database Segregation:** Full read/write compatibility with Calibre's `metadata.db` and dedicated storage in `app.db`.
- **In-Browser Reading:** Integrated viewers for EPUB, KEPUB, PDF, TXT, CBR/CBZ/CBT/CB7 comics, and audiobooks.
- **Device Synchronization:** Production-ready Kobo Store API emulation and automated Send-to-Kindle mailers.
- **Background Worker Engine:** Multi-threaded conversion and email worker queues with live UI status monitoring.
- **SSRF Hardening:** Advocate network integration preventing malicious server-side request redirection.
- **Internationalization:** Multi-language interface with localized date and time representation across 25+ locales.
- **Customizable Themes:** Includes Standard Bootstrap theme, Simple high-contrast theme, and Caliblur modern dark theme.

### 9.2 Optional & Pluggable Features (Dependent on Environment)
- **Format Conversion:** Requires system installation of Calibre's `ebook-convert` or standalone `kepubify`.
- **Comic RAR Extraction:** Requires external `unrar` binary on system PATH.
- **Social & Directory Login:** Pluggable modules for LDAP / Active Directory and OAuth 2.0 (Google, GitHub).
- **External Metadata Scraping:** Scrapers for Google Books, Amazon, ComicVine, Douban, and Lubimyczytac.

---

## 10. Architectural Directives for Downstream LLMs

Any downstream Large Language Model generating code, creating pull requests, or refactoring components within `calibre-web` must abide by these strict architectural rules:

1. **Preserve Calibre Database Integrity:** Never alter the schema of `metadata.db`. Any feature requiring custom persistence (such as reading state, user notes, or application settings) MUST be added to `app.db` via `cps/ub.py` or `cps/config_sql.py`.
2. **Adhere to the Dual-Database Session Model:** Do not mix queries across SQLAlchemy sessions. Use `calibre_db.session` strictly for Calibre entities, and `ub.session` strictly for user and application entities.
3. **Prevent SSRF on Outbound Requests:** When implementing new metadata scrapers or webhook integrations, never use raw `requests.get` or `urllib`. Always use `cw_advocate` or `ValidatingHTTPAdapter` to prevent SSRF against private networks.
4. **Enforce HTML Sanitization:** Any user-supplied text or remote metadata rendered into the UI (especially book summaries and comments) must pass through `clean_html` (`nh3`) to neutralize XSS vulnerabilities.
5. **Keep Request Handlers Non-Blocking:** Never execute heavy CLI binaries (such as `ebook-convert`) or network email dispatches synchronously inside Flask request handlers. Always package heavy tasks into worker jobs under `cps/tasks/` and dispatch them through `WorkerThread`.
6. **Support Graceful Degradation:** When writing integrations that rely on optional Python packages or system binaries, always guard imports with try/except blocks and verify binary availability before exposing UI controls.
