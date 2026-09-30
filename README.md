# SignElectric

A Django web app for signing PDFs and images in the browser. Upload a document, add
text signatures or draw freehand on a canvas, and export the result as a signed PDF.

## Features

- Upload a **PDF** (a chosen page is rendered as the signing surface) or an **image**
- Add and style text (bold, italic, underline, colour, handwriting-style fonts)
- Freehand drawing with an adjustable brush size and colour
- Export the signed document to PDF
- Uploaded files are treated as temporary and cleaned up when you leave the editor

## Tech stack

- **Backend:** Django 6.1 (Python 3.14)
- **Canvas / editor:** [Fabric.js 7](https://fabricjs.com/) (loaded from a CDN)
- **PDF export:** jsPDF (CDN)
- **PDF → image:** `pdf2image` + `pypdf`, backed by [Poppler](https://poppler.freedesktop.org/)
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

1. On the homepage, choose whether to upload a PDF or an image.
2. Upload the file (for a PDF, pick which page to sign).
3. In the editor, add text, draw your signature, and adjust styling.
4. Click **Export** to download the signed PDF.

## Running tests

```bash
python manage.py test documents
```

## Note

This project ships with Django's development configuration (`DEBUG = True` and a
throwaway `SECRET_KEY`). Set a real secret key and disable debug before deploying
anywhere public.
