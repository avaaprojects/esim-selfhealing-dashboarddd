# uploads/ — operator uploads

Files submitted through the Input screen. **Not** predefined data: that lives in `../data/`.

| Folder | Accepts |
|---|---|
| `screenshots/` | PNG, JPEG, GIF, WebP (checked by content; SVG is refused) |
| `csv/` | `.csv` with a header row |
| `logs/` | `.txt`, `.log`, `.out` |

Files are stored as `UPL-0007_<sanitised name>`. Their metadata (client, group, device, incident, who uploaded, checksum) is in `backend/data/inputs.db`. Uploaded files are gitignored, never served without a sign-in, and shown as **OPERATOR UPLOAD** (unverified). Limit: 10 MB per file. Override the location with `UPLOADS_DIR`.
