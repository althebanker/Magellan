name: daily-screen
# Runs the screener every weekday morning in the cloud (no machine needed),
# then publishes dashboard.html to GitHub Pages. Bookmark the Pages URL.

on:
  schedule:
    - cron: "0 11 * * 1-5"      # 11:00 UTC weekdays (~6-7am US ET). Adjust to taste.
  workflow_dispatch: {}          # lets you trigger a run by hand from the Actions tab

permissions:
  contents: write
  pages: write
  id-token: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install yfinance pandas numpy
      - name: Run screener
        env:
          FMP_API_KEY: ${{ secrets.FMP_API_KEY }}        # optional: add in repo Settings > Secrets
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
        run: python screener.py
      - name: Publish to Pages
        run: |
          mkdir -p _site && cp dashboard.html _site/index.html
      - uses: actions/upload-pages-artifact@v3
        with: { path: _site }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
