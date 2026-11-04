# SignElectric

A Django web app for signing documents in the browser. Upload a PDF, a Word file or an
image, sign it yourself, or send it to other people to sign by email.

## Features

- Upload a **PDF**, a **Word file** (doc, docx) or an **image** (png, jpg) with one button
- Every page is shown in a scrollable editor, on desktop and on phones
- Add text in the Mr De Haviland handwriting font, in any colour
- Freehand drawing with an adjustable brush size and colour
- **Auto Place** finds where to sign (PDF and Word only) and puts the signature there
- **Multiple signing**: add placeholders and email each person a link to sign
- Download the signed document as a PDF
- Uploaded files are temporary: they are deleted two minutes after you leave the editor,
  or after ten minutes without any sign of life from the page

## Tech stack

- **Backend:** Django 6.1 (Python 3.14)
- **Canvas / editor:** [Fabric.js 7](https://fabricjs.com/) (loaded from a CDN)
- **PDF export:** jsPDF (CDN)
- **PDF → image:** `pdf2image`, backed by [Poppler](https://poppler.freedesktop.org/)
- **Reading text and lines for Auto Place:** [PyMuPDF](https://pymupdf.readthedocs.io/)
- **Word → PDF:** [LibreOffice](https://www.libreoffice.org/) in headless mode
- **Images:** Pillow
- **Database:** SQLite (default)

## Prerequisites

- Python 3.12+
- **Poppler** — required by `pdf2image` to render PDF pages.
  - Windows: `winget install oschwartz10612.Poppler`. `POPPLER_PATH` in
    `config/settings.py` already points at the folder winget uses, for any Windows user.
    If you install another Poppler version, change the version number in that path.
  - macOS: `brew install poppler`
  - Linux: `sudo apt install poppler-utils`

  On macOS/Linux, Poppler is usually on the system `PATH`, so you can set
  `POPPLER_PATH = None` in `config/settings.py`.
- **LibreOffice** — required only for Word files.
  - Windows: `winget install TheDocumentFoundation.LibreOffice`. `LIBREOFFICE_PATH` in
    `config/settings.py` already points at
    `C:\Program Files\LibreOffice\program\soffice.com`.
  - macOS/Linux: install LibreOffice; `LIBREOFFICE_PATH` is `/usr/bin/soffice`.

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

# 4. Create the database (needed once, and again after pulling new migrations)
python manage.py migrate

# 5. Start the server on your local network
python manage.py runserver 0.0.0.0:8000
```

`0.0.0.0` makes the server reachable from every device on your Wi-Fi, not only this PC.
Find your PC's address with `ipconfig` (the "IPv4 Address" line, e.g. `192.168.18.92`)
and open `http://<that address>:8000/` on the PC or on your phone. Use this same address
as `SITE_URL` (see below), so the links in the emails point to it too.

Make sure the virtual environment is active first (your terminal line starts with `(venv)`).

If you see `no such table: documents_document`, you skipped step 4: run `python manage.py migrate`.

## Usage

1. On the homepage, pick a file and click **Sign only by me**.
2. In the editor, click **Auto Place** to put your signature where the document asks for it,
   or add text and draw your signature yourself.
3. Click **Sign** to download the signed PDF.

Upload limits (`MAX_UPLOAD_SIZE`, `MAX_PAGES`) are set in `config/settings.py`.

## Auto Place

Auto Place reads the text of PDF and Word files (images have no text, so the button is
hidden for them) and looks for places to sign:

- Labels like `Signature`, `Semnătura`, `Signed by`, `Sign here`, `Semnătura olografă`, `Подпись`
- The line after the label, typed (`________`) or drawn by Word (underlined spaces)
- The signer's name: before the label, after a slash (`____ /Ion Pop/`), or on the lines
  above it (`SRL Sugar Soft prin Ion Pop`, a company name, or a role like `Prestator`)
- In **Multiple signing**, also lines with no label (typed or drawn), often with the name under them

It skips places that are already signed, lines inside tables, lines under text,
lines to write on (several lines one under another) and lines across the whole page.
The code is in `documents/autoplace.py`.

## Multiple signing and email

Click **Multiple signing** on the homepage, add placeholders (one per place someone signs,
or use **Auto Place**), then **Sign & send**. Each person gets one email with a link; when they press **Sign**, their
name is written into all their placeholders and the signed PDF is emailed to the sender and
everyone who has signed. When everyone has signed, the document is deleted.

Without email settings, emails are printed in the terminal. To send real emails with Gmail:

1. Turn on 2-Step Verification for the Google account, then create an app password at
   https://myaccount.google.com/apppasswords.
2. Create `config/local_settings.py` (git ignores it) with these three lines:
   ```python
   SITE_URL = 'http://192.168.18.92:8000'
   GMAIL_ADDRESS = 'your.address@gmail.com'
   GMAIL_APP_PASSWORD = 'the16letterspassword'
   ```
   - `SITE_URL`: the address people open on your Wi-Fi
     (your PC's IPv4 address from `ipconfig`, with `:8000`).
   - `GMAIL_APP_PASSWORD`: the app password, without spaces.

   Never commit this file: it holds your password. Restart the server after changing it.

## Running tests

```bash
python manage.py test documents
```

## Note

This project ships with Django's development configuration (`DEBUG = True` and a
throwaway `SECRET_KEY`). Set a real secret key and disable debug before deploying
anywhere public.
