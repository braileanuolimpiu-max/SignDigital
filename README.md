# SignElectric

A Django web app for signing documents in the browser. Upload a PDF, a Word file or an
image, add text signatures or draw freehand on any page, and export the result as a signed PDF.

## Features

- Upload a **PDF**, a **Word file** (doc, docx) or an **image** (png, jpg) with one button
- Every page is shown in a scrollable editor, on desktop and on phones
- Add and style text (bold, italic, underline, colour, handwriting-style fonts)
- Freehand drawing with an adjustable brush size and colour
- Export the signed document to PDF
- Uploaded files are temporary: they are deleted two minutes after you leave the editor,
  or after ten minutes without any sign of life from the page

## Tech stack

- **Backend:** Django 6.1 (Python 3.14)
- **Canvas / editor:** [Fabric.js 7](https://fabricjs.com/) (loaded from a CDN)
- **PDF export:** jsPDF (CDN)
- **PDF → image:** `pdf2image`, backed by [Poppler](https://poppler.freedesktop.org/)
- **Word → PDF:** [LibreOffice](https://www.libreoffice.org/) in headless mode
- **Images:** Pillow
- **Database:** SQLite (default)

## Prerequisites

- Python 3.12+
- **Poppler** — required by `pdf2image` to render PDF pages.
  - Windows: install Poppler (e.g. via `winget install oschwartz10612.Poppler`) and
    point `POPPLER_PATH` in `config/settings.py` at its `Library/bin` folder.
  - macOS: `brew install poppler`
  - Linux: `sudo apt install poppler-utils`

  On macOS/Linux, Poppler is usually on the system `PATH`, so you can set
  `POPPLER_PATH = None` in `config/settings.py`.
- **LibreOffice** — required only for Word files.
  - Windows: `winget install TheDocumentFoundation.LibreOffice`, then point
    `LIBREOFFICE_PATH` in `config/settings.py` at `soffice.com`
    (usually `C:\Program Files\LibreOffice\program\soffice.com`).
  - macOS/Linux: install LibreOffice and point `LIBREOFFICE_PATH` at the `soffice` binary.

  Without it, PDFs and images still work and Word uploads show a clear error.

## Setup

```bash
# 1. Clone
git clone <your-repo-url>
cd SignElectric

# 2. Create and activate a virtual environment
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Apply migrations
python manage.py migrate

# 5. Run the dev server
python manage.py runserver
```

Then open http://127.0.0.1:8000/.

## Usage

1. On the homepage, pick a file and click **Sign only by me**.
2. In the editor, scroll to the page you want, add text, draw your signature, and adjust styling.
3. Click **Export** to download the signed PDF.

Upload limits (`MAX_UPLOAD_SIZE`, `MAX_PAGES`) are set in `config/settings.py`.

## Multiple signing and email

Click **Multiple signing** on the homepage, add placeholders (one per place someone signs),
then **Sign & send**. Each person gets one email with a link; when they press **Sign**, their
name is written into all their placeholders and the signed PDF is emailed to the sender and
everyone who has signed. When everyone has signed, the document is deleted.

Without email settings, emails are printed in the terminal. To send real emails with Gmail:

1. Turn on 2-Step Verification for the Google account, then create an app password at
   https://myaccount.google.com/apppasswords.
2. Copy `config/local_settings.example.py` to `config/local_settings.py` (git ignores it) and fill in:
   - `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD`
   - `SITE_URL`: the address people open, e.g. `http://192.168.1.10:8000` on your Wi-Fi
     (the one `runlan.bat` prints).

## Running tests

```bash
python manage.py test documents
```

## Note

This project ships with Django's development configuration (`DEBUG = True` and a
throwaway `SECRET_KEY`). Set a real secret key and disable debug before deploying
anywhere public.
