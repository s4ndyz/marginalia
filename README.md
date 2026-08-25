# Marginalia

A macOS-first epub reader and annotation tool, built for readers who want to highlight, annotate, and search through their books without leaving a distraction-free interface.

## Features

- **Highlights & Annotations** — Select text and attach notes directly in the margin, just like a physical book.
- **Full-Text Search** — Instantly search across the entire book, not just the current chapter.
- **Table of Contents Navigation** — Jump to any chapter or section directly from the epub's TOC.
- **WYSIWYG Content Editing** — Edit annotations and notes with live formatting, no markup syntax required.
- **Markdown Export** — Export your highlights and annotations as clean Markdown files for use in other note-taking apps.

## Tech Stack

- **Language:** Python
- **UI Framework:** PySide6 (Qt for Python)
- **Storage:** SQLite (local, per-library database for books, highlights, and annotations)

## Installation

### Prerequisites

- macOS
- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) (recommended for dependency management)

### Setup

```bash
# Clone the repository
git clone https://github.com/<your-username>/marginalia.git
cd marginalia

# Install dependencies with uv
uv sync

# Run the app
uv run marginalia
```

> If you're in mainland China or experiencing slow package downloads, you can speed up installation with a mirror, e.g.:
> ```bash
> uv sync --index-url https://pypi.tuna.tsinghua.edu.cn/simple
> ```

## Usage

1. Launch Marginalia and open an `.epub` file from the file picker.
2. Select text anywhere in the book to highlight it or attach a note.
3. Use the search bar to find any word or phrase across the whole book.
4. Navigate chapters via the table of contents panel on the side.
5. When you're ready to review your notes elsewhere, export them as Markdown from the export menu.

## Data Storage

All books, highlights, and annotations are stored locally in a SQLite database — nothing is sent to any server. Your library stays on your machine.
