import json
import os
import re
import time
import gspread
import requests as raw_requests
from bs4 import BeautifulSoup
from curl_cffi import requests

# Set processing batch parameters
BATCH_SIZE = 10         # Number of URLs per sheet write batch
MAX_ROWS_PER_RUN = 50   # Maximum rows to process per workflow run

def trigger_github_dispatch():
    """Triggers a repository dispatch event to auto-start the next workflow run."""
    token = os.environ.get("GH_PAT_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    
    if token and repo:
        url = f"https://api.github.com/repos/{repo}/dispatches"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        data = {"event_type": "continue_scraping"}
        res = raw_requests.post(url, headers=headers, json=data)
        if res.status_code in [200, 204]:
            print("\n Successfully auto-triggered the next GitHub Action workflow run!")
        else:
            print(f"\n Failed to trigger next run: HTTP {res.status_code} - {res.text}")
    else:
        print("\n Trigger skipped: GitHub token or repository context missing.")

def extract_agent_data(session, url):
    """Fetches HTML and parses embedded Next.js JSON-LD schema or falls back to HTML tags."""
    try:
        response = session.get(url, timeout=15)
        if response.status_code != 200:
            return ["", "", "", "", "", f"Error: HTTP {response.status_code}"]

        # Try extracting Next.js JSON-LD Schema
        match = re.search(r'(\{"@context":"https://schema\.org","@type":"RealEstateAgent".*?\})', response.text)
        if match:
            json_str = match.group(1).replace(r'\"', '"')
            data = json.loads(json_str)

            description = data.get("description", "")
            agency = description.split("- ")[-1].split(".")[0].strip() if "-" in description else ""

            return [
                data.get("name", ""),
                agency,
                data.get("telephone", ""),
                data.get("email", ""),
                data.get("image", ""),
                "Scraped"
            ]

        # Fallback to standard HTML tag parsing
        soup = BeautifulSoup(response.text, "html.parser")
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""

        return [name, "", "", "", "", "Scraped (HTML Fallback)"]

    except Exception as e:
        return ["", "", "", "", "", f"Error: {str(e)}"]

def main():
    secret_key = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not secret_key:
        raise ValueError("GCP_SERVICE_ACCOUNT_KEY environment variable is missing.")

    gcp_credentials = json.loads(secret_key)
    gc = gspread.service_account_from_dict(gcp_credentials)

    # Connect to sheet by title
    sheet = gc.open("Homely Agents Data").sheet1

    # Ensure header setup
    headers = ["URL", "Name", "Agency", "Phone", "Email", "Image URL", "Status"]
    existing_headers = sheet.row_values(1)
    if not existing_headers:
        sheet.update(range_name="A1:G1", values=[headers])

    # Fetch URLs and current statuses
    urls_in_col_a = sheet.col_values(1)[1:]
    statuses_in_col_g = sheet.col_values(7)[1:]

    if not urls_in_col_a:
        print("No URLs found in Column A to process.")
        return

    session = requests.Session(impersonate="chrome120")
    
    # Filter pending rows (skip already successfully scraped rows)
    pending_items = []
    for idx, url in enumerate(urls_in_col_a):
        row_num = idx + 2
        status = statuses_in_col_g[idx] if idx < len(statuses_in_col_g) else ""
        
        if status != "Scraped" and url.strip().startswith("http"):
            pending_items.append((row_num, url.strip()))

    total_pending = len(pending_items)
    print(f"Total rows: {len(urls_in_col_a)} | Pending rows to scrape: {total_pending}")

    if total_pending == 0:
        print("All URLs have already been scraped!")
        return

    # Slice tasks for current run limit
    items_to_process = pending_items[:MAX_ROWS_PER_RUN]
    remaining_after_run = total_pending - len(items_to_process)

    # Process items in batches
    for i in range(0, len(items_to_process), BATCH_SIZE):
        chunk = items_to_process[i:i + BATCH_SIZE]
        batch_updates = []

        print(f"\n--- Processing Batch {i//BATCH_SIZE + 1} ({len(chunk)} URLs) ---")

        for row_num, url in chunk:
            print(f"Row {row_num}: Extracting {url}")
            row_data = extract_agent_data(session, url)

            batch_updates.append({
                "range": f"B{row_num}:G{row_num}",
                "values": [row_data]
            })

            time.sleep(1)

        # Write current batch immediately to Google Sheets
        if batch_updates:
            sheet.batch_update(batch_updates)
            print(f"--> Batch {i//BATCH_SIZE + 1} written to Google Sheets!")

    # Auto-trigger next run if pending items still remain
    if remaining_after_run > 0:
        print(f"\n{remaining_after_run} pending items remaining. Triggering next execution run...")
        trigger_github_dispatch()
    else:
        print("\nAll target URLs have been successfully processed!")

if __name__ == "__main__":
    main()
