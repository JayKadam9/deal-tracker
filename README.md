# Amazon.in price-drop deals

A small static website that lists in-stock Amazon.in deals which are:

- at least **₹1,000**
- at least **10% below** BuyHatke’s historical average
- rated **4.0 or higher** (unrated products are excluded)

Deals are grouped by category. You can filter, sort, search, and hide items with fewer than 20 reviews. Product names link to `https://www.amazon.in/dp/{ASIN}`.

The average is BuyHatke’s history average, **not** a strict 30-day mean. A drop of 80% or more can come from an inflated average rather than a typical selling price.

The site ships with a recent snapshot in `data/deals.json` so it works as soon as you publish it. A GitHub Action re-fetches the list every morning at **9:00 AM IST** and republishes the page. If BuyHatke blocks GitHub’s servers, the Action leaves the last good list in place and the site keeps showing yesterday’s deals.

This project is not affiliated with Amazon or BuyHatke.

## Set it up on GitHub Pages (about five minutes)

You only need GitHub’s website. You do not need Git, a terminal, or to give anyone else access to your account.

1. **Unzip the folder** you were sent. You should see `index.html`, `app.js`, `styles.css`, a `data` folder, a `scripts` folder, this README, `workflow-refresh.yml`, and a `.github` folder.

   The `.github` folder is hidden in Finder and File Explorer by default:
   - **Mac:** in Finder, press **Command-Shift-Period** (⌘⇧.) to show hidden files.
   - **Windows:** in File Explorer, open the **View** tab (or the View menu) and turn on **Hidden items**.

2. On GitHub, click **New repository**. Give it a name such as `amazon-in-deals`, set it to **Public**, and click **Create repository**. Do not add a README, licence, or `.gitignore` — start from an empty repo.

3. On the empty repo page, click **uploading an existing file**. Drag in the *contents* of the unzipped folder (the files themselves, not the wrapping folder). You want `index.html` at the root of the repo, together with `.github`, `data`, and `scripts`.

   If you cannot see or upload `.github`, upload everything else first, commit it, then click **Add file → Create new file**. For the filename type exactly:

   `.github/workflows/refresh.yml`

   Open `workflow-refresh.yml` from the unzipped folder, copy all of it, paste it into the GitHub editor, and commit.

4. Commit the files to the **`main`** branch.

5. Open **Settings → Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions** and save.

6. Open the **Actions** tab. Click **Refresh deals and deploy**, then **Run workflow** (branch: `main`). The first run publishes the site with the bundled snapshot; later scheduled runs refresh the data.

   If GitHub asks you to approve a `github-pages` environment, open that Actions run and approve it (or go to **Settings → Environments**).

7. When the run is green, the site is at:

   `https://<your-github-username>.github.io/<repository-name>/`

   For example, a repo named `amazon-in-deals` under `JayKadam9` would be `https://JayKadam9.github.io/amazon-in-deals/`.

The Action also runs every day at 9:00 AM IST (`cron: '30 3 * * *'`). You can start a refresh any time from the Actions tab with **Run workflow**.

## If a daily refresh fails

BuyHatke sits behind Cloudflare and sometimes blocks requests from GitHub-hosted runners (often an HTTP 403). The fetch script then exits without overwriting `data/deals.json`, and the site keeps the last good list. Check the failed run in the Actions tab for the error. The bundled snapshot (or the last successful refresh) stays live.

## What’s in this folder

| Path | What it is |
| --- | --- |
| `index.html`, `styles.css`, `app.js` | The website (no build step) |
| `data/deals.json` | The deals list the site loads |
| `scripts/fetch_deals.py` | Fetches fresh deals from BuyHatke (Python 3 standard library only) |
| `.github/workflows/refresh.yml` | Daily refresh + GitHub Pages deploy |
| `workflow-refresh.yml` | A visible copy of that workflow, for the fallback in step 3 |

To preview on your computer, from this folder run:

```bash
python3 -m http.server 8000
```

Then open `http://localhost:8000`. Opening `index.html` as a file (`file://`) will not load the JSON.

To run the fetch yourself:

```bash
python3 scripts/fetch_deals.py --out data/deals.json
```

If the fetch fails or returns fewer than 100 qualifying items, the existing `data/deals.json` is left unchanged.
